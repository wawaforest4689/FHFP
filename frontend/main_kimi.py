#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FHFP Frontend - NiceGUI Class-Based Architecture
================================================
基于类的页面架构，彻底消除闭包与未解析引用问题。
所有页面继承 BasePage，通过 @ui.page 装饰器注册路由。
"""

import json
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional
import shutil
from components.authdialog import UserAccount,AuthValidator
from components.nav_bar import NavigationBar
from components.filestorage import FileStorage
from components.video_player import video_player


import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.datamodel import CreationMode,ProjectContext,ShotSuggestion,VideoSegment,VideoWork
from core import logger,AppState,THEME_COLORS,APP_TITLE,MAX_SEGMENTS,DEFAULT_BGM_VOLUME,BackendAPI
from nicegui import ui, app, events
import re
import math
import hashlib



# 在 FastAPI 主应用中添加
from fastapi.staticfiles import StaticFiles
# 挂载存储目录为静态文件服务
os.makedirs("../backend/storage",exist_ok=True)
app.mount("/storage", StaticFiles(directory="../backend/storage"), name="storage")




# ==============================================================================
# 3. Mock 后端 (完整实现，用于独立运行)
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



# ==================== MockBackendAPI（完整版） ====================

class MockBackendAPI(BackendAPI):
    """
    完整后端实现，包含：
    1. 用户认证（注册/登录/查重）
    2. 项目管理
    3. AI Agent 对话（Agent1 亮点挖掘 / Agent2 拍摄建议）
    4. 【核心】视频片段上传 -> 真实磁盘存储 + 返回在线播放URL
    5. 【核心】多片段按编号顺序真实拼接（moviepy）-> 生成 preview.mp4 + 返回在线预览URL
    6. 【核心】作品发布 -> 复制为最终版本 + 入库 + 提供 public_url（在线播放）与 download_url（下载到客户端本地）
    7. 个人作品库与公共案例库查询
    """

    def __init__(self):
        # 内存数据索引
        self._users: Dict[str, UserAccount] = {}
        self._projects: Dict[str, Dict[str, Any]] = {}
        self._works: Dict[str, List[Dict[str, Any]]] = {}
        self._segments: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._username_index: Dict[str, str] = {}
        self._phone_index: Dict[str, str] = {}
        self._video_counter = 0

        # 真实文件存储（磁盘IO）
        self._storage = FileStorage("../backend/storage")

    # ------------------------------------------------------------------
    # 1. 用户认证
    # ------------------------------------------------------------------
    def register(self, username: str, password: str,
                 phone: Optional[str] = None,
                 nickname: Optional[str] = None) -> Dict[str, Any]:
        ok, msg = AuthValidator.validate_username(username)
        if not ok:
            return {"success": False, "message": msg}
        ok, msg = AuthValidator.validate_password(password)
        if not ok:
            return {"success": False, "message": msg}
        if phone:
            ok, msg = AuthValidator.validate_phone(phone)
            if not ok:
                return {"success": False, "message": msg}
        if username in self._username_index:
            return {"success": False, "message": "该账号已被注册"}
        if phone and phone in self._phone_index:
            return {"success": False, "message": "该手机号已被绑定"}

        user_id = f"user_{uuid.uuid4().hex[:12]}"
        account = UserAccount(
            user_id=user_id,
            username=username,
            phone=phone,
            password_hash=self._hash_password(password),
            nickname=nickname or username,
            avatar_url=f"https://api.dicebear.com/7.x/avataaars/svg?seed={username}"
        )
        self._users[user_id] = account
        self._username_index[username] = user_id
        if phone:
            self._phone_index[phone] = user_id

        return {
            "success": True,
            "user_id": user_id,
            "message": "注册成功",
            "nickname": account.nickname
        }

    def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        if AuthValidator.PHONE_PATTERN.match(username):
            user_id = self._phone_index.get(username)
        else:
            user_id = self._username_index.get(username)

        if not user_id or user_id not in self._users:
            return {"success": False, "message": "账号或密码错误"}

        account = self._users[user_id]
        if not self._verify_password(password, account.password_hash):
            return {"success": False, "message": "账号或密码错误"}

        return {
            "success": True,
            "user_id": user_id,
            "token": f"jwt_mock_{uuid.uuid4().hex}",
            "nickname": account.nickname,
            "avatar_url": account.avatar_url,
            "message": "登录成功"
        }

    def check_username_exists(self, username: str) -> Dict[str, Any]:
        return {"success": True, "exists": username in self._username_index}

    def check_phone_exists(self, phone: str) -> Dict[str, Any]:
        return {"success": True, "exists": phone in self._phone_index}

    def _hash_password(self, password: str) -> str:
        return hashlib.sha256(f"fhfp_salt_{password}".encode()).hexdigest()

    def _verify_password(self, password: str, password_hash: str) -> bool:
        return self._hash_password(password) == password_hash

    # ------------------------------------------------------------------
    # 2. 项目管理
    # ------------------------------------------------------------------
    def create_project(self, user_id: str, mode: int, title: str,
                       description: str, province: Optional[str] = None) -> Dict[str, Any]:
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
        self._segments[pid] = {}
        return {
            "success": True,
            "project_id": pid,
            "created_at": self._projects[pid]["created_at"],
            "status": "draft"
        }

    # ------------------------------------------------------------------
    # 3. Agent 1：亮点挖掘与摘要
    # ------------------------------------------------------------------
    def agent1_chat(self, project_id: str, message: str,
                    history: List[Dict[str, str]], mode_id: int = 0) -> Dict[str, Any]:
        reply = f"收到您的描述：'{message}'。我已为您提取出以下亮点：1.产地直发 2.新鲜采摘 3.有机认证。请确认或补充。"
        return {
            "success": True,
            "reply": reply,
            "highlights": ["产地直发，保证新鲜", "有机认证，健康放心", "农户直销，价格优惠"],
            "summary_draft": "",
            "suggested_questions": ["请详细描述种植过程", "产品与竞品的主要区别是什么？"],
            "turn_id": len(history) + 1
        }

    def agent1_generate_summary(self, project_id: str, highlights: List[str],
                                custom_notes: Optional[str] = None,
                                mode_id: int = 0) -> Dict[str, Any]:
        summary = "【摘要】本产品坚持传统种植方式，" + "；".join(highlights) + "。"
        if custom_notes:
            summary += f"【用户补充】{custom_notes}"
        return {
            "success": True,
            "summary": summary,
            "keywords": ["农产品", "新鲜", "有机"],
            "emotion_tags": ["朴实", "真诚"],
            "target_audience": "一二线城市注重健康的年轻消费者"
        }

    # ------------------------------------------------------------------
    # 4. Agent 2：拍摄建议生成
    # ------------------------------------------------------------------
    def agent2_generate_shots(self, project_id: str, summary: str,
                              style_preference: Optional[str] = None,
                              mode_id: int = 0) -> Dict[str, Any]:
        shots = []
        templates = [
            {"scene": "全景：果园/农田清晨全景，阳光洒落", "audio": "自然环境音：鸟鸣、微风",
             "copy": "清晨的第一缕阳光，照亮了我们的果园。"},
            {"scene": "特写：农产品表面纹理，露水欲滴", "audio": "轻快吉他背景音乐起",
             "copy": "每一颗果实，都饱含大自然的馈赠。"},
            {"scene": "中景：农户采摘/包装过程，动作熟练", "audio": "包装纸摩擦声+轻快节奏",
             "copy": "从田间到餐桌，我们只追求最新鲜。"},
            {"scene": "近景：双手捧起产品展示，微笑", "audio": "音乐渐强，环境音淡出",
             "copy": "选择我们，就是选择健康与安心。"},
        ]
        for i, tpl in enumerate(templates):
            sid = f"shot_{i + 1}_{uuid.uuid4().hex[:4]}"
            shots.append({
                "shot_id": sid,
                "order": i,
                "scene": tpl["scene"],
                "audio": tpl["audio"],
                "copy": tpl["copy"],
                "duration_hint": 5,
                "camera_movement": "固定机位" if i % 2 == 0 else "缓慢推近"
            })
        return {
            "success": True,
            "shots": shots,
            "total_duration": 20,
            "storyline_arc": "起承转合：环境铺垫 -> 产品展示 -> 过程信任 -> 情感号召"
        }

    # ------------------------------------------------------------------
    # 【核心功能1】视频片段上传：真实存储到本地磁盘 + 返回在线播放URL
    # ------------------------------------------------------------------
    def upload_segment_video(self, project_id: str, segment_id: str,
                             file_data: bytes, file_name: str,
                             content_type: str = None) -> Dict[str, Any]:
        """
        真实存储视频片段到磁盘，返回可供 <video> 标签直接播放的 URL。

        存储路径：
            storage/projects/{project_id}/segments/{segment_id}/video.{ext}

        访问路径（需配合 FastAPI StaticFiles 挂载 /storage）：
            /storage/projects/{project_id}/segments/{segment_id}/video.{ext}
        """
        try:
            # 真实写入磁盘
            video_url = self._storage.save_segment(project_id, segment_id, file_data, file_name)

            # 更新内存索引
            if project_id not in self._segments:
                self._segments[project_id] = {}
            self._segments[project_id][segment_id] = {
                "video_url": video_url,
                "file_name": file_name,
                "content_type": content_type,
                "uploaded_at": datetime.now().isoformat(),
                "size_bytes": len(file_data)
            }

            return {
                "success": True,
                "video_url": video_url,  # 前端直接用作 <video src="...">
                "duration": 5.0,  # 简化处理，实际可用 ffprobe 读取
                "resolution": "1920x1080",
                "message": "上传成功，已持久化到磁盘"
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"文件存储失败: {str(e)}"
            }

    def update_segment_meta(self, project_id: str, segment_id: str,
                            scene: str, audio: str, copy: str) -> Dict[str, Any]:
        return {"success": True, "updated_at": datetime.now().isoformat()}

    def reorder_segments(self, project_id: str,
                         segment_id_list: List[str]) -> Dict[str, Any]:
        return {
            "success": True,
            "new_orders": {sid: idx for idx, sid in enumerate(segment_id_list)}
        }

    def delete_segment(self, project_id: str, segment_id: str) -> Dict[str, Any]:
        if project_id in self._segments and segment_id in self._segments[project_id]:
            del self._segments[project_id][segment_id]
        return {"success": True, "deleted_segment_id": segment_id}

    def add_segment(self, project_id: str,
                    after_segment_id: Optional[str] = None) -> Dict[str, Any]:
        new_sid = f"seg_new_{uuid.uuid4().hex[:4]}"
        return {
            "success": True,
            "new_segment": {
                "segment_id": new_sid,
                "project_id": project_id,
                "order": 999,
                "scene": "（新片段画面）",
                "audio": "（新片段音频）",
                "copy": "（新片段文案）",
                "duration_hint": 5
            }
        }


    def _gene_subtitle(self, merged_video: str, vinfo: Dict, scene_anno: str, output: str) -> bool:
        """
        仅字幕烧录工具函数。
        调用 video_composer.subtitle_for_fhfp() 进行字幕合成。
        """
        try:
            import importlib.util
            import sys

            composer_path = Path(__file__).resolve().parent / "video_composer.py"
            if not composer_path.exists():
                composer_path = Path("video_composer.py")

            if composer_path.exists():
                spec = importlib.util.spec_from_file_location("video_composer", composer_path)
                composer_mod = importlib.util.module_from_spec(spec)
                sys.modules["video_composer"] = composer_mod
                spec.loader.exec_module(composer_mod)
                composer_mod.subtitle_for_fhfp(merged_video, vinfo, scene_anno, output)
                return True
            else:
                import shutil
                shutil.copy2(merged_video, output)
                print("[WARN] video_composer.py 未找到，跳过字幕烧录")
                return True
        except Exception as e:
            print(f"[ERROR] 字幕烧录失败: {e}")
            import shutil
            try:
                shutil.copy2(merged_video, output)
                return True
            except:
                return False

    def _tts_and_avatar(self, merged_video: str, vinfo: Dict, scene_anno: str,
                        output: str, tts_voice: str, digital_human: str) -> bool:
        """
        TTS配音 + 数字人头像 + 字幕烧录工具函数。
        调用 video_composer.avatar_for_fhfp() 进行完整合成。
        """
        try:
            import importlib.util
            import sys

            composer_path = Path(__file__).resolve().parent.parent / "video_composer.py"
            if not composer_path.exists():
                composer_path = Path("video_composer.py")

            if composer_path.exists():
                spec = importlib.util.spec_from_file_location("video_composer", composer_path)
                composer_mod = importlib.util.module_from_spec(spec)
                sys.modules["video_composer"] = composer_mod
                spec.loader.exec_module(composer_mod)

                original_male_voice = getattr(composer_mod, 'VOICE_MALE', None)
                original_female_voice = getattr(composer_mod, 'VOICE_FEMALE', None)
                original_male_file = getattr(composer_mod, 'AVATAR_MALE_FILE', None)
                original_female_file = getattr(composer_mod, 'AVATAR_FEMALE_FILE', None)

                if tts_voice == "Yunxi":
                    composer_mod.VOICE_MALE = "zh-CN-YunxiNeural"
                    composer_mod.VOICE_FEMALE = "zh-CN-YunxiNeural"
                elif tts_voice=="Xiaoying":
                    composer_mod.VOICE_MALE = "zh-CN-XiaoyingNeural"
                    composer_mod.VOICE_FEMALE = "zh-CN-XiaoyingNeural"

                if digital_human == "man":
                    composer_mod.AVATAR_MALE_FILE = "man.gif"
                    composer_mod.AVATAR_FEMALE_FILE = "man.gif"
                elif digital_human=="woman":
                    composer_mod.AVATAR_MALE_FILE = "girl.gif"
                    composer_mod.AVATAR_FEMALE_FILE = "girl.gif"

                try:
                    composer_mod.avatar_for_fhfp(merged_video, vinfo, scene_anno, output)
                    return True
                finally:
                    if original_male_voice: composer_mod.VOICE_MALE = original_male_voice
                    if original_female_voice: composer_mod.VOICE_FEMALE = original_female_voice
                    if original_male_file: composer_mod.AVATAR_MALE_FILE = original_male_file
                    if original_female_file: composer_mod.AVATAR_FEMALE_FILE = original_female_file
            else:
                import shutil
                shutil.copy2(merged_video, output)
                print("[WARN] video_composer.py 未找到，跳过TTS和数字人")
                return True
        except Exception as e:
            print(f"[ERROR] TTS+数字人合成失败: {e}")
            import shutil
            try:
                shutil.copy2(merged_video, output)
                return True
            except:
                return False

    def _build_scene_annotation(self, project_id: str) -> str:
        """
        从项目片段构建场景标注文本（TXT格式）。
        格式：[{"figure": "主讲人", "script": "台词"}](0-5)
        """
        segments = self._segments.get(project_id, {})
        if not segments:
            return ""

        lines = []
        current_time = 0
        sorted_segs = sorted(segments.values(), key=lambda x: x.get("order", 0))

        for seg_meta in sorted_segs:
            script = seg_meta.get("copy", "")
            duration = seg_meta.get("duration", 5.0)
            figure=seg_meta.get("figure",seg_meta.get("script",""))
            start = current_time
            end = current_time + duration
            seg_json = json.dumps([{"figure":figure, "script": script}], ensure_ascii=False)
            lines.append(f"{seg_json}({int(start)}-{int(end)})")
            current_time = end

        return "\n".join(lines)


    # ------------------------------------------------------------------
    # 【核心功能2】粗剪合成：按编号顺序真实拼接 + 生成在线预览URL
    # ------------------------------------------------------------------
    def rough_cut(self, project_id: str, segment_sequence: List[str],
                  bgm_url: Optional[str] = None,
                  bgm_volume: float = 1.0,  # 0.0~10.0, 1.0=100%
                  subtitle_enabled: bool = False,
                  tts_voice: Optional[str] = None,
                  digital_human: Optional[str] = None) -> Dict[str, Any]:
        """
        使用 ffmpeg 拼接视频片段。
        BGM 混合：简单线性叠加，先分别处理两个音频的音量，再合并。
        100% = BGM 响度与原声相近，通过 loudnorm 归一化后按用户比例缩放。
        """
        import subprocess
        import tempfile
        import time
        import math

        # 收集有效片段
        video_files = []
        for seg_id in segment_sequence:
            seg_meta = self._segments.get(project_id, {}).get(seg_id)
            if not seg_meta or not seg_meta.get("video_url"):
                continue
            abs_path = self._storage.get_absolute_path(seg_meta["video_url"])
            if abs_path.exists():
                video_files.append(str(abs_path))

        if not video_files:
            return {"success": False, "message": "没有有效视频片段，请先上传素材"}

        output_path = self._storage.get_output_path(project_id, "preview.mp4")
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if output_path.exists():
            output_path.unlink()
            time.sleep(0.2)

        # 创建 concat 列表（使用绝对路径，避免相对路径问题）
        concat_list_path = output_path.parent / "concat_list.txt"
        with open(concat_list_path, 'w', encoding='utf-8') as f:
            for vf in video_files:
                # Windows 路径反斜杠转斜杠，并确保路径无空格问题
                vf_clean = vf.replace('\\', '/')
                f.write(f"file '{vf_clean}'\n")

        try:
            has_bgm = bgm_url and self._storage.get_absolute_path(bgm_url).exists()

            # ========== 第一步：拼接视频（带原声） ==========
            temp_video = output_path.parent / "temp_video.mp4"

            cmd_video = [
                'ffmpeg', '-y',
                '-f', 'concat',
                '-safe', '0',
                '-i', str(concat_list_path).replace('\\', '/'),
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-c:a', 'aac',
                '-b:a', '192k',
                '-movflags', '+faststart',
                str(temp_video).replace('\\', '/')
            ]

            print(f"[DEBUG] Video concat cmd: {' '.join(cmd_video)}")
            result = subprocess.run(cmd_video, capture_output=True, text=True, timeout=300)

            # 清理 concat 列表
            concat_list_path.unlink(missing_ok=True)

            if result.returncode != 0:
                print(f"[ERROR] Video concat stderr:\n{result.stderr}")
                return {"success": False, "message": f"视频拼接失败: {result.stderr[:500]}"}

            if not temp_video.exists() or temp_video.stat().st_size < 1000:
                return {"success": False, "message": "视频拼接输出无效"}

            # ========== 第二步：处理 BGM（如有） ==========
            if has_bgm:
                bgm_path = str(self._storage.get_absolute_path(bgm_url))
                bgm_path_clean = bgm_path.replace('\\', '/')

                # 先获取视频时长
                video_duration = 0
                try:
                    probe = subprocess.run(
                        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                         '-of', 'default=noprint_wrappers=1:nokey=1',
                         str(temp_video).replace('\\', '/')],
                        capture_output=True, text=True, timeout=30
                    )
                    if probe.returncode == 0:
                        video_duration = float(probe.stdout.strip())
                except:
                    video_duration = len(video_files) * 5.0

                # 处理 BGM：归一化 + 音量调节 + 截断/循环匹配视频时长
                # 用户音量 0~10，1.0=100%，线性映射到 dB
                # 0% = -100dB(静音), 100% = 0dB, 1000% = +20dB
                if bgm_volume <= 0:
                    volume_db = -100
                else:
                    volume_db = 20 * math.log10(bgm_volume)

                temp_bgm = output_path.parent / "temp_bgm.aac"

                # 简单处理：loudnorm 归一化 + volume 调节 + 循环/截断
                cmd_bgm = [
                    'ffmpeg', '-y',
                    '-i', bgm_path_clean,
                    '-af', f'loudnorm=I=-23:TP=-2:LRA=11,volume={volume_db}dB',
                    '-t', str(video_duration),  # 截断到视频时长
                    '-c:a', 'aac',
                    '-b:a', '192k',
                    '-ar', '48000',
                    str(temp_bgm).replace('\\', '/')
                ]

                print(f"[DEBUG] BGM process cmd: {' '.join(cmd_bgm)}")
                result_bgm = subprocess.run(cmd_bgm, capture_output=True, text=True, timeout=120)

                if result_bgm.returncode != 0:
                    print(f"[ERROR] BGM process failed: {result_bgm.stderr}")
                    # BGM 处理失败，直接复制视频（无 BGM）
                    has_bgm = False
                else:
                    # ========== 第三步：混合音频（最简单方式：adelay + amix） ==========
                    # 或者更简单的：直接用 map 合并两个音频流

                    # 方案：视频原声 + BGM 分别作为两个输入，用 amix 混合
                    # 为避免 filter_complex 问题，使用预处理的独立音频文件

                    # 提取视频原声为单独文件
                    temp_video_audio = output_path.parent / "temp_video_audio.aac"
                    cmd_extract_audio = [
                        'ffmpeg', '-y',
                        '-i', str(temp_video).replace('\\', '/'),
                        '-vn',  # 不要视频
                        '-c:a', 'copy',
                        str(temp_video_audio).replace('\\', '/')
                    ]
                    subprocess.run(cmd_extract_audio, capture_output=True, timeout=60)

                    # 混合两个音频（使用 amix，但确保输入格式一致）
                    temp_mixed_audio = output_path.parent / "temp_mixed_audio.aac"

                    # 先统一两个音频的格式
                    temp_video_audio_fmt = output_path.parent / "temp_video_audio_fmt.aac"
                    temp_bgm_fmt = output_path.parent / "temp_bgm_fmt.aac"

                    # 统一采样率和格式
                    for src, dst in [(temp_video_audio, temp_video_audio_fmt), (temp_bgm, temp_bgm_fmt)]:
                        cmd_fmt = [
                            'ffmpeg', '-y',
                            '-i', str(src).replace('\\', '/'),
                            '-ar', '48000',
                            '-ac', '2',
                            '-c:a', 'aac',
                            '-b:a', '192k',
                            str(dst).replace('\\', '/')
                        ]
                        subprocess.run(cmd_fmt, capture_output=True, timeout=60)

                    # 使用 amix 混合（两个输入，等权重）
                    cmd_mix = [
                        'ffmpeg', '-y',
                        '-i', str(temp_video_audio_fmt).replace('\\', '/'),
                        '-i', str(temp_bgm_fmt).replace('\\', '/'),
                        '-filter_complex', '[0:a][1:a]amix=inputs=2:duration=first[aout]',
                        '-map', '[aout]',
                        '-c:a', 'aac',
                        '-b:a', '192k',
                        '-ar', '48000',
                        str(temp_mixed_audio).replace('\\', '/')
                    ]

                    print(f"[DEBUG] Audio mix cmd: {' '.join(cmd_mix)}")
                    result_mix = subprocess.run(cmd_mix, capture_output=True, text=True, timeout=120)

                    # 清理中间文件
                    for f in [temp_video_audio, temp_video_audio_fmt, temp_bgm, temp_bgm_fmt]:
                        f.unlink(missing_ok=True)

                    if result_mix.returncode != 0:
                        print(f"[ERROR] Audio mix failed: {result_mix.stderr}")
                        has_bgm = False
                    else:
                        # ========== 第四步：合并视频和混合音频 ==========
                        cmd_final = [
                            'ffmpeg', '-y',
                            '-i', str(temp_video).replace('\\', '/'),
                            '-i', str(temp_mixed_audio).replace('\\', '/'),
                            '-c:v', 'copy',
                            '-map', '0:v:0',
                            '-map', '1:a:0',
                            '-shortest',
                            '-movflags', '+faststart',
                            str(output_path).replace('\\', '/')
                        ]

                        print(f"[DEBUG] Final mux cmd: {' '.join(cmd_final)}")
                        result = subprocess.run(cmd_final, capture_output=True, text=True, timeout=120)

                        temp_mixed_audio.unlink(missing_ok=True)
                        temp_video.unlink(missing_ok=True)

            # 无 BGM 或 BGM 处理失败：直接复制拼接后的视频
            if not has_bgm:
                import shutil
                shutil.copy2(temp_video, output_path)
                temp_video.unlink(missing_ok=True)

            # 验证输出
            if result.returncode != 0:
                print(f"[ERROR] Final stderr:\n{result.stderr}")
                return {"success": False, "message": f"合成失败: {result.stderr[:500]}"}

            if not output_path.exists() or output_path.stat().st_size < 1000:
                return {"success": False, "message": "生成的视频文件无效"}


            # 获取时长
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
                    estimated = len(video_files) * 5.0
                    if duration > estimated * 10:
                        duration = estimated
                else:
                    duration = len(video_files) * 5.0
            except:
                duration = len(video_files) * 5.0

            preview_url = f"/storage/projects/{project_id}/output/preview.mp4"
            return {
                "success": True,
                "preview_url": preview_url,
                "duration": duration,
                "status": "preview_ready",
                "message": f"粗剪完成，时长: {duration:.1f}秒"
            }

        except Exception as e:
            return {"success": False, "message": f"合成异常: {str(e)}"}

    def rough_cut_v2(self, project_id: str, segment_sequence: List[str],
                  bgm_url: Optional[str] = None,
                  bgm_volume: float = 1.0,
                  subtitle_enabled: bool = False,
                  tts_voice: Optional[str] = None,
                  digital_human: Optional[str] = None,
                  mode: int = 0) -> Dict[str, Any]:
        """
        使用 ffmpeg 拼接视频片段，支持字幕烧录和TTS+数字人（仅产品介绍模式）。
        """
        import subprocess
        import time
        import math

        video_files = []
        for seg_id in segment_sequence:
            seg_meta = self._segments.get(project_id, {}).get(seg_id)
            if not seg_meta or not seg_meta.get("video_url"):
                continue
            abs_path = self._storage.get_absolute_path(seg_meta["video_url"])
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

            # 第一步：拼接视频
            temp_video = output_path.parent / "temp_video.mp4"
            cmd_video = [
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', str(concat_list_path).replace('\\', '/'),
                '-c:v', 'libx264', '-preset', 'fast', '-crf', '23',
                '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart',
                str(temp_video).replace('\\', '/')
            ]
            result = subprocess.run(cmd_video, capture_output=True, text=True, timeout=300)
            concat_list_path.unlink(missing_ok=True)

            if result.returncode != 0:
                return {"success": False, "message": f"视频拼接失败: {result.stderr[:500]}"}
            if not temp_video.exists() or temp_video.stat().st_size < 1000:
                return {"success": False, "message": "视频拼接输出无效"}

            # 获取视频信息
            vinfo = {"width": 1920, "height": 1080, "duration": 0}
            try:
                probe = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                     '-of', 'default=noprint_wrappers=1:nokey=1',
                     str(temp_video).replace('\\', '/')],
                    capture_output=True, text=True, timeout=30
                )
                if probe.returncode == 0:
                    vinfo["duration"] = float(probe.stdout.strip())
            except:
                vinfo["duration"] = len(video_files) * 5.0

            try:
                probe_v = subprocess.run(
                    ['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                     '-show_entries', 'stream=width,height', '-of', 'csv=s=x:p=0',
                     str(temp_video).replace('\\', '/')],
                    capture_output=True, text=True, timeout=30
                )
                if probe_v.returncode == 0:
                    wh = probe_v.stdout.strip().split('x')
                    if len(wh) == 2:
                        vinfo["width"] = int(wh[0])
                        vinfo["height"] = int(wh[1])
            except:
                pass

            # 第二步：字幕/TTS/数字人处理
            temp_processed = output_path.parent / "temp_processed.mp4"
            scene_anno = self._build_scene_annotation(project_id)

            need_tts_avatar = (mode == 0 and tts_voice is not None and digital_human is not None)

            if need_tts_avatar:
                print(f"[INFO] 产品介绍模式，启用TTS({tts_voice}) + 数字人({digital_human})")
                ok = self._tts_and_avatar(
                    merged=str(temp_video), vinfo=vinfo, scene_anno=scene_anno,
                    output=str(temp_processed), tts_voice=tts_voice, digital_human=digital_human
                )
                if ok and temp_processed.exists():
                    temp_video = temp_processed
                else:
                    print("[WARN] TTS+数字人处理失败，回退到基础版本")
            elif subtitle_enabled and scene_anno:
                print("[INFO] 启用字幕烧录")
                ok = self._gene_subtitle(
                    merged=str(temp_video), vinfo=vinfo, scene_anno=scene_anno, output=str(temp_processed)
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
    def publish_video(self, project_id: str, title: str, description: str,
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
    # 7. 作品库查询（保留）
    # ------------------------------------------------------------------
    def get_personal_works(self, user_id: str, keyword: Optional[str] = None,
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

    def get_case_library(self, category: Optional[str] = None,
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

# ==============================================================================
# 5. 全局主题与共享组件
# ==============================================================================

def apply_global_theme() -> None:
    ui.colors(primary=THEME_COLORS["primary"], secondary=THEME_COLORS["secondary"], accent=THEME_COLORS["accent"],
              dark=THEME_COLORS["dark"], positive=THEME_COLORS["primary"], negative=THEME_COLORS["danger"],
              info="#3b82f6", warning="#f59e0b")
    ui.add_css(f"""
    body {{ background-color: {THEME_COLORS["dark"]} !important; color: {THEME_COLORS["text_primary"]} !important; font-family: 'Inter', 'Noto Sans SC', system-ui, sans-serif; }}
    .fhfp-card {{ background-color: {THEME_COLORS["secondary"]} !important; border-radius: 12px !important; border: 1px solid {THEME_COLORS["border"]} !important; }}
    .fhfp-nav {{ background-color: rgba(15, 23, 42, 0.95) !important; backdrop-filter: blur(12px) !important; border-bottom: 1px solid {THEME_COLORS["border"]} !important; }}
    .upload-dropzone {{ border: 2px dashed {THEME_COLORS["border"]} !important; border-radius: 8px !important; transition: all 0.3s ease; }}
    .upload-dropzone:hover {{ border-color: {THEME_COLORS["primary"]} !important; background-color: rgba(74, 222, 128, 0.05) !important; }}
    """)




# ==============================================================================
# 6. 抽象基类 BasePage
# ==============================================================================

class BasePage(ABC):
    """
    所有页面的抽象基类。

    设计原则：
      - 每个页面是一个类，在 __init__ 中保存对关键 UI 元素的引用
      - 所有事件响应是类的 bound method（self.xxx），彻底消灭闭包
      - build() 方法负责绘制页面，由 @ui.page 路由处理器调用
    """

    def __init__(self):
        self.state = AppState()

    @abstractmethod
    def build(self) -> None:
        """子类必须实现此方法以构建页面 UI"""
        raise NotImplementedError

    def _check_auth(self, redirect_path: str = '/') -> bool:
        """通用鉴权检查"""
        if not self.state.is_authenticated():
            ui.notify("请先登录", type="warning")
            ui.navigate.to(redirect_path)
            return False
        return True

    def _check_project(self, redirect_path: str = '/create') -> bool:
        """检查是否已选择模式"""
        if self.state.current_project.mode is None:
            ui.notify("请先选择创作模式", type="warning")
            ui.navigate.to(redirect_path)
            return False
        return True

    def _get_backend_base_url(self):
        if hasattr(self,"base_url"):
            return self.base_url
        else:
            return ""

# ==============================================================================
# 7. 页面实现
# ==============================================================================

# ------------------------------------------------------------------------------
# 7.1 首页
# ------------------------------------------------------------------------------

class LandingPage(BasePage):
    def build(self) -> None:
        apply_global_theme()
        NavigationBar(self.state)

        with ui.column().classes('w-full items-center justify-center min-h-screen pt-20 pb-12'):
            ui.icon('auto_awesome', size='64px').classes('text-green-400 mb-6 animate-pulse')
            ui.label('智能创作平台').classes('text-5xl font-bold text-white mb-3 text-center')
            ui.label(APP_TITLE).classes('text-xl text-green-400 font-medium mb-6 tracking-widest')
            ui.label('利用AI技术，让农产品广告创作更简单、更高效。从创意到成品，一站式解决方案。').classes(
                'text-center text-slate-400 mb-10 max-w-2xl text-lg leading-relaxed px-4')
            with ui.row().classes('gap-4 mb-16'):
                ui.button('开始创作', on_click=lambda: ui.navigate.to('/create')).classes(
                    'bg-green-500 hover:bg-green-400 text-black font-bold px-8 py-3 rounded-lg shadow-lg shadow-green-500/20 transition')
                ui.button('查看案例', on_click=lambda: ui.navigate.to('/cases')).props('outline').classes(
                    'text-green-400 border-green-400 hover:bg-green-500/10 px-8 py-3 rounded-lg transition')

        with ui.column().classes('w-full max-w-5xl mx-auto px-6 pb-20'):
            ui.label('项目介绍').classes('text-3xl font-bold text-white mb-6 border-l-4 border-green-500 pl-4')
            ui.label(
                '本项目针对当前"三农"领域自媒体带货视频存在的审美差异大、广告感强、内容同质化严重等问题，基于VidiCraft技术栈升级，构建面向个体农户的AI辅助创作系统。系统提供"产品介绍"与"剧情设计"双模式，通过两层智能体协作，将创意构思转化为可直接执行的拍摄脚本与粗剪成片。').classes(
                'text-slate-400 leading-relaxed mb-8')
            with ui.row().classes('w-full gap-6'):
                features = [("psychology", "双模式创作", "产品介绍模式侧重卖点拆解，剧情模式打造人物IP"),
                            ("smart_toy", "AI双智能体", "Agent1挖掘亮点与摘要，Agent2输出结构化拍摄建议"),
                            ("video_library", "案例驱动", "内置优质农产品带货案例库，提供可复用的创作模板"),
                            ("cloud_upload", "云端粗剪", "支持片段上传、自动字幕、BGM合成与一键发布")]
                for icon, title, desc in features:
                    with ui.card().classes('fhfp-card flex-1 p-5'):
                        ui.icon(icon, size='40px').classes('text-green-400 mb-3')
                        ui.label(title).classes('text-lg font-bold text-white mb-2')
                        ui.label(desc).classes('text-sm text-slate-400 leading-relaxed')

        with ui.column().classes('w-full max-w-5xl mx-auto px-6 pb-24'):
            ui.label('团队介绍').classes('text-3xl font-bold text-white mb-6 border-l-4 border-green-500 pl-4')
            team = [("王语其", "项目负责人 / NiceGUI-FastAPI前后端交互框架搭建/LLM量化与微调 / 双层数据集构建 / 数据库前端与交互头设计"),
                    ("蒋思卿", "数据采集（37条） / PostgresQL数据库搭建与摘要语义匹配 / TTS语音模块（接口）/ 字幕生成 / 数字人主播形象设计&画面融合"),
                    ("曾俊达", "数据采集（42条）/TTS语音模块（本地模型）"),
                    ("崔庭赫", "数据采集（39条）"),("闫宇深", "团队调剂（活动位）"),
                    ("韦庆腾", "问卷调研 / LOGO设计/数据采集（60条）"),
                    ("王珏洛睿", "问卷调研 / LOGO设计/数据采集（18条）")]
            with ui.row().classes('w-full gap-4 flex-wrap'):
                for name, role in team:
                    with ui.card().classes('fhfp-card p-4 min-w-[200px] flex-1'):
                        ui.label(name).classes('text-white font-bold text-lg mb-1')
                        ui.label(role).classes('text-green-400 text-sm')
                        ui.label('华南理工大学').classes('text-slate-500 text-xs mt-2')

        with ui.footer().classes('w-full bg-slate-900 border-t border-slate-800 py-6'):
            with ui.row().classes('w-full max-w-6xl mx-auto px-6 justify-between items-center'):
                ui.label(f'© 2026 {APP_TITLE}. All rights reserved.').classes('text-slate-500 text-sm')
                ui.label('Synthetic Design of AI System Course Project').classes('text-slate-600 text-xs')


# ------------------------------------------------------------------------------
# 7.2 模式选择页
# ------------------------------------------------------------------------------

class ModeSelectionPage(BasePage):
    def build(self) -> None:
        apply_global_theme()
        NavigationBar(self.state)

        # 自动创建项目
        if self.state.current_project.project_id is None:
            try:
                resp = self.state.backend.create_project(
                    user_id=self.state.user.get("user_id", "anonymous") if self.state.user else "anonymous",
                    mode=0, title="未命名项目", description=""
                )
                if resp.get("success"): self.state.set_project_id(resp["project_id"])
            except Exception as e:
                logger.error(f"Auto create project failed: {e}")

        with ui.column().classes('w-full items-center justify-center min-h-screen pt-24 px-6'):
            ui.label('选择您的创作模式').classes('text-4xl font-bold text-white mb-3')
            ui.label('不同的模式将决定AI助手的对话策略与成片风格').classes('text-slate-400 mb-12')
            with ui.row().classes('w-full max-w-4xl gap-8 justify-center'):
                with ui.card().classes(
                        'fhfp-card flex-1 p-8 cursor-pointer hover:scale-[1.02] transition duration-300').on('click',
                                                                                                             lambda: self._select_mode(
                                                                                                                     CreationMode.PRODUCT_INTRO)):
                    with ui.column().classes('items-center text-center'):
                        ui.icon('inventory_2', size='56px').classes('text-green-400 mb-4')
                        ui.label('产品介绍').classes('text-2xl font-bold text-white mb-3')
                        ui.label('结构化讲解与卖点分析').classes('text-green-400 text-sm font-medium mb-4')
                        ui.label(
                            '适用于：产品推广、产地直发、促销引流、竞品对比。\nAI将协助您拆解产品卖点，生成对比性强、信息密度高的讲解脚本。').classes(
                            'text-slate-400 text-sm leading-relaxed whitespace-pre-line')
                        ui.badge('MODE 0', color='grey').classes('mt-4')
                with ui.card().classes(
                        'fhfp-card flex-1 p-8 cursor-pointer hover:scale-[1.02] transition duration-300').on('click',
                                                                                                             lambda: self._select_mode(
                                                                                                                     CreationMode.STORY_DESIGN)):
                    with ui.column().classes('items-center text-center'):
                        ui.icon('movie', size='56px').classes('text-green-400 mb-4')
                        ui.label('剧情设计').classes('text-2xl font-bold text-white mb-3')
                        ui.label('叙事型故事与人物IP').classes('text-green-400 text-sm font-medium mb-4')
                        ui.label(
                            '适用于：品牌故事、新农人IP、长期内容线。\nAI将挖掘您生活中的闪光点，编织有温度、有共鸣的叙事脚本。\n支持TTS配音与数字人主播。').classes(
                            'text-slate-400 text-sm leading-relaxed whitespace-pre-line')
                        ui.badge('MODE 1', color='green').classes('mt-4')

    def _select_mode(self, mode: CreationMode) -> None:
        self.state.set_mode(mode)
        ui.notify(f"已选择: {'产品介绍' if mode == CreationMode.PRODUCT_INTRO else '剧情设计'} 模式", type="positive")
        ui.navigate.to('/agent1')


# ------------------------------------------------------------------------------
# 7.3 智能体1号 - 聊天与摘要 (重构版，无闭包)
# ------------------------------------------------------------------------------

class Agent1ChatPage(BasePage):
    """
    智能体1号交互页（类架构）

    状态管理：
      - chat_history: 实例属性，保存当前页面的对话历史
      - current_highlights: 实例属性，保存当前提取的亮点
      - 所有 UI 容器引用保存在实例属性中，供方法间调用
    """

    def __init__(self):
        super().__init__()
        self.chat_history: List[Dict[str, str]] = []
        self.current_highlights: List[str] = []
        # UI 引用将在 build() 中初始化
        self.messages_container: Optional[ui.column] = None
        self.highlights_container: Optional[ui.column] = None
        self.msg_input: Optional[ui.textarea] = None

    def build(self) -> None:
        apply_global_theme()
        NavigationBar(self.state)

        if not self._check_project('/create'):
            return

        with ui.row().classes('w-full h-screen pt-16'):
            # 左侧边栏
            with ui.column().classes('w-1/4 h-full bg-slate-800/50 border-r border-slate-700 p-4 hidden lg:flex'):
                ui.label('当前项目').classes('text-white font-bold mb-4')
                with ui.card().classes('bg-slate-700/50 p-3 w-full mb-4'):
                    mode_text = "产品介绍" if self.state.current_project.mode == CreationMode.PRODUCT_INTRO else "剧情设计"
                    ui.label(f'模式: {mode_text}').classes('text-green-400 text-sm font-bold')
                    ui.label(f'ID: {self.state.current_project.project_id}').classes('text-slate-500 text-xs mt-1')
                ui.label('已提取亮点').classes('text-white font-bold mb-2 text-sm')
                self.highlights_container = ui.column().classes('w-full gap-2')

            # 右侧聊天区
            with ui.column().classes('flex-1 h-full flex flex-col p-6'):
                with ui.row().classes('w-full items-center mb-4 pb-4 border-b border-slate-700'):
                    ui.icon('support_agent', size='32px').classes('text-green-400 mr-3')
                    with ui.column():
                        ui.label('小耘助手').classes('text-xl font-bold text-white')
                        ui.label('亮点挖掘与摘要生成').classes('text-sm text-slate-400')

                # 消息区
                self.messages_container = ui.column().classes('w-full flex-1 overflow-y-auto gap-4 mb-4 pr-2')
                self._render_assistant_message(
                    "你好！我是小耘，您的农产品广告创作助手。\n请告诉我您要推广的产品/故事主题，我会帮您挖掘亮点、梳理叙事逻辑。")

                # 输入区
                with ui.row().classes('w-full gap-2 items-end'):
                    self.msg_input = ui.textarea(placeholder='输入您的问题，如：我要推广山东烟台的红富士苹果...').props(
                        'outlined').classes('flex-1').style('min-height: 60px')
                    with ui.column().classes('gap-2'):
                        ui.button(icon='send', on_click=self._send_message).classes(
                            'bg-green-500 text-black rounded-full w-12 h-12')
                        ui.button('生成摘要', on_click=self._generate_summary).classes(
                            'bg-slate-700 text-white text-xs px-3 py-2 rounded')

    # ------------------------------------------------------------------
    # 以下全部为类方法（bound methods），无闭包，无 unresolved reference
    # ------------------------------------------------------------------

    def _render_user_message(self, text: str) -> None:
        """渲染用户消息"""
        with self.messages_container:
            with ui.row().classes('w-full justify-end'):
                ui.label(text).classes(
                    'bg-green-600 text-white p-3 rounded-2xl rounded-tr-sm max-w-[80%] whitespace-pre-wrap')

    def _render_assistant_message(self, text: str) -> None:
        """渲染助手消息"""
        with self.messages_container:
            with ui.row().classes('w-full justify-start'):
                with ui.row().classes(
                        'bg-slate-700 text-slate-200 p-3 rounded-2xl rounded-tl-sm max-w-[80%] whitespace-pre-wrap'):
                    ui.markdown(text)

    def _update_highlights_ui(self) -> None:
        """刷新左侧亮点面板"""
        self.highlights_container.clear()
        with self.highlights_container:
            for hl in self.current_highlights:
                with ui.card().classes('bg-slate-700/80 p-2 w-full'):
                    ui.label(hl).classes('text-green-300 text-xs leading-relaxed')

    def _send_message(self) -> None:
        """发送消息"""
        text = self.msg_input.value
        if not text:
            return
        self.msg_input.value = ""

        self._render_user_message(text)
        self.chat_history.append({"role": "user", "content": text})

        try:
            resp = self.state.backend.agent1_chat(
                project_id=self.state.current_project.project_id or "mock_proj",
                message=text,
                history=self.chat_history,
                mode_id=self.state.get_mode_id(),
            )
            if resp.get("success"):
                reply = resp.get("reply", "")
                highlights = resp.get("highlights", [])
                suggestions = resp.get("suggested_questions", [])

                self.chat_history.append({"role": "assistant", "content": reply})

                if highlights:
                    self.current_highlights = highlights
                    self._update_highlights_ui()

                full_reply = reply
                if suggestions:
                    full_reply += "\n\n**建议追问：**\n" + "\n".join(f"- {q}" for q in suggestions)
                self._render_assistant_message(full_reply)
            else:
                self._render_assistant_message("抱歉，服务暂时繁忙，请稍后重试。")
        except Exception as e:
            logger.error(f"Agent1 chat error: {e}")
            self._render_assistant_message("网络错误，请检查连接。")

    def _generate_summary(self) -> None:
        """生成摘要按钮回调"""
        if not self.current_highlights:
            ui.notify("请先与助手对话，提取至少一个亮点", type="warning")
            return

        try:
            resp = self.state.backend.agent1_generate_summary(
                project_id=self.state.current_project.project_id or "mock_proj",
                highlights=self.current_highlights,
                custom_notes="请生成适合短视频口播的摘要"
            )
            if resp.get("success"):
                summary = resp.get("summary", "")
                self.state.current_project.agent1_summary = summary
                self.state.current_project.agent1_highlights = self.current_highlights

                with ui.dialog() as dlg, ui.card().classes('fhfp-card w-[600px] max-w-[90vw]'):
                    ui.label('摘要已生成').classes('text-xl font-bold text-white mb-2')
                    ui.label('请确认或修改摘要内容，确认后将进入拍摄建议生成。').classes('text-sm text-slate-400 mb-4')
                    summary_input = ui.textarea(value=summary).classes('w-full mb-4').style('min-height: 120px')
                    with ui.row().classes('w-full justify-end gap-2'):
                        ui.button('返回修改', on_click=dlg.close).props('flat')
                        ui.button('确认并继续',
                                  on_click=lambda: self._confirm_summary(dlg, summary_input.value)).classes(
                            'bg-green-500 text-black font-bold')
                dlg.open()
            else:
                ui.notify("摘要生成失败", type="negative")
        except Exception as e:
            logger.error(f"Generate summary error: {e}")
            ui.notify("生成失败，请重试", type="negative")

    def _confirm_summary(self, dlg: ui.dialog, final_summary: str) -> None:
        """确认摘要"""
        self.state.current_project.agent1_summary = final_summary
        dlg.close()
        ui.notify("进入智能体2号：拍摄建议生成", type="positive")
        ui.navigate.to('/agent2')


# ------------------------------------------------------------------------------
# 7.4 智能体2号 - 拍摄建议编辑
# ------------------------------------------------------------------------------

class Agent2EditorPage(BasePage):
    def __init__(self):
        super().__init__()
        self.shots_data: List[Dict[str, Any]] = []
        self.shots_container: Optional[ui.column] = None

    def build(self) -> None:
        apply_global_theme()
        NavigationBar(self.state)

        if not self.state.current_project.agent1_summary:
            ui.notify("请先在Agent1完成摘要生成", type="warning")
            ui.navigate.to('/agent1')
            return

        with ui.column().classes('w-full min-h-screen pt-20 pb-12 px-6 max-w-6xl mx-auto'):
            with ui.row().classes('w-full justify-between items-start mb-6'):
                with ui.column():
                    ui.label('拍摄建议生成').classes('text-3xl font-bold text-white mb-2')
                    ui.label('智能体2号基于摘要，为您规划每个镜头的画面、音频与文案').classes('text-slate-400')
                ui.button('重新生成', icon='refresh', on_click=self._regenerate_shots).props('flat').classes(
                    'text-green-400')

            with ui.card().classes('fhfp-card w-full p-4 mb-8'):
                ui.label('摘要原文').classes('text-sm text-green-400 font-bold mb-2')
                ui.label(self.state.current_project.agent1_summary).classes('text-slate-300 text-sm leading-relaxed')

            self.shots_container = ui.column().classes('w-full gap-4 mb-8')
            self._load_shots()

            with ui.row().classes('w-full justify-center gap-4'):
                ui.button('添加空白片段', icon='add', on_click=self._add_blank_shot).classes(
                    'bg-slate-700 text-white px-6')
                ui.button('确认建议，进入拍摄上传', icon='check_circle', on_click=self._confirm_shots).classes(
                    'bg-green-500 text-black font-bold px-8')

    def _load_shots(self) -> None:
        if not self.shots_data:
            try:
                resp = self.state.backend.agent2_generate_shots(
                    project_id=self.state.current_project.project_id or "mock_proj",
                    summary=self.state.current_project.agent1_summary,
                    mode_id=self.state.get_mode_id(),
                    style_preference="朴实" if self.state.current_project.mode == CreationMode.PRODUCT_INTRO else "温馨"
                )
                if resp.get("success"):
                    self.shots_data = resp.get("shots", [])
                    self._render_shots()
                else:
                    ui.notify("生成失败", type="negative")
            except Exception as e:
                logger.error(f"Agent2 generate error: {e}")
                ui.notify("网络错误", type="negative")
        else:
            self._render_shots()

    def _render_shots(self) -> None:
        self.shots_container.clear()
        with self.shots_container:
            for idx, shot in enumerate(self.shots_data):
                self._render_shot_card(idx, shot)

    def _render_shot_card(self, idx: int, shot: Dict[str, Any]) -> None:
        with self.shots_container:
            with ui.card().classes('fhfp-card w-full p-5'):
                with ui.row().classes('w-full justify-between items-center mb-4 pb-3 border-b border-slate-700'):
                    with ui.row().classes('items-center gap-3'):
                        ui.badge(f'#{idx + 1}', color='green').classes('text-sm font-bold')
                        ui.label(f'预估 {shot.get("duration_hint", 5)}s').classes('text-slate-500 text-xs')
                    with ui.row().classes('gap-1'):
                        ui.button(icon='arrow_upward', on_click=lambda i=idx: self._move_shot(i, -1)).props(
                            'flat dense size=sm').classes('text-slate-400')
                        ui.button(icon='arrow_downward', on_click=lambda i=idx: self._move_shot(i, 1)).props(
                            'flat dense size=sm').classes('text-slate-400')
                        ui.button(icon='delete', on_click=lambda i=idx: self._delete_shot(i)).props(
                            'flat dense size=sm').classes('text-red-400')
                with ui.row().classes('w-full gap-4'):
                    with ui.column().classes('flex-1'):
                        ui.label('画面建议').classes('text-xs text-green-400 font-bold mb-1')
                        ui.textarea(value=shot.get("scene", "")).classes('w-full text-sm').style('min-height: 80px')
                    with ui.column().classes('flex-1'):
                        ui.label('音频建议').classes('text-xs text-blue-400 font-bold mb-1')
                        ui.textarea(value=shot.get("audio", "")).classes('w-full text-sm').style('min-height: 80px')
                    with ui.column().classes('flex-1'):
                        ui.label('文案/旁白').classes('text-xs text-yellow-400 font-bold mb-1')
                        ui.textarea(value=shot.get("copy", "")).classes('w-full text-sm').style('min-height: 80px')

    def _move_shot(self, idx: int, direction: int) -> None:
        new_idx = idx + direction
        if 0 <= new_idx < len(self.shots_data):
            self.shots_data[idx], self.shots_data[new_idx] = self.shots_data[new_idx], self.shots_data[idx]
            self._render_shots()
            ui.notify(f"已调整至位置 {new_idx + 1}", type="info")

    def _delete_shot(self, idx: int) -> None:
        if len(self.shots_data) <= 1:
            ui.notify("至少保留一个片段", type="warning")
            return
        self.shots_data.pop(idx)
        # 先通知再渲染，避免 slot 被删除后的访问问题
        ui.notify("已删除", type="info")
        self._render_shots()

    def _add_blank_shot(self) -> None:
        if len(self.shots_data) >= MAX_SEGMENTS:
            ui.notify(f"最多支持 {MAX_SEGMENTS} 个片段", type="warning")
            return
        new_shot = {
            "shot_id": f"shot_manual_{uuid.uuid4().hex[:4]}",
            "order": len(self.shots_data),
            "scene": "（请填写画面建议）",
            "audio": "（请填写音频建议）",
            "copy": "（请填写文案/旁白）",
            "duration_hint": 5
        }
        self.shots_data.append(new_shot)
        self._render_shots()
        ui.notify("已添加空白片段", type="positive")

    def _regenerate_shots(self) -> None:
        self.shots_data = []
        self._load_shots()

    def _confirm_shots(self) -> None:
        segments = []
        for idx, shot in enumerate(self.shots_data):
            seg = VideoSegment(
                segment_id=shot.get("shot_id", f"seg_{uuid.uuid4().hex[:8]}"),
                project_id=self.state.current_project.project_id or "",
                order=idx,
                suggestion=ShotSuggestion(
                    shot_id=shot.get("shot_id", ""),
                    scene=shot.get("scene", ""),
                    audio=shot.get("audio", ""),
                    copy=shot.get("copy", ""),
                    order=idx,
                    duration_hint=shot.get("duration_hint", 5)
                ),
                scene=shot.get("scene", ""),
                audio=shot.get("audio", ""),
                copy=shot.get("copy", "")
            )
            segments.append(seg)
        self.state.current_project.segments = segments
        self.state.current_project.shot_suggestions = [s.suggestion for s in segments]
        ui.notify(f"已确认 {len(segments)} 个片段，进入视频上传", type="positive")
        ui.navigate.to('/upload')


# ------------------------------------------------------------------------------
# 7.5 视频上传与粗剪 (Upload & Rough Cut)
# ------------------------------------------------------------------------------

class UploadEditorPage(BasePage):
    """
    视频上传、粗剪合成、下载/发布 三合一页面
    """

    def __init__(self):
        super().__init__()
        self.segments: List[VideoSegment] = []
        self.preview_url: Optional[str] = None
        self.bgm_url: Optional[str] = None
        self.bgm_volume: float = 0.3
        self.subtitle_on: bool = True

        # UI 引用
        self.segments_container: Optional[ui.column] = None
        self.preview_card: Optional[ui.card] = None

    def build(self):
        """构建完整页面"""
        self._init_segments()

        with ui.row().classes('w-full h-screen pt-16 gap-0'):
            # ========== 左侧：片段列表与上传 ==========
            with ui.column().classes('w-1/3 h-full bg-slate-800/40 border-r border-slate-700 overflow-y-auto p-4'):
                ui.label('分镜视频上传').classes('text-lg font-bold text-white mb-2')
                ui.label('按顺序上传每个片段，支持调整顺序').classes('text-xs text-slate-500 mb-4')

                self.segments_container = ui.column().classes('w-full gap-3')
                self._render_segments()

                ui.button('添加新片段', icon='add', on_click=self._add_segment).classes(
                    'w-full bg-slate-700 hover:bg-slate-600 text-white mt-4')

            # ========== 右侧：预览与操作 ==========
            with ui.column().classes('flex-1 h-full overflow-y-auto p-6'):
                # 预览播放器
                self.preview_card = ui.card().classes(
                    'w-full aspect-video mb-6 flex items-center justify-center bg-slate-800 border-slate-700')
                self._update_preview()

                # 配置区
                with ui.column().classes('w-full gap-4'):
                    # BGM
                    with ui.card().classes('w-full p-4 bg-slate-800 border-slate-700'):
                        ui.label('背景音乐 (BGM)').classes('text-sm font-bold text-white mb-3')
                        with ui.row().classes('w-full items-center gap-4'):
                            ui.upload(label='上传BGM (MP3/WAV)', auto_upload=True,
                                      on_upload=self._handle_bgm_upload).classes('flex-1').props(
                                'accept=.mp3,.wav,.m4a color=green')
                            vol = ui.slider(min=0, max=10, step=0.01, value=1).classes('w-32')  # 0.0~10.0
                            ui.label().bind_text_from(vol, 'value',
                                                      lambda v: f'{float(v) * 100:.0f}%').classes(
                                'text-slate-400 text-sm')

                    # 选项
                    with ui.card().classes('w-full p-4 bg-slate-800 border-slate-700'):
                        ui.label('基础选项').classes('text-sm font-bold text-white mb-3')
                        ui.switch('自动生成字幕', value=self.subtitle_on).classes('text-white')

                    # 操作按钮
                    with ui.row().classes('w-full justify-end gap-3 mt-4'):
                        ui.button('预览合成', icon='play_circle', on_click=self._do_rough_cut).classes(
                            'bg-green-600 hover:bg-green-500 text-white px-6')
                        ui.button('下载到本地', icon='download', on_click=self._download_preview).classes(
                            'bg-blue-600 hover:bg-blue-500 text-white px-6')
                        ui.button('发布作品', icon='publish', on_click=self._publish_work).classes(
                            'bg-green-500 hover:bg-green-400 text-black font-bold px-6')

    # ==================== 片段管理 ====================

    def _init_segments(self):
        """初始化片段列表（从state加载或创建默认4个）"""
        existing = getattr(self.state, 'current_project', None)
        if existing and hasattr(existing, 'segments') and existing.segments:

            # 修复②：从后端真实存储中恢复 video_url（页面跳转后 state 可能重置为新的 AppState 实例）
            backend_segments = self.state.backend._segments.get(
                self.state.current_project.project_id, {}
            )

            if backend_segments!={}:
                # 修复①：用 deepcopy 替代 asdict，避免 suggestion 字段类型丢失（asdict 会把 ShotSuggestion 变成 dict）
                import copy
                self.segments = [copy.deepcopy(s) for s in self.state.current_project.segments]
                for i in range(len(self.segments)):
                    meta = backend_segments.get(self.segments[i].segment_id)
                    # 有可能只有文本描述但还没有上传视频
                    if meta and meta.get("video_url"):
                        self.segments[i].video_url = meta["video_url"]
                        self.segments[i].video_duration = meta.get("duration", 5.0)
            else:
                for s in existing.segments:
                    self.segments.append(VideoSegment(
                        segment_id=s.segment_id,
                        order=s.order,
                        video_url=s.video_url,
                        scene=getattr(s, 'scene', '') or getattr(s, 'suggestion', None).scene if hasattr(s,
                                                                                                         'suggestion') else '',
                        audio=getattr(s, 'audio', '') or getattr(s, 'suggestion', None).audio if hasattr(s,
                                                                                                         'suggestion') else '',
                        copy=getattr(s, 'copy', '') or getattr(s, 'suggestion', None).copy if hasattr(s,
                                                                                                      'suggestion') else ''
                    ))
        else:
            for i in range(4):
                self.segments.append(VideoSegment(
                    segment_id=f"seg_{uuid.uuid4().hex[:6]}",
                    order=i
                ))

    def _render_segments(self):
        """渲染左侧片段列表"""
        if not self.segments_container:
            return
        self.segments_container.clear()

        with self.segments_container:
            for idx, seg in enumerate(self.segments):
                with ui.card().classes('w-full p-3 bg-slate-700 border-slate-600'):
                    # 头部：序号 + 操作按钮
                    with ui.row().classes('w-full justify-between items-center mb-2'):
                        with ui.row().classes('items-center gap-2'):
                            ui.badge(f'{idx + 1}', color='green').classes('text-xs')
                            status_icon = 'check_circle' if seg.video_url else 'hourglass_empty'
                            status_color = 'text-green-400' if seg.video_url else 'text-slate-500'
                            ui.icon(status_icon, size='16px').classes(status_color)

                        with ui.row().classes('gap-1'):
                            ui.button(icon='arrow_upward', on_click=lambda i=idx: self._move_segment(i, -1)).props(
                                'flat dense size=sm').classes('text-slate-400')
                            ui.button(icon='arrow_downward', on_click=lambda i=idx: self._move_segment(i, 1)).props(
                                'flat dense size=sm').classes('text-slate-400')
                            ui.button(icon='delete', on_click=lambda i=idx: self._delete_segment(i)).props(
                                'flat dense size=sm').classes('text-red-400')

                    # 上传区
                    if seg.video_url:
                        with ui.row().classes('w-full items-center gap-2 mb-2'):
                            ui.icon('videocam', size='20px').classes('text-green-400')
                            ui.label(seg.segment_id or '已上传').classes('text-xs text-green-400 truncate flex-1')
                            ui.button('替换', on_click=lambda i=idx: self._open_upload_dialog(i)).props(
                                'flat dense size=sm').classes('text-green-400')
                    else:
                        with ui.column().classes(
                                'w-full border-2 border-dashed border-slate-600 rounded p-3 items-center cursor-pointer'
                        ).on('click', lambda i=idx: self._open_upload_dialog(i)):
                            ui.icon('add', size='32px').classes('text-slate-500')
                            ui.label('点击上传视频').classes('text-xs text-slate-500')

                    # 描述输入（绑定到对象）
                    with ui.column().classes('w-full gap-2 mt-2'):
                        ui.input('画面', value=seg.scene).bind_value(seg, 'scene').classes(
                            'w-full text-xs').props('dense dark')
                        ui.input('音频', value=seg.audio).bind_value(seg, 'audio').classes(
                            'w-full text-xs').props('dense dark')
                        ui.input('文案', value=seg.copy).bind_value(seg, 'copy').classes(
                            'w-full text-xs').props('dense dark')

    def _open_upload_dialog(self, idx: int):
        """打开上传对话框"""
        with ui.dialog() as dlg, ui.card().classes('p-6 bg-slate-800 border-slate-700'):
            ui.label(f'上传片段 #{idx + 1}').classes('text-white font-bold mb-4')
            ui.upload(auto_upload=True, on_upload=lambda e: self._handle_segment_upload(e, idx, dlg)).classes(
                'w-64').props('accept=.mp4,.mov,.avi color=green')
            ui.button('取消', on_click=dlg.close).props('flat').classes('mt-4 text-white')
        dlg.open()

    async def _handle_segment_upload(self, e: events.UploadEventArguments, idx: int, dlg: ui.dialog):
        """处理片段上传：真实存储到磁盘"""
        try:
            if not hasattr(e, 'file'):
                ui.notify("上传组件异常", type="negative")
                return

            file_data = await e.file.read()
            if len(file_data) == 0:
                ui.notify("文件读取失败", type="negative")
                return

            seg = self.segments[idx]
            ui.notify(f"正在上传 {e.file.name} ...", type="info")

            resp = self.state.backend.upload_segment_video(
                project_id=self.state.current_project.project_id,
                segment_id=seg.segment_id,
                file_data=file_data,
                file_name=e.file.name,
                content_type=e.file.content_type
            )

            if resp.get("success"):
                seg.video_url = resp.get("video_url")
                seg.file_name = e.file.name
                ui.notify("上传成功！", type="positive")
                dlg.close()
                self._render_segments()
            else:
                ui.notify(f"上传失败: {resp.get('message', '未知错误')}", type="negative")
                dlg.close()
        except Exception as ex:
            ui.notify(f"上传异常: {str(ex)}", type="negative")
            dlg.close()

    async def _handle_bgm_upload(self, e: events.UploadEventArguments):
        """处理BGM上传"""
        try:
            file_data = await e.file.read()
            resp = self.state.backend._storage.save_bgm(
                self.state.current_project.project_id,
                file_data,
                e.file.name
            )
            self.bgm_url = resp
            ui.notify("背景音乐已上传", type="positive")
        except Exception as ex:
            ui.notify(f"BGM上传失败: {str(ex)}", type="negative")

    def _move_segment(self, idx: int, direction: int):
        """移动片段顺序"""
        new_idx = idx + direction
        if 0 <= new_idx < len(self.segments):
            self.segments[idx], self.segments[new_idx] = self.segments[new_idx], self.segments[idx]
            for i, seg in enumerate(self.segments):
                seg.order = i
            self._render_segments()
            ui.notify(f"已调整至位置 {new_idx + 1}", type="info")

    def _delete_segment(self, idx: int):
        """删除片段"""
        if len(self.segments) <= 1:
            ui.notify("至少保留一个片段", type="warning")
            return
        self.segments.pop(idx)
        for i, seg in enumerate(self.segments):
            seg.order = i
        self._render_segments()
        ui.notify("片段已删除", type="info")

    def _add_segment(self):
        """添加空白片段"""
        if len(self.segments) >= 20:
            ui.notify("最多支持20个片段", type="warning")
            return
        self.segments.append(VideoSegment(
            segment_id=f"seg_{uuid.uuid4().hex[:6]}",
            order=len(self.segments)
        ))
        self._render_segments()
        ui.notify("已添加新片段", type="positive")

    # ==================== 功能2：视频合成与预览 ====================

    def _do_rough_cut(self):
        """执行粗剪：按编号顺序真实拼接"""
        seg_ids = [s.segment_id for s in self.segments if s.video_url]
        if not seg_ids:
            ui.notify("请先上传至少一个视频片段", type="warning")
            return

        ui.notify("正在合成视频，请稍候（可能需要几秒到几分钟）...", type="info")


        try:
            resp = self.state.backend.rough_cut(
                project_id=self.state.current_project.project_id,
                segment_sequence=seg_ids,
                bgm_url=self.bgm_url,
                bgm_volume=self.bgm_volume,
                subtitle_enabled=self.subtitle_on
            )

            if resp.get("success"):
                self.preview_url = resp.get("preview_url")
                self._update_preview()
                duration = resp.get('duration', 0)
                ui.notify(f"合成完成！总时长: {duration:.1f}秒", type="positive")
                self._update_preview()

            else:
                ui.notify(f"合成失败: {resp.get('message')}", type="negative")
        except Exception as ex:
            ui.notify(f"合成异常: {str(ex)}", type="negative")

    def _update_preview(self):
        """刷新预览区 - 使用自定义播放器"""
        if not self.preview_card:
            return
        self.preview_card.clear()

        with self.preview_card:
            if self.preview_url:
                # 使用自定义播放器替代基础 ui.video
                # video_player(video_url=self.preview_url,container_classes='w-full h-full rounded-lg')
                ui.video(self.preview_url).classes('w-full h-full')
            else:
                with ui.column().classes('items-center justify-center h-full'):
                    ui.icon('play_circle_outline', size='48px').classes('text-slate-600 mb-2')
                    ui.label('粗剪成片预览区').classes('text-slate-500 text-lg')
                    ui.label('点击"预览合成"生成真实视频').classes('text-slate-600 text-sm mt-2')

    # ==================== 功能3：下载与发布 ====================

    def _download_preview(self):
        """真实下载：触发浏览器下载到本地目录"""
        if not self.preview_url:
            ui.notify("请先生成预览视频", type="warning")
            return

        abs_path = self.state.backend._storage.get_absolute_path(self.preview_url)
        if not abs_path.exists():
            ui.notify("预览文件不存在", type="negative")
            return

        filename = f"fhfp_{self.state.current_project.project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        ui.download(src=self.preview_url, filename=filename)
        ui.notify("下载已开始，请查看浏览器下载栏", type="positive")

    def _publish_work(self):
        """发布到个人作品库"""
        if not self.preview_url:
            ui.notify("请先生成预览视频", type="warning")
            return

        with ui.dialog() as dlg, ui.card().classes('p-6 bg-slate-800 border-slate-700 w-96'):
            ui.label('发布作品').classes('text-xl font-bold text-white mb-4')

            title_inp = ui.input('作品标题',
                                 value=getattr(self.state.current_project, 'title', None) or '未命名作品'
                                 ).classes('w-full mb-3')

            desc_inp = ui.textarea('作品描述',
                                   value=getattr(self.state.current_project, 'description', None) or ''
                                   ).classes('w-full mb-3')

            tags_inp = ui.input('标签（逗号分隔）', value='农产品, AI创作').classes('w-full mb-4')

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('取消', on_click=dlg.close).props('flat').classes('text-white')
                ui.button('确认发布', on_click=lambda: self._do_publish(
                    dlg, title_inp.value, desc_inp.value, tags_inp.value
                )).classes('bg-green-500 text-black font-bold')
        dlg.open()

    def _do_publish(self, dlg: ui.dialog, title: str, description: str, tags_str: str):
        """执行发布"""
        tags = [t.strip() for t in tags_str.split(',') if t.strip()]
        try:
            resp = self.state.backend.publish_video(
                project_id=self.state.current_project.project_id,
                title=title,
                description=description,
                tags=tags
            )
            if resp.get("success"):
                ui.notify("发布成功！已存入个人作品库", type="positive")
                dlg.close()
                # 可选：跳转到个人作品页
                # ui.navigate.to('/personal')
            else:
                ui.notify(f"发布失败: {resp.get('message')}", type="negative")
        except Exception as ex:
            ui.notify(f"发布异常: {str(ex)}", type="negative")


# ------------------------------------------------------------------------------
# 7.6 公共案例库
# ------------------------------------------------------------------------------

class CaseLibraryPage(BasePage):
    def __init__(self):
        super().__init__()
        self.filter_category: Optional[str] = None
        self.search_keyword: str = ""

    def build(self) -> None:
        apply_global_theme()
        NavigationBar(self.state)

        with ui.column().classes('w-full min-h-screen pt-24 pb-12 px-6 max-w-7xl mx-auto'):
            ui.label('农产品带货案例库').classes('text-3xl font-bold text-white mb-2')
            ui.label('精选优质农产品带货视频案例，助力您的销售').classes('text-slate-400 mb-8')

            with ui.row().classes('w-full gap-4 mb-8'):
                self.search_inp = ui.input('搜索农产品案例...').classes('flex-1').props('outlined clearable')
                self.category_sel = ui.select(['全部', '水果', '蔬菜', '粮油', '畜牧'], label='分类筛选',
                                              value='全部').classes('w-40')
                ui.button('搜索', icon='search', on_click=self._apply_filter).classes(
                    'bg-green-500 text-black font-bold')

            self.cases_grid = ui.row().classes('w-full gap-4 flex-wrap justify-center')
            self._render_cases()

    def _apply_filter(self) -> None:
        self.filter_category = None if self.category_sel.value == '全部' else self.category_sel.value
        self.search_keyword = self.search_inp.value or ""
        self._render_cases()

    def _render_cases(self) -> None:
        self.cases_grid.clear()
        items = PREBUILT_CASES
        if self.filter_category:
            items = [c for c in items if c.get("category") == self.filter_category]
        if self.search_keyword:
            kw = self.search_keyword.lower()
            items = [c for c in items if kw in c["title"].lower() or kw in c.get("description", "").lower()]
        items = sorted(items, key=lambda x: x.get("views", 0), reverse=True)

        with self.cases_grid:
            if not items:
                ui.label('未找到匹配案例').classes('text-slate-500 w-full text-center py-12')
                return
            for case in items:
                with ui.card().classes(
                        'fhfp-card w-[300px] flex-shrink-0 cursor-pointer hover:scale-[1.02] transition'):
                    with ui.row().classes(
                            'w-full h-40 bg-slate-700 rounded-lg mb-3 items-center justify-center overflow-hidden relative'):
                        ui.image(case["thumbnail"]).classes('w-full h-full object-cover')
                        ui.badge(case["duration"], color='black').classes('absolute bottom-2 right-2 text-xs')
                    ui.label(case["title"]).classes('text-white font-bold mb-1')
                    ui.label(case["description"]).classes('text-slate-400 text-sm mb-3 line-clamp-2')
                    with ui.row().classes('gap-1 mb-3'):
                        for tag in case.get("tags", []):
                            ui.badge(tag, color='green').classes('text-xs')
                    with ui.row().classes('w-full justify-between text-xs text-slate-500'):
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('visibility', size='14px')
                            ui.label(str(case.get("views", 0)))
                        with ui.row().classes('items-center gap-1'):
                            ui.icon('favorite', size='14px')
                            ui.label(str(case.get("likes", 0)))

    def get_data_source(self) -> List[VideoWork]:
        """从后端获取公共案例数据"""
        try:
            resp = self.state.backend.get_case_library()
            if resp.get("success"):
                return self._convert_to_works(resp.get("items", []), is_public=True)
        except Exception as e:
            logger.error(f"Load case library error: {e}")

        # 回退到模拟数据
        return self._get_mock_public_works()

    def _convert_to_works(self, items: List[Dict], is_public: bool) -> List[VideoWork]:
        """将后端数据转换为 VideoWork"""
        works = []
        for item in items:
            # 解析 PostgresQL 数据格式
            shot_descs = []
            if '片段描述' in item:
                try:
                    shot_descs = [json.loads(s) for s in item['片段描述']]
                except:
                    pass
            # 优先使用 download_url（直接文件链接），降级到 public_url
            raw_url = item.get('download_url') or item.get('public_url') or item.get('video_url', '')
            works.append(VideoWork(
                work_id=item.get('case_id', ''),
                title=item.get('title', ''),
                description=item.get('description', ''),
                thumbnail=item.get('thumbnail', ''),
                video_url=raw_url,  # 回退到缩略图
                duration=item.get('duration', '0:00'),
                duration_seconds=self._parse_duration(item.get('duration', '0:00')),
                author=item.get('author', '未知作者'),
                category_level1=item.get('一级门类', '未分类'),
                category_level2=item.get('二级门类', '未分类'),
                region=item.get('region', '未知地域'),
                publish_date=item.get('publish_date', '2024-01-01'),
                views=item.get('views', 0),
                likes=item.get('likes', 0),
                shares=item.get('shares', 0),
                comments=item.get('comments', 0),
                summary=item.get('文本摘要', ''),
                highlights=item.get('亮点分析', []),
                shot_descriptions=shot_descs,
                tags=item.get('tags', []),
                is_public=is_public
            ))
        return works

    def _parse_duration(self, duration_str: str) -> int:
        """解析时长字符串为秒数"""
        parts = duration_str.split(':')
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        return 0

    def _get_mock_public_works(self) -> List[VideoWork]:
        """模拟公共库数据（基于你提供的 PostgresQL 实例）"""
        # 解析你提供的 txt 文件数据
        pg_data = {
            "片段描述": [...],  # 你的数据
            "文本摘要": "...",
            "一级门类": "农副加工产品",
            "二级门类": "肉禽加工品"
        }

        return [
            VideoWork(
                work_id="case_mingmokun_001",
                title="名模坤香肠腊肉推广",
                description="三个月30万单的返乡创业故事",
                thumbnail="https://picsum.photos/seed/sausage/400/300",
                video_url="/storage/videos/public/mingmokun_001/video.mp4",
                duration="3:45",
                duration_seconds=225,
                author="名模坤",
                category_level1="农副加工产品",
                category_level2="肉禽加工品",
                region="四川",
                publish_date="2024-04-03",
                views=1250000,
                likes=45600,
                shares=12300,
                comments=8900,
                summary=pg_data.get("文本摘要", ""),
                highlights=["三个月卖出30万单", "真实乡村创业故事", "幽默自嘲风格"],
                shot_descriptions=[json.loads(s) for s in pg_data.get("片段描述", [])],
                tags=["香肠", "腊肉", "返乡创业", "乡村生活"],
                is_public=True
            ),
            # ... 更多案例
        ]

    def _download_work(self, work: VideoWork) -> None:
        """
        下载作品到本地。

        支持两种数据来源:
            1. VideoWork 实体（推荐，类型安全）
            2. 裸字典（兼容旧接口）

        Args:
            work: VideoWork 实例或包含 download_url/public_url 的字典
        """
        # 统一提取 URL（兼容 Dict 和 VideoWork）
        if isinstance(work, VideoWork):
            url = work.video_url
            title = work.title
        else:
            url = work.get("download_url") or work.get("public_url") or work.get("video_url", "")
            title = work.get("title", "FHFP作品")

        if not url:
            ui.notify("下载链接不可用", type="warning")
            return

        # URL 补全：相对路径 → 绝对路径
        # 生产环境应从配置读取 base_url，而非硬编码
        base_url = self._get_backend_base_url()
        if url.startswith('/'):
            url = f"{base_url}{url}"


        filename = f"{title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"

        try:
            # NiceGUI 的 ui.download 创建隐藏 <a> 标签触发浏览器下载
            # 注意：无法携带自定义 HTTP Header（如 Authorization）
            ui.download(src=url, filename=filename)
            ui.notify(f"「{title}」下载已开始", type="positive")

        except Exception as e:
            logger.error(f"Download trigger failed: {e}")
            # 降级方案：提供手动链接
            self._show_manual_download_dialog(url, title)

    def _show_manual_download_dialog(self, url: str, title: str) -> None:
        """降级弹窗：提供直接下载链接"""
        with ui.dialog() as dlg, ui.card().classes('fhfp-card p-6 max-w-md'):
            ui.label('下载作品').classes('text-xl font-bold text-white mb-2')
            ui.label(f'「{title}」').classes('text-green-400 mb-4')
            ui.label('如果下载未自动开始，请右键点击链接选择"另存为"').classes('text-sm text-slate-400 mb-4')

            with ui.row().classes('w-full gap-2'):
                ui.link('直接下载', url, new_tab=True).classes(
                    'bg-green-600 text-white px-4 py-2 rounded text-center flex-1')
                ui.button('复制链接', on_click=lambda: ui.clipboard.write(url)).classes(
                    'bg-slate-700 text-white px-4 py-2 rounded')

            ui.button('关闭', on_click=dlg.close).props('flat').classes('w-full mt-4')
        dlg.open()

# ------------------------------------------------------------------------------
# 7.7 个人作品库
# ------------------------------------------------------------------------------

class PersonalLibraryPage(BasePage):
    def __init__(self):
        super().__init__()
        self.current_page = 1
        self.page_size = 12

    def build(self) -> None:
        apply_global_theme()
        NavigationBar(self.state)

        if not self._check_auth('/'):
            return

        with ui.column().classes('w-full min-h-screen pt-24 pb-12 px-6 max-w-7xl mx-auto'):
            ui.label('我的作品库').classes('text-3xl font-bold text-white mb-2')
            ui.label('管理您的AI创作成果').classes('text-slate-400 mb-8')

            with ui.row().classes('w-full gap-4 mb-8'):
                self.personal_search = ui.input('搜索我的作品...').classes('flex-1').props('outlined clearable')
                ui.button('搜索', icon='search', on_click=self._load_works).classes('bg-green-500 text-black font-bold')
                ui.button('刷新', icon='refresh', on_click=self._load_works).props('flat').classes('text-green-400')

            self.works_grid = ui.row().classes('w-full gap-4 flex-wrap')
            self._load_works()

    def _load_works(self) -> None:
        self.works_grid.clear()
        try:
            resp = self.state.backend.get_personal_works(
                user_id=self.state.user.get("user_id", ""),
                keyword=self.personal_search.value or None,
                page=self.current_page,
                page_size=self.page_size
            )
            items = resp.get("items", []) if resp.get("success") else []
            with self.works_grid:
                if not items:
                    with ui.column().classes('w-full items-center py-12'):
                        ui.icon('inventory_2', size='48px').classes('text-slate-600 mb-4')
                        ui.label('暂无作品，快去创作吧！').classes('text-slate-500')
                        ui.button('开始创作', on_click=lambda: ui.navigate.to('/create')).classes(
                            'bg-green-500 text-black mt-4')
                    return
                for work in items:
                    with ui.card().classes('fhfp-card w-[300px] flex-shrink-0'):
                        with ui.row().classes('w-full h-40 bg-slate-700 rounded-lg mb-3 items-center justify-center'):
                            ui.image(work.get("thumbnail", "https://picsum.photos/seed/empty/400/300")).classes(
                                'w-full h-full object-cover')
                        ui.label(work.get("title", "未命名")).classes('text-white font-bold mb-1')
                        ui.label(work.get("description", "")).classes('text-slate-400 text-sm mb-3 line-clamp-2')
                        with ui.row().classes('w-full justify-between items-center'):
                            ui.label(work.get("created_at", "")[:10]).classes('text-xs text-slate-500')
                            with ui.row().classes('gap-1'):
                                ui.button(icon='play_arrow', on_click=lambda w=work: self._play_work(w)).props(
                                    'flat dense round').classes('text-green-400')
                                ui.button(icon='download', on_click=lambda w=work: self._download_work(w)).props(
                                    'flat dense round').classes('text-slate-400')
        except Exception as e:
            logger.error(f"Load personal works error: {e}")
            ui.notify("加载失败", type="negative")

    def _play_work(self, work: Dict) -> None:
        with ui.dialog() as dlg, ui.card().classes('fhfp-card w-[800px] max-w-[95vw]'):
            ui.label(work.get("title")).classes('text-white font-bold mb-4')
            if work.get("public_url"):
                ui.video(work["public_url"]).classes('w-full rounded-lg')
            else:
                ui.label('视频地址不可用').classes('text-slate-500')
            ui.button('关闭', on_click=dlg.close).props('flat').classes('mt-4')
        dlg.open()

    def get_data_source(self) -> List[VideoWork]:
        """从后端获取个人作品"""
        if not self.state.is_authenticated():
            return []

        try:
            resp = self.state.backend.get_personal_works(
                user_id=self.state.user.get("user_id", "")
            )
            if resp.get("success"):
                return self._convert_to_works(resp.get("items", []), is_public=False)
        except Exception as e:
            logger.error(f"Load personal works error: {e}")

        return []

    def _convert_to_works(self, items: List[Dict], is_public: bool) -> List[VideoWork]:
        """转换个人作品数据"""
        works = []

        for item in items:
            # 优先使用 download_url（直接文件链接），降级到 public_url
            raw_url = item.get('download_url') or item.get('public_url') or item.get('video_url', '')
            works.append(VideoWork(
                work_id=item.get('work_id', ''),
                title=item.get('title', '未命名作品'),
                description=item.get('description', ''),
                thumbnail=item.get('thumbnail', ''),
                video_url=raw_url,
                duration=item.get('duration', '0:00'),
                duration_seconds=0,
                author=self.state.user.get("nickname", "我") if self.state.user else "我",
                category_level1=item.get('category_level1', 'AI创作'),
                category_level2=item.get('category_level2', '智能生成'),
                region="",
                publish_date=item.get('created_at', '')[:10],
                views=item.get('views', 0),
                likes=item.get('likes', 0),
                shares=0,
                comments=0,
                summary=item.get('description', ''),
                highlights=[],
                shot_descriptions=[],
                tags=item.get('tags', []),
                is_public=False
            ))
        return works

    def _download_work(self, work: VideoWork) -> None:
        """
        下载作品到本地。

        支持两种数据来源:
            1. VideoWork 实体（推荐，类型安全）
            2. 裸字典（兼容旧接口）

        Args:
            work: VideoWork 实例或包含 download_url/public_url 的字典
        """
        # 统一提取 URL（兼容 Dict 和 VideoWork）
        if isinstance(work, VideoWork):
            url = work.video_url
            title = work.title
        else:
            url = work.get("download_url") or work.get("public_url") or work.get("video_url", "")
            title = work.get("title", "FHFP作品")

        if not url:
            ui.notify("下载链接不可用", type="warning")
            return

        # URL 补全：相对路径 → 绝对路径
        # 生产环境应从配置读取 base_url，而非硬编码
        base_url = self._get_backend_base_url()
        if url.startswith('/'):
            url = f"{base_url}{url}"


        filename = f"{title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"

        try:
            # NiceGUI 的 ui.download 创建隐藏 <a> 标签触发浏览器下载
            # 注意：无法携带自定义 HTTP Header（如 Authorization）
            ui.download(src=url, filename=filename)
            ui.notify(f"「{title}」下载已开始", type="positive")

        except Exception as e:
            logger.error(f"Download trigger failed: {e}")
            # 降级方案：提供手动链接
            self._show_manual_download_dialog(url, title)


    def _show_manual_download_dialog(self, url: str, title: str) -> None:
        """降级弹窗：提供直接下载链接"""
        with ui.dialog() as dlg, ui.card().classes('fhfp-card p-6 max-w-md'):
            ui.label('下载作品').classes('text-xl font-bold text-white mb-2')
            ui.label(f'「{title}」').classes('text-green-400 mb-4')
            ui.label('如果下载未自动开始，请右键点击链接选择"另存为"').classes('text-sm text-slate-400 mb-4')

            with ui.row().classes('w-full gap-2'):
                ui.link('直接下载', url, new_tab=True).classes(
                    'bg-green-600 text-white px-4 py-2 rounded text-center flex-1')
                ui.button('复制链接', on_click=lambda: ui.clipboard.write(url)).classes(
                    'bg-slate-700 text-white px-4 py-2 rounded')

            ui.button('关闭', on_click=dlg.close).props('flat').classes('w-full mt-4')
        dlg.open()


# ==============================================================================
# 8. 路由注册与程序入口
# ==============================================================================

# 关键：@ui.page 装饰器注册路由，实例化页面类并调用 build()
# 这些函数定义在模块顶层，NiceGUI 会自动识别
"""
@ui.page('/')
def landing_route():
    LandingPage().build()


@ui.page('/create')
def mode_selection_route():
    ModeSelectionPage().build()


@ui.page('/agent1')
def agent1_route():
    Agent1ChatPage().build()


@ui.page('/agent2')
def agent2_route():
    Agent2EditorPage().build()


@ui.page('/upload')
def upload_route():
    UploadEditorPage().build()


@ui.page('/cases')
def cases_route():
    CaseLibraryPage().build()


@ui.page('/personal')
def personal_route():
    PersonalLibraryPage().build()
"""

def main() -> None:
    state = AppState()
    state.set_backend(MockBackendAPI())
    # 生产环境切换：
    # from http_backend import HttpBackendAPI
    # state.set_backend(HttpBackendAPI(base_url="http://localhost:8000"))

    ui.run(
        title=APP_TITLE,
        favicon='🌱',
        dark=True,
        reload=False,
        # uvicorn_reload_dirs=".",
        port=4000,
        show=True
    )


if __name__ in {"__main__","__mp_main__"}:
    main()


