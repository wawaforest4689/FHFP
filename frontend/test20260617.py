import sys
from pathlib import Path
import json
import json5


dir=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(dir))

from common.datamodel import VideoSegment

def escape_json_quotes(s: str) -> str:
    """
    将字符串中所有未转义的双引号转为 \"
    保留已转义的 \"
    """
    # 保护已有的 \"
    protected = s.replace('\\"', '\x00').replace('\\n','').replace('\n','')
    # 替换剩余的 "
    escaped = protected.replace('"', '\\"')
    escaped=escaped.replace('\\"{','"{').replace('}\\"','}"')

    # 恢复 \"
    return escaped.replace('\x00', '\\"')

D={"scene":"这里是种植百香果的花园。","audio":"背景音乐选用的是高山流水。","text":json.dumps([{"人物":"旁白","台词":"四季轮转，周而复始。又到了春天——百香果成熟的季节。"}],ensure_ascii=False)}

print(rf"tmp\\tts_{123456789}.mp3")
shots2 = "[\"{\\n  \\\"scene\\\": \\\"视频展示了'乡村户外庭院'场景，阳光明媚。一名男子（小王）手持几只鸭子向镜头介绍，并拿出手机拍摄。随后转场至室内，一位老人（爷爷）在桌前讲解腌制技巧，桌上放有盐和花椒等配料。接着是特写镜头展示手工抹酱、挂网风干的板鸭成品及成品被粉丝抢购的热闹画面。最后以切开的板鸭特写结束，展示了其晶莹的油脂和红亮的颜色。\\\",\\n  \\\"audio\\\": \\\"背景为轻快舒缓的纯音乐配乐。主要人声清晰，包含男子的开场白、老人的专业指导以及感叹词。中间穿插了商业配音的评论音效（如‘大运平台’）。结尾处有切开食物时发出的声音。\\\",\\n  \\\"text\\\": [\\n    {\\n      \\\"figure\\\": \\\"主人公\\\",\\n      \\\"script\\\": \\\"大家好，我是小王，我回老家来学习怎么制作板鸭。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"旁白/主人公\\\",\\n      \\\"script\\\": \\\"我们家养的有几种鸭子，有一种叫王大炮的，个头大，跑得也快。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"老人（爷爷）\\\",\\n      \\\"script\\\": \\\"要想做成火候大的板鸭，选材很重要。鸭子肥瘦均匀，肉质紧实。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"老人（爷爷）\\\",\\n      \\\"script\\\": \\\"配料一定要足，盐是要用花盐，还有花椒。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"老人（爷爷）\\\",\\n      \\\"script\\\": \\\"抹完酱之后把鸭子挂起来，记得要放在太阳底下。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"老人（爷爷）\\\",\\n      \\\"script\\\": \\\"很多人喜欢吃这个板鸭，香而不腻。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"旁白\\\",\\n      \\\"script\\\": \\\"很多人喜欢吃这个板鸭，香而不腻。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"旁白\\\",\\n      \\\"script\\\": \\\"很多人都喜欢吃这种板鸭。\\\"\\n    }\\n  ]\\n}\", \"{\\n  \\\"scene\\\": \\\"视频展示了传统美食板鸭的制作与销售过程。首先，在阳光明媚的室外，一只白色鸭子挂在木杆上晾晒，旁边有一桶已加工好的板鸭。接着切换到室内，一位老奶奶正在品尝产品并展示给镜头。随后是一系列特写镜头：打开包装、取出鸭掌、展示诱人的色泽以及成品。最后是顾客在直播中热情互动的画面。整体光线自然，运镜平稳，聚焦于美食细节和人物状态。\\\",\\n  \\\"audio\\\": \\\"背景播放着轻快的纯音乐。开头有清脆的敲击声。主要声音包括：老奶奶品尝时的满足感慨、展示产品的自豪语气、以及网络直播环境下热烈的弹幕与评论音效（如“秒抢光”、“又好吃又便宜”），营造出产品受欢迎的氛围。\\\",\\n  \\\"text\\\": [\\n    {\\n      \\\"figure\\\": \\\"老奶奶\\\",\\n      \\\"script\\\": \\\"哇，真香啊！\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"主播\\\",\\n      \\\"script\\\": \\\"给大家看下咱们今天的主要内容，是一种传统的板鸭，香而不腻，很多人吃过一次就忘不了。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"观众1\\\",\\n      \\\"script\\\": \\\"又是熟悉的味道\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"观众2\\\",\\n      \\\"script\\\": \\\"每一年必买\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"观众3\\\",\\n      \\\"script\\\": \\\"提前预定一下\\\"\\n    }\\n  ]\\n}\", \"{\\n  \\\"scene\\\": \\\"视频展示了如何切开一块深色油状物质。镜头从顶部开始，沿着横向切口进行特写拍摄。光线明亮，背景模糊，主体突出。\\\",\\n  \\\"audio\\\": \\\"背景音乐持续且节奏轻快，伴有轻微的环境杂音。没有明显的对话或人声噪音。\",\n  \\\"text\\\": []\\n}\"]"

print(shots2)
print(escape_json_quotes(shots2))

try:
    shots2 = json.loads(escape_json_quotes(shots2))
except json.JSONDecodeError as e:
    print(f"JSON decode error:{e}.")
    try:
        shots2=json5.loads(escape_json_quotes(shots2))
    except:
        pass

print(shots2)
for shot in shots2:
    print(shot)
    try:
        shot=json.loads(shot)
        vs_obj=VideoSegment(**shot)
        print(vs_obj.text)
    except json.JSONDecodeError as e:
        print(f'JSON5 decode error:{e}.')


