from scenedetect import open_video,SceneManager,split_video_ffmpeg,save_images,AdaptiveDetector
import subprocess
import os
import torch
from pydantic import BaseModel,Field,ValidationError
from typing import List
import json
from openai import OpenAI
import dashscope
from dashscope import MultiModalConversation
from dashscope.audio.qwen_omni import MultiModality
import base64
from pathlib import Path
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import logging



model_id="Qwen/Qwen2.5-Omni-3B"
local_model_path="E:/LLMs/Qwen2.5-Omni-3B"
json_format_png="D:/wf200/Documents/mypython/FHFP_video_assistant/dataset/片段分析.png"
separate_mark=("：",":")
scene_descript1_png=r"D:\wf200\Documents\mypython\FHFP_video_assistant\dataset\描述1.png"
scene_descript2_png=r"D:\wf200\Documents\mypython\FHFP_video_assistant\dataset\描述2.png"
abstract_format_png=r"D:\wf200\Documents\mypython\FHFP_video_assistant\dataset\摘要.png"
anno_suffix="_anno.json"
dashscope_api_key="sk-bcb509c0f2814ca3a3429d5b9604852e"
safety_protocol="file:///"

# 降低显存占用
os.environ["QWEN_OMNI_VIDEO_READER_BACKEND"] = "decord"


class Data_Gen():
    def __init__(self):
        self.model=OpenAI(api_key=dashscope_api_key,base_url="https://dashscope.aliyuncs.com/compatible-mode/v1")
        dashscope.api_key=dashscope_api_key
        # 创建一个配置好超时和重试的 Session
        session = requests.Session()
        retries = Retry(total=3, backoff_factor=1)
        session.mount('https://', HTTPAdapter(max_retries=retries))
        # 设置连接超时5秒，读取超时180秒（3分钟）
        session.timeout = (5, 180)
        dashscope.session=session
        dashscope.base_http_api_url = 'https://dashscope.aliyuncs.com/api/v1'
        # logging.basicConfig(level=logging.DEBUG)


    def encode_image(self,image_path):
        """将本地图片编码为 Base64 字符串，并返回带有正确前缀的 Data URL"""
        path = Path(image_path)
        with open(path, "rb") as image_file:
            # 1. 将图片内容编码为 Base64，并转换为字符串格式
            encoded_string = base64.b64encode(image_file.read()).decode('utf-8')

        # 2. 获取图片后缀，判断 MIME 类型
        image_type = path.suffix.lower()
        if image_type == '.png':
            mime_type = 'image/png'
        elif image_type == '.jpg' or image_type == '.jpeg':
            mime_type = 'image/jpeg'
        else:
            # 如果是其他格式，比如 .webp，可以继续在这里添加条件
            # 这里为方便起见，默认按 JPEG 处理，实际使用时请按需修改
            mime_type = 'image/jpeg'
            print(f"警告: 未识别的图片格式 {image_type}，将按 {mime_type} 处理。")

        # 3. 构建符合 API 要求的 Data URL 格式
        data_url = f"data:{mime_type};base64,{encoded_string}"
        # print(type(data_url))
        assert(isinstance(data_url,str))

        return data_url


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
            cli = ["ffmpeg"]
            video_name = ''
            # 先视频，后音频，固定顺序
            files.sort(reverse=True)
            for fn in files:
                if fn.split('.')[-1] == 'mp4':
                    if cli.count('-i') == 0:
                        cli.extend(['-i', os.path.join(path, fn)])
                    else:
                        cli.insert(1,'-i')
                        cli.insert(2,os.path.join(path,fn))
                    video_name = os.path.join(path, fn.split('.')[0])
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
    def video_slicing(self,video_path, length=6, threshold=3.0):
        if not os.path.isfile(video_path):
            raise FileNotFoundError(f"Current file {video_path} is not available.")
        dir = os.path.dirname(video_path)
        final_dir = os.path.basename(dir)
        parent_dir = os.path.dirname(dir)
        new_dir = os.path.join(parent_dir, final_dir + '_scene_frames')
        # 手动创建目录，以防split_video_ffmpeg无法写出视频
        os.makedirs(new_dir,exist_ok=True)

        # 空目录判断，降低片段重分割成本
        if len(os.listdir(str(new_dir)))==0:
            video = open_video(video_path)
            fr = video.frame_rate
            min_scene_len = int(length * fr + 0.5)
            manager = SceneManager()
            manager.add_detector(AdaptiveDetector(adaptive_threshold=threshold, window_width=2,
                                                  min_content_val=15.0,min_scene_len=min_scene_len))
            manager.detect_scenes(video, show_progress=True)
            # 长度补足处理（默认把短片段向前补齐，短片段只有可能在两个长片段之间或视频首尾出现）
            # 如果不做短片段检测和修正处理，会导致大模型检测信息不足从而无法生成合法片段描述json
            # 这样还可以解决merge_last_scene无法解决的问题
            scenes=manager.get_scene_list()
            i=0
            while i<(len(scenes)-1):
                start,end=scenes[i]
                start2,end2=scenes[i+1]
                sc1=end.frame_num-start.frame_num
                sc2=end2.frame_num-start2.frame_num
                if sc1<min_scene_len or sc2<min_scene_len:
                    scenes[i]=(start,end2)
                    del scenes[i+1]
                else:
                    i+=1
            for i,(start,end) in enumerate(scenes):
                video=open_video(video_path)
                scene_dir=new_dir+f'/Scene{i+1}'
                os.makedirs(scene_dir,exist_ok=True)
                # 片段均匀抽帧（包含首尾帧，'5'对应视频四等分）
                save_images([(start,end)],video,5,output_dir=scene_dir)
                # 音频导出
                start_time=start.seconds
                end_time=end.seconds
                audio_name=scene_dir+f'/Scene{i+1}.wav'
                cli=['ffmpeg', "-i", video_path, "-ss", str(start_time), "-to", str(end_time),
                     "-vn","-acodec","pcm_s16le",audio_name, '-y']
                print(f"Start extracting audio {i+1} now!")
                subprocess.run(cli,cwd=os.getcwd())
                print(f"Extracting audio {i+1} successfully.")
            # split_video_ffmpeg(input_video_path=video_path, scene_list=manager.get_scene_list(), output_dir=str(new_dir), show_progress=True)

        return new_dir


    # 大模型批量推理生成片段级别json描述
    def agent_infer_l2(self,scenes_dir):
        if not os.path.isdir(scenes_dir):
            raise NotADirectoryError(f"Current directory {scenes_dir} is not available.")
        image_dirs = os.listdir(scenes_dir)

        outmap={}
        for snum,dir in enumerate(image_dirs):
            dir=os.path.join(scenes_dir,dir)
            files=os.listdir(dir)
            images=[os.path.join(dir,f) for f in files if f.split(".")[-1]=='jpg']
            # print([self.encode_image(fn) for fn in images])
            audio=[os.path.join(dir,f) for f in files if f.split(".")[-1]=='wav'][0]
            # 生成长度软编码
            conversation = [
                              {"role": "user",
                               "content": [*[{"type": "image", "image": self.encode_image(fn)} for fn in images],
                                           {"type":"audio","audio":audio},
                                           {"type": "text",
                                            "text": '''请你针对每个场景抽取的视频帧，严格按照以下JSON格式输出。
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
                                            三个部分生成内容总字数建议不超过350-400字，如果人物台词轮数多、字数多，提炼出当前片段的主要台词。'''}]}]
            eligible=False
            count=0
            while not eligible:
                count+=1
                outmap,eligible=self.valid_check(conversation,dir,outmap,snum+1)
                if count>=6:
                    raise Exception("Budget is negative on Qwen API.Please make further check.")
        return outmap

    # 大模型推理格式校验
    def valid_check(self,conversation,video_file,output_map,index):
        eligible=False
        try:
            text=""
            """
            # OpenAI兼容框架（默认是增量输出）
            completion = self.model.chat.completions.create(model="qwen3.5-omni-plus", messages=conversation,
                                                            temperature=0.85, top_p=0.9, max_tokens=512,
                                                            modalities=["text"], extra_body={"enable_thinking":False},
                                                            stream=True,stream_options={"include_usage": True})
            for chunk in completion:
                # 处理文本请求
                if chunk.choices and chunk.choices[0].delta.content:
                    text += chunk.choices[0].delta.content
            """
            # DashScope（阿里百炼）兼容框架，默认result_format是message而不是text

            completion=MultiModalConversation.call(model="qwen3.5-omni-plus",messages=conversation,
                                                   temperature=0.85,top_p=0.9,max_tokens=512,
                                                   modalities=["text"],enable_thinking=False,result_format="message",
                                                   stream=True,stream_options={'include_usage':True},incremental_output=True)
            
            for chunk in completion:
                if chunk.output and chunk.output.choices:
                    message_content=chunk.output.choices[0].message.content
                    if isinstance(message_content, list) and len(message_content) > 0:
                        text += message_content[0]["text"]

            print(f'Turn {index} Qwen3.5-Omni-Plus Answer:{text}\n')
            # DeepSeek说法:清除可能的Markdown标记
            data = json.loads(text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip())
            result = SceneAnnotation(**data)
            json_anno = result.model_dump_json(indent=0, ensure_ascii=False)
            output_map[video_file] = json_anno
            eligible = True
        except json.JSONDecodeError as e:
            print(f"❌ JSON 语法错误: {e}")
        # 结构不符合预期情况可能在文案这一栏出现
        except ValidationError as e:
            print(f"❌ 结构不符合预期:\n{e.json()}")
        except Exception as e:
            print(f"请求错误：{e}")

        return output_map,eligible

    # 大模型单样本推理生成文本摘要描述
    def agent_infer_l1(self,video_dir):
        if not os.path.isdir(video_dir):
            raise NotADirectoryError(f"Current directory {video_dir} is not available.")
        video_files = os.listdir(video_dir)
        anno_text=[]
        video_name=""
        for fn in video_files:
            if anno_suffix in fn:
                # 读取列表
                with open(os.path.join(video_dir,fn),'r',encoding='utf-8') as f:
                    anno_text=json.loads(f.read())
            # 可能是合成音视频也可能是单视频（无音频）
            elif fn.split(".")[-1]=="mp4":
                video_name=fn

        # 使用换行符连接各个片段描述json字符串
        anno_text="\n".join(anno_text)
        # 生成长度软编码
        conversation = [{"role": "system", "content": [{"type": "text", "text": "You are a helpful assistant."}]},
                        {"role": "user",
                         "content": [{"type": "text", "text": anno_text},
                                     # {"type": "image", "image": self.encode_image(scene_descript1_png)},
                                     # {"type": "image","image": self.encode_image(scene_descript2_png)},
                                     # {"type": "image", "image": self.encode_image(abstract_format_png)},
                                     {"type": "text",
                                      "text": "请根据以上文本，把每一个片段的内容概括成1-3句话"+
                                      "然后按照片段组织顺序形成整个视频的文本摘要，摘要需要包括主要人物、主要事件、主要场景以及主题提炼，同时逻辑连贯和重点突出，主次分明。生成内容不超过500字。"}]}]

        # 无重试机制
        try:
            completion = MultiModalConversation.call(model="qwen3-vl-plus", messages=conversation,
                                                    temperature=0.85, top_p=0.9, max_tokens=512,
                                                    enable_thinking = False,result_format="message",
                                                    stream=True,stream_options={"include_usage": True},
                                                    incremental_output=True)
            text=""
            for chunk in completion:
                # 处理文本请求
                if chunk.output and chunk.output.choices:
                    message_content=chunk.output.choices[0].message.content
                    if isinstance(message_content, list) and len(message_content) > 0:
                        text += message_content[0]["text"]
            print(f"{video_name} Qwen3-VL-Plus Answer:{text}")
            return text
        except Exception as e:
            print(f"请求错误：{e}")


    # 从片段级json拼接成视频级json数组（.json），写入原始视频存储目录，
    # 并调用大模型生成摘要文本（返回json数组，用于构建视频片段json微调数据集.jsonl）
    """
    original_dir:一个up主的单个视频（VideoX）的目录
    """
    def generate_scene_annotation_by_video(self,video_dir,min_len=6,threshold=3.0):
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
                json_array=json.loads(f.read())
            return json_array,True

        new_dir = self.video_slicing(vpath, min_len, threshold)
        outmap = self.agent_infer_l2(new_dir)
        # 需要严格控制片段顺序
        outmap = sorted(list(outmap.items()), key=lambda x: x[0])
        # 可以写入列表
        json_array = [scene_annotation[1] for scene_annotation in outmap]
        with open(fname, 'w', encoding='utf-8') as f:
            content=json.dumps(json_array,ensure_ascii=False)
            f.write(content)

        return json_array,False

    """
    片段级别注释和摘要、亮点分析不含有作者和视频名称信息，标签文件含有
    标签文件名称用于读取人工标签文件（包括基础信息、数据统计和亮点提取）、定位对齐亮点分析、摘要文本和片段注释内容
    后面构建数据集时代码要调整
    L2_ds_name需要包括“.jsonl”在内
    """
    def Generate_Dataset(self,dataset_dir,L2_ds_name,min_len=6,threshold=3.0):
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
                    v_dir=os.path.dirname(l)
                    # 视频摘要目录不含有MP4，不需要做分镜和大模型推理
                    if ".mp4" not in "".join(os.listdir(v_dir)):
                        continue
                    item={}
                    # L2层推理
                    json_array,done=self.generate_scene_annotation_by_video(v_dir,min_len=min_len,threshold=threshold)
                    item_json_fn = os.path.join(v_dir, "item.json")
                    # 之前摘要模型是qwen3-vl-flash,效果不好需要覆盖(注释相应代码)
                    if done and os.path.isfile(item_json_fn):
                        with open(item_json_fn, 'r', encoding='utf-8') as f:
                            item = json.loads(f.read())
                        # 二轮标注区分
                        if item.get("亮点分析","")!="":
                            # 包括亮点信息标签（l0，人工第二轮标注，在一个txt里面，不要手动换行即可，可以用自带“笔记本”软件编辑）
                            dataset.append(item)
                            continue
                        else:
                            # 只含有基础信息标签（人工第一轮标注）
                            l2=os.path.join(os.path.dirname(l),"标签2.txt")
                            if os.path.isfile(l2):
                                item = self.human_label_annotation(l2, item)
                            else:
                                item = self.human_label_annotation(l, item)
                            dataset.append(item)
                            if "亮点分析" not in item.keys():
                                print("有同学没有完成合格的二轮标注！")

                    # 只有片段描述但没有视频级综合数据写入
                    elif done:
                        item["片段描述"] = json_array
                        # L1层推理
                        abstract_text=self.agent_infer_l1(v_dir)
                        item["文本摘要"]=abstract_text
                        item = self.human_label_annotation(l, item)
                        dataset.append(item)

                    # 视频级数据集写入
                    with open(item_json_fn,'w',encoding='utf-8') as f:
                        content=json.dumps(item,ensure_ascii=False,indent=2)
                        f.write(content)

            # 目录遍历深度限定为1层
            break

        # 综合数据集写入
        with open(L2_ds_name,'w',encoding='utf-8') as f:
            for item in dataset:
                f.write(json.dumps(item,ensure_ascii=False)+"\n")


    # 基础信息标签提取
    # 冒号检测，键固定，PostgreSQL存储
    # item是字典,可传递引用或值
    def human_label_annotation(self,fn,item):
        with open(fn,'r',encoding="utf-8") as f:
            content=f.readlines()
        for line in content:
            line=line.strip()
            for mark in separate_mark:
                if mark in line:
                    line=line.split(mark)
                    break
            if isinstance(line,str):
                continue
            # 人数数量和数据统计都是字符串（上万的统计数据有同学使用w来标记，无法转换为整数）
            # “亮点分析”值需要去掉左右括号
            line[0]=line[0].strip()
            if line[0] in ("一级","二级"):
                line[0]+="门类"
            item[line[0]]=line[1].strip('(').strip(')')
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
    data_generator.Generate_Dataset("../../dataset","20260614_for_demo.jsonl",min_len=20,threshold=4.0)


