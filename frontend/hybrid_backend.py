#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
FHFP Backend Client (Hybrid: FastAPI for AI + Local for everything else)
=============================================================================

架构：
  - Agent1/Agent2 大模型推理 → HTTP 异步访问 FastAPI 后端
  - 上传、片段管理、粗剪、发布、下载 → 前端本地线程池执行

FastAPI 后端只需要保留 /api/agent1/* 和 /api/agent2/* 路由
=============================================================================
"""

import logging
import json
import os
import uuid
import shutil
import subprocess
import time
import math
import hashlib
import asyncio
from datetime import datetime
from nicegui import ui
from typing import Any, Dict, List, Optional
from pathlib import Path

import sys
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx
from common.schema import *
from common.datamodel import *
from components.authdialog import UserAccount, AuthValidator
from components.filestorage import FileStorage
from video_composer import subtitle_for_fhfp, avatar_for_fhfp, get_video_info
from core import logger,AppState,THEME_COLORS,APP_TITLE,MAX_SEGMENTS,DEFAULT_BGM_VOLUME


# Protocol 定义
from typing import Protocol

# ==============================================================================
# 预置案例数据
# ==============================================================================

PREBUILT_CASES: List[Dict[str, Any]] = [
    {"case_id": "case_001", "title": "有机苹果采摘展示", "description": "展示新鲜苹果从采摘到包装的全过程",
     "category": "水果", "tags": ["水果", "有机", "采摘"], "thumbnail": "https://picsum.photos/seed/apple/400/300",
     "views": 2450, "likes": 132, "duration": "1:45"},
    {"case_id": "case_002", "title": "新鲜大葱产地直发", "description": "展示大葱从田间到餐桌的新鲜供应链",
     "category": "蔬菜", "tags": ["大葱", "产地直发", "新鲜"], "thumbnail": "https://picsum.photos/seed/onion/400/300",
     "views": 3210, "likes": 187, "duration": "2:30"},
    {"case_id": "case_003", "title": "夏日西瓜促销活动", "description": "炎热夏季的西瓜促销带货视频",
     "category": "水果", "tags": ["水果", "促销", "夏季"], "thumbnail": "https://picsum.photos/seed/watermelon/400/300",
     "views": 4521, "likes": 256, "duration": "0:45"},
]

class BackendAPI(Protocol):
    async def authenticate(self, username: str, password: str) -> Dict[str, Any]: ...
    async def create_project(self, user_id: str, mode: int, title: str, description: str,
                       province: Optional[str] = None) -> Dict[str, Any]: ...
    async def agent1_chat(self, project_id: str, message: str, history: List[Dict[str, str]], mode_id: int = 0) -> Dict[str, Any]: ...
    async def agent1_generate_summary(self, project_id: str, highlights: str,
                                  custom_notes: Optional[str] = None, mode_id: int = 0) -> Dict[str, Any]: ...
    async def agent2_generate_shots(self, project_id: str, summary: str, mode_id: int = 0,
                                style_preference: Optional[str] = None) -> Dict[str, Any]: ...
    async def upload_segment_video(self, project_id: str, segment_id: str, file_data: bytes, file_name: str,
                             content_type: str) -> Dict[str, Any]: ...
    async def upload_bgm(self, project_id: str, file_data: bytes, file_name: str,
                   content_type: str = "audio/mpeg") -> Dict[str, Any]: ...
    async def rough_cut(self, project_id: str, segment_sequence: List[str], bgm_url: Optional[str] = None,
                  bgm_volume: float = 0.3, subtitle_enabled: bool = True, tts_voice: Optional[str] = None,
                  digital_human: Optional[str] = None, mode: int = 0) -> Dict[str, Any]: ...
    async def publish_video(self, project_id: str, title: str, description: str, tags: List[str],
                      cover_frame: Optional[int] = 0) -> Dict[str, Any]: ...
    async def get_case_library(self, category: Optional[str] = None, keyword: Optional[str] = None,
                         sort_by: str = "views", page: int = 1, page_size: int = 12) -> Dict[str, Any]: ...
    async def get_personal_works(self, user_id: str, keyword: Optional[str] = None, sort_by: str = "date", page: int = 1,
                           page_size: int = 12) -> Dict[str, Any]: ...
    async def check_username_exists(self, username: str) -> Dict[str, Any]: ...
    async def check_phone_exists(self, phone: str) -> Dict[str, Any]: ...
    async def check_file_exists(self, url_path: str) -> bool: ...
    async def get_project_segments(self, project_id: str) -> Dict[str, Any]: ...
    async def reorder_segments(self, project_id: str, segment_id_list: List[str]) -> Dict[str, Any]: ...
    async def add_segment(self, project_id: str, after_segment_id: Optional[str] = None) -> Dict[str, Any]: ...
    async def update_segment(self, project_id: str, segment_id: str,
                       scene: Optional[str] = None, audio: Optional[str] = None,
                       text: Optional[str] = None) -> Dict[str, Any]: ...
    async def delete_segment(self, project_id: str, segment_id: str) -> Dict[str, Any]: ...


class HybridBackendAPI(BackendAPI):
    """
    混合后端：
      - AI 推理 → HTTP 访问 FastAPI (localhost:8000)
      - 其他所有操作 → 前端本地线程池执行
    """

    # FastAPI 后端只保留 AI 相关路由
    ENDPOINTS = {
        "register": "/api/auth/register",
        "authenticate": "/api/auth/login",
        "check_username": "/api/auth/check-username",
        "check_phone": "/api/auth/check-phone",
        "create_project": "/api/projects",
        "agent1_chat": "/api/agent1/chat",
        "agent1_summary": "/api/agent1/summary",
        "agent2_shots": "/api/agent2/shots",
    }

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            headers={"Content-Type": "application/json", "Accept": "application/json"}
        )
        # 本地存储和内存索引
        self._storage = FileStorage("../backend/storage")
        self._users: Dict[str, UserAccount] = {}
        self._projects: Dict[str, Dict[str, Any]] = {}
        self._works: Dict[str, List[Dict[str, Any]]] = {}
        self._segments: Dict[str, Dict[str, VideoSegment]] = {}
        self._username_index: Dict[str, str] = {}
        self._phone_index: Dict[str, str] = {}

    async def close(self):
        await self.client.aclose()

    # ==============================================================================
    # 辅助：在线程池中执行同步函数
    # ==============================================================================
    async def _run_sync(self, func, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, func, *args, **kwargs)

    # ==============================================================================
    # HTTP 请求封装（只用于 AI 相关接口）
    # ==============================================================================
    async def _post(self, endpoint_key: str, payload: dict) -> dict:
        url = f"{self.base_url}{self.ENDPOINTS[endpoint_key]}"
        try:
            resp = await self.client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            return {"success": False, "message": "请求超时"}
        except httpx.ConnectError:
            return {"success": False, "message": "无法连接到后端"}
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error: {url}, status: {e.response.status_code}")
            return {"success": False, "message": f"HTTP {e.response.status_code}"}
        except Exception as e:
            logger.error(f"Request failed: {url}, error: {e}")
            return {"success": False, "message": str(e)}

    async def _get(self, endpoint_key: str, params: Optional[dict] = None) -> dict:
        url = f"{self.base_url}{self.ENDPOINTS[endpoint_key]}"
        try:
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"GET failed: {url}, error: {e}")
            return {"success": False, "message": str(e)}

    # ==============================================================================
    # 1. 用户认证（HTTP → FastAPI）
    # ==============================================================================
    async def register(self, username: str, password: str,
                 phone: Optional[str] = None,
                 nickname: Optional[str] = None) -> Dict[str, Any]:
        req = RegisterRequest(username=username, password=password,
                              phone=phone, nickname=nickname)
        return await self._post("register", req.model_dump(exclude_none=True))

    async def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        req = LoginRequest(username=username, password=password)
        return await self._post("authenticate", req.model_dump())

    async def check_username_exists(self, username: str) -> Dict[str, Any]:
        return await self._get("check_username", {"username": username})

    async def check_phone_exists(self, phone: str) -> Dict[str, Any]:
        return await self._get("check_phone", {"phone": phone})

    # ==============================================================================
    # 2. 项目管理（HTTP → FastAPI）
    # ==============================================================================
    async def create_project(self, user_id: str, mode: int, title: str,
                       description: str, province: Optional[str] = None) -> Dict[str, Any]:
        req = CreateProjectRequest(user_id=user_id, mode=mode, title=title,
                                   description=description, province=province)
        resp = await self._post("create_project", req.model_dump(exclude_none=True))
        if resp.get("success"):
            # 本地同步创建 segments 字典
            self._segments[resp["project_id"]] = {}
        return resp

    # ==============================================================================
    # 3. Agent 接口（HTTP → FastAPI，核心 AI 推理）
    # ==============================================================================
    async def agent1_chat(self, project_id: str, message: str,
                    history: List[Dict[str, str]], mode_id: int = 0) -> Dict[str, Any]:
        req = Agent1ChatRequest(project_id=project_id, message=message,
                                history=history, mode_id=mode_id)
        return await self._post("agent1_chat", req.model_dump())

    async def agent1_generate_summary(self, project_id: str,
                                 highlights: str,
                                 custom_notes: Optional[str] = None, mode_id: int = 0) -> Dict[str, Any]:
        req = Agent1SummaryRequest(project_id=project_id, highlights=highlights,
                                   custom_notes=custom_notes, mode_id=mode_id)
        return await self._post("agent1_summary", req.model_dump(exclude_none=True))

    async def agent2_generate_shots(self, project_id: str, summary: str,
                              mode_id: int = 0,
                              style_preference: Optional[str] = None) -> Dict[str, Any]:
        req = Agent2ShotsRequest(project_id=project_id, summary=summary,
                                 mode_id=mode_id, style_preference=style_preference)
        return await self._post("agent2_shots", req.model_dump(exclude_none=True))

    # ==============================================================================
    # 4. 片段管理（本地同步，线程池包装）
    # ==============================================================================
    async def get_project_segments(self, project_id: str) -> Dict[str, Any]:
        def _get():
            segments = self._segments.get(project_id, {})
            segment_list = sorted(segments.values(), key=lambda x: x.order)
            return {
                "success": True,
                "project_id": project_id,
                "segments": segment_list,
                "count": len(segment_list)
            }
        return await self._run_sync(_get)

    async def reorder_segments(self, project_id: str, segment_id_list: List[str]) -> Dict[str, Any]:
        def _reorder():
            segments = self._segments.get(project_id, {})
            valid_ids = [sid for sid in segment_id_list if sid in segments]
            for i, sid in enumerate(valid_ids):
                segments[sid].order = i
            return {
                "success": True,
                "new_orders": {sid: i for i, sid in enumerate(valid_ids)}
            }
        return await self._run_sync(_reorder)

    async def add_segment(self, project_id: str, after_segment_id: Optional[str] = None) -> Dict[str, Any]:
        def _add():
            if project_id not in self._segments:
                self._segments[project_id] = {}

            segments = self._segments[project_id]
            new_sid = f"seg_{uuid.uuid4().hex[:8]}"

            if not segments:
                new_order = 0
            elif after_segment_id is None or after_segment_id not in segments:
                new_order = max(s.order for s in segments.values()) + 1
            else:
                new_order = segments[after_segment_id].order + 1
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

            return {"success": True, "new_segment": new_seg}
        return await self._run_sync(_add)

    async def delete_segment(self, project_id: str, segment_id: str) -> Dict[str, Any]:
        def _delete():
            segments = self._segments.get(project_id, {})
            if segment_id not in segments:
                return {"success": False, "message": "片段不存在"}

            deleted_order = segments[segment_id].order
            del segments[segment_id]

            for s in segments.values():
                if s.order > deleted_order:
                    s.order -= 1

            return {"success": True, "deleted_segment_id": segment_id}
        return await self._run_sync(_delete)

    async def update_segment(self, project_id: str, segment_id: str,
                             scene: Optional[str] = None,
                             audio: Optional[str] = None,
                             text: Optional[str] = None) -> Dict[str, Any]:
        def _update():
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

            return {"success": True, "updated_segment": seg}
        return await self._run_sync(_update)

    # ==============================================================================
    # 5. 【同步本地】视频/BGM 上传（不走 HTTP）
    # ==============================================================================
    async def upload_segment_video(self, project_id: str, segment_id: str,
                                   file_data: bytes, file_name: str,
                                   content_type: str = "video/mp4") -> Dict[str, Any]:
        def _upload():
            try:
                video_url = self._storage.save_segment(project_id, segment_id, file_data, file_name)

                if project_id not in self._segments:
                    self._segments[project_id] = {}

                abs_path = self._storage.get_absolute_path(video_url)
                vinfo = get_video_info(str(abs_path))
                h = vinfo.get("height", 1080)
                w = vinfo.get("width", 1920)
                duration = vinfo.get("duration", 5.0)

                if segment_id in self._segments[project_id]:
                    seg = self._segments[project_id][segment_id]
                    seg.video_url = video_url
                    seg.video_duration = duration
                else:
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
                return {"success": False, "message": f"文件存储失败: {str(e)}"}
        return await self._run_sync(_upload)

    async def upload_bgm(self, project_id: str, file_data: bytes, file_name: str,
                         content_type: str = "audio/mpeg") -> Dict[str, Any]:
        def _upload():
            try:
                bgm_url = self._storage.save_bgm(project_id, file_data, file_name)
                return {
                    "success": True,
                    "bgm_url": bgm_url,
                    "message": "BGM 上传成功"
                }
            except Exception as e:
                return {"success": False, "message": f"BGM 上传失败: {str(e)}"}
        return await self._run_sync(_upload)

    # ==============================================================================
    # 6. 【同步本地】文件存在性检查
    # ==============================================================================
    async def check_file_exists(self, url_path: str) -> bool:
        def _check():
            if not url_path:
                return False
            try:
                abs_path = self._storage.get_absolute_path(url_path)
                return abs_path.exists() and abs_path.stat().st_size > 1000
            except Exception:
                return False
        return await self._run_sync(_check)

    # ==============================================================================
    # 7. 【后台线程】粗剪合成（本地 ffmpeg，不阻塞事件循环）
    # ==============================================================================
    async def rough_cut(self, project_id: str, segment_sequence: List[str],
                   bgm_url: Optional[str] = None,
                   bgm_volume: float = 1.0,
                   subtitle_enabled: bool = False,
                   tts_voice: Optional[str] = None,
                   digital_human: Optional[str] = None,
                   mode: int = 0) -> Dict[str, Any]:
        return await self._run_sync(
            self._sync_rough_cut,
            project_id, segment_sequence, bgm_url, bgm_volume,
            subtitle_enabled, tts_voice, digital_human, mode
        )

    def _sync_rough_cut(self, project_id, segment_sequence, bgm_url, bgm_volume,
                        subtitle_enabled, tts_voice, digital_human, mode):
        """同步执行粗剪（在线程池中运行）"""
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

                vinfo_raw = get_video_info(p_clean)
                print(f"[转码] 片段{i}: 原始分辨率 {vinfo_raw.get('width', 0)}x{vinfo_raw.get('height', 0)}")

                cmd = [
                    "ffmpeg", "-y", "-i", p_clean,
                    "-vf", "fps=25,format=yuv420p",
                    "-af", "aresample=48000:async=1,aformat=sample_fmts=fltp:channel_layouts=stereo",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                    "-pix_fmt", "yuv420p",
                    "-movflags", "+faststart",
                    uniform
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, encoding='utf-8', errors="replace")
                if result.returncode != 0:
                    print(f"[ERROR] 转码片段{i}失败: {result.stderr[:500]}")
                    return {"success": False, "message": f"转码片段{i}失败"}

                uniform_files.append(uniform)

            # 第二步：concat 协议拼接
            list_file = str(temp_dir / "concat_list.txt").replace('\\', '/')
            with open(list_file, "w", encoding="utf-8") as f:
                for uf in uniform_files:
                    f.write(f"file '{uf}'\n")

            cmd = [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                "-movflags", "+faststart",
                str(temp_video).replace('\\', '/')
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300, encoding='utf-8', errors="replace")

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

            # 字幕/TTS/数字人处理
            vinfo = get_video_info(str(temp_video))
            temp_processed = output_path.parent / "temp_processed.mp4"
            scene_anno = self._build_scene_annotation(project_id)

            need_tts_avatar = (mode == 0 and tts_voice is not None and digital_human is not None)
            print(f"[INFO] TTS={tts_voice}, Avatar={digital_human}, need_tts_avatar={need_tts_avatar}")

            if need_tts_avatar:
                print(f"[INFO] 产品介绍模式，启用TTS({tts_voice}) + 数字人({digital_human})")
                ok = self._tts_and_avatar(
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
                result_bgm = subprocess.run(cmd_bgm, capture_output=True, text=True, timeout=120, encoding='utf-8', errors="replace")

                if result_bgm.returncode != 0:
                    print(f"[WARN] BGM处理失败: {result_bgm.stderr[:300]}")
                    has_bgm = False
                else:
                    # 提取视频音频
                    temp_video_audio = output_path.parent / "temp_video_audio.aac"
                    r1 = subprocess.run([
                        'ffmpeg', '-y', '-i', str(temp_video).replace('\\', '/'),
                        '-vn', '-c:a', 'copy', str(temp_video_audio).replace('\\', '/')
                    ], capture_output=True, text=True, timeout=60, encoding='utf-8', errors="replace")

                    if r1.returncode != 0:
                        print(f"[WARN] 提取视频音频失败: {r1.stderr[:300]}")
                        has_bgm = False
                    else:
                        # 统一音频格式
                        temp_video_audio_fmt = output_path.parent / "temp_video_audio_fmt.aac"
                        temp_bgm_fmt = output_path.parent / "temp_bgm_fmt.aac"

                        for src, dst in [(temp_video_audio, temp_video_audio_fmt), (temp_bgm, temp_bgm_fmt)]:
                            subprocess.run([
                                'ffmpeg', '-y', '-i', str(src).replace('\\', '/'),
                                '-ar', '48000', '-ac', '2', '-c:a', 'aac', '-b:a', '192k',
                                str(dst).replace('\\', '/')
                            ], capture_output=True, timeout=60, encoding='utf-8', errors="replace")

                        # 混合音频
                        temp_mixed_audio = output_path.parent / "temp_mixed_audio.aac"
                        cmd_mix = [
                            'ffmpeg', '-y',
                            '-i', str(temp_video_audio_fmt).replace('\\', '/'),
                            '-i', str(temp_bgm_fmt).replace('\\', '/'),
                            '-filter_complex', '[0:a][1:a]amix=inputs=2:duration=first[aout]',
                            '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000',
                            str(temp_mixed_audio).replace('\\', '/')
                        ]
                        result_mix = subprocess.run(cmd_mix, capture_output=True, text=True, timeout=120, encoding='utf-8', errors="replace")

                        # 清理中间文件
                        for f in [temp_video_audio, temp_video_audio_fmt, temp_bgm, temp_bgm_fmt]:
                            try:
                                f.unlink(missing_ok=True)
                            except:
                                pass

                        if result_mix.returncode != 0:
                            print(f"[WARN] 音频混合失败: {result_mix.stderr[:300]}")
                            has_bgm = False
                        else:
                            # 最终合成
                            cmd_final = [
                                'ffmpeg', '-y',
                                '-i', str(temp_video).replace('\\', '/'),
                                '-i', str(temp_mixed_audio).replace('\\', '/'),
                                '-c:v', 'copy', '-map', '0:v:0', '-map', '1:a:0',
                                '-shortest', '-movflags', '+faststart',
                                str(output_path).replace('\\', '/')
                            ]
                            result = subprocess.run(cmd_final, capture_output=True, text=True, timeout=120, encoding='utf-8', errors="replace")
                            try:
                                temp_mixed_audio.unlink(missing_ok=True)
                            except:
                                pass
                            try:
                                temp_video.unlink(missing_ok=True)
                            except:
                                pass

            if not has_bgm:
                shutil.copy2(temp_video, output_path)
                try:
                    temp_video.unlink(missing_ok=True)
                except:
                    pass

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
                    capture_output=True, text=True, timeout=30, encoding='utf-8', errors="replace")
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
    # 内部工具函数
    # ------------------------------------------------------------------
    def _gene_subtitle(self, merged_video: str, vinfo: Dict, scene_anno: str, output: str) -> bool:
        try:
            subtitle_for_fhfp(merged_video, vinfo, scene_anno, output)
            return True
        except Exception as e:
            print(f"[ERROR] 字幕烧录失败: {e}")
            try:
                shutil.copy2(merged_video, output)
                return True
            except:
                return False

    def _tts_and_avatar(self, merged_video: str, vinfo: Dict, scene_anno: str,
                        output: str, tts_voice: str, digital_human: str) -> bool:
        try:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                future = asyncio.run_coroutine_threadsafe(
                    avatar_for_fhfp(merged_video, vinfo, scene_anno, output, tts_voice, digital_human),
                    loop
                )
                future.result(timeout=300)
                return True
            except RuntimeError:
                asyncio.run(avatar_for_fhfp(merged_video, vinfo, scene_anno, output, tts_voice, digital_human))
                return True
        except Exception as e:
            print(f"[ERROR] TTS+数字人合成失败: {e}")
            try:
                shutil.copy2(merged_video, output)
                return True
            except:
                return False

    def _build_scene_annotation(self, project_id: str) -> str:
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

    # ==============================================================================
    # 8. 发布作品（本地同步）
    # ==============================================================================
    async def publish_video(self, project_id: str, title: str, description: str,
                      tags: List[str], cover_frame: Optional[int] = 0) -> Dict[str, Any]:
        def _publish():
            try:
                preview_path = self._storage.get_output_path(project_id, "preview.mp4")
                if not preview_path.exists():
                    return {
                        "success": False,
                        "message": "预览文件不存在，请先生成粗剪预览"
                    }

                final_name = f"final_{uuid.uuid4().hex[:8]}.mp4"
                final_path = self._storage.get_output_path(project_id, final_name)
                shutil.copy2(preview_path, final_path)

                final_url = f"/storage/projects/{project_id}/output/{final_name}"
                work_id = f"work_{uuid.uuid4().hex[:8]}"

                project = self._projects.get(project_id, {})
                user_id = project.get("user_id", "anonymous")

                work_data = {
                    "work_id": work_id,
                    "project_id": project_id,
                    "user_id": user_id,
                    "title": title,
                    "description": description,
                    "tags": tags or [],
                    "video_url": final_url,
                    "download_url": final_url,
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
                    "public_url": final_url,
                    "download_url": final_url,
                    "created_at": work_data["created_at"],
                    "message": "作品已发布到个人库，支持在线播放与下载"
                }

            except Exception as e:
                return {"success": False, "message": f"发布失败: {str(e)}"}
        return await self._run_sync(_publish)

    # ==============================================================================
    # 9. 查询接口（本地同步）
    # ==============================================================================
    async def get_personal_works(self, user_id: str, keyword: Optional[str] = None,
                           sort_by: str = "date", page: int = 1,
                           page_size: int = 12) -> Dict[str, Any]:
        def _get():
            works = self._works.get(user_id, [])
            if keyword:
                kw = keyword.lower()
                works = [w for w in works if kw in w.get("title", "").lower()
                         or kw in w.get("description", "").lower()]
            if sort_by == "date":
                works = sorted(works, key=lambda x: x.get("created_at", ""), reverse=True)
            elif sort_by == "views":
                works = sorted(works, key=lambda x: x.get("views", 0), reverse=True)
            total = len(works)
            start = (page - 1) * page_size
            return {
                "success": True,
                "total": total,
                "page": page,
                "page_size": page_size,
                "items": works[start:start + page_size]
            }
        return await self._run_sync(_get)

    async def get_case_library(self, category: Optional[str] = None,
                         keyword: Optional[str] = None,
                         sort_by: str = "views", page: int = 1,
                         page_size: int = 12) -> Dict[str, Any]:
        def _get():
            items = PREBUILT_CASES.copy()
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
        return await self._run_sync(_get)



