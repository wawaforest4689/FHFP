#!/usr/bin/env python3
"""
视频合成工具 v3
功能：将无声视频片段 + 文案TXT 合成为带字幕、配音、说话人头像小窗的完整视频

使用方式：
  python video_composer.py                     # 自动扫描当前目录
  python video_composer.py --txt 文案.txt       # 单txt多视频模式
  python video_composer.py --output out.mp4    # 自定义输出文件名

目录结构：
  1.mp4  2.mp4  3.mp4    ← 主内容视频（无声）
  文案_test.txt           ← 文案（单txt模式）
  man.gif                 ← 男声说话人头像GIF（可选，放根目录）
  girl.gif                ← 女声说话人头像GIF（可选，放根目录）
  msyh.ttc               ← 微软雅黑字体（从 C:/Windows/Fonts/ 复制过来）

TXT 格式：
  每行：[{"figure": "男子", "script": "台词内容"}, ...](起始秒-终止秒)
  figure 含"男"字 → 男声 + 显示 man.gif 头像
  其他（主讲人/女等） → 女声 + 显示 girl.gif 头像
"""

from __future__ import annotations

import os
import re
import sys
import json
import glob
import uuid
import shutil
import asyncio
import argparse
import subprocess
import platform as _platform
from pathlib import Path
from typing import List, Dict, Optional



# ═══════════════════════════ 配置区（按需修改）═══════════════════════════

# TTS 声音（edge-tts，需联网）
VOICE_MALE   = "zh-CN-YunxiNeural"      # 男声
VOICE_FEMALE = "zh-CN-XiaoyingNeural"   # 女声（晓伊）
# 其他女声可选：zh-CN-XiaoxiaoNeural / zh-CN-XiaoyiNeural / zh-CN-XiaoruiNeural

# 字幕字体：把 msyh.ttc 复制到脚本同目录即可
SUBTITLE_FONT      = "msyh.ttc"
SUBTITLE_FONTSIZE  = 28      # 字号 px（缩小以减少换行）
SUBTITLE_POSITION  = 0.88    # 字幕距顶部比例（0~1），0.88 ≈ 画面底部
SUBTITLE_MAX_CHARS = 28      # 每行最多字符数（加宽，尽量一行显示）

# 说话人头像小窗配置
AVATAR_W        = 200        # 头像窗口宽度 px（高度等比缩放）
AVATAR_MARGIN   = 20         # 距画面右/下边缘距离 px
AVATAR_OPACITY  = 1.0        # 透明度 0~1
AVATAR_H_RATIO=0.33

# 说话人头像文件名（放项目根目录）
AVATAR_MALE_FILE   = "avatars/man.mp4"
AVATAR_FEMALE_FILE = "avatars/girl.mp4"

# 男声 figure 关键词
MALE_KEYWORDS = {"男子", "男", "男性", "男生", "先生", "旁白男"}

# TXT 字段名映射（支持中英文两种格式）
# 英文格式：{"figure": "男子", "script": "台词"}
# 中文格式：{"人物": "旁白", "台词": "内容"}
FIELD_FIGURE = ("figure", "人物")   # 说话人字段，按顺序尝试
FIELD_SCRIPT = ("script", "台词")   # 台词字段，按顺序尝试

# ═══════════════════════════════════════════════════════════════════════


# ─────────────── 工具函数 ───────────────

def get_safe_tmpdir() -> str:
    """返回纯英文临时目录（解决 Windows 中文路径问题）"""
    if _platform.system() == "Windows":
        base = "C:/ffmpeg_tmp"
        os.makedirs(base, exist_ok=True)
        d = os.path.join(base, uuid.uuid4().hex)
        os.makedirs(d, exist_ok=True)
        return d
    else:
        import tempfile
        return tempfile.mkdtemp()


def copy_safe(src: str, safe_dir: str, name: str) -> str:
    """复制文件到安全目录，返回新路径"""
    ext = os.path.splitext(src)[1]
    dst = os.path.join(safe_dir, name + ext)
    shutil.copy2(src, dst)
    return dst


def get_field(seg: Dict, keys: tuple) -> str:
    """从 seg 字典中按优先级尝试多个字段名，返回第一个存在的值"""
    for k in keys:
        if k in seg:
            return seg[k]
    return ""


def is_male(figure: str) -> bool:
    return any(kw in figure for kw in MALE_KEYWORDS)


def wrap_text(text: str, max_chars: int = SUBTITLE_MAX_CHARS) -> str:
    lines = []
    while len(text) > max_chars:
        lines.append(text[:max_chars])
        text = text[max_chars:]
    if text:
        lines.append(text)
    return "\n".join(lines)


import os
import subprocess
import shlex
from typing import List, Dict, Optional


# 字幕样式配置
FONT_FILE = "msyh.ttc"  # 请替换为你系统的字体路径
SUBTITLE_FONT_SIZE = 24
SUBTITLE_FONT_COLOR = "white"
SUBTITLE_BORDER_WIDTH = 2
SUBTITLE_BORDER_COLOR = "black"
SUBTITLE_BOX = 1
SUBTITLE_BOX_COLOR = "black@0.5"
SUBTITLE_BOX_BORDER_W = 5


def escape_drawtext(text: str) -> str:
    """对 drawtext 的 text 参数进行转义"""
    text = text.replace("\\", "\\\\")
    text = text.replace("'", "\\'")
    text = text.replace(":", "\\:")
    text = text.replace("\n", "\\n")
    return text


def build_drawtext_filters(plan: List[Dict], w: int, h: int) -> List[str]:
    """生成 drawtext 滤镜列表，每个带 enable 时间控制"""
    filters = []
    for seg in plan:
        text = seg.get("script", "")
        if not text:
            continue
        start = seg["start"]
        end = seg["end"]

        escaped_text = escape_drawtext(text)
        x_expr = "(w-text_w)/2"
        y_expr = str(int(h * SUBTITLE_POSITION))
        print(f'y_expr:{y_expr}')
        # 注意：enable 中的逗号需要转义为 \,
        drawtext = (
            f"drawtext=fontfile={FONT_FILE}:"
            f"text='{escaped_text}':"
            f"fontsize={SUBTITLE_FONT_SIZE}:"
            f"fontcolor={SUBTITLE_FONT_COLOR}:"
            f"borderw={SUBTITLE_BORDER_WIDTH}:"
            f"bordercolor={SUBTITLE_BORDER_COLOR}:"
            f"box={SUBTITLE_BOX}:"
            f"boxcolor={SUBTITLE_BOX_COLOR}:"
            f"boxborderw={SUBTITLE_BOX_BORDER_W}:"
            f"x={x_expr}:"
            f"y={y_expr}:"
            f"enable='between(t\\,{start:.3f}\\,{end:.3f})'"
        )
        filters.append(drawtext)
    return filters


def run_ffmpeg(cmd: List[str], tag: str = "ffmpeg") -> bool:
    """运行 ffmpeg 命令，修复 Windows GBK 编码问题"""
    cmd_str = ' '.join(shlex.quote(str(c)) for c in cmd)
    print(f"[{tag}] 执行命令:\n{cmd_str}\n")

    # Windows 下使用文件路径而非 TemporaryFile 避免编码问题
    import tempfile
    stdout_path = os.path.join(tempfile.gettempdir(), f"ffmpeg_out_{uuid.uuid4().hex}.txt")
    stderr_path = os.path.join(tempfile.gettempdir(), f"ffmpeg_err_{uuid.uuid4().hex}.txt")

    try:
        with open(stdout_path, 'w', encoding='utf-8', errors='replace') as stdout_f, \
                open(stderr_path, 'w', encoding='utf-8', errors='replace') as stderr_f:

            result = subprocess.run(
                cmd,
                stdout=stdout_f,
                stderr=stderr_f,
                check=True,
                timeout=600,
                encoding='utf-8',
                errors="replace"
            )

        # 读取错误输出检查
        if os.path.exists(stderr_path):
            with open(stderr_path, 'r', encoding='utf-8', errors='replace') as f:
                stderr_text = f.read()
            if stderr_text and "error" in stderr_text.lower():
                print(f"[{tag}] 警告: {stderr_text[:500]}")

        print(f"[{tag}] 成功")
        return True

    except subprocess.TimeoutExpired:
        print(f"[{tag}] 错误: 命令执行超时（超过10分钟）")
        return False
    except subprocess.CalledProcessError as e:
        # 读取错误输出
        if os.path.exists(stderr_path):
            with open(stderr_path, 'r', encoding='utf-8', errors='replace') as f:
                err = f.read()
            print(f"[{tag}] 错误: 返回码 {e.returncode}, stderr: {err[:1000]}")
        else:
            print(f"[{tag}] 错误: 返回码 {e.returncode}")
        return False
    except Exception as e:
        print(f"[{tag}] 错误: {type(e).__name__}: {e}")
        return False
    finally:
        # 清理临时文件
        for p in [stdout_path, stderr_path]:
            try:
                if os.path.exists(p):
                    os.remove(p)
            except:
                pass


def compose_video(merged_video: str, plan: List[Dict], output: str,
                  video_info: Dict,
                  avatar_male_path: Optional[str] = None,
                  avatar_female_path: Optional[str] = None) -> bool:
    """
    合成字幕 + 配音 + 说话人头像小窗到视频。
    """
    w = video_info["width"]
    h = video_info["height"]

    max_plan_dur = max((s["end"] for s in plan), default=0.0)
    target_dur = max(video_info.get("duration", 0), max_plan_dur)

    # ── 计算头像位置 ──
    avatar_x = w - int(h*AVATAR_H_RATIO*9/16) - AVATAR_MARGIN
    subtitle_top_px = int(h * SUBTITLE_POSITION) - 10
    avatar_y = max(0, subtitle_top_px - int(h*AVATAR_H_RATIO))

    # ── 头像时间段 enable ──
    def make_enable(segs_for_gender):
        if not segs_for_gender:
            return "0"
        parts = [f"between(t,{s['start']:.3f},{s['end']:.3f})" for s in segs_for_gender]
        return "+".join(parts)

    male_segs = [s for s in plan if s.get("is_male", True)]
    female_segs = [s for s in plan if not s.get("is_male", True)]

    male_enable = make_enable(male_segs)
    female_enable = make_enable(female_segs)

    # ── 输入索引规划 ──
    tts_valid = [s for s in plan if s.get("tts_path") and os.path.exists(s["tts_path"])]
    n_tts = len(tts_valid)

    avatar_inputs = []
    male_input_idx = None
    female_input_idx = None

    base_idx = 1 + n_tts  # 0=主视频, 1..n_tts=TTS音频
    if avatar_male_path and os.path.exists(avatar_male_path) and male_segs:
        male_input_idx = base_idx
        avatar_inputs += ["-i", avatar_male_path]
        base_idx += 1
    if avatar_female_path and os.path.exists(avatar_female_path) and female_segs:
        female_input_idx = base_idx
        avatar_inputs += ["-i", avatar_female_path]
        base_idx += 1

    # ── TTS 音频滤镜 ──
    # ── 音频滤镜：原视频音频 + 所有 TTS → amix ──
    audio_filter_parts = []
    tts_input_args = []

    audio_maps = []
    audio_labels = []

    # 原视频音频（adelay=0 保持同步）
    audio_maps.append("[0:a]adelay=0|0[orig_a]")
    audio_labels.append("[orig_a]")

    # TTS 音频
    for idx, seg in enumerate(tts_valid):
        tts_input_args += ["-i", seg["tts_path"]]
        delay_ms = int(seg["start"] * 1000)
        audio_maps.append(f"[{idx + 1}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")
        audio_labels.append(f"[a{idx}]")

    if audio_maps:
        audio_filter_parts.append(";".join(audio_maps))
        n_audio = len(audio_labels)
        audio_filter_parts.append(
            f"{''.join(audio_labels)}amix=inputs={n_audio}:dropout_transition=0[aout]"
        )

    # ── 视频滤镜链构建 ──
    subtitle_filters = build_drawtext_filters(plan, w, h)

    # 视频滤镜链：从 [0:v] 开始，经过 tpad，然后叠加头像，然后叠加字幕
    # 注意：多个滤镜用逗号连接，标签用 [] 包裹

    # 步骤1: tpad 延长视频
    video_chain = f"[0:v]tpad=stop_mode=clone:stop_duration={target_dur:.3f}"

    # 步骤2: 叠加男头像
    if male_input_idx is not None:
        # 当前输出是某个标签，下一个滤镜接收它
        video_chain += f"[v1];[v1][{male_input_idx}:v]overlay=x={avatar_x}:y={avatar_y}:format=auto:enable='{male_enable}'"

    # 步骤3: 叠加女头像
    if female_input_idx is not None:
        if male_input_idx is not None:
            # 前面有男头像，输出是 [v2]
            video_chain += f"[v2];[v2][{female_input_idx}:v]overlay=x={avatar_x}:y={avatar_y}:format=auto:enable='{female_enable}'"
        else:
            # 前面没有男头像，直接从 tpad 输出
            video_chain += f"[v1];[v1][{female_input_idx}:v]overlay=x={avatar_x}:y={avatar_y}:format=auto:enable='{female_enable}'"

    # 步骤4: 叠加字幕
    if subtitle_filters:
        # 确定当前最后一个输出标签
        if male_input_idx is not None and female_input_idx is not None:
            last_label = "v2"
        elif male_input_idx is not None or female_input_idx is not None:
            last_label = "v1"
        else:
            last_label = None  # 直接从 tpad 输出

        if last_label:
            video_chain += f"[{last_label}];[{last_label}]" + ",".join(subtitle_filters) + "[vout]"
        else:
            video_chain += "," + ",".join(subtitle_filters) + "[vout]"
    else:
        # 无字幕，直接输出
        if male_input_idx is not None and female_input_idx is not None:
            video_chain += "[v2];[v2]copy[vout]"
        elif male_input_idx is not None or female_input_idx is not None:
            video_chain += "[v1];[v1]copy[vout]"
        else:
            video_chain += "[vout]"

    # ── 组装完整 filter_complex ──
    all_fc_parts = []

    # 视频部分（必须是一个完整的滤镜链，用 ; 与其他链分隔）
    all_fc_parts.append(video_chain)

    # 音频部分
    if audio_filter_parts:
        all_fc_parts.extend(audio_filter_parts)

    filter_complex = ";".join(all_fc_parts)

    # ── 组装 ffmpeg 命令 ──
    cmd = ["ffmpeg", "-y", "-i", merged_video] + tts_input_args + avatar_inputs
    cmd += ["-filter_complex", filter_complex]
    cmd += ["-map", "[vout]"]

    if audio_filter_parts:
        cmd += ["-map", "[aout]"]
    else:
        # 没有 TTS 时，保留原视频音频
        cmd += ["-map", "0:a?"]

    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-t", f"{target_dur:.3f}",
        output
    ]

    return run_ffmpeg(cmd, tag="最终合成")


def compose_video_v2(merged_video: str, plan: List[Dict], output: str,
                  video_info: Dict,
                  avatar_male_path: Optional[str] = None,
                  avatar_female_path: Optional[str] = None) -> bool:
    """
    合成字幕 + 配音 + 说话人头像小窗到视频。
    支持 GIF 直接输入（透明背景）和 MP4 输入。
    """
    w = video_info["width"]
    h = video_info["height"]
    target_dur = max(video_info.get("duration", 0),
                     max((s["end"] for s in plan), default=0.0))

    # 头像目标高度 = 画面 1/3
    avatar_target_h = h // 3
    avatar_margin = 20

    # TTS
    tts_valid = [s for s in plan if s.get("tts_path") and os.path.exists(s["tts_path"])]
    n_tts = len(tts_valid)

    # 输入索引规划
    inputs = ["-i", merged_video]
    tts_input_args = []

    for seg in tts_valid:
        tts_input_args += ["-i", seg["tts_path"]]
        inputs += ["-i", seg["tts_path"]]

    # 头像输入（GIF 或 MP4 都可以）
    male_input_idx = None
    female_input_idx = None

    if avatar_male_path and os.path.exists(avatar_male_path):
        male_input_idx = 1 + n_tts
        inputs += ["-i", avatar_male_path]

    elif avatar_female_path and os.path.exists(avatar_female_path):
        female_input_idx = 1 + n_tts + (1 if male_input_idx else 0)
        inputs += ["-i", avatar_female_path]

    # assert(male_input_idx is not None or female_input_idx is not None)

    # 头像时间段 enable
    def make_enable(segs_for_gender):
        if not segs_for_gender:
            return "0"
        parts = [f"between(t,{s['start']:.3f},{s['end']:.3f})" for s in segs_for_gender]
        return "+".join(parts)

    male_enable,female_enable="",""
    if male_input_idx is not None:
        male_enable = make_enable(plan)
    else:
        female_enable = make_enable(plan)

    # ── 音频滤镜 ──
    audio_parts = []

    if tts_valid:
        audio_maps = []
        audio_labels = []

        # 原视频音频
        audio_maps.append("[0:a]adelay=0|0[orig_a]")
        audio_labels.append("[orig_a]")

        # TTS 音频
        for idx, seg in enumerate(tts_valid):
            delay_ms = int(seg["start"] * 1000)
            audio_maps.append(f"[{idx + 1}:a]adelay={delay_ms}|{delay_ms}[a{idx}]")
            audio_labels.append(f"[a{idx}]")

        audio_parts.append(";".join(audio_maps))
        n_audio = len(audio_labels)
        audio_parts.append(
            f"{''.join(audio_labels)}amix=inputs={n_audio}:dropout_transition=0[aout]"
        )

    # ── 视频滤镜链 ──
    subtitle_filters = build_drawtext_filters(plan, w, h)

    # 计算头像尺寸
    def get_scaled_size(path):
        if not path:
            return (0, 0)
        try:
            r = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=width,height",
                 "-of", "csv=s=x:p=0", path],
                capture_output=True, text=True, encoding="utf-8", errors="replace"
            )
            if r.returncode == 0 and r.stdout.strip():
                w_h = r.stdout.strip().split('x')
                orig_w, orig_h = int(w_h[0]), int(w_h[1])
                scaled_h = avatar_target_h
                scaled_w = int(orig_w * scaled_h / orig_h) if orig_h > 0 else 200
                return (scaled_w, scaled_h)
        except:
            pass
        return (200, avatar_target_h)

    # 构建视频链
    fc_parts = []
    fc_parts.append(f"[0:v]tpad=stop_mode=clone:stop_duration={target_dur:.3f}[v0]")

    current_v = "v0"
    v_idx = 1

    def add_avatar(input_idx, enable_expr, path):
        nonlocal current_v, v_idx
        if input_idx is None or not path:
            return

        scaled_w, scaled_h = get_scaled_size(path)
        pos_x = w - scaled_w - avatar_margin
        pos_y = h - scaled_h - avatar_margin
        print(f"scaled w,h:{scaled_w, scaled_h}")
        print(f"x,y:{pos_x, pos_y}")

        out_label = f"v{v_idx}"

        fc_parts.append(
            f"[{current_v}][{input_idx}:v]"
            f"overlay=x={pos_x}:y={pos_y}:format=auto:enable='{enable_expr}'[{out_label}]"
        )

        current_v = out_label
        v_idx += 1

    # 叠加头像
    if male_input_idx is not None:
        add_avatar(male_input_idx, male_enable, avatar_male_path)
    elif female_input_idx is not None:
        add_avatar(female_input_idx, female_enable, avatar_female_path)

    # 叠加字幕
    if subtitle_filters:
        fc_parts.append(f"[{current_v}]" + ",".join(subtitle_filters) + "[vout]")
    else:
        fc_parts.append(f"[{current_v}]copy[vout]")

    # 音频链
    if audio_parts:
        fc_parts.extend(audio_parts)

    filter_complex = ";".join(fc_parts)

    # ── 组装 ffmpeg 命令 ──
    cmd = ["ffmpeg", "-y"] + inputs
    cmd += ["-filter_complex", filter_complex]
    cmd += ["-map", "[vout]"]

    if audio_parts:
        cmd += ["-map", "[aout]"]
    else:
        cmd += ["-map", "0:a"]

    cmd += [
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        "-t", f"{target_dur:.3f}",
        output
    ]

    return run_ffmpeg(cmd, tag="最终合成")

def get_video_info(path: str) -> Dict:
    """
    获取视频信息（分辨率、时长）。
    修复：增加错误诊断、路径安全检查、stderr输出。
    """
    print(str(Path(__file__).resolve().parent))
    print(path)
    if os.path.abspath(path)!=path:
        path=os.path.join(str(Path(__file__).resolve().parent.parent),"backend",path.strip('/')).replace('\\','/')
    # 先检查文件是否存在且可读
    if not os.path.exists(path):
        print(f"  [ffprobe错误] 文件不存在: {path}")
        return {"width": 0, "height": 0, "duration": 0.0}

    if not os.path.isfile(path):
        print(f"  [ffprobe错误] 路径不是文件: {path}")
        return {"width": 0, "height": 0, "duration": 0.0}

    file_size = os.path.getsize(path)
    if file_size == 0:
        print(f"  [ffprobe错误] 文件大小为0（可能上传未完成）: {path}")
        return {"width": 0, "height": 0, "duration": 0.0}

    # 路径安全检查：Windows下如果有空格或特殊字符，用引号包裹
    # 但 subprocess.run 用列表传参时不需要手动加引号
    # 这里检查路径是否包含可能导致问题的字符
    if any(c in path for c in ['\n', '\r', '\t']):
        print(f"  [ffprobe警告] 路径包含控制字符: {repr(path)}")

    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error",          # 用 "error" 级别显示错误，"quiet" 会隐藏
             "-print_format", "json",
             "-show_streams", "-show_format",
             path],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace"
        )

        # 返回码非0时，打印详细错误信息
        if r.returncode != 0:
            stderr_msg = r.stderr.strip() if r.stderr else "(无stderr输出)"
            stdout_msg = r.stdout.strip() if r.stdout else "(无stdout输出)"
            print(f"  [ffprobe错误] 返回码: {r.returncode}")
            print(f"  [ffprobe错误] 文件: {path}")
            print(f"  [ffprobe错误] 文件大小: {file_size} bytes")
            print(f"  [ffprobe错误] stderr: {stderr_msg[:500]}")
            print(f"  [ffprobe错误] stdout: {stdout_msg[:500]}")
            return {"width": 0, "height": 0, "duration": 0.0}

        # 解析JSON
        if not r.stdout.strip():
            print(f"  [ffprobe错误] stdout为空: {path}")
            return {"width": 0, "height": 0, "duration": 0.0}

        try:
            info = json.loads(r.stdout)
        except json.JSONDecodeError as e:
            print(f"  [ffprobe错误] JSON解析失败: {e}")
            print(f"  [ffprobe错误] stdout前200字符: {r.stdout[:200]}")
            return {"width": 0, "height": 0, "duration": 0.0}

        width = height = 0
        duration = 0.0

        # 从视频流获取宽高和时长
        video_stream_found = False
        for s in info.get("streams", []):
            if s.get("codec_type") == "video":
                video_stream_found = True
                width = s.get("width", 0) or 0
                height = s.get("height", 0) or 0

                # 尝试从视频流获取时长
                if duration == 0:
                    for key in ["duration", "duration_ts", "tags.duration"]:
                        val = s.get(key)
                        if val:
                            try:
                                duration = float(val)
                                break
                            except (ValueError, TypeError):
                                pass
                break  # 只取第一个视频流

        if not video_stream_found:
            print(f"  [ffprobe警告] 未找到视频流: {path}")

        # 从 format 级别获取时长（更可靠）
        if duration == 0:
            fmt = info.get("format", {})
            for key in ["duration", "tags.duration"]:
                val = fmt.get(key)
                if val and val != "N/A":
                    try:
                        duration = float(val)
                        break
                    except (ValueError, TypeError):
                        pass

        # 兜底：通过帧数/帧率计算
        if duration == 0:
            for s in info.get("streams", []):
                if s.get("codec_type") == "video":
                    try:
                        nb_frames = s.get("nb_frames")
                        r_frame_rate = s.get("r_frame_rate", "0/1")
                        if nb_frames and r_frame_rate:
                            if "/" in str(r_frame_rate):
                                num, den = str(r_frame_rate).split("/")
                                fps = float(num) / float(den) if float(den) != 0 else 0
                            else:
                                fps = float(r_frame_rate)
                            if fps > 0:
                                duration = float(nb_frames) / fps
                                break
                    except (ValueError, TypeError, ZeroDivisionError):
                        pass

        # 最终兜底：如果 duration 仍然为0，用文件大小估算（非常粗略）
        if duration == 0 and file_size > 0:
            # 假设平均码率 2MB/s（仅作为最后手段）
            estimated = file_size / (2 * 1024 * 1024)
            print(f"  [ffprobe警告] 无法获取时长，按文件大小估算: ~{estimated:.1f}s")
            duration = estimated

        return {"width": width, "height": height, "duration": duration}

    except Exception as e:
        print(f"  [ffprobe错误] 异常: {type(e).__name__}: {e}")
        return {"width": 0, "height": 0, "duration": 0.0}



def get_audio_duration(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


# ─────────────── 文案解析 ───────────────

def parse_txt(txt_path: str) -> List[Dict]:
    """
    解析 txt，返回 block 列表。
    block = {"start": int, "end": int, "segments": [{"figure":..,"script":..}, ...]}
    """
    result  = []
    text    = Path(txt_path).read_text(encoding="utf-8").strip()
    pattern = re.compile(r'(\[.*?\])\s*\((\d+)-(\d+)\)', re.DOTALL)

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        m = pattern.search(line)

        if not m:
            print("  [警告] 无法解析：{}".format(line[:80]))
            continue
        # raw_json = m.group(1).replace('\\n', '\n').replace('\\"', '"')
        raw_json=m.group(1)
        try:
            segs = eval(raw_json)
        except Exception as e:
            print(f'Can not deserialize json text to list:{e}')
        result.append({"start": int(m.group(2)), "end": int(m.group(3)), "segments": segs})

    return result

def parse_str(text:str):
    result=[]
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        # raw_json = m.group(1).replace('\\n', '\n').replace('\\"', '"')
        ind=line[::-1].index('(')
        raw_json=line[:len(line)-ind-1]
        start,end=map(int,line[len(line)-ind-1:].strip('(').strip(')').split('-'))
        print(f'start:{start},end:{end}')

        segs = eval(raw_json)
        result.append({"start": start, "end": end, "segments": segs})

    return result


def plan_segments(blocks: List[Dict],male:bool) -> List[Dict]:
    """按字符比例把 block 内台词分配到具体时间段，返回 plan 列表"""
    plan = []
    for block in blocks:
        segs        = block["segments"]
        block_start = block["start"]
        block_end   = block["end"]
        if not segs:
            continue
        total_chars = sum(len(get_field(s, FIELD_SCRIPT)) for s in segs) or len(segs)
        block_dur   = block_end - block_start
        cur         = float(block_start)
        for seg in segs:
            script  = get_field(seg, FIELD_SCRIPT)
            figure  = get_field(seg, FIELD_FIGURE)
            ratio   = len(script) / total_chars
            end     = cur + block_dur * ratio
            plan.append({
                "start":    round(cur, 3),
                "end":      round(end, 3),
                "script":   script,
                "figure":   figure,
                "is_male":  male,
                # 2026/06/12: 由于暂时没有性别中立角色，所以用女角色代替性别中立角色（但我们支持用户手动更改人物属性）
                "voice":    VOICE_MALE if male else VOICE_FEMALE,
                "tts_path": None,
            })
            cur = end
    return plan


# ─────────────── TTS ───────────────

async def _tts_edge(text: str, voice: str, path: str) -> bool:
    try:
        import edge_tts
        await edge_tts.Communicate(text, voice).save(path)
        return True
    except Exception as e:
        print("  [TTS] edge-tts失败: {}".format(e))
        return False


def _tts_fallback(text: str, path: str) -> bool:
    try:
        import pyttsx3
        e = pyttsx3.init()
        e.save_to_file(text, path)
        e.runAndWait()
        e.stop()
        return os.path.exists(path)
    except Exception as ex:
        print("  [TTS] pyttsx3失败: {}".format(ex))
    return False


async def generate_tts(text: str, voice: str, path: str) -> bool:
    ok = await _tts_edge(text, voice, path)
    if not ok:
        print("  [TTS] 切换离线方案...")
        ok = _tts_fallback(text, path)
    return ok


def adjust_speed(src: str, dst: str, target_dur: float) -> bool:
    src_dur = get_audio_duration(src)
    if src_dur <= 0 or target_dur <= 0:
        shutil.copy(src, dst)
        return True
    ratio = max(0.5, min(2.0, src_dur / target_dur))
    if ratio > 2.0:
        filt = "atempo=2.0,atempo={:.4f}".format(ratio / 2.0)
    elif ratio < 0.5:
        filt = "atempo=0.5,atempo={:.4f}".format(ratio * 2.0)
    else:
        filt = "atempo={:.4f}".format(ratio)
    return run_ffmpeg(["ffmpeg", "-y", "-i", src, "-filter:a", filt, dst])


async def generate_all_tts(plan: List[Dict], safe_dir: str) -> List[Dict]:
    for idx, seg in enumerate(plan):
        raw = os.path.join(safe_dir, "tts_raw_{}.mp3".format(idx))
        adj = os.path.join(safe_dir, "tts_adj_{}.mp3".format(idx))
        print("  [TTS {}/{}] {} | {}...".format(
            idx + 1, len(plan), seg["figure"], seg["script"][:30]))
        ok = await generate_tts(seg["script"], seg["voice"], raw)
        if ok and os.path.exists(raw):
            ok2 = adjust_speed(raw, adj, seg["end"] - seg["start"])
            seg["tts_path"] = adj if ok2 else raw
        else:
            seg["tts_path"] = None
            print("    -> 生成失败，该段静音")
    return plan


# ─────────────── 视频拼接 ───────────────

def concat_videos(video_paths: List[str], output: str, safe_dir: str) -> bool:
    """拼接多段主视频，自动复制到安全路径"""
    if len(video_paths) == 1:
        shutil.copy2(video_paths[0], output)
        return True
    safe_paths = []
    for i, p in enumerate(video_paths):
        sp = copy_safe(p, safe_dir, "input_{:03d}".format(i))
        safe_paths.append(sp)
        print("    复制: {} -> {}".format(os.path.basename(p), os.path.basename(sp)))
    list_file = os.path.join(safe_dir, "concat_list.txt")
    with open(list_file, "w", encoding="utf-8") as f:
        for p in safe_paths:
            f.write("file '{}'\n".format(p.replace("\\", "/")))
    ok = run_ffmpeg(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
         "-c:v", "libx264", "-preset", "fast", "-crf", "23", "-an", output],
        tag="拼接"
    )
    return ok


# ─────────────── 说话人头像预处理 ───────────────

def prepare_avatar(src_path: str, safe_dir: str, name: str,
                   target_h: int, total_duration: float) -> Optional[str]:
    """
    把头像 GIF/视频预处理为可叠加的视频文件。

    GIF 透明背景方案：
      1. GIF -> 单次 mov（qtrle 编码，支持 rgba alpha 通道）
      2. 用 concat list 把 mov 循环拼接到目标时长，输出 mov
      叠加时 overlay=format=auto 识别 alpha，背景完全透明。

    普通视频：stream_loop 循环，输出 mp4。
    """
    if not src_path or not os.path.exists(src_path):
        print("  [头像] 未找到 {}，跳过头像叠加".format(src_path))
        return None

    safe_src = copy_safe(src_path, safe_dir, name + "_orig")
    ext = os.path.splitext(src_path)[1].lower()
    out_path = os.path.join(safe_dir, name + "_loop.mp4")

    if ext == ".gif":
        # ── 步骤1：GIF -> 单次 mov（qtrle 保留 alpha）──
        single_mov = os.path.join(safe_dir, name + "_single.mov")
        ok = run_ffmpeg([
            "ffmpeg", "-y",
            "-ignore_loop", "0",
            "-i", safe_src,
            "-t", "60",  # 最多取 60s，覆盖一次完整循环
            "-vf", "fps=25,scale=-2:{}".format(target_h),
            "-an",
            "-c:v", "qtrle",  # QuickTime RLE，原生支持 rgba alpha
            "-pix_fmt", "argb",  # 带 alpha 的像素格式
            single_mov
        ], tag="GIF->MOV(alpha)-" + name)
        if not ok:
            return None

        # ── 步骤2：获取单次时长 ──
        single_dur = get_audio_duration(single_mov)
        if single_dur <= 0:
            single_dur = 2.0

        # ── 步骤3：concat 循环拼接到目标时长 ──
        out_path = os.path.join(safe_dir, name + "_loop.mov")
        loop_cnt = int(total_duration / single_dur) + 2
        list_file = os.path.join(safe_dir, name + "_list.txt")
        mov_fwd = single_mov.replace("\\", "/")
        with open(list_file, "w", encoding="utf-8") as f:
            for _ in range(loop_cnt):
                f.write("file '{}'\n".format(mov_fwd))
        ok = run_ffmpeg([
            "ffmpeg", "-y",
            "-f", "concat", "-safe", "0", "-i", list_file,
            "-t", "{:.3f}".format(total_duration),
            "-c:v", "qtrle",  # 保持 alpha，不重新编码为 h264
            "-pix_fmt", "argb",
            out_path
        ], tag="MOV循环-" + name)



    else:
        # 普通视频：stream_loop 循环，输出 mp4
        ok = run_ffmpeg([
            "ffmpeg", "-y",
            "-stream_loop", "-1",
            "-i", safe_src,
            "-t", "{:.3f}".format(total_duration),
            "-vf", "scale=-2:{}".format(target_h),
            "-an",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            out_path
        ], tag="视频头像预处理-" + name)

    return out_path if ok else None





# ─────────────── 文件扫描 ───────────────

def find_files(directory: str, output_name: str):
    """扫描目录，返回 (mp4_list, txt_list, mode)"""
    out_base  = os.path.basename(output_name)
    # 排除输出文件、头像文件
    excluded  = {out_base, AVATAR_MALE_FILE, AVATAR_FEMALE_FILE}
    mp4_files = sorted([
        p for p in glob.glob(os.path.join(directory, "*.mp4"))
        if os.path.basename(p) not in excluded
    ])
    txt_files = sorted(glob.glob(os.path.join(directory, "*.txt")))

    if not mp4_files:
        return [], [], None

    # 多文件配对模式（1.mp4 + 1.txt）
    numbered_mp4, numbered_txt = {}, {}
    for p in mp4_files:
        m = re.match(r'^(\d+)\.mp4$', os.path.basename(p))
        if m:
            numbered_mp4[int(m.group(1))] = p
    for p in txt_files:
        m = re.match(r'^(\d+)\.txt$', os.path.basename(p))
        if m:
            numbered_txt[int(m.group(1))] = p
    common = sorted(set(numbered_mp4) & set(numbered_txt))
    if common:
        return ([numbered_mp4[k] for k in common],
                [numbered_txt[k] for k in common], "multi")

    # 单 txt 模式
    if len(txt_files) == 1:
        return mp4_files, txt_files, "single_txt"
    if txt_files:
        return mp4_files, [txt_files[0]], "single_txt"
    return mp4_files, [], None


# ─────────────── 主流程 ───────────────

def main():
    parser = argparse.ArgumentParser(description="视频字幕+配音+头像合成工具 v3")
    parser.add_argument("--dir",    default=".", help="项目目录（默认当前目录）")
    parser.add_argument("--txt",    default=None, help="指定 txt 文案文件")
    parser.add_argument("--output", default="output_final.mp4", help="输出文件名")
    args = parser.parse_args()

    work_dir = os.path.abspath(args.dir)
    output   = os.path.join(work_dir, args.output)

    print("=" * 60)
    print("  视频合成工具 v3（字幕 + 配音 + 说话人头像）")
    print("=" * 60)

    # ── 确定文件列表 ──
    if args.txt:
        txt_files = [os.path.abspath(args.txt)]
        excluded  = {os.path.basename(args.output), AVATAR_MALE_FILE, AVATAR_FEMALE_FILE}
        mp4_files = sorted([
            p for p in glob.glob(os.path.join(work_dir, "*.mp4"))
            if os.path.basename(p) not in excluded
        ])
        if not mp4_files:
            print("[错误] 未找到 .mp4 文件")
            sys.exit(1)
        mode = "single_txt"
    else:
        mp4_files, txt_files, mode = find_files(work_dir, output_name=args.output)
        if not mp4_files:
            print("[错误] 未找到主视频文件（不含 man.mp4 / girl.mp4）")
            sys.exit(1)
        if not txt_files:
            print("[错误] 未找到 .txt 文案文件")
            sys.exit(1)

    # ── 头像文件路径 ──
    avatar_male_src   = os.path.join(work_dir, AVATAR_MALE_FILE)
    avatar_female_src = os.path.join(work_dir, AVATAR_FEMALE_FILE)
    has_male_avatar   = os.path.exists(avatar_male_src)
    has_female_avatar = os.path.exists(avatar_female_src)

    print("\n模式: {}".format("多文件配对" if mode == "multi" else "单txt多视频"))
    print("主视频: {}".format([os.path.basename(p) for p in mp4_files]))
    print("文案  : {}".format([os.path.basename(p) for p in txt_files]))
    print("男声头像: {}".format(AVATAR_MALE_FILE if has_male_avatar else "未找到，跳过"))
    print("女声头像: {}".format(AVATAR_FEMALE_FILE if has_female_avatar else "未找到，跳过"))

    safe_dir = get_safe_tmpdir()
    print("\n[临时目录] {}".format(safe_dir))

    try:
        # ── 阶段1: 拼接主视频 ──
        print("\n[1/5] 拼接主视频片段...")
        merged = os.path.join(safe_dir, "merged.mp4")
        if not concat_videos(mp4_files, merged, safe_dir):
            print("[错误] 视频拼接失败")
            sys.exit(1)
        vinfo = get_video_info(merged)
        print("  -> {}x{}, {:.1f}s".format(vinfo["width"], vinfo["height"], vinfo["duration"]))

        # ── 阶段2: 解析文案 ──
        print("\n[2/5] 解析文案...")
        all_blocks = []
        if mode == "multi":
            offset = 0.0
            for i, (vp, tp) in enumerate(zip(mp4_files, txt_files)):
                vi     = get_video_info(vp)
                blocks = parse_txt(tp)
                for b in blocks:
                    b["start"] += offset
                    b["end"]   += offset
                all_blocks.extend(blocks)
                print("  [{}] {}: {}段, 偏移{:.1f}s".format(
                    i + 1, os.path.basename(tp), len(blocks), offset))
                offset += vi["duration"]
        else:
            all_blocks = parse_txt(txt_files[0])
            print("  解析出 {} 个片段".format(len(all_blocks)))
        if not all_blocks:
            print("[错误] 文案解析失败，请检查格式")
            sys.exit(1)

        plan = plan_segments(all_blocks,False)
        print("  共 {} 段台词，男声 {} 段，女声 {} 段".format(
            len(plan),
            sum(1 for s in plan if s["is_male"]),
            sum(1 for s in plan if not s["is_male"])))

        # ── 阶段3: 生成 TTS ──
        print("\n[3/5] 生成配音...")
        plan = generate_all_tts(plan, safe_dir)

        # ── 阶段4: 预处理头像视频 ──
        print("\n[4/5] 预处理说话人头像视频...")
        total_dur = max(vinfo["duration"],
                        max((s["end"] for s in plan), default=0.0))

        male_segs   = [s for s in plan if s["is_male"]]
        female_segs = [s for s in plan if not s["is_male"]]

        avatar_male_path   = None
        avatar_female_path = None

        target_h=int(vinfo["height"]*AVATAR_H_RATIO)//2*2
        if has_male_avatar and male_segs:
            avatar_male_path = prepare_avatar(
                avatar_male_src, safe_dir, "avatar_man", target_h, total_dur)
        if has_female_avatar and female_segs:
            avatar_female_path = prepare_avatar(
                avatar_female_src, safe_dir, "avatar_girl", target_h, total_dur)

        if avatar_male_path:
            print("  男声头像准备完成")
        if avatar_female_path:
            print("  女声头像准备完成")

        # ── 阶段5: 最终合成 ──
        print("\n[5/5] 合成字幕 + 配音 + 头像...")
        safe_out = os.path.join(safe_dir, "output_final.mp4")
        ok = compose_video(merged, plan, safe_out, vinfo,
                           avatar_male_path, avatar_female_path)
        if ok:
            shutil.move(safe_out, output)
            size_mb = os.path.getsize(output) / 1024 / 1024
            print("\n完成！输出文件: {} ({:.1f} MB)".format(output, size_mb))
        else:
            print("\n合成失败，请检查上方错误信息")
            sys.exit(1)

    finally:
        try:
            shutil.rmtree(safe_dir, ignore_errors=True)
        except Exception:
            pass


def subtitle_for_fhfp(merged,vinfo:Dict,scene_anno,output):
    print("=" * 60)
    print("  视频合成工具 v3（TTS配音 + 说话人头像）")
    print("=" * 60)
    plan = parse_str(scene_anno)
    print(plan)
    plan = plan_segments(plan,True)
    print(plan)

    assert ({"width", "height", "duration"}.issubset(vinfo.keys()))
    ok=compose_video_v2(merged,plan,output,vinfo,None,None)
    if ok:
        size_mb = os.path.getsize(output) / 1024 / 1024
        print("\n完成！输出文件: {} ({:.1f} MB)".format(output, size_mb))
    else:
        print("\n合成失败，请检查上方错误信息")
        # sys.exit(1)



# vinfo包括视频分辨率、时长信息
# async用于异步收集edge_tts()返回音频（免费接口）
async def avatar_for_fhfp(merged,vinfo:Dict,scene_anno:str,output,tts_voice:str,digital_human:str):
    print("=" * 60)
    print("  视频合成工具 v3（TTS配音 + 说话人头像）")
    print("=" * 60)
    assert({"width","height","duration"}.issubset(vinfo.keys()))
    assert(tts_voice in ["Yunxi","Xiaoying"])
    assert(digital_human in ["man","woman"])
    plan=parse_str(scene_anno)
    male=(tts_voice=="Yunxi")
    plan = plan_segments(plan,male)


    dir=str(Path(__file__).resolve().parent)

    # ── 阶段3: 生成 TTS ──
    print("\n[3/5] 生成配音...")
    plan = await generate_all_tts(plan, dir)

    print("\n[临时目录] {}".format(dir))
    # ── 阶段4: 预处理头像视频 ──
    print("\n[4/5] 预处理说话人头像视频...")
    total_dur = max(vinfo["duration"],
                    max((s["end"] for s in plan), default=0.0))

    target_h = int(vinfo["height"] * AVATAR_H_RATIO)//2*2
    if digital_human=="man":
        # ── 头像文件路径 ──
        avatar_male_src = os.path.join(os.path.dirname(Path(__file__)), AVATAR_MALE_FILE)
        avatar_male_path = prepare_avatar(
            avatar_male_src, dir, "avatar_man", target_h, total_dur)
        print("  男声头像准备完成")
        # ── 阶段5: 最终合成 ──
        print("\n[5/5] 合成字幕 + 配音 + 头像...")
        ok = compose_video_v2(merged, plan, output, vinfo,
                              avatar_male_path, None)
        if ok:
            size_mb = os.path.getsize(output) / 1024 / 1024
            print("\n完成！输出文件: {} ({:.1f} MB)".format(output, size_mb))
        else:
            print("\n合成失败，请检查上方错误信息")
            # sys.exit(1)
    else:
        avatar_female_src = os.path.join(os.path.dirname(Path(__file__)), AVATAR_FEMALE_FILE)
        avatar_female_path = prepare_avatar(
            avatar_female_src, dir, "avatar_girl", target_h, total_dur)
        print("  女声头像准备完成")
        # ── 阶段5: 最终合成 ──
        print("\n[5/5] 合成字幕 + 配音 + 头像...")
        ok = compose_video_v2(merged, plan, output, vinfo,
                              None,avatar_female_path)
        if ok:
            size_mb = os.path.getsize(output) / 1024 / 1024
            print("\n完成！输出文件: {} ({:.1f} MB)".format(output, size_mb))
        else:
            print("\n合成失败，请检查上方错误信息")
            # sys.exit(1)





if __name__ == "__main__":
    # main()
    from pathlib import Path
    # vinfo={"width":1920,"height":1080,"duration":20.2}
    dir=Path(__file__).resolve().parent
    scene_anno=""
    with open(os.path.join(str(dir),"test.txt"),encoding="utf-8") as f:
        scene_anno=f.read().strip()
    print(f'scene_anno:{scene_anno}')
    # subtitle_for_fhfp("preview.mp4",vinfo,scene_anno,"preview_with_subtitle.mp4")
    vinfo=get_video_info("preview.mp4")
    avatar_for_fhfp("preview_landscape.mp4",vinfo,scene_anno,"previewl_with_avatar.mp4","Yunxi","man")



