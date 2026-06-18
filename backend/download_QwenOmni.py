from huggingface_hub import snapshot_download
from openai import OpenAI
import requests
from dashscope import MultiModalConversation
import dashscope
from dashscope_api_annotate import Data_Gen

def download_omini():
    snapshot_download(repo_id="Qwen/Qwen2.5-Omni-3B",
                      local_dir="D:/models/Qwen2.5-Omni-3B",
                      local_dir_use_simulinks=False,
                      resume_download=True)

def test_dashscope_api():
    # API Key设定
    dashscope_api_key="sk-bcb509c0f2814ca3a3429d5b9604852e"
    dashscope.api_key = "sk-bcb509c0f2814ca3a3429d5b9604852e"

    # 配置客户端
    client = OpenAI(api_key=dashscope_api_key,base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",)

    try:
        # 3. 发送一个简单的测试请求
        completion = client.chat.completions.create(
            model="qwen-turbo",  # 使用支持快速响应的模型
            messages=[
                {'role': 'system', 'content': '你是人工智能助手'},
                {'role': 'user', 'content': '你好，这是一条测试消息，请简单回复"我是通易千问大语言模型"。'}
            ],
            temperature=0.85,
            top_p=0.9,
            max_tokens=50
        )

        # 4. 如果成功获取到回复，说明API Key有效
        print("✅ API Key 有效！")
        print(f"模型回复: {completion.choices[0].message.content}")
    except Exception as e:
        # 5. 如果调用失败，打印错误信息以进行故障排查
        print("❌ API Key 无效或调用出错！")
        print(f"错误信息: {e}")

    json_format_png = r"d:\wf200\Documents\mypython\FHFP_video_assistant\dataset\片段分析.png"
    audio_path=r"d:\wf200\Documents\mypython\FHFP_video_assistant\dataset\一个卖土豆的胖子\video1_scene_frames\Scene1\Scene1.wav"
    dg=Data_Gen()
    messages=[{"role":"user","content":[{"type":"audio","audio":audio_path},{"type":"text","text":"Can you describe the audio?"}]}]
    completion=MultiModalConversation.call("qwen2.5-omni-7b",messages=messages,max_tokens=64,stream=True,
                                           stream_options={'include_usage':True})
    text=""
    for chunk in completion:
        if chunk.output and chunk.output.choices:
            message_content = chunk.output.choices[0].message.content
            if isinstance(message_content, list) and len(message_content) > 0:
                text += message_content[0]["text"]
    print(text)


if __name__ == "__main__":
    test_dashscope_api()


