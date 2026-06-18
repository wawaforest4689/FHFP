#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backend/main.py
===============
FastAPI 后端主程序

直接复用 finetune.py 中的 Video_Assistant_System：
  - 模型加载/切换：_load_abstract_agent() / _load_scene_agent()
  - 显存管理：_release_gpu_memory()
  - 推理：generate_response()

注意：
  - 这里不需要 ModelManager，直接用 Video_Assistant_System 自带的管理能力
  - 但需要加 asyncio.Lock 防止并发请求导致同时加载两个模型（显存溢出）
"""

import uvicorn
import asyncio
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, File, Form, UploadFile, Body, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.schema import *
from common.datamodel import *
from frontend.components.filestorage import FileStorage
from frontend.video_composer import get_video_info, subtitle_for_fhfp, avatar_for_fhfp
# 复用你的模型代码
from finetune import Video_Assistant_System, idea_sys, abstract_sys, scene_sys

logger = logging.getLogger("FHFP-Backend")


# ==============================================================================
# 业务服务（直接调用 finetune.Video_Assistant_System）
# ==============================================================================

class BackendService:
    """
    后端业务服务

    直接复用 Video_Assistant_System：
      - 初始化时加载 abstract_agent（Agent1）
      - Agent2 请求时切换 scene_agent（自动释放显存）
      - 用 asyncio.Lock 防止并发切换导致显存溢出
    """

    def __init__(self):
        self._users: Dict[str, Dict] = {}
        self._username_index: Dict[str, str] = {}
        self._phone_index: Dict[str, str] = {}
        self._projects: Dict[str, Dict] = {}
        self._storage = FileStorage()
        self._segments: Dict[str, Dict[str, VideoSegment]] = {}
        self._works: Dict[str, List[Dict[str, Any]]] = {}

        # 初始化模型（启动时加载 Agent1）
        # self.vas = Video_Assistant_System(lamb=2e-1)
        self.vas=0
        # self.vas._load_abstract_agent()
        # self.vas.abstract_model.eval()
        self._current_agent = "abstract"  # 当前加载的模型

        # 防止并发切换模型
        self._model_lock = asyncio.Lock()

    async def _ensure_agent(self, agent_name: str):
        """确保指定模型已加载（线程安全）"""
        if self._current_agent == agent_name:
            return

        async with self._model_lock:
            if self._current_agent == agent_name:
                return

            # 同步切换模型（在独立线程中执行，不阻塞事件循环）
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._sync_switch, agent_name)
            self._current_agent = agent_name

    def _sync_switch(self, agent_name: str):
        """同步执行模型切换（Video_Assistant_System 自带显存管理）"""
        if agent_name == "abstract":
            # self.vas._load_abstract_agent()
            # self.vas.abstract_model.eval()
            self.vas=0
        elif agent_name == "scene":
            # self.vas._load_scene_agent()
            # self.vas.scene_model.eval()
            self.vas=1

    # ------------------------------------------------------------------
    # 用户认证
    # ------------------------------------------------------------------

    async def register(self, username: str, password: str, phone: Optional[str] = None, nickname: Optional[str] = None) -> \
    Dict[str, Any]:
        if username in self._username_index:
            return {"success": False, "message": "账号已存在"}
        if phone and phone in self._phone_index:
            return {"success": False, "message": "手机号已绑定"}

        user_id = f"user_{uuid.uuid4().hex[:12]}"
        self._users[user_id] = {
            "user_id": user_id, "username": username, "phone": phone,
            "nickname": nickname or username,
            "avatar_url": f"https://api.dicebear.com/7.x/avataaars/svg?seed={username}"
        }
        self._username_index[username] = user_id
        if phone:
            self._phone_index[phone] = user_id

        return {"success": True, "user_id": user_id, "message": "注册成功", "nickname": nickname or username}

    async def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        user_id = self._phone_index.get(username) or self._username_index.get(username)
        if not user_id or user_id not in self._users:
            return {"success": False, "message": "账号或密码错误"}

        user = self._users[user_id]
        return {
            "success": True, "user_id": user_id,
            "token": f"jwt_{uuid.uuid4().hex}",
            "nickname": user["nickname"],
            "avatar_url": user["avatar_url"],
            "message": "登录成功"
        }

    async def create_project(self, user_id: str, mode: int, title: str, description: str,
                       province: Optional[str] = None) -> Dict[str, Any]:
        """
        创建新项目

        Args:
            user_id: 用户ID
            mode: 创作模式 (0=产品介绍, 1=剧情设计)
            title: 项目标题
            description: 项目描述
            province: 省份/地区（可选）

        Returns:
            Dict: {success, project_id, created_at, status}
        """
        pid = f"proj_{uuid.uuid4().hex[:8]}"
        self._projects[pid] = {
            "project_id": pid,
            "user_id": user_id,
            "mode": mode,
            "title": title,
            "description": description,
            "province": province,
            "created_at": datetime.now().isoformat(),
            "status": "draft"
        }
        self._segments[pid]={}
        return {
            "success": True,
            "project_id": pid,
            "created_at": self._projects[pid]["created_at"],
            "status": "draft"
        }

    # ------------------------------------------------------------------
    # Agent1: 多轮对话（直接调用 vas.generate_response）
    # ------------------------------------------------------------------

    async def agent1_chat(self, req: Agent1ChatRequest) -> Dict[str, Any]:
        await self._ensure_agent("abstract")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_agent1_chat, req)

    def _sync_agent1_chat(self, req: Agent1ChatRequest) -> Dict[str, Any]:
        """同步执行 Agent1 推理（在线程中运行）"""
        # self.vas.mode_id=req.mode_id
        # 有问题，会导致历史丢失
        # self.vas.idea_history=[{"role":"system","content":idea_sys}]
        # 非阻塞式亮点抽取环节
        # if len(self.vas.idea_history) == 1:
        #     req.message = f"我选择模式{self.vas.mode_id}" + req.message
        # self.vas.idea_history.append({"role": "user", "content": req.message})
        # response = self.vas.generate_response(req.message, max_new_tokens=768, choice="abstract",
        #                                       history=self.vas.idea_history)
        response="This is a simple test to ensure functionality."
        print(f"Agent: {response}")

        # self.vas.idea_history.append({"role": "assistant", "content": response})

        # history_len=sum([len(item["content"]) for item in self.vas.idea_history[1:]])
        print(f'History conversation length:100.')


        return  {
            "success": True,
            "reply": "This is the last turn reponse from VAS.",
            "summary_draft": "",
            "suggested_questions": ["请详细描述种植过程", "产品与竞品的主要区别是什么？"],
            "turn_id": len(req.history) + 1
        }

    # ------------------------------------------------------------------
    # Agent1: 生成摘要
    # ------------------------------------------------------------------

    async def agent1_summary(self, req: Agent1SummaryRequest) -> Dict[str, Any]:
        await self._ensure_agent("abstract")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_agent1_summary, req)

    def _sync_agent1_summary(self, req: Agent1SummaryRequest) -> Dict[str, Any]:
        # self.vas.mode_id = req.mode_id
        # summary = self.vas.abstract_chat(req.highlights)
        summary="This is a fake summary to verify the procedure."

        return {
            "success": True,
            "summary": summary,
            "keywords": ["农产品", "新鲜", "有机"],
            "emotion_tags": ["朴实", "真诚"],
            "target_audience": "一二线城市注重健康的年轻消费者"
        }

    # ------------------------------------------------------------------
    # Agent2: 生成拍摄建议（JSON）
    # ------------------------------------------------------------------

    async def agent2_generate(self, req: Agent2ShotsRequest) -> Dict[str, Any]:
        await self._ensure_agent("scene")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_agent2_generate, req)

    def _sync_agent2_generate(self, req: Agent2ShotsRequest) -> Dict[str, Any]:
        # self.vas.mode_id = req.mode_id
        # raw_output = self.vas.scene_chat(req.summary)

        # shots = self._parse_scene_json(raw_output)
        shots = [
            {"scene": "全景：果园/农田清晨全景，阳光洒落", "audio": "自然环境音：鸟鸣、微风",
             "text": json.dumps([{"人物":"男性","台词":"清晨的第一缕阳光，照亮了我们的果园。"}],ensure_ascii=False)},
            {"scene": "特写：农产品表面纹理，露水欲滴", "audio": "轻快吉他背景音乐起",
             "text": json.dumps([{"人物":"男性","台词":"每一颗果实，都饱含大自然的馈赠。"}],ensure_ascii=False)},
            {"scene": "中景：农户采摘/包装过程，动作熟练", "audio": "包装纸摩擦声+轻快节奏",
             "text": json.dumps([{"人物":"男性","台词":"从田间到餐桌，我们只追求最新鲜。"}],ensure_ascii=False)},
            {"scene": "近景：双手捧起产品展示，微笑", "audio": "音乐渐强，环境音淡出",
             "text": json.dumps([{"人物":"男性","台词":"选择我们，就是选择健康与安心。"}],ensure_ascii=False)},
        ]
        return {
            "success": True,
            "shots": shots,
            "total_duration": sum(s.get("duration_hint", 5) for s in shots),
            "storyline_arc": "起承转合：环境铺垫 -> 产品展示 -> 过程信任 -> 情感号召"
        }

    def _parse_scene_json(self, raw: str) -> List[Dict[str, Any]]:
        """鲁棒解析 Agent2 输出的 JSON"""
        text = raw.strip()
        if text.startswith("```"):
            parts = text.split("```")
            if len(parts) >= 3:
                text = parts[1]
                if text.startswith("json"):
                    text = text[4:].strip()

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
            data = json.loads(match.group(1)) if match else None

        if not data:
            return [{
                "shot_id": f"shot_fallback_{uuid.uuid4().hex[:4]}",
                "order": 0, "scene": "（解析失败）请检查模型输出格式",
                "audio": "无音频", "text": raw[:200],
                "duration_hint": 5, "camera_movement": ""
            }]

        items = data if isinstance(data, list) else data.get("shots", [data])

        shots = []
        for i, item in enumerate(items):
            assert({"画面","音频","文案"}.issubset(item.keys()))
            shots.append({
                "shot_id": f"shot_{i}_{uuid.uuid4().hex[:4]}",
                "order": i,
                "scene": item.get("画面", ""),
                "audio": item.get("音频", ""),
                "text": item.get("文案",[]),
                "duration_hint": item.get("时长", 5)
            })
        return shots

    # ------------------------------------------------------------------
    # 【新增】BGM 上传
    # ------------------------------------------------------------------
    async def upload_bgm(self, project_id: str, file_data: bytes, file_name: str) -> Dict[str, Any]:
        """
        【新增】保存 BGM 文件到存储，返回可访问的 URL。
        """
        try:
            bgm_url = self._storage.save_bgm(project_id, file_data, file_name)
            return {
                "success": True,
                "bgm_url": bgm_url,
                "message": "BGM 上传成功"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"BGM 上传失败: {str(e)}"
            }

    # ------------------------------------------------------------------
    # 【核心功能2】粗剪合成：按编号顺序真实拼接 + 生成在线预览URL
    # ------------------------------------------------------------------

    def _build_scene_annotation(self, project_id: str) -> str:
        """
        【新增】从项目片段构建场景标注文本（TXT格式）。
        格式：[{"figure": "主讲人", "script": "台词"}](0-5)
        """
        segments = self._segments.get(project_id, {})
        if not segments:
            return ""

        lines = []
        current_time = 0
        sorted_segs = sorted(segments.values(), key=lambda x: x.order)

        for seg_meta in sorted_segs:
            assert (isinstance(seg_meta, VideoSegment))
            script = seg_meta.text
            duration = seg_meta.video_duration
            start = current_time
            end = current_time + duration
            lines.append(f"{script}({int(start)}-{int(end)})")
            print(lines[-1])
            current_time = end

        return "\n".join(lines)

    def _gene_subtitle(self, merged_video: str, vinfo: Dict, scene_anno: str, output: str) -> bool:
        """
        【新增】仅字幕烧录工具函数。
        """
        try:
            subtitle_for_fhfp(merged_video, vinfo, scene_anno, output)
            return True
        except Exception as e:
            print(f"[ERROR] 字幕烧录失败: {e}")
            import shutil
            try:
                shutil.copy2(merged_video, output)
                return True
            except:
                return False

    async def _tts_and_avatar(self, merged_video: str, vinfo: Dict, scene_anno: str,
                        output: str, tts_voice: str, digital_human: str) -> bool:
        """
        【新增】TTS配音 + 数字人头像 + 字幕烧录工具函数。
        """
        try:
            await avatar_for_fhfp(merged_video, vinfo, scene_anno, output, tts_voice, digital_human)
            return True
        except Exception as e:
            print(f"[ERROR] TTS+数字人合成失败: {e}")
            import shutil
            try:
                shutil.copy2(merged_video, output)
                return True
            except:
                return False

    async def rough_cut(self, req: RoughCutRequest) -> Dict[str, Any]:
        """
        【修复】接收 RoughCutRequest Pydantic 对象，与 api_rough_cut 对齐。
        使用 ffmpeg 拼接视频片段，支持字幕烧录和TTS+数字人（仅产品介绍模式）。
        """
        import subprocess
        import time
        import math

        # 【修复】从 Pydantic 对象提取参数
        project_id = req.project_id
        segment_sequence = req.segment_sequence
        bgm_url = req.bgm_url
        bgm_volume = req.bgm_volume
        subtitle_enabled = req.subtitle_enabled
        tts_voice = req.tts_voice
        digital_human = req.digital_human
        mode = req.mode if hasattr(req, 'mode') else 0

        video_files = []
        for seg_id in segment_sequence:
            seg_meta = self._segments.get(project_id, {}).get(seg_id)

            if not seg_meta or not seg_meta.video_url:
                continue
            assert (isinstance(seg_meta, VideoSegment))
            abs_path = self._storage.get_absolute_path(seg_meta.video_url)
            if abs_path.exists():
                video_files.append(str(abs_path))

        if not video_files:
            return {"success": False, "message": "没有有效视频片段，请先上传素材"}

        output_path = self._storage.get_output_path(project_id, "preview.mp4")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.exists():
            output_path.unlink()
            time.sleep(0.2)

        concat_list_path = output_path.parent / "concat_list.txt"
        with open(concat_list_path, 'w', encoding='utf-8') as f:
            for vf in video_files:
                vf_clean = vf.replace('\\', '/')
                f.write(f"file '{vf_clean}'\n")

        try:
            has_bgm = bgm_url and self._storage.get_absolute_path(bgm_url).exists()

            # 第一步：统一转码所有片段
            temp_video = output_path.parent / "temp_output.mp4"
            uniform_files = []
            temp_dir = output_path.parent / "temp_concat"
            temp_dir.mkdir(exist_ok=True)

            for i, p in enumerate(video_files):
                uniform = str(temp_dir / f"uniform_{i:03d}.mp4").replace('\\', '/')
                p_clean = str(p).replace('\\', '/')

                # 探测原始分辨率（用于日志）
                vinfo_raw = get_video_info(p_clean)
                orig_w = vinfo_raw.get("width", 0)
                orig_h = vinfo_raw.get("height", 0)
                print(f"[转码] 片段{i}: 原始分辨率 {orig_w}x{orig_h}")

                cmd = [
                    "ffmpeg", "-y", "-i", p_clean,
                    # 关键修改：不强制 1920x1080，只统一 fps 和像素格式
                    "-vf", "fps=25,format=yuv420p",
                    "-af", "aresample=48000:async=1,aformat=sample_fmts=fltp:channel_layouts=stereo",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    uniform
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode != 0:
                    print(f"[ERROR] 转码片段{i}失败: {result.stderr[:500]}")
                    return {"success": False, "message": f"转码片段{i}失败"}

                uniform_files.append(uniform)

            # 第二步：使用 concat 协议拼接
            list_file = str(temp_dir / "concat_list.txt").replace('\\', '/')
            with open(list_file, "w", encoding="utf-8") as f:
                for uf in uniform_files:
                    f.write(f"file '{uf}'\n")

            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-movflags", "+faststart",
                temp_video
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

            # 清理临时文件
            for uf in uniform_files:
                try:
                    os.remove(uf)
                except:
                    pass
            try:
                os.remove(list_file)
                os.rmdir(temp_dir)
            except:
                pass

            if result.returncode != 0:
                return {"success": False, "message": f"视频拼接失败: {result.stderr[:500]}"}
            if not temp_video.exists() or temp_video.stat().st_size < 1000:
                return {"success": False, "message": "视频拼接输出无效"}

            # 第二步：字幕/TTS/数字人处理
            vinfo = get_video_info(str(temp_video))
            temp_processed = output_path.parent / "temp_processed.mp4"
            scene_anno = self._build_scene_annotation(project_id)

            need_tts_avatar = (mode == 0 and tts_voice is not None and digital_human is not None)
            print(tts_voice, digital_human)
            if need_tts_avatar:
                print(f"[INFO] 产品介绍模式，启用TTS({tts_voice}) + 数字人({digital_human})")
                ok = await self._tts_and_avatar(
                    merged_video=str(temp_video), vinfo=vinfo, scene_anno=scene_anno,
                    output=str(temp_processed), tts_voice=tts_voice, digital_human=digital_human
                )
                if ok and temp_processed.exists():
                    temp_video = temp_processed
                else:
                    print("[WARN] TTS+数字人处理失败，回退到基础版本")
            elif subtitle_enabled and scene_anno:
                print("[INFO] 启用字幕烧录")
                ok = self._gene_subtitle(
                    merged_video=str(temp_video), vinfo=vinfo, scene_anno=scene_anno, output=str(temp_processed)
                )
                if ok and temp_processed.exists():
                    temp_video = temp_processed
                else:
                    print("[WARN] 字幕烧录失败，回退到无字幕版本")
            else:
                print("[INFO] 无字幕/TTS/数字人处理")

            # 第三步：BGM混合
            if has_bgm:
                bgm_path = str(self._storage.get_absolute_path(bgm_url))
                bgm_path_clean = bgm_path.replace('\\', '/')
                video_duration = vinfo["duration"] if vinfo["duration"] > 0 else len(video_files) * 5.0

                if bgm_volume <= 0:
                    volume_db = -100
                else:
                    volume_db = 20 * math.log10(bgm_volume)

                temp_bgm = output_path.parent / "temp_bgm.aac"
                cmd_bgm = [
                    'ffmpeg', '-y', '-i', bgm_path_clean,
                    '-af', f'loudnorm=I=-23:TP=-2:LRA=11,volume={volume_db}dB',
                    '-t', str(video_duration), '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
                    str(temp_bgm).replace('\\', '/')
                ]
                result_bgm = subprocess.run(cmd_bgm, capture_output=True, text=True, timeout=120)

                if result_bgm.returncode != 0:
                    has_bgm = False
                else:
                    temp_video_audio = output_path.parent / "temp_video_audio.aac"
                    subprocess.run([
                        'ffmpeg', '-y', '-i', str(temp_video).replace('\\', '/'),
                        '-vn', '-c:a', 'copy', str(temp_video_audio).replace('\\', '/')
                    ], capture_output=True, timeout=60)

                    temp_video_audio_fmt = output_path.parent / "temp_video_audio_fmt.aac"
                    temp_bgm_fmt = output_path.parent / "temp_bgm_fmt.aac"

                    for src, dst in [(temp_video_audio, temp_video_audio_fmt), (temp_bgm, temp_bgm_fmt)]:
                        subprocess.run([
                            'ffmpeg', '-y', '-i', str(src).replace('\\', '/'),
                            '-ar', '48000', '-ac', '2', '-c:a', 'aac', '-b:a', '192k',
                            str(dst).replace('\\', '/')
                        ], capture_output=True, timeout=60)

                    temp_mixed_audio = output_path.parent / "temp_mixed_audio.aac"
                    cmd_mix = [
                        'ffmpeg', '-y',
                        '-i', str(temp_video_audio_fmt).replace('\\', '/'),
                        '-i', str(temp_bgm_fmt).replace('\\', '/'),
                        '-filter_complex', '[0:a][1:a]amix=inputs=2:duration=first[aout]',
                        '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
                        str(temp_mixed_audio).replace('\\', '/')
                    ]
                    result_mix = subprocess.run(cmd_mix, capture_output=True, text=True, timeout=120)

                    for f in [temp_video_audio, temp_video_audio_fmt, temp_bgm, temp_bgm_fmt]:
                        f.unlink(missing_ok=True)

                    if result_mix.returncode != 0:
                        has_bgm = False
                    else:
                        cmd_final = [
                            'ffmpeg', '-y',
                            '-i', str(temp_video).replace('\\', '/'),
                            '-i', str(temp_mixed_audio).replace('\\', '/'),
                            '-c:v', 'copy', '-map', '0:v:0', '-map', '1:a:0',
                            '-shortest', '-movflags', '+faststart',
                            str(output_path).replace('\\', '/')
                        ]
                        result = subprocess.run(cmd_final, capture_output=True, text=True, timeout=120)
                        temp_mixed_audio.unlink(missing_ok=True)
                        temp_video.unlink(missing_ok=True)

            if not has_bgm:
                import shutil
                shutil.copy2(temp_video, output_path)
                temp_video.unlink(missing_ok=True)

            if result.returncode != 0:
                return {"success": False, "message": f"合成失败: {result.stderr[:500]}"}
            if not output_path.exists() or output_path.stat().st_size < 1000:
                return {"success": False, "message": "生成的视频文件无效"}

            duration = 0
            try:
                probe = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1',
                     str(output_path).replace('\\', '/')],
                    capture_output=True, text=True, timeout=30
                )
                if probe.returncode == 0:
                    duration = float(probe.stdout.strip())
            except:
                duration = len(video_files) * 5.0

            preview_url = f"/storage/projects/{project_id}/output/preview.mp4"
            return {
                "success": True,
                "preview_url": preview_url,
                "duration": duration,
                "status": "preview_ready",
                "message": f"粗剪完成，时长: {duration:.1f}秒",
                "features_applied": {
                    "subtitle": subtitle_enabled,
                    "tts": need_tts_avatar,
                    "digital_human": need_tts_avatar,
                    "bgm": has_bgm
                }
            }

        except Exception as e:
            return {"success": False, "message": f"合成异常: {str(e)}"}


    # ------------------------------------------------------------------
    # 【核心功能3】发布作品：入库 + 提供下载URL（服务器文件→客户端本地）
    # ------------------------------------------------------------------
    async def publish_video(self, project_id: str, title: str, description: str,
                            tags: List[str], cover_frame: Optional[int] = 0) -> Dict[str, Any]:
        """
        发布流程：
        1. 将 preview.mp4 复制为 final_{hash}.mp4（持久化最终版本）
        2. 写入个人作品库（内存索引）
        3. 返回：
           - public_url:  在线播放地址（浏览器直接播放）
           - download_url: 下载地址（浏览器通过 <a download> 或 ui.download 保存到客户端本地目录）
        """
        try:
            preview_path = self._storage.get_output_path(project_id, "preview.mp4")
            if not preview_path.exists():
                return {
                    "success": False,
                    "message": "预览文件不存在，请先生成粗剪预览"
                }

            # 复制为最终版本（防止后续编辑覆盖 preview 影响已发布作品）
            final_name = f"final_{uuid.uuid4().hex[:8]}.mp4"
            final_path = self._storage.get_output_path(project_id, final_name)
            shutil.copy2(preview_path, final_path)

            # 统一资源路径（StaticFiles 挂载后可访问）
            final_url = f"/storage/projects/{project_id}/output/{final_name}"
            work_id = f"work_{uuid.uuid4().hex[:8]}"

            # 获取项目关联用户
            project = self._projects.get(project_id, {})
            user_id = project.get("user_id", "anonymous")

            # 记录作品元数据
            work_data = {
                "work_id": work_id,
                "project_id": project_id,
                "user_id": user_id,
                "title": title,
                "description": description,
                "tags": tags or [],
                "video_url": final_url,  # 在线播放
                "download_url": final_url,  # 触发浏览器下载到客户端本地
                "thumbnail": "",
                "created_at": datetime.now().isoformat(),
                "views": 0,
                "likes": 0,
                "duration": 0
            }

            if user_id not in self._works:
                self._works[user_id] = []
            self._works[user_id].insert(0, work_data)

            return {
                "success": True,
                "work_id": work_id,
                "public_url": final_url,  # 在线播放地址
                "download_url": final_url,  # 下载地址（服务器文件 → 客户端本地）
                "created_at": work_data["created_at"],
                "message": "作品已发布到个人库，支持在线播放与下载"
            }

        except Exception as e:
            return {
                "success": False,
                "message": f"发布失败: {str(e)}"
            }

        # ------------------------------------------------------------------
        # TODO:公共数据库查询
        # ------------------------------------------------------------------

    async def get_personal_works(self, user_id: str, keyword: Optional[str] = None,
                                 sort_by: str = "date", page: int = 1,
                                 page_size: int = 12) -> Dict[str, Any]:
        works = self._works.get(user_id, [])

        if keyword:
            kw = keyword.lower()
            works = [
                w for w in works
                if kw in w.get("title", "").lower()
                   or kw in w.get("description", "").lower()
            ]

        if sort_by == "date":
            works = sorted(works, key=lambda x: x.get("created_at", ""), reverse=True)
        elif sort_by == "views":
            works = sorted(works, key=lambda x: x.get("views", 0), reverse=True)

        total = len(works)
        start = (page - 1) * page_size
        paginated = works[start:start + page_size]

        return {
            "success": True,
            "total": total,
            "page": page,
            "page_size": page_size,
            "items": paginated
        }

    async def get_case_library(self, category: Optional[str] = None,
                               keyword: Optional[str] = None,
                               sort_by: str = "views", page: int = 1,
                               page_size: int = 12) -> Dict[str, Any]:
        items = [
            {"case_id": "case_001", "title": "有机苹果采摘展示",
             "description": "展示新鲜苹果从采摘到包装的全过程",
             "category": "水果", "tags": ["水果", "有机", "采摘"],
             "thumbnail": "https://picsum.photos/seed/apple/400/300",
             "views": 2450, "likes": 132, "duration": "1:45"},
            {"case_id": "case_002", "title": "新鲜大葱产地直发",
             "description": "展示大葱从田间到餐桌的新鲜供应链",
             "category": "蔬菜", "tags": ["大葱", "产地直发", "新鲜"],
             "thumbnail": "https://picsum.photos/seed/onion/400/300",
             "views": 3210, "likes": 187, "duration": "2:30"},
            {"case_id": "case_003", "title": "夏日西瓜促销活动",
             "description": "炎热夏季的西瓜促销带货视频",
             "category": "水果", "tags": ["水果", "促销", "夏季"],
             "thumbnail": "https://picsum.photos/seed/watermelon/400/300",
             "views": 4521, "likes": 256, "duration": "0:45"},
        ]
        if category:
            items = [c for c in items if c.get("category") == category]
        if keyword:
            kw = keyword.lower()
            items = [c for c in items if kw in c["title"].lower()
                     or kw in c.get("description", "").lower()]
        items = sorted(items, key=lambda x: x.get(sort_by, 0), reverse=True)
        start = (page - 1) * page_size
        return {
            "success": True,
            "total": len(items),
            "page": page,
            "page_size": page_size,
            "items": items[start:start + page_size]
        }

    async def get_project_segments(self, project_id: str) -> Dict[str, Any]:
        """获取项目的所有片段数据（用于前端恢复状态）"""
        segments = self._segments.get(project_id, {})
        return {
            "success": True,
            "project_id": project_id,
            "segments": segments,
            "count": len(segments)
        }

    # ------------------------------------------------------------------
    # 【新增】调整片段顺序
    # ------------------------------------------------------------------
    async def reorder_segments(self, project_id: str, segment_id_list: List[str]) -> Dict[str, Any]:
        segments = self._segments.get(project_id, {})
        if not segments:
            return {"success": False, "message": "项目没有片段"}

        # 只保留存在的 segment_id
        valid_ids = [sid for sid in segment_id_list if sid in segments]
        # 按新顺序重设 order
        for i, sid in enumerate(valid_ids):
            segments[sid].order = i

        return {
            "success": True,
            "project_id": project_id,
            "new_orders": {sid: i for i, sid in enumerate(valid_ids)}
        }

    # ------------------------------------------------------------------
    # 【新增】添加新片段（尾部）
    # ------------------------------------------------------------------
    async def add_segment(self, project_id: str, after_segment_id: str = "") -> Dict[str, Any]:
        if project_id not in self._segments:
            self._segments[project_id] = {}

        segments = self._segments[project_id]
        new_sid = f"seg_{uuid.uuid4().hex[:8]}"

        # 计算 order
        if not segments:
            new_order = 0
        elif after_segment_id is None or after_segment_id not in segments:
            new_order = max(s.order for s in segments.values()) + 1
        # 这种功能暂不支持，不支持在中间添加片段，目前仅支持尾部添加+上下箭头调整顺序
        # TODO:增加任意位置添加单个空白片段功能
        else:
            new_order = segments[after_segment_id].order + 1
            # 后面的 segment order +1
            for s in segments.values():
                if s.order >= new_order:
                    s.order += 1

        new_seg = VideoSegment(
            segment_id=new_sid,
            project_id=project_id,
            order=new_order,
            scene="（新片段画面）",
            audio="（新片段音频）",
            text="（新片段文案）"
        )
        segments[new_sid] = new_seg

        # 【修复】返回纯字典，确保 JSON 序列化一致
        return {
            "success": True,
            "new_segment": {
                "segment_id": new_seg.segment_id,
                "project_id": new_seg.project_id,
                "order": new_seg.order,
                "scene": new_seg.scene,
                "audio": new_seg.audio,
                "text": new_seg.text,
            }
        }

    # ------------------------------------------------------------------
    # 【新增】删除片段
    # ------------------------------------------------------------------
    async def delete_segment(self, project_id: str, segment_id: str) -> Dict[str, Any]:
        segments = self._segments.get(project_id, {})
        if segment_id not in segments:
            return {"success": False, "message": "片段不存在"}

        deleted_order = segments[segment_id].order
        del segments[segment_id]

        # 后面的 segment order -1
        for s in segments.values():
            if s.order > deleted_order:
                s.order -= 1

        return {"success": True, "deleted_segment_id": segment_id}

    # ------------------------------------------------------------------
    # 【新增】修改片段文本（scene/audio/text）
    # ------------------------------------------------------------------
    async def update_segment(self, project_id: str, segment_id: str,
                             scene: Optional[str] = None,
                             audio: Optional[str] = None,
                             text: Optional[str] = None) -> Dict[str, Any]:
        segments = self._segments.get(project_id, {})
        if segment_id not in segments:
            return {"success": False, "message": "片段不存在"}

        seg = segments[segment_id]
        if scene is not None:
            seg.scene = scene
        if audio is not None:
            seg.audio = audio
        if text is not None:
            seg.text = text

        return {
            "success": True,
            "updated_segment": seg  # VideoSegment 对象
        }

    # ------------------------------------------------------------------
    # 【修改】视频片段上传（覆写上传）
    # ------------------------------------------------------------------
    async def upload_segment_video(self, project_id: str, segment_id: str,
                                   file_data: bytes, file_name: str) -> Dict[str, Any]:
        """
        覆写上传视频片段：
        - 如果 segment_id 不存在，自动创建新片段
        - 如果存在，覆写视频文件和时长
        """
        try:
            # 真实写入磁盘
            video_url = self._storage.save_segment(project_id, segment_id, file_data, file_name)

            # 确保项目 segments 字典存在
            if project_id not in self._segments:
                self._segments[project_id] = {}

            # 获取视频信息
            vinfo = get_video_info(video_url)
            h = vinfo.get("height", 1080)
            w = vinfo.get("width", 1920)
            duration = vinfo.get("duration", 5.0)

            # 如果 segment 已存在，更新视频信息；否则创建新片段
            if segment_id in self._segments[project_id]:
                seg = self._segments[project_id][segment_id]
                seg.video_url = video_url
                seg.video_duration = duration
            else:
                # 自动创建新片段，放在尾部
                existing = self._segments[project_id]
                new_order = max(s.order for s in existing.values()) + 1 if existing else 0
                seg = VideoSegment(
                    segment_id=segment_id,
                    project_id=project_id,
                    order=new_order,
                    video_url=video_url,
                    video_duration=duration,
                    scene="（新片段画面）",
                    audio="（新片段音频）",
                    text="（新片段文案）"
                )
                self._segments[project_id][segment_id] = seg

            return {
                "success": True,
                "video_url": video_url,
                "duration": duration,
                "resolution": f"{w}x{h}",
                "segment_id": segment_id,
                "message": "上传成功"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"文件存储失败: {str(e)}"
            }

# ==============================================================================
# 全局实例 + FastAPI 应用
# ==============================================================================

# 全局业务实例（直接包含 Video_Assistant_System）
backend = BackendService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动时模型已初始化，无需额外操作"""
    logger.info("Backend started")
    yield
    # 清理
    del backend.vas
    import gc
    gc.collect()
    import torch
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("Backend shutdown")


app = FastAPI(title="FHFP Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务（让前端能访问上传的视频）
app.mount("/storage", StaticFiles(directory="../backend/storage"), name="storage")


# ==============================================================================
# 5. API 路由（直接调用 backend 实例）
# ==============================================================================

@app.post("/api/auth/register")
async def api_register(req: RegisterRequest):
    return await backend.register(req.username, req.password, req.phone, req.nickname)


@app.post("/api/auth/login")
async def api_login(req: LoginRequest):
    return await backend.authenticate(req.username, req.password)


@app.post("/api/projects")
async def api_create_project(req: CreateProjectRequest):
    return await backend.create_project(
        req.user_id, req.mode, req.title, req.description, req.province
    )


@app.post("/api/agent1/chat")
async def api_agent1_chat(req: Agent1ChatRequest):
    return await backend.agent1_chat(req)


@app.post("/api/agent1/summary")
async def api_agent1_summary(req: Agent1SummaryRequest):
    return await backend.agent1_summary(req)


@app.post("/api/agent2/shots")
async def api_agent2_shots(req: Agent2ShotsRequest):
    return await backend.agent2_generate(req)


# ==============================================================================
# 【新增】BGM 上传路由
# ==============================================================================
@app.post("/api/upload/bgm")
async def api_upload_bgm(
    project_id: str = Form(...),
    file: UploadFile = File(...)
):
    file_data = await file.read()
    return await backend.upload_bgm(project_id, file_data, file.filename)


@app.post("/api/video/rough-cut")
async def api_rough_cut(req: RoughCutRequest):
    # 【修复】直接传递 Pydantic 对象，backend.rough_cut 接收 RoughCutRequest
    return await backend.rough_cut(req)


@app.post("/api/video/publish")
async def api_publish(req: PublishRequest):
    return await backend.publish_video(req.project_id, req.title, req.description, req.tags, req.cover_frame)


@app.get("/api/cases")
async def api_cases(category: Optional[str] = None, keyword: Optional[str] = None, sort_by: str = "views",
                    page: int = 1, page_size: int = 12):
    return await backend.get_case_library(category, keyword, sort_by, page, page_size)


@app.get("/api/works")
async def api_works(user_id: str, keyword: Optional[str] = None, sort_by: str = "date", page: int = 1,
                    page_size: int = 12):
    return await backend.get_personal_works(user_id, keyword, sort_by, page, page_size)

@app.get("/api/projects/{project_id}/segments")
async def api_get_segments(project_id: str):
    return await backend.get_project_segments(project_id)

@app.post("/api/projects/{project_id}/segments/reorder")
async def api_reorder_segments(project_id: str, body: Dict[str, Any] = Body(...)):
    segment_id_list = body.get("segment_id_list", [])
    return await backend.reorder_segments(project_id, segment_id_list)

@app.post("/api/projects/{project_id}/segments")
async def api_add_segment(project_id: str, body: Dict[str, Any] = Body(...)):
    after_segment_id = body.get("after_segment_id", "")
    after_id = after_segment_id if after_segment_id else None
    return await backend.add_segment(project_id, after_id)

@app.patch("/api/projects/{project_id}/segments/{segment_id}")
async def api_update_segment(project_id: str, segment_id: str, body: Dict[str, Any] = Body(...)):
    return await backend.update_segment(
        project_id, segment_id,
        scene=body.get("scene",""),
        audio=body.get("audio",""),
        text=body.get("text",[])
    )

@app.delete("/api/projects/{project_id}/segments/{segment_id}")
async def api_delete_segment(project_id: str, segment_id: str):
    return await backend.delete_segment(project_id, segment_id)


@app.post("/api/upload/segment")
async def api_upload_segment(
    project_id: str = Form(...),
    segment_id: str = Form(...),
    file: UploadFile = File(...)
):
    file_data = await file.read()
    return await backend.upload_segment_video(project_id, segment_id, file_data, file.filename)

@app.get("/api/storage/check")
async def api_check_file_exists(url_path: str = Query(...)):
    """
    检查服务器上指定 URL 路径的文件是否存在。
    将 URL 路径转为本地绝对路径后检查。
    """
    try:
        abs_path = backend._storage.get_absolute_path(url_path)
        exists = abs_path.exists() and abs_path.stat().st_size > 1000
        return {"success": True, "exists": exists}
    except Exception as e:
        return {"success": False, "message": str(e), "exists": False}


@app.get("/health")
async def health_check():
    return {"status": "ok", "current_agent": backend._current_agent}



if __name__ == "__main__":
    uvicorn.run("main_with_empty_model:app", host="0.0.0.0", port=8000, reload=False, workers=1)

    