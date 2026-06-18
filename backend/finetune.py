import os.path
import json
import torch
from transformers import (AutoModelForCausalLM, BitsAndBytesConfig,get_cosine_schedule_with_warmup,
                          AutoTokenizer)
from peft import prepare_model_for_kbit_training,LoraConfig, get_peft_model, TaskType,PeftModel
import torch.nn as nn
import copy
from torch.nn.utils import clip_grad_norm_
from datetime import datetime
from typing import Optional, List, Dict,Literal
from torch.utils.data import Dataset,DataLoader
import gc
from matplotlib import pyplot as plt


scene_model_id='Qwen/Qwen2.5-7B-Instruct'
org_scene_model_path='E:/LLMs/Scene_Qwen2.5-7B-Instruct'
# scene_merged_dir="./scene_agent_merged"
SCENE_LORA_DIR="./scene-lora"
SCENE_MODE_EMBED_DIR="./scene-mode-embed"

abstract_model_id='Qwen/Qwen2.5-7B-Instruct'
org_abstract_model_path='E:/LLMs/Abstract_Qwen2.5-7B-Instruct'
# abstract_merged_dir="./abstract_agent_merged"
ABSTRACT_LORA_DIR="./abstract-lora"
ABSTRACT_MODE_EMBED_DIR="./abstract-mode-embed"

idea_sys=("下面是描述一个特定任务的说明。请你根据用户输入，通过不断提问和多轮交流，"
            "深入挖掘用户想要介绍的农产品的亮点（用户选择模式0）或者农村生活的故事亮点（用户选择模式1），"
          "在用户发出“我现在需要你给我生成我的亮点总结。”的请求后，生成50-150字的关键短语形式的亮点总结。")


abstract_sys=("下面是描述一个特定任务的说明。"
            "请你根据关键短语组成的亮点分析生成对应的农产品视频文本摘要（500字左右），围绕农产品的介绍或者故事设计展开。")


scene_sys='''
下面是一个特定任务的说明。请你根据农产品视频文本摘要，设计长度合适的多个视频片段的拍摄建议（JSON格式），每个片段的拍摄建议的JSON格式要求如下:
- 必须只输出一个JSON对象，不要有任何额外的解释、注释或Markdown标记。
- 输出必须严格遵循下方给出的JSON Schema,注意花括号的个数和对应关系，最后不要多出一个或者少一个花括号。
[JSON Schema]
{
  "画面":（用简单的文本形式描述片段中场景布局、人物动作与神态、人物关系、光线与时间、运镜、出场元素等）
  "音频":（用简单的文本形式从提供的音频文件中分析背景音频，包括大自然声音、背景人群说话声、装置声音、噪声、配乐等等）
  "文案":[{"人物":（主人公，旁白，配角1，配角2等等）,"台词":（说话的内容，以字幕或者声音的形式呈现）},{"人物":"","台词":""},{"人物":"","台词":""}]
}
从画面、音频、文案三个角度进行分析。文案是JSON对象的列表，按照人物的对话顺序组织JSON对象，JSON对象包括人物和台词两个属性。
如果该片段没有任何非人声音频或者人物对话，文案输出成[]空列表，音频写"无音频"。
三个部分生成内容总字数建议不超过350-400字，如果人物台词轮数多、字数多，提炼出当前片段的主要台词。
'''



class Video_Assistant_System():
    def __init__(self,lamb):
        self.lamb=lamb
        self.abstract_model=None
        self.scene_model=None
        self.mode_map={"产品介绍":0,"剧情设计":1}
        self._load_abstract_agent()
        # 装载分词器
        self.tokenizer=AutoTokenizer.from_pretrained(org_scene_model_path,local_files_only=True)

    def _load_scene_agent(self):
        if self.abstract_model is not None:
            del self.abstract_model
            self._release_gpu_memory()
            self.abstract_model=None

        # Agent1(摘要->片段描述)量化配置
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

        base_model = AutoModelForCausalLM.from_pretrained(
            pretrained_model_name_or_path=org_scene_model_path,
            quantization_config=quantization_config,
            device_map="cuda",
            attn_implementation="eager",
            local_files_only=True,
        )


        # prepare_model_for_kbit_training()包含冻结参数、梯度流动、梯度检查（降低显存占用）和LayerNorm精度提升
        base_model = prepare_model_for_kbit_training(base_model)
        self.scene_model=base_model
        self._load_lora()


    def _load_abstract_agent(self):
        if self.scene_model is not None:
            del self.scene_model
            self._release_gpu_memory()
            self.scene_model=None

        # Agent2（用户对话->亮点（无需微调）,亮点->摘要）量化配置
        quantization_config = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_use_double_quant=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
        )

        base_model = AutoModelForCausalLM.from_pretrained(
            pretrained_model_name_or_path=org_abstract_model_path,
            quantization_config=quantization_config,
            device_map="cuda",
            attn_implementation="eager",
            local_files_only=True,
        )


        # prepare_model_for_kbit_training()包含冻结参数、梯度流动、梯度检查（降低显存占用）和LayerNorm精度提升
        base_model = prepare_model_for_kbit_training(base_model)
        self.abstract_model=base_model
        self._load_lora()


    # 加装bfloat16低秩适配器、模式标签嵌入层
    def _load_lora(self):
        # Adapter配置，精度不可指定,默认和bnb_4bit_compute_dtype一致(bfloat16)
        lora_config = LoraConfig(
            r=16,
            lora_alpha=32,
            target_modules=["q_proj", "k_proj", "v_proj", "o_proj",
                            "gate_proj", "up_proj", "down_proj"],
            lora_dropout=0.05,
            bias="none",
            task_type=TaskType.CAUSAL_LM,
        )
        if self.scene_model is not None:
            if os.path.isdir(SCENE_LORA_DIR) and len(os.listdir(SCENE_LORA_DIR))>0:
                model_path = os.path.join(SCENE_LORA_DIR, sorted(os.listdir(SCENE_LORA_DIR))[-1])
                self.scene_model = PeftModel.from_pretrained(self.scene_model, model_path)
                print(f'Continue SFT on bf16 LoRA for scene agent...')
            else:
                self.scene_model = get_peft_model(self.scene_model, lora_config)
                print(f'Initial SFT on bf16 LoRA for scene agent...')
            # 加入模式标签嵌入层
            self.scene_model = Qwen2WithModes(self.scene_model,1, 2, 3584, self.lamb).eval()
            # self.scene_model.print_trainable_parameters()
            print(f'Trainable parameters of scene_model:{[name for (name,param) in self.scene_model.named_parameters() if param.requires_grad]}')


        elif self.abstract_model is not None:
            if os.path.isdir(ABSTRACT_LORA_DIR) and len(os.listdir(ABSTRACT_LORA_DIR))>0:
                model_path = os.path.join(ABSTRACT_LORA_DIR, sorted(os.listdir(ABSTRACT_LORA_DIR))[-1])
                self.abstract_model = PeftModel.from_pretrained(self.abstract_model, model_path)
                print(f'Continue SFT on bf16 LoRA for abstract agent...')
            else:
                self.abstract_model = get_peft_model(self.abstract_model, lora_config)
                print(f'Initial SFT on bf16 LoRA for abstract agent...')
            # 加入模式标签嵌入层
            self.abstract_model = Qwen2WithModes(self.abstract_model, 0,2, 3584, self.lamb).eval()
            # 查看可训练参数
            # self.abstract_model.print_trainable_parameters()
            print(f'Trainable parameters of abstract_model:{[name for (name, param) in self.abstract_model.named_parameters() if param.requires_grad]}')

    # TODO:准备预定义测试用户交互文本数据集，快速验证工作流
    def _prepare_dataset(self,dpath):
        if not os.path.isfile(dpath):
            raise FileExistsError(f'{dpath} dataset file does not exist.')

        abs_dataset,scene_dataset=[],[]
        with open(dpath,'r',encoding='utf-8') as f:
            for i,line in enumerate(f.readlines()):
                # 加入line字符串检查防止出现误报KeyError
                if line.strip("\n").strip(" ")=="":
                    continue
                item=json.loads(line.strip("\n").strip(" "))
                if item.get("亮点分析","")=="":
                    # raise KeyError("'亮点分析'键不存在，请检查数据集格式")
                    print(f"{dpath} line {i} item does not contain '亮点分析'.")
                    scene_item={"input":item["文本摘要"],"output":item["片段描述"],"mode":self.mode_map[item["视频模式"].strip()]}
                    scene_dataset.append(scene_item)
                else:
                    abs_item = {"input": item["亮点分析"], "output": item["文本摘要"],"mode":self.mode_map[item["视频模式"]]}
                    abs_dataset.append(abs_item)
                    scene_item={"input":item["文本摘要"],"output":item["片段描述"],"mode":self.mode_map[item["视频模式"]]}
                    scene_dataset.append(scene_item)

        return abs_dataset,scene_dataset


    # 双智能体微调
    def finetune(self,dpath,lr=2e-4,num_epochs=3,batch_size=4):
        abs_dataset,scene_dataset=self._prepare_dataset(dpath)
        data_collator = DataCollatorForCausalLM(tokenizer=self.tokenizer, pad_to_multiple_of=32)

        if self.scene_model is not None:
            print(f'Finetuning scene_agent with dataset of {len(scene_dataset)} samples.')
            scene_dataset = InstructionDataset(data=scene_dataset, tokenizer=self.tokenizer, max_length=12288,
                                               prompt_template=scene_sys)
            self.train_dataloader_sce = DataLoader(dataset=scene_dataset, collate_fn=data_collator,
                                                   num_workers=0, pin_memory=True,batch_size=batch_size,shuffle=True)
            # 创建自定义训练器类对象开始训练
            scene_trainer = QLoRATrainer(
                model=self.scene_model,
                model_type=1,
                tokenizer=self.tokenizer,
                train_dataloader=self.train_dataloader_sce,
                lr=lr,
                num_epochs=num_epochs,
                output_dir=SCENE_LORA_DIR
            )
            scene_trainer.train()
            # 训练完成后不合并LoRA和INT4模型，否则会变成bf16模型，INT4量化失效，显存溢出RTX GeForce 5070（12GB）
            """
            scene_model_name=datetime.strftime(datetime.now(),'%Y%m%d_%H%M%S')+'.ckpt'
            os.makedirs(scene_merged_dir,exist_ok=True)
            scene_trainer.merge_and_save(os.path.join(scene_merged_dir,scene_model_name))
            """

        elif self.abstract_model is not None:
            print(f'Finetuning abstract_agent with dataset of {len(abs_dataset)} samples.')
            abs_dataset = InstructionDataset(data=abs_dataset, tokenizer=self.tokenizer, max_length=768,
                                             prompt_template=abstract_sys)
            self.train_dataloader_abs = DataLoader(dataset=abs_dataset, collate_fn=data_collator,
                                                   num_workers=0, pin_memory=True,batch_size=batch_size,shuffle=True)
            abstract_trainer = QLoRATrainer(
                model=self.abstract_model,
                model_type=0,
                tokenizer=self.tokenizer,
                train_dataloader=self.train_dataloader_abs,
                lr=lr,
                num_epochs=num_epochs,
                output_dir=ABSTRACT_LORA_DIR
            )
            abstract_trainer.train()
            """
            abstract_model_name=datetime.strftime(datetime.now(),'%Y%m%d_%H%M%S')+'.ckpt'
            os.makedirs(abstract_merged_dir,exist_ok=True)
            scene_trainer.merge_and_save(os.path.join(abstract_merged_dir,abstract_model_name))
            """

    def generate_response(self, input_text, max_new_tokens=256,choice:str=Literal["abstract","scene"],history=None):
        """自回归生成"""
        prompt = [{"role":"system","content":
            ("下面是描述一个特定任务的说明。"
            "请你根据用户请求或指令生成符合条件的回答")},
            {"role":"user", "content":input_text}] if history is None else history
        prompt=self.tokenizer.apply_chat_template(prompt,tokenize=False,add_generation_prompt=True)
        if choice not in ["abstract","scene"]:
            raise ValueError("choice should be either 'abstract' or 'scene'")
        # device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        # 摘要生成推理模式
        if choice=="abstract":
            inputs = self.tokenizer(prompt, return_tensors="pt").to(self.abstract_model.device)
            with torch.no_grad():
                outputs = self.abstract_model.generate(
                    **inputs,
                    mode_id=self.mode_id,
                    max_new_tokens=max_new_tokens,
                    do_sample=True,
                    temperature=0.7,
                    top_p=0.9,
                    repetition_penalty=1.1,
                    pad_token_id=self.tokenizer.pad_token_id,
                    eos_token_id=self.tokenizer.eos_token_id,
                )
        # 片段描述推理模式
        else:
            inputs = self.tokenizer(prompt, return_tensors="pt").to(self.scene_model.device)
            with torch.no_grad():
                outputs = self.scene_model.generate(
                    **inputs,
                    mode_id=self.mode_id,
                    max_new_tokens=max_new_tokens,
                    do_sample=True,
                    temperature=0.7,
                    top_p=0.9,
                    repetition_penalty=1.1,
                    pad_token_id=self.tokenizer.pad_token_id,
                    eos_token_id=self.tokenizer.eos_token_id,
                )

        # 只取生成的部分
        # generated = outputs[0][inputs['input_ids'].shape[1]:]
        response = self.tokenizer.decode(outputs[0], skip_special_tokens=True)

        return response


    # idea_history只有当用户完成视频创作之后才删除，确保前端交互持续生效
    def idea_chat_test(self):
        self.idea_history=[{"role":"system","content":idea_sys}]
        # 亮点抽取环节
        mark=True
        while mark:
            user_prompt=input("请输入你的想法(回车发送,空输入退出):").strip()
            if user_prompt=="":
                user_prompt="我现在需要你给我生成我的亮点总结。"
                mark=False
            if len(self.idea_history)==1:
                user_prompt=f"我选择模式{self.mode_id}"+user_prompt
            self.idea_history.append({"role":"user","content":user_prompt})
            response=self.generate_response(user_prompt,max_new_tokens=256,choice="abstract",history=self.idea_history)
            print(f"Agent: {response}")
            self.idea_history.append({"role":"assistant","content":response})

        history_len=sum([len(item["content"]) for item in self.idea_history[1:]])
        print(f'History conversation length:{history_len},entering abstract generation section.')

        return self.idea_history[-1]["content"]


    # abstract_history只有当用户完成视频创作之后才删除，确保前端交互持续生效
    def abstract_chat_test(self,text):
        self.abstract_history=[{"role":"system","content":abstract_sys},
            {"role":"user","content":text}]
        # 摘要生成环节（单轮对话）
        user_prompt=""
        while True:
            response=self.generate_response(user_prompt,768,"abstract",self.abstract_history)
            print(f'Agent: {response}')
            self.abstract_history.append({"role":"assistant","content":response})
            user_prompt=input("请输入你的想法(回车发送,空输入退出):").strip()
            if user_prompt=="":
                break
            self.abstract_history.append({"role":"user","content":user_prompt})

        return self.abstract_history[-1]["content"]


    # scene_history只有当用户完成视频创作之后才删除，确保前端交互持续生效
    def scene_chat_test(self,text):
        if self.scene_model is not None and self.abstract_model is None:
            self._load_abstract_agent()

        self.scene_history=[{"role":"system","content":scene_sys},{"role":"user","content":text}]

        self._load_scene_agent()

        user_prompt=""
        while True:
            response=self.generate_response(user_prompt,12288,"scene",self.scene_history)
            print(f'Agent: {response}')
            self.scene_history.append({"role":"assistant","content":response})
            user_prompt=input("请输入你的想法(回车发送,空输入退出):").strip()
            if user_prompt=="":
                break
            self.scene_history.append({"role":"user","content":user_prompt})

        return self.scene_history[-1]["content"]

    def log_chat(self):
        # 手动日志记录（同时对外提供日志记录的接口）
        time=datetime.strftime(datetime.now(),"%Y%m%d_%H%M%S")
        chat_dir="chat_"+time
        os.makedirs(chat_dir,exist_ok=True)
        idea_chat_log="idea_chat.txt"
        with open (os.path.join(chat_dir,idea_chat_log),"w",encoding="utf-8") as f:
            f.write(json.dumps(self.idea_history,ensure_ascii=False,indent=2))
        abs_chat_log="abstract_chat.txt"
        with open (os.path.join(chat_dir,abs_chat_log),"w",encoding="utf-8") as f:
            f.write(json.dumps(self.abstract_history,ensure_ascii=False,indent=2))
        scene_chat_log="scene_chat.txt"
        with open (os.path.join(chat_dir,scene_chat_log),"w",encoding="utf-8") as f:
            f.write(json.dumps(self.scene_history,ensure_ascii=False,indent=2))
        print(f'Idea,abstract and scene chatting conversations are saved in {chat_dir}.')



    # TODO: 后端工作流：多轮对话抽取亮点+摘要生成+片段描述推理（需要切换智能体，切换过程有一定耗时）
    # TODO: 支持预输入文本温度采样推理演示和实时文本交互双模式
    """
    支持对话历史记录与导出
    """
    def workflow(self):
        self.mode_id=int(input("请选择智能体的文案设计模式（0产品介绍/1剧情设计）:"))
        idea=self.idea_chat_test()
        abstract=self.abstract_chat_test(idea)
        self.scene_chat_test(abstract)

        # 记录亮点提取、摘要生成、片段拍摄建议对话内容
        self.log_chat()

    def _release_gpu_memory(self):
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.synchronize()
            torch.cuda.empty_cache()


# 视频模式：剧情设计/产品介绍，根据mode标签动态选择，语言风格差异可能较大，lamb调控语言风格差异性
# model_type=0对应摘要生成智能体，model_type=1对应片段建议智能体
class Qwen2WithModes(nn.Module):
    def __init__(self, model, model_type=0,num_tags=2,hidden_size=3584,lamb=2e-1):
        super().__init__()
        self.device=torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model=model
        self.model_type=model_type
        self.lamb=lamb
        self.mode_tag_embedding = nn.Embedding(num_tags,hidden_size,dtype=torch.bfloat16).to(self.device)
        if model_type==1 and os.path.isdir(SCENE_MODE_EMBED_DIR):
            weight_path=os.path.join(SCENE_MODE_EMBED_DIR,sorted(os.listdir(SCENE_MODE_EMBED_DIR))[-1])
            self.mode_tag_embedding.load_state_dict(torch.load(weight_path,weights_only=False).state_dict())
            print(f'Continue SFT on mode_tag_embedding layer for scene agent...')
        elif model_type==0 and os.path.isdir(ABSTRACT_MODE_EMBED_DIR):
            weight_path=os.path.join(ABSTRACT_MODE_EMBED_DIR,sorted(os.listdir(ABSTRACT_MODE_EMBED_DIR))[-1])
            self.mode_tag_embedding.load_state_dict(torch.load(weight_path,weights_only=False).state_dict())
            print(f'Continue SFT on mode_tag_embedding layer for abstract agent...')
        else:
            # 对于dout进行类Kaiming初始化
            nn.init.normal_(self.mode_tag_embedding.weight,mean=0.0,std=(2/hidden_size)**0.5)
            print(f'Initial SFT on mode_tag_embedding layer for agent {self.model_type+1}...')

    # 训练函数
    def forward(self, input_ids=None,mode_id=None,**kwargs):
        # print(input_ids.device)
        # print(mode_id.device)
        inputs_embeds = self.model.get_input_embeddings()(input_ids)
        tag_embeds = self.mode_tag_embedding(mode_id)
        # 将标签向量[广播]到每个token位置并相加
        inputs_embeds = inputs_embeds + self.lamb*tag_embeds.unsqueeze(1)
        return self.model(inputs_embeds=inputs_embeds, **kwargs)

    # 推理函数
    def generate(self,input_ids,mode_id,attention_mask,**kwargs):
        inputs_embeds = self.model.get_input_embeddings()(input_ids).to(self.device)
        tag_embeds = self.mode_tag_embedding(torch.tensor(mode_id,dtype=torch.int,device=self.device)).to(self.device)
        inputs_embeds = inputs_embeds + self.lamb*tag_embeds.reshape(1,1,-1)
        return self.model.generate(inputs_embeds=inputs_embeds, attention_mask=attention_mask, **kwargs)


"""
INT4量化模型的QLoRA微调
使用bitsandbytes进行4bit量化 + PEFT进行LoRA适配
"""


class QLoRATrainer:
    """
    INT4 + QLoRA低秩适配器数据集微调，损失不需要scaler以防止梯度下溢消失，
    前向传播的时候也不需要对数据流autocast（bitsandbytesconfig自动从INT4变为bf16）
    """
    def __init__(
            self,
            model,
            model_type,
            tokenizer,
            train_dataloader,
            val_dataloader=None,
            lr=2e-4,  # LoRA通常用稍大学习率
            warmup_rate=0.1,
            num_epochs=3,
            gradient_accumulation_steps=4,
            max_grad_norm=0.3,  # LoRA常用较小的grad_norm
            output_dir="./qlora-checkpoints",
    ):
        self.model=model
        self.model_type=model_type
        self.tokenizer = tokenizer
        self.train_dataloader = train_dataloader
        self.val_dataloader = val_dataloader
        self.num_epochs=num_epochs
        self.lr_max=lr
        self.warmup_rate=warmup_rate

        # 关键：optimizer只优化LoRA参数（INT4权重已被冻结）
        # 过滤出需要梯度的参数
        trainable_params = [p for p in model.parameters() if p.requires_grad]

        self.optimizer = torch.optim.AdamW(
            trainable_params,  # 只有LoRA矩阵A和B
            lr=lr,
            betas=(0.9, 0.999),
            weight_decay=0.001,
        )

        # epoch最后不足累积步数的batch不会继续累积到下一个epoch
        total_steps = len(train_dataloader) // gradient_accumulation_steps * num_epochs
        warmup_steps=int(warmup_rate*total_steps)
        self.scheduler = get_cosine_schedule_with_warmup(
            self.optimizer,
            num_warmup_steps=warmup_steps,
            num_training_steps=total_steps,
            num_cycles=1.0,
        )

        self.gradient_accumulation_steps = gradient_accumulation_steps
        self.max_grad_norm = max_grad_norm
        self.output_dir = output_dir
        self.global_step = 0

        # 模型参数保存目录
        os.makedirs(output_dir, exist_ok=True)
        self.base=len(os.listdir(self.output_dir))

    def _compute_loss(self, batch):
        """计算loss"""
        input_ids = batch["input_ids"].to(self.model.device)
        attention_mask = batch["attention_mask"].to(self.model.device)
        labels = batch["labels"].to(self.model.device)
        mode_id=batch["mode"].to(self.model.device)

        outputs = self.model(
            input_ids=input_ids,
            mode_id=mode_id,
            attention_mask=attention_mask,
            labels=labels,
        )
        # 自回归大语言模型LLM采用teacher-forcing策略进行矩阵式损失计算，无需KV缓存（但对于大批量+长样本情况容易爆炸）
        return outputs.loss

    def train_epoch(self, epoch)->float:
        total_loss = 0
        for step, batch in enumerate(self.train_dataloader):
            loss = self._compute_loss(batch)
            loss = loss / self.gradient_accumulation_steps
            loss.backward()  # ← 只回传到LoRA参数，INT4权重无梯度

            total_loss += loss.item() * self.gradient_accumulation_steps

            if (step + 1) % self.gradient_accumulation_steps == 0:
                # 梯度裁剪
                clip_grad_norm_(
                    parameters=[p for p in self.model.parameters() if p.requires_grad],
                    max_norm=self.max_grad_norm
                )

                self.optimizer.step()  # ← 只更新LoRA参数
                self.scheduler.step()  # ← 学习率调度
                self.optimizer.zero_grad()
                self.global_step += 1
                print(f'Current global step:{self.global_step}')

        total_loss/=len(self.train_dataloader)
        print(f'Epoch {epoch}: Average loss:{total_loss:.2f}')
        return total_loss


    def train(self):
        ls=[]
        self.model.train()
        for e in range(self.num_epochs):
            loss=self.train_epoch(e+1)
            self.save_model(e+1)
            ls.append(loss)
        # 假设刚好能够完全整除（默认drop_last=False）
        datasize=len(self.train_dataloader)*self.train_dataloader.batch_sampler.batch_size
        plt.figure(figsize=(19.2,10.8))
        plt.plot(range(1,self.num_epochs+1),ls,'b-o')
        plt.grid(True,which="major")
        plt.autoscale()
        plt.xlabel("Epoch")
        plt.ylabel("Average Loss")
        plt.legend([f"batch_size:{self.train_dataloader.batch_sampler.batch_size},lr_max:{self.lr_max},warmup:{self.warmup_rate}"],
                   loc="upper right")
        title=(f"Finetuning Agent-{self.model_type+1}[Qwen2.5-7B-Instruct](QLoRA+Additional Embedding)"+
            f"on {datasize} one-turn conversation samples")
        plt.title(title)
        time=datetime.strftime(datetime.now(),"%Y%m%d_%H%M%S")
        plt.savefig(f"SFT_Loss_Agent{self.model_type+1}_{time}.pdf")
        plt.show()


    def save_model(self, epoch):
        """保存LoRA适配器（不是完整模型）"""
        save_dir = os.path.join(self.output_dir, f"lora-epoch-{epoch+self.base}")
        self.model.model.save_pretrained(save_dir)  # 只保存LoRA权重（几十MB）
        print(f"LoRA adapter saved to {save_dir}")

        # 保存mode_tag_embedding_layer
        if self.model_type==1:
            os.makedirs(SCENE_MODE_EMBED_DIR,exist_ok=True)
            torch.save(self.model.mode_tag_embedding,os.path.join(SCENE_MODE_EMBED_DIR,f"mode-embed-epoch-{epoch+self.base}.pth"))
        else:
            os.makedirs(ABSTRACT_MODE_EMBED_DIR,exist_ok=True)
            torch.save(self.model.mode_tag_embedding,os.path.join(ABSTRACT_MODE_EMBED_DIR,f"mode-embed-epoch-{epoch+self.base}.pth"))


    def merge_and_save(self,output_path):
        """
        训练完成后，可以将LoRA权重合并回基础模型
        合并后的模型可以正常推理，不需要额外加载LoRA
        """
        # 合并LoRA到基础模型
        merged_model = self.model.merge_and_unload()

        # 保存合并后的模型
        merged_model.save_pretrained(output_path)
        print(f"Merged model saved to {output_path}")



# 自然语言【亮点-摘要/摘要-片段描述】数据集：用于构建训练数据和验证数据迭代器
class InstructionDataset(Dataset):
    """
    将输入-输出对格式化为自回归训练样本
    格式: （系统PROMPT）输入（软编码SEP）输出[硬编码EOS token]
    标签: 只计算输出部分的损失（输入部分mask掉）
    对于摘要-片段描述，最大长度建议12288=1024*12左右；对于亮点-摘要描述，最大长度建议768
    """

    def __init__(self,data: List[Dict[str, str]],tokenizer:AutoTokenizer,max_length: int = 12288,prompt_template: Optional[list] = None):
        self.tokenizer = tokenizer
        self.max_length = max_length
        self.samples = []

        # 默认模板（可根据任务调整）
        if prompt_template is None:
            self.prompt_template = [{"role": "system", "content":
                ("下面是描述一个特定任务的说明。"
                 "请你根据用户请求或指令生成符合条件的回答")}
                ]
        else:
            self.prompt_template = prompt_template

        self._prepare_data(data)

    def _prepare_data(self, data: List[Dict[str, str]]):
        """预处理所有样本"""
        for item in data:
            # 双键保险
            input_text = item.get("input", item.get("instruction", ""))
            output_text = item.get("output", item.get("response", ""))

            # 构建完整文本
            prompt=self.prompt_template[:]
            assert (not (prompt is self.prompt_template))
            prompt.append({"role":"user","content":input_text})
            prompt_text=self.tokenizer.apply_chat_template(prompt,tokenize=False,add_generation_prompt=True)
            # 计算prompt长度，用于后续mask
            # 返回列表方便拼接
            prompt_tokenized = self.tokenizer(
                prompt_text,
                truncation=True,
                max_length=self.max_length,
                padding=False,
                return_tensors=None,
            )
            prompt_len = len(prompt_tokenized["input_ids"])

            full_prompt=prompt[:]
            assert (not (full_prompt is prompt))
            full_prompt.append({"role":"assistant","content":output_text})
            full_text=self.tokenizer.apply_chat_template(full_prompt,tokenize=False,add_generation_prompt=True)

            # Tokenize
            tokenized = self.tokenizer(
                full_text,
                truncation=True,
                max_length=self.max_length,
                padding=False,
                return_tensors=None,
            )

            input_ids = tokenized["input_ids"]
            attention_mask = tokenized["attention_mask"]


            # 构建标签：prompt部分设为-100（不计算损失），只训练输出部分
            labels = [-100] * prompt_len + input_ids[prompt_len:]
            # 确保labels长度与input_ids一致
            # labels = labels[:len(input_ids)]

            mode=item.get("mode","")

            self.samples.append({
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "labels": labels,
                "mode":mode
            })

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        return self.samples[idx]


# 数据聚合器：用于构建训练数据和验证数据迭代器
class DataCollatorForCausalLM:
    """batch内部动态零余数padding"""
    def __init__(self, tokenizer, pad_to_multiple_of: Optional[int] = None):
        self.tokenizer = tokenizer
        self.pad_to_multiple_of = pad_to_multiple_of

    def __call__(self, features: List[Dict[str, List[int]]]) -> Dict[str, torch.Tensor]:
        # 找出最大长度
        max_length = max(len(f["input_ids"]) for f in features)

        if self.pad_to_multiple_of is not None:
            max_length = (
                    (max_length + self.pad_to_multiple_of - 1)
                    // self.pad_to_multiple_of
                    * self.pad_to_multiple_of
            )

        batch_input_ids = []
        batch_attention_mask = []
        batch_labels = []
        batch_mode=[]

        for feature in features:
            input_ids = feature["input_ids"]
            attention_mask = feature["attention_mask"]
            labels = feature["labels"]
            mode=feature["mode"]

            # Padding
            padding_length = max_length - len(input_ids)

            input_ids = input_ids + [self.tokenizer.pad_token_id] * padding_length
            attention_mask = attention_mask + [0] * padding_length
            labels = labels + [-100] * padding_length

            batch_input_ids.append(input_ids)
            batch_attention_mask.append(attention_mask)
            batch_labels.append(labels)
            batch_mode.append(mode)


        return {
            "input_ids": torch.tensor(batch_input_ids, dtype=torch.long),
            "attention_mask": torch.tensor(batch_attention_mask, dtype=torch.long),
            "labels": torch.tensor(batch_labels, dtype=torch.long),
            "mode":torch.tensor(batch_mode,dtype=torch.long)
        }



if __name__ == "__main__":
    dpath="../../20260607_001.jsonl"
    lamb=2e-1
    vas_test=Video_Assistant_System(lamb)
    vas_test.finetune(dpath,num_epochs=2)
    # vas_test.workflow()
