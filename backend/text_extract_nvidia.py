from transformers import Qwen2_5OmniForConditionalGeneration,Qwen2_5OmniProcessor
from qwen_omni_utils import process_mm_info
import huggingface_hub
from scenedetect import open_video,SceneManager,ContentDetector,split_video_ffmpeg
# import cv2
# import pydub
import subprocess
import os
import torch
from pydantic import BaseModel,Field,ValidationError
from typing import Literal, List
import json


model_id="Qwen/Qwen2.5-Omni-3B"
local_model_path="E:/LLMs/Qwen2.5-Omni-3B"
json_format_png=r"D:\mypython\FHFP_video_assistant\dataset\片段分析.png"
separate_mark="："
scene_descript1_png=r"D:\mypython\FHFP_video_assistant\dataset\描述1.png"
scene_descript2_png=r"D:\mypython\FHFP_video_assistant\dataset\描述2.png"
abstract_format_png=r"D:\mypython\FHFP_video_assistant\dataset\摘要.png"
anno_suffix="_anno.json"
ffmpeg_path=r"D:/software/program files/ffmpeg-8.0.1-essentials_build/bin/ffmpeg.exe"
safety_protocol="file:///"

# 降低显存占用
os.environ["QWEN_OMNI_VIDEO_READER_BACKEND"] = "decord"


class Data_Gen():
    def __init__(self):
        self.model,self.processor=self._deploy_qwen_omni()

    # 部署大模型
    def _deploy_qwen_omni(self):
        device = torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
        # model = Qwen2_5_VLForConditionalGeneration.from_pretrained(model_id,torch_dtype=torch.bfloat16, device_map="auto",
        #        attn_implementation="eager",local_files_only=False).to(device).eval()
        model = Qwen2_5OmniForConditionalGeneration.from_pretrained(local_model_path,torch_dtype=torch.bfloat16,device_map="cuda",
                attn_implementation="sdpa",local_files_only=True).eval()
        model.disable_talker()
        processor = Qwen2_5OmniProcessor.from_pretrained(local_model_path,local_files_only=True)
        return model, processor

    # for audio-vision separated cases
    # 音视频合并（适用于音视频双轨分离情形）
    # 对于含音频的视频，可以直接bypass
    def va_comb(self,user_dir):
        if not os.path.isdir(user_dir):
            raise NotADirectoryError(f"Current directory {user_dir} is not available.")
        video_dirs = sorted(os.listdir(user_dir), reverse=False)
        labels = []
        for v_d in video_dirs:
            path = os.path.join(user_dir, v_d)
            files = os.listdir(path)
            cli = [ffmpeg_path]
            video_name = ''
            # 先视频，后音频，固定顺序
            files.sort(reverse=True)
            for fn in files:
                if fn.split('.')[-1] == 'mp4':
                    cli.extend(['-i', os.path.join(path,fn)])
                    video_name = os.path.join(path,fn.split('.')[0])
                elif fn.split('.')[-1] == 'mp3':
                    cli.extend(['-i', os.path.join(path,fn)])
                elif fn.split('.')[-1] == 'txt':
                    # 记录包括userdir和videodir目录名称的标签路径（否则直接被覆盖）
                    labels.append(os.path.join(path,fn))
            cli.extend(['-map', '0:v:0', '-map', '1:a:0', '-shortest', '-y', f'{video_name}_compound.mp4'])
            # 固定单次写入
            if cli.count('-i')==2:
                try:
                    # print(cli)
                    print(' '.join(cli))
                    # print(os.getcwd())
                    subprocess.run(cli, cwd=os.getcwd())
                    print(f'Successfully generating {video_name}_compound.mp4!')
                except Exception as e:
                    print(f'Error:{e}')
        return labels

    # 使用PySceneDetect切分视频（片段最短时长默认20s），并构建新视频级目录
    def video_slicing(self,video_path, length=6, threshold=27.0):
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"Current file {video_path} is not available.")
        dir = os.path.dirname(video_path)
        final_dir = os.path.basename(dir)
        parent_dir = os.path.dirname(dir)
        new_dir = os.path.join(parent_dir, final_dir + '_scenes')
        # 手动创建目录，以防split_video_ffmpeg无法写出视频
        os.makedirs(new_dir,exist_ok=True)
        # 空目录判断，降低片段重分割成本
        if len(os.listdir(str(new_dir)))==0:
            video = open_video(video_path)
            fr = video.frame_rate
            min_len = int(length * fr + 0.5)
            manager = SceneManager()
            manager.add_detector(ContentDetector(threshold=threshold, min_scene_len=min_len))
            manager.detect_scenes(video, show_progress=True)
            print(manager.get_scene_list())
            split_video_ffmpeg(input_video_path=video_path, scene_list=manager.get_scene_list(), output_dir=str(new_dir), show_progress=True)

        return new_dir


    # 大模型批量推理生成片段级别json描述
    def agent_infer_l2(self,scenes_dir):
        if not os.path.isdir(scenes_dir):
            raise NotADirectoryError(f"Current directory {scenes_dir} is not available.")
        video_files = os.listdir(scenes_dir)
        # 生成长度软编码
        conversations = [[{"role": "system", "content": [{"type": "text", "text": "You are a helpful assistant."}]},
                        {"role": "user",
                         "content": [{"type": "video", "video": os.path.join(scenes_dir,fn)},
                                     {"type": "image",
                                      "image": json_format_png},
                                     {"type": "text",
                                      "text": "请你针对每个场景的视频片段，结合我给的片段分析的json输出格式要求图片，" +
                                              "从画面、音频、文案三个角度进行分析，画面分析的时候要求抓住视频片段的主旨，包括人物关系、场景设定、出场元素、" +
                                              "视频运镜的分析，音频需要描述背景音乐（如果有）的选择、视频原声带的声音（大自然声音、背景人群说话声、装置声音、噪音等），" +
                                              "文案按照人物的对话顺序组织，按照格式指定说话人物（人物）和说话内容（台词）。输出格式必须是和参考图片一致的json对象。生成内容不超过250字。"}]}]
                                     for fn in video_files if ".mp4" in fn]
        texts=[]
        all_audios,all_images,all_videos=[],[],[]
        for i,conversation in enumerate(conversations):
            text = self.processor.apply_chat_template(conversation, add_generation_prompt=True, tokenize=False)
            audios, images, videos = process_mm_info(conversation, use_audio_in_video=True)
            # process占位符会自动根据同一对话属性进行不同模态的关系绑定+非文本模态无法padding
            assert(len(images)>0)
            assert(len(videos)>0)
            assert(len(audios)>0)
            texts.append(text)
            all_audios.extend(audios)
            all_images.extend(images)
            all_videos.extend(videos)

        # 格式检查和重试机制（因为此处每段对话只有1图片，1视频，1音频，所以代码做了简化处理）
        outmap,ineligibles=self.valid_check(texts,all_audios,all_images,all_videos,video_files,256)
        while len(ineligibles)>0:
            video_files = [video_files[ie] for ie in ineligibles]
            texts = [texts[ie] for ie in ineligibles]
            all_audios = [all_audios[ie] for ie in ineligibles]
            all_images = [all_images[ie] for ie in ineligibles]
            all_videos = [all_videos[ie] for ie in ineligibles]
            outputs,ineligibles=self.valid_check(texts,all_audios,all_images,all_videos,video_files,256)
            # 键不会重复（重复时右边键值对覆盖左边键值对）
            outmap={**outmap,**outputs}
        return outmap

    # 大模型推理格式校验
    def valid_check(self,texts,audios,images,videos,video_files,max_new_tokens=256):
        inputs = self.processor(text=texts, audio=audios, images=images, videos=videos, return_tensors="pt",
                           padding=True).to(self.model.device).to(self.model.dtype)

        # 生成长度硬编码
        with torch.no_grad():
            outputs = self.model.generate(**inputs, max_new_tokens=max_new_tokens, use_cache=True,return_audio=False)
        outtexts = self.processor.batch_decode(outputs, skip_special_tokens=True)

        output_map={}
        ineligibles=[]
        for i in range(len(outtexts)):
            try:
                # DeepSeek说法:清除可能的Markdown标记
                data = json.loads(outtexts[i].strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
                result = SceneAnnotation(**data)
                json_anno = result.model_dump_json(indent=2, ensure_ascii=False)
                output_map[video_files[i]]=json_anno
            except json.JSONDecodeError as e:
                print(f"❌ JSON 语法错误: {e}")
                ineligibles.append(i)
            # 结构不符合预期情况可能在文案这一栏出现
            except ValidationError as e:
                print(f"❌ 结构不符合预期:\n{e.json()}")
                ineligibles.append(i)
        return output_map,ineligibles

    # 大模型单样本推理生成文本摘要描述
    def agent_infer_l1(self,video_dir):
        if not os.path.isdir(video_dir):
            raise NotADirectoryError(f"Current directory {video_dir} is not available.")
        video_files = os.listdir(video_dir)
        for fn in video_files:
            if anno_suffix in fn:
                # 读取列表
                with open(fn,'r+',encoding='utf-8') as f:
                    anno_text=json.load(f)

        # 使用换行符连接各个片段描述json字符串
        anno_text="\n".join(anno_text)
        # 生成长度软编码
        conversation = [{"role": "system", "content": [{"type": "text", "text": "You are a helpful assistant."}]},
                        {"role": "user",
                         "content": [{"type": "text", "text": anno_text},
                                     {"type": "image", "image": scene_descript1_png},
                                     {"type": "image","image": scene_descript2_png},
                                     {"type": "image", "image": abstract_format_png},
                                     {"type": "text",
                                      "text": "根据图1和图2的片段描述（参考），生成图3的文本摘要（参考）。要求把每一个片段描述概括成1-3句话"+
                                      "然后按照片段组织顺序形成整个视频的文本摘要。概括过程需要保持逻辑连贯和重点突出，主次分明。生成内容不超过500字。"}]}]

        text = self.processor.apply_chat_template(conversation, add_generation_prompt=True, tokenize=False)
        audios, images, videos = process_mm_info(conversation, use_audio_in_video=True)
        # process占位符会自动根据同一对话属性进行不同模态的关系绑定+非文本模态无法padding
        texts=[text]

        # 无需重试机制
        outtext=''
        if self.model is not None and self.processor is not None:
            inputs=self.processor(text=texts,audio=audios,images=images,videos=videos,
                                  return_tensors="pt").to(self.model.device).to(self.model.dtype)
            with torch.no_grad():
                outputs=self.model.generate(**inputs,max_new_tokens=512,use_cache=True,return_audio=False)
            outtext=self.processor.batch_decode(outputs)[0]
        return outtext

    # 从片段级json拼接成视频级json数组（.json），写入原始视频存储目录，
    # 并调用大模型生成摘要文本（返回json数组，用于构建视频片段json微调数据集.jsonl）
    """
    original_dir:一个up主的单个视频（VideoX）的目录
    """
    def generate_scene_annotation_by_video(self,video_dir,min_len=6,threshold=27.0):
        if not os.path.isdir(video_dir):
            raise NotADirectoryError(f"{video_dir} does not exist.")
        files=os.listdir(video_dir)
        # 过滤合成音视频和单视频场景
        vpath=''
        for file in files:
            if file.split('.')[-1]=='mp4' and '_compound' in os.path.basename(file):
                vpath=os.path.join(video_dir,file)
        if vpath=='':
            for file in files:
                if file.split('.')[-1]=='mp4':
                    vpath=os.path.join(video_dir,file)

        print(f'Current video:{vpath}')
        fname = os.path.join(os.path.dirname(vpath), os.path.basename(vpath).split('.')[0] + anno_suffix)
        if os.path.isfile(fname):
            print(f'{fname} annotation file already exists.')
            json_array=[]
            with open(fname,'r',encoding='utf-8') as f:
                json_array=json.load(f)
            return json_array

        new_dir = self.video_slicing(vpath, min_len, threshold)
        if self.model is not None and self.processor is not None:
            outmap=self.agent_infer_l2(new_dir)
            # 需要严格控制片段顺序
            outmap=sorted(list(outmap.items()),key=lambda x:x[0])
            # 可以写入列表
            json_array=[scene_annotation[1] for scene_annotation in outmap]
            with open(fname,'w',encoding='utf-8') as f:
                json.dump(json_array,f)

            return json_array
        else:
            raise EnvironmentError("Model and processor are not initialized.")

    """
    片段级别注释和摘要、亮点分析不含有作者和视频名称信息，标签文件含有
    标签文件名称用于读取人工标签文件（包括基础信息、数据统计和亮点提取）、定位对齐亮点分析、摘要文本和片段注释内容
    后面构建数据集时代码要调整
    L2_ds_name需要包括“.jsonl”在内
    """
    def Generate_Dataset(self,dataset_dir,L2_ds_name,min_len=6,threshold=27.0):
        # 音视频合成等预处理操作
        if not os.path.isdir(dataset_dir):
            raise NotADirectoryError(f"{dataset_dir} does not exist.")
        if ".jsonl" not in L2_ds_name:
            raise ValueError(f"{L2_ds_name} should be in .jsonl format.")
        if L2_ds_name in os.listdir(dataset_dir):
            print(f'{L2_ds_name} dataset version already exists.')
            return

        dataset=[]
        for root,dirs,files in os.walk(dataset_dir):
            for dir in dirs:
                user_dir=os.path.join(root,dir)
                labels=self.va_comb(user_dir)
                for l in labels:
                    item={}
                    # L2层推理
                    json_array=self.generate_scene_annotation_by_video(os.path.dirname(l),min_len=min_len,threshold=threshold)
                    item['片段描述']=json_array
                    # L1层推理
                    abstract_text=self.agent_infer_l1(os.path.dirname(l))
                    item['文本摘要']=abstract_text
                    # 基础信息标签（人工标注）
                    # TODO: 亮点信息标签（l0，也是人工标注，在一个txt里面，不要手动换行即可，可以用自带“笔记本”软件编辑）
                    item=self.human_label_annotation(l,item)
                    dataset.append(item)
                    # 视频级数据集写入
                    with open(os.path.dirname(l),'w',encoding='utf-8') as f:
                        json.dump(item,f)

            # 深度限定为1层
            break

        # 综合数据集写入
        with open(L2_ds_name,'w',encoding='utf-8') as f:
            for item in dataset:
                f.write(json.dumps(item,ensure_ascii=False)+"\n")


    # 基础信息标签提取
    # 冒号检测，键固定，PostgreSQL存储
    # item是字典,可传递引用或值
    def human_label_annotation(self,fn,item):
        with open(fn,'r') as f:
            content=f.readlines()
        for line in content:
            line=line.strip()
            if separate_mark not in line:
                continue
            line=line.split(separate_mark)
            # 人数数量和数据统计都是字符串（上万的统计数据有同学使用w来标记，无法转换为整数）
            item[line[0]]=line[1]
        return item


class DialogueItem(BaseModel):
    figure:str=Field(...,alias="人物")
    script:str=Field(...,alias="台词")

class SceneAnnotation(BaseModel):
    scene:str=Field(...,alias="画面")
    audio:str=Field(...,alias="音频")
    text:List[DialogueItem]=Field(...,alias="文案")


if __name__=='__main__':
    data_generator=Data_Gen()
    data_generator.Generate_Dataset("dataset","20260523_001.jsonl")


