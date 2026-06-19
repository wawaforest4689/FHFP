#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
FHFP Frontend - NiceGUI Class-Based Architecture
================================================
基于类的页面架构，彻底消除闭包与未解析引用问题。
所有页面继承 BasePage，通过 @ui.page 装饰器注册路由。
2026/06/17:MockBackendAPI完全调通（任何分辨率的视频都可以TTS、数字人、字幕，但数字人位置只由第一个片段的视频分辨率决定，不同分辨率视频直接拼接
可能会报错也可能数字人位置出现”偏差“，和AI生成的视频格式无关）
v3: HttpBackendAPI无法进入视频上传页面
"""

import json
import os
import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Dict, List, Optional
import shutil
import asyncio
import copy
from pathlib import Path
import sys
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))


from components.authdialog import UserAccount,AuthValidator
from components.nav_bar import NavigationBar
from components.filestorage import FileStorage
# from components.video_player import video_player



from common.datamodel import CreationMode,ProjectContext,VideoSegment,VideoWork
from core import logger,AppState,THEME_COLORS,APP_TITLE,MAX_SEGMENTS,DEFAULT_BGM_VOLUME,BackendAPI
from video_composer import subtitle_for_fhfp,avatar_for_fhfp,get_video_info,_tts_edge
from nicegui import ui, app, events,context
import hashlib



# 在 FastAPI 主应用中添加
from fastapi.staticfiles import StaticFiles
# 挂载存储目录为静态文件服务
os.makedirs("../backend/storage",exist_ok=True)
app.mount("/storage", StaticFiles(directory="../backend/storage"), name="storage")
os.makedirs("static",exist_ok=True)
os.makedirs("tts_temp",exist_ok=True)
app.add_static_files('/static', Path(__file__).parent / 'static')
app.add_static_files('/tts', Path(__file__).parent / 'tts_temp')


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

    【2026-06-17 修改】统一接口：移除前端直接访问 _storage，所有文件操作通过 API 方法封装。
    """

    def __init__(self):
        # 内存数据索引
        self._users: Dict[str, UserAccount] = {}
        self._projects: Dict[str, Dict[str, Any]] = {}
        self._works: Dict[str, List[Dict[str, Any]]] = {}
        self._segments: Dict[str, Dict[str, VideoSegment]] = {}
        self._username_index: Dict[str, str] = {}
        self._phone_index: Dict[str, str] = {}
        self._video_counter = 0

        # 【保留】真实文件存储（磁盘IO），但不再暴露给前端，仅内部使用
        self._storage = FileStorage("../backend/storage")

    # ------------------------------------------------------------------
    # 1. 用户认证
    # ------------------------------------------------------------------
    async def register(self, username: str, password: str,
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

    async def authenticate(self, username: str, password: str) -> Dict[str, Any]:
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
    async def create_project(self, user_id: str, mode: int, title: str,
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
    async def agent1_chat(self, project_id: str, message: str,
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

    async def agent1_generate_summary(self, project_id: str, highlights: str,
                                custom_notes: Optional[str] = None,
                                mode_id: int = 0) -> Dict[str, Any]:
        summary = "【摘要】本产品坚持传统种植方式，" + highlights + "。"
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
    async def agent2_generate_shots(self, project_id: str, summary: str,
                              mode_id: int = 0, style_preference: Optional[str] = None) -> Dict[str, Any]:
        shots = []
        templates = [
            {"scene": "全景：果园/农田清晨全景，阳光洒落", "audio": "自然环境音：鸟鸣、微风",
             "text": json.dumps([{"人物":"男性","台词":"清晨的第一缕阳光，照亮了我们的果园。"}],ensure_ascii=False)},
            {"scene": "特写：农产品表面纹理，露水欲滴", "audio": "轻快吉他背景音乐起",
             "text": json.dumps([{"人物":"男性","台词":"每一颗果实，都饱含大自然的馈赠。"}],ensure_ascii=False)},
            {"scene": "中景：农户采摘/包装过程，动作熟练", "audio": "包装纸摩擦声+轻快节奏",
             "text": json.dumps([{"人物":"男性","台词":"从田间到餐桌，我们只追求最新鲜。"}],ensure_ascii=False)},
            {"scene": "近景：双手捧起产品展示，微笑", "audio": "音乐渐强，环境音淡出",
             "text": json.dumps([{"人物":"男性","台词":"选择我们，就是选择健康与安心。"}],ensure_ascii=False)},
        ]
        for i, tpl in enumerate(templates):
            sid = f"shot_{i + 1}_{uuid.uuid4().hex[:4]}"
            shots.append({
                "shot_id": sid,
                "order": i,
                "scene": tpl["scene"],
                "audio": tpl["audio"],
                "text": tpl["text"],
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
    # 【新增】BGM 上传接口（统一封装，前端不再直接访问 _storage）
    # ------------------------------------------------------------------
    async def upload_bgm(self, project_id: str, file_data: bytes, file_name: str) -> Dict[str, Any]:
        """
        【新增】保存 BGM 文件到存储，返回可访问的 URL。
        与 HttpBackendAPI 接口完全对齐。
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
    # 内部工具函数（不暴露给前端）
    # ------------------------------------------------------------------
    def _gene_subtitle(self, merged_video: str, vinfo: Dict, scene_anno: str, output: str) -> bool:
        """
        仅字幕烧录工具函数。
        调用 video_composer.subtitle_for_fhfp() 进行字幕合成。
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

    async def _tts_and_avatar(self, merged_video: str, vinfo:Dict, scene_anno: str,
                        output: str, tts_voice: str, digital_human: str) -> bool:
        """
        TTS配音 + 数字人头像 + 字幕烧录工具函数。
        调用 video_composer.avatar_for_fhfp() 进行完整合成。
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

    # ------------------------------------------------------------------
    # 【核心功能2】粗剪合成：按编号顺序真实拼接 + 生成在线预览URL
    # ------------------------------------------------------------------
    async def rough_cut(self, project_id: str, segment_sequence: List[str],
                  bgm_url: Optional[str] = None,
                  bgm_volume: float = DEFAULT_BGM_VOLUME,
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
                result_bgm = subprocess.run(cmd_bgm, capture_output=True, text=True, timeout=120, encoding='utf-8', errors="replace")

                if result_bgm.returncode != 0:
                    has_bgm = False
                else:
                    temp_video_audio = output_path.parent / "temp_video_audio.aac"
                    subprocess.run([
                        'ffmpeg', '-y', '-i', str(temp_video).replace('\\', '/'),
                        '-vn', '-c:a', 'copy', str(temp_video_audio).replace('\\', '/')
                    ], capture_output=True, timeout=60, encoding='utf-8', errors="replace")

                    temp_video_audio_fmt = output_path.parent / "temp_video_audio_fmt.aac"
                    temp_bgm_fmt = output_path.parent / "temp_bgm_fmt.aac"

                    for src, dst in [(temp_video_audio, temp_video_audio_fmt), (temp_bgm, temp_bgm_fmt)]:
                        subprocess.run([
                            'ffmpeg', '-y', '-i', str(src).replace('\\', '/'),
                            '-ar', '48000', '-ac', '2', '-c:a', 'aac', '-b:a', '192k',
                            str(dst).replace('\\', '/')
                        ], capture_output=True, timeout=60, encoding='utf-8', errors="replace")

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
                        result = subprocess.run(cmd_final, capture_output=True, text=True, timeout=120, encoding='utf-8', errors="replace")
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
    # 7. 作品库查询（保留）
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

    # ------------------------------------------------------------------
    # 片段管理 API（与 HttpBackendAPI 完全对齐）
    # ------------------------------------------------------------------
    async def get_project_segments(self, project_id: str) -> Dict[str, Any]:
        """从后端获取项目的片段数据"""
        segments = self._segments.get(project_id, {})
        seg_dict={}
        for k,v in segments.items():
            video_dict={"scene":v.scene,"audio":v.audio,
                          "text":v.text,"segment_id":v.segment_id,
                          "project_id":project_id,"order":v.order}
            seg_dict[k]=video_dict

        return {
            "success": True,
            "project_id": project_id,
            "segments": seg_dict,  # Dict[str,Dict[str,Any]]
            "count": len(segments)
        }

    async def reorder_segments(self, project_id: str, segment_id_list: List[str]) -> Dict[str, Any]:
        segments = self._segments.get(project_id, {})
        valid_ids = [sid for sid in segment_id_list if sid in segments]
        for i, sid in enumerate(valid_ids):
            segments[sid].order = i
        return {
            "success": True,
            "new_orders": {sid: i for i, sid in enumerate(valid_ids)}
        }

    async def add_segment(self, project_id: str, after_segment_id: str = "") -> Dict[str, Any]:
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

        return {
            "success": True,
            "new_segment": {
                "segment_id": new_seg.segment_id,
                "project_id": new_seg.project_id,
                "order": new_seg.order,
                "scene": new_seg.scene,
                "audio": new_seg.audio,
                "text": new_seg.text,
                "video_url": new_seg.video_url,
                "video_duration": new_seg.video_duration,
            }
        }

    async def delete_segment(self, project_id: str, segment_id: str) -> Dict[str, Any]:
        segments = self._segments.get(project_id, {})
        if segment_id not in segments:
            return {"success": False, "message": "片段不存在"}

        deleted_order = segments[segment_id].order
        del segments[segment_id]

        for s in segments.values():
            if s.order > deleted_order:
                s.order -= 1

        return {"success": True, "deleted_segment_id": segment_id}

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
            "updated_segment": seg
        }

    async def upload_segment_video(self, project_id: str, segment_id: str,
                                   file_data: bytes, file_name: str,
                                   content_type: str = "video/mp4") -> Dict[str, Any]:
        """异步文件上传（multipart），后端会自动创建或覆写 segment"""
        try:
            video_url = self._storage.save_segment(project_id, segment_id, file_data, file_name)

            if project_id not in self._segments:
                self._segments[project_id] = {}

            vinfo = get_video_info(video_url)
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
            return {
                "success": False,
                "message": f"文件存储失败: {str(e)}"
            }

    async def check_file_exists(self, url_path: str) -> bool:
        """
        检查服务器上指定 URL 路径的文件是否存在。
        通过 FileStorage 将 URL 转为本地路径后检查。
        """
        if not url_path:
            return False
        try:
            abs_path = self._storage.get_absolute_path(url_path)
            return abs_path.exists() and abs_path.stat().st_size > 1000
        except Exception:
            return False


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
        # 2026.06.17:添加首页全屏视频背景
        video = ui.video('static/horizontal.mp4').classes('fixed inset-0 w-full h-full object-cover -z-10')
        video.props('autoplay loop playsinline muted')

        mute_btn = ui.button(icon='volume_off').classes(
            'fixed bottom-18 right-6 z-50 bg-white/20 backdrop-blur text-white rounded-full w-12 h-12'
        ).props('flat')

        async def toggle_mute():
            result = await ui.run_javascript('''
                const v = document.querySelector('video');
                v.muted = !v.muted;
                return v.muted;
            ''')
            # 根据返回的 muted 状态切换图标
            mute_btn.props(f'icon={"volume_off" if result else "volume_up"}')
            mute_btn.update()

        mute_btn.on_click(toggle_mute)

        with ui.column().classes('relative z-10 w-full items-center justify-center min-h-screen pt-20 pb-12'):
            ui.icon('auto_awesome', size='64px').classes('text-green-400 mb-6 animate-pulse')
            ui.label('农心向荣创作平台').classes('text-5xl font-bold text-white mb-3 text-center')
            # ui.label('TEST TEST TEST').classes('text-red-500 text-4xl')
            ui.label(APP_TITLE).classes('text-xl text-green-400 font-medium mb-6 tracking-widest')
            ui.label('利用AI技术，让农产品短视频创作更简单、更高效。从对话到亮点，从亮点到摘要，从摘要到片段拍摄建议，一个AI赋能的贯通的创作流程。').classes(
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
        # 2026.06.17:添加模式选择页全屏视频背景
        video = ui.video('static/mode_select.mp4').classes('fixed inset-0 w-full h-full object-cover -z-10')
        video.props('autoplay loop playsinline muted')

        mute_btn = ui.button(icon='volume_off').classes(
            'fixed bottom-18 right-6 z-50 bg-white/20 backdrop-blur text-white rounded-full w-12 h-12'
        ).props('flat')

        async def toggle_mute():
            result = await ui.run_javascript('''
                const v = document.querySelector('video');
                v.muted = !v.muted;
                return v.muted;
            ''')
            # 根据返回的 muted 状态切换图标
            mute_btn.props(f'icon={"volume_off" if result else "volume_up"}')
            mute_btn.update()

        mute_btn.on_click(toggle_mute)
        # 先创建 UI，不管项目是否已创建
        with ui.column().classes('relative z-10 w-full items-center justify-center min-h-screen pt-24 px-6'):
            ui.label('选择您的创作模式').classes('text-4xl font-bold text-white mb-3')
            ui.label('不同的模式将决定AI助手的对话策略与成片风格').classes('text-slate-400 mb-12')

            # 如果项目还没创建，显示加载状态
            if self.state.current_project.project_id is None:
                self.loading_label = ui.label('正在初始化项目...').classes('text-green-400 mb-8')
                # 用 ui.timer 在"当前 slot 上下文"只支持同步通信
                # ui.timer(0.1, self._auto_create_project, once=True)
                asyncio.create_task(self._auto_create_project())

            else:
                self.loading_label = None

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
                            '适用于：产品推广、产地直发、促销引流、竞品对比。\nAI将协助您拆解产品卖点，生成对比性强、信息密度高的讲解脚本。\n支持字幕生成、TTS配音与数字人主播讲解。').classes(
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
                            '适用于：品牌故事、新农人IP、长期内容线。\nAI将挖掘您生活中的闪光点，编织有温度、有共鸣的叙事脚本。\n支持字幕生成').classes(
                            'text-slate-400 text-sm leading-relaxed whitespace-pre-line')
                        ui.badge('MODE 1', color='green').classes('mt-4')

    async def _auto_create_project(self):
        try:
            resp = await self.state.backend.create_project(
                user_id=self.state.user.get("user_id", "anonymous") if self.state.user else "anonymous",
                mode=0, title="未命名项目", description=""
            )
            if resp.get("success"): self.state.set_project_id(resp["project_id"])
        except Exception as e:
            logger.error(f"Auto create project failed: {e}")

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
        self.current_highlights: str = ""
        self.messages_container: Optional[ui.column] = None
        self.highlights_container: Optional[ui.column] = None
        self.msg_input: Optional[ui.textarea] = None

        # 【预创建】对话框引用，在 build() 中初始化
        self.summary_dlg: Optional[ui.dialog] = None
        self.summary_dlg_input: Optional[ui.textarea] = None
        self.summary_dlg_highlights: Optional[ui.label] = None

        # 控制tts语音的播放、合成等
        self.tts_state="none"
        self.tts_result=None
        self.client=ui.context.client


    def build(self) -> None:
        apply_global_theme()
        NavigationBar(self.state)

        if not self._check_project('/create'):
            return

        # 【预创建】摘要确认对话框（在 build 的 slot 上下文中创建）
        self._build_summary_dialog()
        ui.timer(0.5,self._poll_tts,once=False)

        with ui.row().classes('w-full h-screen pt-16'):
            # 左侧边栏：实时显示每轮提取的亮点
            with ui.column().classes('w-1/4 h-full bg-slate-800/50 border-r border-slate-700 p-4 flex flex-col'):
                # 上半部分：固定信息（不滚动）
                with ui.column().classes('w-full shrink-0'):
                    ui.label('当前项目').classes('text-white font-bold mb-4')
                    with ui.card().classes('bg-slate-700/50 p-3 w-full mb-4'):
                        mode_text = "产品介绍" if self.state.current_project.mode == CreationMode.PRODUCT_INTRO else "剧情设计"
                        ui.label(f'模式: {mode_text}').classes('text-green-400 text-sm font-bold')
                        ui.label(f'ID: {self.state.current_project.project_id}').classes('text-slate-500 text-xs mt-1')

                    with ui.row().classes('w-full justify-between items-center mb-2'):
                        ui.label('已提取亮点').classes('text-white font-bold text-sm')
                        with ui.row().classes('items-center gap-1'):
                            # 麦克风：生成 TTS（始终显示）
                            self.mic_btn = ui.button(
                                icon='mic',
                                on_click=lambda: asyncio.create_task(self._generate_tts())
                            ).classes('text-green-400').props('flat round dense')

                            # 扬声器：播放控制（初始隐藏）
                            self.tts_btn = ui.button(
                                icon='volume_off',
                                on_click=self._on_tts_click
                            ).classes('text-slate-500').props('flat round dense')

                # 中间部分：亮点内容（可滚动，不用 scroll_area）
                with ui.column().classes('flex-1 w-full overflow-y-auto min-h-0'):
                    self.highlights_label = ui.label().classes('text-green-300 text-xs leading-relaxed')

                # 下半部分：数字人 GIF（固定底部，限制高度）
                with ui.column().classes('w-full shrink-0 mt-2'):
                    ui.image('avatars/man.gif').classes('w-full max-h-56 object-contain rounded-lg').props('fit=contain')


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
                    "你好！我是小耘，您的农产品短视频创作助手。\n请告诉我您要推广的产品/故事主题，我会帮您挖掘亮点、梳理叙事逻辑。")

                # 输入区
                with ui.row().classes('w-full gap-2 items-end'):
                    self.msg_input = ui.textarea(placeholder='输入您的问题...').props(
                        'outlined').classes('flex-1').style('min-height: 60px')
                    with ui.column().classes('gap-2'):
                        ui.button(icon='send', on_click=lambda: asyncio.create_task(self._send_message())).classes(
                            'bg-green-500 text-black rounded-full w-12 h-12')
                        # 【修复】使用 lambda 包装 async 调用
                        ui.button('生成摘要', on_click=lambda: asyncio.create_task(self._generate_summary())).classes(
                            'bg-slate-700 text-white text-xs px-3 py-2 rounded')

    # ==============================================================================
    # 【预创建】摘要确认对话框（在 build 的 slot 上下文中创建）
    # ==============================================================================
    def _build_summary_dialog(self) -> None:
        """在 build() 中预创建对话框，避免异步回调中创建 UI"""
        self.summary_dlg = ui.dialog().props('persistent')
        with self.summary_dlg, ui.card().classes('fhfp-card w-[600px] max-w-[90vw]'):
            ui.label('摘要已生成').classes('text-xl font-bold text-white mb-2')
            ui.label('请确认或修改摘要内容，确认后将进入拍摄建议生成。').classes('text-sm text-slate-400 mb-4')

            # 亮点显示区（可更新）
            with ui.card().classes('bg-green-900/30 border border-green-500/30 p-3 w-full mb-4'):
                ui.label('✨ 提取亮点').classes('text-green-400 text-xs font-bold mb-1')
                self.summary_dlg_highlights = ui.label().classes(
                    'text-green-300 text-sm leading-relaxed whitespace-pre-wrap')

            # 摘要编辑区
            ui.label('📝 生成摘要').classes('text-white text-sm font-bold mb-2')
            self.summary_dlg_input = ui.textarea().classes('w-full mb-4').style('min-height: 120px')

            with ui.row().classes('w-full justify-end gap-2'):
                ui.button('返回修改', on_click=self.summary_dlg.close).props('flat')
                ui.button('确认并继续', on_click=self._on_confirm_summary).classes(
                    'bg-green-500 text-black font-bold')

    # ==============================================================================
    # 异步回调：只更新数据 + 打开预创建的对话框
    # ==============================================================================
    async def _generate_summary(self) -> None:
        with self.client:
            """生成摘要：异步获取数据，然后打开预创建的对话框"""
            if not self.current_highlights:
                ui.notify("请先与助手对话，提取至少一个亮点", type="warning")
                return

            resp = {}
            try:
                resp = await self.state.backend.agent1_generate_summary(
                    project_id=self.state.current_project.project_id or "mock_proj",
                    highlights=self.current_highlights,
                    custom_notes="请生成适合短视频口播的摘要"
                )
                if resp.get("success", False):
                    summary = resp.get("summary", "")
                    self.state.current_project.agent1_summary = summary
                    self.state.current_project.agent1_highlights = self.current_highlights

                    # 【关键】更新预创建的对话框内容
                    self.summary_dlg_highlights.set_text(self.current_highlights)
                    self.summary_dlg_input.set_value(summary)

                    # 打开对话框（在异步回调中调用 open() 是安全的）
                    await self.summary_dlg.open()
                else:
                    ui.notify("摘要生成失败", type="negative")
            except Exception as e:
                logger.error(f"Generate summary error: {e}")
                ui.notify("生成失败，请重试", type="negative")

    # ==============================================================================
    # 同步回调：处理对话框确认（在 slot 上下文中执行）
    # ==============================================================================
    def _on_confirm_summary(self) -> None:
        """确认摘要：同步回调，安全操作 UI"""
        final_summary = self.summary_dlg_input.value
        self.state.current_project.agent1_summary = final_summary
        self.summary_dlg.close()
        ui.notify("进入智能体2号：拍摄建议生成", type="positive")
        ui.navigate.to('/agent2')


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

    """
    def _update_highlights_ui(self) -> None:
        self.highlights_container.clear()
        with self.highlights_container:
            if self.current_highlights:
                with ui.card().classes('bg-slate-700/80 p-2 w-full'):
                    ui.label(self.current_highlights).classes('text-green-300 text-xs leading-relaxed')
    """
    # ------------------------------------------------------------------
    # 以下全部为类方法（bound methods），无闭包，无 unresolved reference
    # ------------------------------------------------------------------
    def _poll_tts(self):
        """主线程轮询"""
        if self.tts_result is not None:
            result = self.tts_result
            if result['success']:
                self._set_up_audio()
            else:
                ui.notify("生成失败", type="warning")
            self.tts_result = None

    async def _generate_tts(self) -> None:
        """点击麦克风：异步生成 TTS"""
        if not self.current_highlights:
            ui.notify("暂无亮点内容可生成语音", type="warning")
            return

        # 禁用麦克风，防止重复点击
        self.mic_btn.disable()
        self._set_tts_state("generating")

        os.makedirs("tts_temp", exist_ok=True)
        audio_path = os.path.join(
            str(Path(__file__).resolve().parent),
            f"tts_temp/tts_{self.state.current_project.project_id}_turn{len(self.chat_history)}.mp3"
        )

        success = await _tts_edge(self.current_highlights, "zh-CN-XiaoxiaoNeural", audio_path)

        if success:
            self.current_audio_path = audio_path
            self.tts_result = {'success': True}
        else:
            self._set_tts_state("none")
            self.tts_result = {'success': False}
            ui.notify("语音生成失败", type="warning")

        self.mic_btn.enable()

    def _on_tts_click(self) -> None:
        """点击扬声器：播放控制"""
        if self.tts_state in ("ready", "finished"):
            self._play_audio()
        elif self.tts_state == "playing":
            self._pause_audio()
        elif self.tts_state == "stop":
            self._play_audio()

    def _set_up_audio(self)->None:
        # 用 JS 创建音频元素
        ui.run_javascript(f'''
              var audio = document.getElementById("tts-audio");
              if (!audio) {{
                  audio = document.createElement('audio');
                  audio.id = 'tts-audio';
                  audio.style.display = 'none';
                  document.body.appendChild(audio);
              }}
              audio.src = '/tts/{os.path.basename(self.current_audio_path)}';
              console.log("TTS: src set to", audio.src);
          ''')
        self._set_tts_state("ready")
        # 自动生成完就播放
        self._play_audio()


    def _play_audio(self) -> None:
        if self.tts_state=="none":
            # 用 JS 创建音频元素
            ui.run_javascript(f'''
                  var audio = document.getElementById("tts-audio");
                  if (!audio) {{
                      audio = document.createElement('audio');
                      audio.id = 'tts-audio';
                      audio.style.display = 'none';
                      document.body.appendChild(audio);
                  }}
                  audio.src = '/tts/{os.path.basename(self.current_audio_path)}';
                  console.log("TTS: src set to", audio.src);
              ''')
            self._set_tts_state("ready")
        ui.run_javascript('document.getElementById("tts-audio").play();')
        self._set_tts_state("playing")
        self._start_ended_check()

    def _pause_audio(self) -> None:
        ui.run_javascript('document.getElementById("tts-audio").pause();')
        self._set_tts_state("stop")

    def _start_ended_check(self) -> None:
        self._poll_timer = ui.timer(0.5, self._check_ended, once=False)

    def _check_ended(self) -> None:
        if self.tts_state != "playing":
            if hasattr(self, '_poll_timer'):
                self._poll_timer.cancel()
            return

        async def check():
            ended = await ui.run_javascript('document.getElementById("tts-audio").ended;')
            if ended:
                self._set_tts_state("finished")
                if hasattr(self, '_poll_timer'):
                    self._poll_timer.cancel()

        asyncio.create_task(check())

    def _set_tts_state(self, state: str) -> None:
        self.tts_state = state

        # 麦克风状态
        if state == "generating":
            self.mic_btn.props('icon=mic')
            self.mic_btn.classes(replace='bg-yellow-400')  # 生成中变黄
        else:
            self.mic_btn.props('icon=mic')
            self.mic_btn.classes(replace='bg-green-400')  # 默认绿色
        self.mic_btn.update()

        # 扬声器状态
        if state == "none" or "generating":
            self.tts_btn.props('icon=volume_off')
            self.tts_btn.classes(replace='bg-slate-400')
        elif state == "ready":
            self.tts_btn.props('icon=volume_off')
            self.tts_btn.classes(replace='bg-yellow-400')
        elif state == "playing":
            self.tts_btn.props('icon=volume_up')
            self.tts_btn.classes(replace='bg-green-400')
        elif state == "stop":
            self.tts_btn.props('icon=stop')
            self.tts_btn.classes(replace='bg-red-400')
        elif state == "finished":
            self.tts_btn.props('icon=volume_off')
            self.tts_btn.classes(replace='bg-blue-400')
        self.tts_btn.update()


    async def _send_message(self) -> None:
        """发送消息 - 已改为 async，支持 await 调用后端"""
        text = self.msg_input.value
        if not text:
            return
        self.msg_input.value = ""
        self.msg_input.disable()  # 禁用输入，防止重复提交和"以为卡死"

        self._render_user_message(text)
        self.chat_history.append({"role": "user", "content": text})

        try:
            resp = await self.state.backend.agent1_chat(
                project_id=self.state.current_project.project_id or "mock_proj",
                message=text,
                history=self.chat_history,
                mode_id=self.state.get_mode_id(),
            )
            if resp.get("success"):
                reply = resp.get("reply", "")
                suggestions = resp.get("suggested_questions", [])

                self.chat_history.append({"role": "assistant", "content": reply})

                if reply:
                    self.current_highlights = reply
                    self.highlights_label.set_text(self.current_highlights)

                full_reply = reply
                if suggestions:
                    full_reply += "\n\n**建议追问：**\n" + "\n".join(f"- {q}" for q in suggestions)
                self._render_assistant_message(full_reply)
            else:
                self._render_assistant_message("抱歉，服务暂时繁忙，请稍后重试。")
        except Exception as e:
            logger.error(f"Agent1 chat error: {e}")
            self._render_assistant_message("网络错误，请检查连接。")
        finally:
            self.msg_input.enable()  # 无论成败都恢复输入




# ------------------------------------------------------------------------------
# 7.4 智能体2号 - 拍摄建议编辑
# ------------------------------------------------------------------------------

class Agent2EditorPage(BasePage):
    def __init__(self):
        super().__init__()
        self.shots_data: List[Dict[str, Any]] = []
        self.shots_container: Optional[ui.column] = None
        self.cached_shots_data: List[Dict[str,Any]] = []
        self.notify_msg=None
        self.client=ui.context.client
        asyncio.create_task(self._load_shots_async())


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
                # 重新生成按钮 - 保持同步回调
                ui.button('重新生成', icon='refresh', on_click=self._regenerate_shots).props('flat').classes(
                    'text-green-400')

            with ui.card().classes('fhfp-card w-full p-4 mb-8'):
                ui.label('摘要原文').classes('text-sm text-green-400 font-bold mb-2')
                ui.label(self.state.current_project.agent1_summary).classes('text-slate-300 text-sm leading-relaxed')

            # 预先创建容器
            self.shots_container = ui.column().classes('w-full gap-4 mb-8')

            ui.timer(1,self._render_shots,once=False)

            with ui.row().classes('w-full justify-center gap-4'):
                ui.button('添加空白片段', icon='add', on_click=self._add_blank_shot).classes(
                    'bg-slate-700 text-white px-6')
                ui.button('确认建议，进入拍摄上传', icon='check_circle', on_click=self._confirm_shots).classes(
                    'bg-green-500 text-black font-bold px-8')

    async def _load_shots_async(self) -> None:
        """异步加载拍摄建议（从后端获取数据）"""
        with self.client:
            try:
                resp = await self.state.backend.agent2_generate_shots(
                    project_id=self.state.current_project.project_id or "mock_proj",
                    summary=self.state.current_project.agent1_summary,
                    mode_id=self.state.get_mode_id(),
                    style_preference="朴实" if self.state.current_project.mode == CreationMode.PRODUCT_INTRO else "温馨"
                )
                if resp.get("success"):
                    self.shots_data = resp.get("shots", [])
                    self.notify_msg = ("positive", "生成成功！" + resp.get("storyline_arc", ""))
                else:
                    self.notify_msg = ("negative", "生成失败！" + resp.get("storyline_arc", ""))

            except Exception as e:
                logger.error(f"Agent2 generate error: {e}")
                ui.notify("网络错误", type="negative")

    def _render_shots(self) -> None:
        """同步渲染已加载的数据"""
        # print(self.shots_data)
        if not self.shots_container or self.cached_shots_data==self.shots_data:
            return
        if self.notify_msg:
            ui.notify(self.notify_msg[1],type=self.notify_msg[0])
            self.notify_msg=None

        # print(self.shots_data)
        self._do_render()
        self.cached_shots_data=self.shots_data[:]


    def _do_render(self) -> None:
        """重建所有卡片"""
        self.shots_container.clear()
        with self.shots_container:
            for idx, shot in enumerate(self.shots_data):
                self._render_shot_card(idx, shot)

    # 内容编辑：静默更新，不重建
    def _update_shot_field(self, idx: int, field: str, value: str) -> None:
        self.shots_data[idx][field] = value
        # 不触发任何渲染

    def _render_shot_card(self, idx: int, shot: Dict[str, Any]) -> None:
        """渲染单个 shot 卡片"""
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
                        # ✅ 绑定 on_change，只更新数据，不重建
                        ui.textarea(
                            value=shot.get("scene", ""),
                            on_change=lambda e, i=idx: self._update_shot_field(i, "scene", e.value)
                        ).classes('w-full text-sm').style('min-height: 80px')

                    with ui.column().classes('flex-1'):
                        ui.label('音频建议').classes('text-xs text-blue-400 font-bold mb-1')
                        ui.textarea(
                            value=shot.get("audio", ""),
                            on_change=lambda e, i=idx: self._update_shot_field(i, "audio", e.value)
                        ).classes('w-full text-sm').style('min-height: 80px')

                    with ui.column().classes('flex-1'):
                        ui.label('文案建议').classes('text-xs text-yellow-400 font-bold mb-1')
                        ui.textarea(
                            value=json.dumps(shot.get("text", ""),ensure_ascii=False),
                            on_change=lambda e, i=idx: self._update_shot_field(i, "text", e.value)
                        ).classes('w-full text-sm').style('min-height: 80px')

    def _move_shot(self, idx: int, direction: int) -> None:
        new_idx = idx + direction
        if 0 <= new_idx < len(self.shots_data):
            self.shots_data[idx], self.shots_data[new_idx] = self.shots_data[new_idx], self.shots_data[idx]
            self._do_render()
            ui.notify(f"已调整至位置 {new_idx + 1}", type="info")

    def _delete_shot(self, idx: int) -> None:
        if len(self.shots_data) <= 1:
            ui.notify("至少保留一个片段", type="warning")
            return
        self.shots_data.pop(idx)
        self._do_render()
        ui.notify("已删除", type="info")


    def _add_blank_shot(self) -> None:
        if len(self.shots_data) >= MAX_SEGMENTS:
            ui.notify(f"最多支持 {MAX_SEGMENTS} 个片段", type="warning")
            return
        new_shot = {
            "shot_id": f"shot_manual_{uuid.uuid4().hex[:4]}",
            "order": len(self.shots_data),
            "scene": "请填写画面建议",
            "audio": "请填写音频建议",
            "text": "[{\"figure\":\"男性\",\"script\":,\"请填写台词\"}]",
            "duration_hint": 5.0
        }
        self.shots_data.append(new_shot)
        self._do_render()
        ui.notify("已添加空白片段", type="positive")


    def _regenerate_shots(self) -> None:
        """重新生成 - 清空数据后创建异步任务，build主函数内轮询"""
        self.shots_data = []
        # 对于模拟后端，还没搞清楚重新生成为什么会导致整个卡片变成空白（我的理解应该是显示和之前一样的卡片）
        asyncio.create_task(self._load_shots_async())

    async def _confirm_shots(self) -> None:
        """【修复】确认拍摄建议时，同步创建后端 segments"""
        segments = []

        # 【新增】先在后端创建所有 segments
        pid = self.state.current_project.project_id
        # created_segments = []

        for idx, shot in enumerate(self.shots_data):
            # 调用后端 add_segment 创建片段
            resp = await self.state.backend.add_segment(pid)
            if not resp.get("success"):
                ui.notify(f"创建片段失败: {resp.get('message')}", type="negative")
                return

            # 获取VideoSegment对象
            new_seg = resp.get("new_segment")
            if new_seg is None:
                ui.notify("后端返回异常", type="negative")
                return

            seg_id = new_seg.segment_id

            # 更新文本
            await self.state.backend.update_segment(
                pid, seg_id,
                scene=shot.get("scene", ""),
                audio=shot.get("audio", ""),
                text=shot.get("text", "")
            )

            # 构建前端 VideoSegment
            seg = VideoSegment(
                segment_id=seg_id,
                project_id=pid,
                order=idx,
                scene=shot.get("scene", ""),
                audio=shot.get("audio", ""),
                text=shot.get("text", "")
            )
            segments.append(seg)
            # created_segments.append(seg_id)

        self.state.current_project.segments = segments
        ui.notify(f"已确认 {len(segments)} 个片段", type="positive")
        ui.navigate.to('/upload')


# ------------------------------------------------------------------------------
# 7.5 视频上传与粗剪 (Upload & Rough Cut)
# ------------------------------------------------------------------------------

class UploadEditorPage(BasePage):
    """
    视频上传、粗剪合成、下载/发布 三合一页面
    所有片段操作走后端接口，前端只做 UI 渲染和状态缓存
    """

    def __init__(self):
        super().__init__()
        self.segments: List[VideoSegment] = []
        self.preview_url: Optional[str] = None
        self.bgm_url: Optional[str] = None
        self.bgm_volume: float = DEFAULT_BGM_VOLUME
        self.subtitle_on: bool = True

        self.tts_voice: Optional[str] = None
        self.digital_human: Optional[str] = None

        self.segments_container: Optional[ui.column] = None
        self.preview_card: Optional[ui.card] = None
        self.tts_container: Optional[ui.row] = None
        self.avatar_container: Optional[ui.row] = None
        self.avatar_cards: Dict[str, Dict] = {}

        self._is_rendering: bool = False
        self._fixed_preview_url: Optional[str] = None

        self.client=ui.context.client


    def build(self):
        """构建完整页面"""
        apply_global_theme()
        NavigationBar(self.state)

        # 异步从后端同步（页面刷新后缓存可能丢失）
        asyncio.create_task(self._init_segments())

        pid = getattr(self.state.current_project, 'project_id', None)
        if pid:
            self._fixed_preview_url = f"/storage/projects/{pid}/output/preview.mp4"

        with ui.row().classes('w-full h-screen pt-16 gap-0'):
            # ========== 左侧：片段列表与上传 ==========
            with ui.column().classes('w-1/3 h-full bg-slate-800/40 border-r border-slate-700 overflow-y-auto p-4'):
                ui.label('分镜视频上传').classes('text-lg font-bold text-white mb-2')
                ui.label('按顺序上传每个片段，支持调整顺序').classes('text-xs text-slate-500 mb-4')

                self.segments_container = ui.column().classes('w-full gap-3')
                # 【修复】立刻渲染已有 segments（Agent2 生成的）
                self._render_segments()

                ui.button('添加新片段', icon='add', on_click=self._add_segment).classes(
                    'w-full bg-slate-700 hover:bg-slate-600 text-white mt-4')

            # ========== 右侧：预览与操作 ==========
            with ui.column().classes('flex-1 h-full overflow-y-auto p-6'):
                # ... 右侧代码不变 ...
                self.preview_card = ui.card().classes(
                    'w-full aspect-video mb-6 flex items-center justify-center bg-slate-800 border-slate-700')
                self._update_preview()

                if self._fixed_preview_url:
                    asyncio.create_task(self._try_restore_preview())

                # 配置区（BGM、TTS、数字人、字幕等，保持不变）
                with ui.column().classes('w-full gap-4'):
                    # BGM
                    with ui.card().classes('w-full p-4 bg-slate-800 border-slate-700'):
                        ui.label('背景音乐 (BGM)').classes('text-sm font-bold text-white mb-3')
                        with ui.row().classes('w-full items-center gap-4'):
                            ui.upload(label='上传BGM (MP3/WAV)', auto_upload=True,
                                      on_upload=self._handle_bgm_upload).classes('flex-1').props(
                                'accept=.mp3,.wav,.m4a color=green')
                            vol = ui.slider(min=0, max=10, step=0.01, value=DEFAULT_BGM_VOLUME).classes('w-32')
                            ui.label().bind_text_from(vol, 'value',
                                                      lambda v: f'{float(v) * 100:.0f}%').classes(
                                'text-slate-400 text-sm')

                    # TTS、数字人、字幕、操作按钮...（保持原有代码不变）
                    self.tts_container = ui.row().classes('w-full')
                    with self.tts_container:
                        if self._is_product_mode():
                            with ui.card().classes('w-full p-4 bg-slate-800 border-slate-700'):
                                ui.label('TTS 配音音色').classes('text-sm font-bold text-white mb-3')
                                ui.label('选择AI配音的音色风格').classes('text-xs text-slate-500 mb-3')
                                with ui.row().classes('w-full gap-4'):
                                    self._build_tts_option("Yunxi", "云希 · 男声", "沉稳大气，适合产品讲解", "male")
                                    self._build_tts_option("Xiaoying", "晓莹 · 女声", "亲切温柔，适合情感表达", "female")

                    self.avatar_container = ui.row().classes('w-full')
                    with self.avatar_container:
                        if self._is_product_mode():
                            with ui.card().classes('w-full p-4 bg-slate-800 border-slate-700'):
                                ui.label('数字人主播').classes('text-sm font-bold text-white mb-3')
                                ui.label('选择虚拟主播形象（选中后播放动画）').classes('text-xs text-slate-500 mb-3')
                                with ui.row().classes('w-full gap-4 justify-center'):
                                    self._build_avatar_option("man", "男主播",
                                                              "/storage/avatars/man.gif",
                                                              "/storage/avatars/man_static.png")
                                    self._build_avatar_option("woman", "女主播",
                                                              "/storage/avatars/girl.gif",
                                                              "/storage/avatars/girl_static.png")

                    with ui.card().classes('w-full p-4 bg-slate-800 border-slate-700'):
                        ui.label('基础选项').classes('text-sm font-bold text-white mb-3')

                        def _on_subtitle_toggle(e):
                            self.subtitle_on = e.value
                            status = "启用" if e.value else "关闭"
                            ui.notify(f"字幕烧录已{status}", type="info")

                        ui.switch('自动生成字幕', value=self.subtitle_on).classes(
                            'text-white').on_value_change(_on_subtitle_toggle)
                        ui.notify(f"字幕烧录已{'启用' if self.subtitle_on else '关闭'}", type="info", timeout=1500)

                    with ui.row().classes('w-full justify-end gap-3 mt-4'):
                        self.btn_preview = ui.button('预览合成', icon='play_circle',
                                                     on_click=self._do_rough_cut).classes(
                            'bg-green-600 hover:bg-green-500 text-white px-6')
                        ui.button('下载到本地', icon='download', on_click=self._download_preview).classes(
                            'bg-blue-600 hover:bg-blue-500 text-white px-6')
                        ui.button('发布作品', icon='publish', on_click=self._publish_work).classes(
                            'bg-green-500 hover:bg-green-400 text-black font-bold px-6')


    # ------------------------------------------------------------------
    # 片段管理 - 全部走后端接口
    # ------------------------------------------------------------------
    async def _init_segments(self):
        with self.client:
            pid = self.state.current_project.project_id
            if not pid:
                ui.notify("项目未初始化", type="negative")
                return

            # 【缓存为空】从后端拉取
            resp = await self.state.backend.get_project_segments(pid)
            if resp.get("success"):
                self.segments = resp.get("segments", [])
                self.state.current_project.segments = self.segments  # 更新缓存
                self._render_segments()
            else:
                ui.notify(f"加载片段失败: {resp.get('message')}", type="negative")


    def _render_segments(self):
        """渲染片段列表（纯 UI，不操作数据）"""
        if not self.segments_container:
            return
        self.segments_container.clear()

        # 按 order 排序显示
        if self.segments:
            # 所有列表元素都是VideoSegment，这一步处理是为了加强保障
            if isinstance(self.segments[0],dict):
                self.segments=[VideoSegment(**seg) for seg in self.segments]
            self.segments=sorted(self.segments,key=lambda s:s.order)

            with self.segments_container:
                for idx, seg in enumerate(self.segments):
                    self._render_segment_card(idx, seg)

    def _render_segment_card(self, idx: int, seg: VideoSegment):
        """渲染单个片段卡片"""
        with ui.card().classes('w-full p-3 bg-slate-700 border-slate-600'):
            with ui.row().classes('w-full justify-between items-center mb-2'):
                with ui.row().classes('items-center gap-2'):
                    ui.badge(f'{idx + 1}', color='green').classes('text-xs')
                    status_icon = 'check_circle' if seg.video_url else 'hourglass_empty'
                    status_color = 'text-green-400' if seg.video_url else 'text-slate-500'
                    ui.icon(status_icon, size='16px').classes(status_color)
                with ui.row().classes('gap-1'):
                    # 上移
                    ui.button(icon='arrow_upward',
                              on_click=lambda i=idx: asyncio.create_task(self._move_segment(i, -1))).props(
                        'flat dense size=sm').classes('text-slate-400')
                    # 下移
                    ui.button(icon='arrow_downward',
                              on_click=lambda i=idx: asyncio.create_task(self._move_segment(i, 1))).props(
                        'flat dense size=sm').classes('text-slate-400')
                    # 删除
                    ui.button(icon='delete',
                              on_click=lambda i=idx: asyncio.create_task(self._delete_segment(i))).props(
                        'flat dense size=sm').classes('text-red-400')

            # 视频上传区
            if seg.video_url:
                with ui.row().classes('w-full items-center gap-2 mb-2'):
                    ui.icon('videocam', size='20px').classes('text-green-400')
                    ui.label(seg.segment_id or '已上传').classes('text-xs text-green-400 truncate flex-1')
                    ui.button('替换',
                              on_click=lambda i=idx: self._open_upload_dialog(i)).props(
                        'flat dense size=sm').classes('text-green-400')
            else:
                with ui.column().classes(
                        'w-full border-2 border-dashed border-slate-600 rounded p-3 items-center cursor-pointer').on(
                    'click', lambda i=idx: self._open_upload_dialog(i)):
                    ui.icon('add', size='32px').classes('text-slate-500')
                    ui.label('点击上传视频').classes('text-xs text-slate-500')

            # 文本编辑区 - 失去焦点时自动保存到后端
            with ui.column().classes('w-full gap-2 mt-2'):
                scene_input = ui.input('画面', value=seg.scene).classes('w-full text-xs').props('dense dark')
                audio_input = ui.input('音频', value=seg.audio).classes('w-full text-xs').props('dense dark')
                text_input = ui.input('文案', value=json.dumps(seg.text,ensure_ascii=False)).classes('w-full text-xs').props('dense dark')

                # 失去焦点时保存
                scene_input.on('blur', lambda e, s=seg: asyncio.create_task(
                    self._save_text(s.segment_id, scene=e.value)))
                audio_input.on('blur', lambda e, s=seg: asyncio.create_task(
                    self._save_text(s.segment_id, audio=e.value)))
                text_input.on('blur', lambda e, s=seg: asyncio.create_task(
                    self._save_text(s.segment_id, text=e.value)))

    async def _save_text(self, segment_id: str, scene: Optional[str] = None,
                         audio: Optional[str] = None, text: Optional[str] = None):
        """保存文本修改到后端"""
        pid = self.state.current_project.project_id
        resp = await self.state.backend.update_segment(pid, segment_id, scene=scene, audio=audio, text=text)
        if not resp.get("success"):
            ui.notify(f"保存失败: {resp.get('message')}", type="negative")

    async def _move_segment(self, idx: int, direction: int):
        """调顺序：先改本地顺序，再调后端接口"""
        new_idx = idx + direction
        if not (0 <= new_idx < len(self.segments)):
            return

        # 本地交换
        self.segments[idx], self.segments[new_idx] = self.segments[new_idx], self.segments[idx]

        # 生成新的 segment_id 顺序列表
        sorted_segs = sorted(self.segments, key=lambda s: s.order)
        new_order_ids = [s.segment_id for s in sorted_segs]

        # 调后端接口
        pid = self.state.current_project.project_id
        resp = await self.state.backend.reorder_segments(pid, new_order_ids)

        if resp.get("success"):
            # 刷新本地 order
            for i, sid in enumerate(new_order_ids):
                for seg in self.segments:
                    if seg.segment_id == sid:
                        seg.order = i
                        break
            self._render_segments()
            ui.notify(f"已调整至位置 {new_idx + 1}", type="info")
        else:
            ui.notify("调整顺序失败", type="negative")
            # 回滚（重新拉取）
            await self._init_segments()

    async def _delete_segment(self, idx: int):
        """删除片段"""
        if len(self.segments) <= 1:
            ui.notify("至少保留一个片段", type="warning")
            return

        seg = self.segments[idx]
        pid = self.state.current_project.project_id

        resp = await self.state.backend.delete_segment(pid, seg.segment_id)
        if resp.get("success"):
            self.segments.pop(idx)
            # 重新拉取确保 order 正确
            await self._init_segments()
            ui.notify("片段已删除", type="info")
        else:
            ui.notify(f"删除失败: {resp.get('message')}", type="negative")

    async def _add_segment(self):
        """添加新片段（尾部）"""
        if len(self.segments) >= 20:
            ui.notify("最多支持20个片段", type="warning")
            return

        pid = self.state.current_project.project_id
        # 获取最后一个 segment_id 作为 after_segment_id
        sorted_segs = sorted(self.segments, key=lambda s: s.order)
        after_id = sorted_segs[-1].segment_id if sorted_segs else ""

        resp = await self.state.backend.add_segment(pid, after_segment_id=after_id)
        if resp.get("success"):
            new_seg = resp.get("new_segment")
            if new_seg:
                self.segments.append(new_seg)
                self._render_segments()
                ui.notify("已添加新片段", type="positive")
        else:
            ui.notify(f"添加失败: {resp.get('message')}", type="negative")

    def _open_upload_dialog(self, idx: int):
        """打开上传对话框"""
        with ui.dialog() as dlg, ui.card().classes('p-6 bg-slate-800 border-slate-700'):
            ui.label(f'上传片段 #{idx + 1}').classes('text-white font-bold mb-4')
            ui.upload(auto_upload=True,
                      on_upload=lambda e: self._handle_segment_upload(e, idx, dlg)).classes(
                'w-64').props('accept=.mp4,.mov,.avi color=green')
            ui.button('取消', on_click=dlg.close).props('flat').classes('mt-4 text-white')
        dlg.open()

    async def _handle_segment_upload(self, e: events.UploadEventArguments, idx: int, dlg: ui.dialog):
        """处理视频上传（覆写上传）"""
        try:
            if not hasattr(e, 'file'):
                ui.notify("上传组件异常", type="negative")
                return
            file_data = await e.file.read()
            if len(file_data) == 0:
                ui.notify("文件读取失败", type="negative")
                return

            seg = self.segments[idx]
            pid = self.state.current_project.project_id

            ui.notify(f"正在上传 {e.file.name} ...", type="info")

            # 新接口：直接传 segment_id，后端自动创建或覆写
            resp = await self.state.backend.upload_segment_video(
                project_id=pid,
                segment_id=seg.segment_id,
                file_data=file_data,
                file_name=e.file.name)

            if resp.get("success"):
                # 更新本地状态
                seg.video_url = resp.get("video_url")
                seg.video_duration = resp.get("duration")
                seg.file_name = e.file.name
                ui.notify("上传成功！", type="positive")
                await dlg.close()
                self._render_segments()
            else:
                ui.notify(f"上传失败: {resp.get('message', '未知错误')}", type="negative")
                await dlg.close()
        except Exception as ex:
            ui.notify(f"上传异常: {str(ex)}", type="negative")
            await dlg.close()

    # ==================================================================
    # 【修复】BGM 上传：不再直接访问 backend._storage
    # ==================================================================
    async def _handle_bgm_upload(self, e: events.UploadEventArguments):
        """【修复】通过 API 上传 BGM，不再直接访问 _storage"""
        try:
            file_data = await e.file.read()
            resp = await self.state.backend.upload_bgm(
                project_id=self.state.current_project.project_id,
                file_data=file_data,
                file_name=e.file.name
            )
            if resp.get("success"):
                self.bgm_url = resp.get("bgm_url")
                ui.notify("背景音乐已上传", type="positive")
            else:
                ui.notify(f"BGM上传失败: {resp.get('message')}", type="negative")
        except Exception as ex:
            ui.notify(f"BGM上传失败: {str(ex)}", type="negative")

    # ------------------------------------------------------------------
    # 【修复】预览恢复：通过 API 检查文件是否存在
    # ------------------------------------------------------------------
    async def _try_restore_preview(self):
        """【修复】通过后端 API 检查预览文件是否存在，安全恢复"""
        if not self._fixed_preview_url:
            return
        exists = await self.state.backend.check_file_exists(self._fixed_preview_url)
        if exists:
            self.preview_url = self._fixed_preview_url
            self._update_preview()
        else:
            print(f"[INFO] 预览文件不存在: {self._fixed_preview_url}")

    # ------------------------------------------------------------------
    # 【修复】下载预览：通过 API 检查文件是否存在后再下载
    # ------------------------------------------------------------------
    async def _download_preview(self):
        if not self.preview_url:
            ui.notify("请先生成预览视频", type="warning")
            return
        # 【修复】先检查文件是否存在
        exists = await self.state.backend.check_file_exists(self.preview_url)
        if not exists:
            ui.notify("预览文件不存在，请先生成", type="warning")
            return
        filename = f"fhfp_{self.state.current_project.project_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
        ui.download(src=self.preview_url, filename=filename)
        ui.notify("下载已开始，请查看浏览器下载栏", type="positive")


    def _is_product_mode(self) -> bool:
        try:
            return self.state.current_project.mode == CreationMode.PRODUCT_INTRO
        except:
            return False

    # TTS、数字人选择（保持原有逻辑，略）
    def _build_tts_option(self, voice_id: str, label: str, desc: str, icon: str) -> None:
        is_selected = self.tts_voice == voice_id
        border_color = "border-green-500" if is_selected else "border-slate-700"
        bg_color = "bg-slate-700/50" if is_selected else "bg-slate-800"
        text_color = "text-green-400" if is_selected else "text-slate-400"
        card = ui.card().classes(
            f'flex-1 p-4 cursor-pointer transition-all duration-300 hover:scale-[1.02] '
            f'{bg_color} border-2 {border_color}'
        )
        card.on('click', lambda e, vid=voice_id: self._select_tts(vid))
        with card:
            with ui.row().classes('w-full items-center gap-3'):
                ui.icon(icon, size='28px').classes(text_color)
                with ui.column().classes('gap-1'):
                    ui.label(label).classes(f'font-bold {text_color}')
                    ui.label(desc).classes('text-xs text-slate-500')
            if is_selected:
                with ui.row().classes('w-full justify-end mt-2'):
                    ui.icon('check_circle', size='20px').classes('text-green-400')

    def _select_tts(self, voice_id: str) -> None:
        self.tts_voice = voice_id if self.tts_voice != voice_id else None
        self.tts_container.clear()
        with self.tts_container:
            if self._is_product_mode():
                with ui.card().classes('w-full p-4 bg-slate-800 border-slate-700'):
                    ui.label('TTS 配音音色').classes('text-sm font-bold text-white mb-3')
                    with ui.row().classes('w-full gap-4'):
                        self._build_tts_option("Yunxi", "云希 · 男声", "沉稳大气，适合产品讲解", "male")
                        self._build_tts_option("Xiaoying", "晓伊 · 女声", "亲切温柔，适合情感表达", "female")
        msg = f"已选择: {voice_id}" if self.tts_voice else "已取消音色选择"
        ui.notify(msg, type="positive")

    def _build_avatar_option(self, avatar_id: str, label: str, gif_url: str, static_url: str) -> None:
        is_selected = self.digital_human == avatar_id
        border_color = "border-green-500" if is_selected else "border-slate-700"
        bg_color = "bg-slate-700/30" if is_selected else "bg-slate-800"
        label_color = "text-green-400" if is_selected else "text-slate-400"
        card = ui.card().classes(
            f'w-[140px] cursor-pointer transition-all duration-300 hover:scale-[1.05] '
            f'{bg_color} border-2 {border_color} overflow-hidden'
        )
        card.on('click', lambda e, aid=avatar_id: self._select_avatar(aid))
        with card:
            img_src = gif_url if is_selected else static_url
            img_style = "" if is_selected else 'filter: grayscale(60%);opacity:0.7;'
            img_html = f'<div style="width:100%;height:120px;overflow:hidden;display:flex;align-items:center;justify-content:center;background:#1e293b;"><img src="{img_src}" style="width:100%;height:100%;object-fit:cover;{img_style}"></div>'
            ui.html(img_html).classes('w-full')
            with ui.row().classes('w-full justify-center items-center gap-1 py-2'):
                ui.label(label).classes(f'text-sm font-bold {label_color}')
                if is_selected:
                    ui.icon('check_circle', size='16px').classes('text-green-400')

    def _select_avatar(self, avatar_id: str) -> None:
        self.digital_human = avatar_id if self.digital_human != avatar_id else None
        self.avatar_container.clear()
        with self.avatar_container:
            if self._is_product_mode():
                with ui.card().classes('w-full p-4 bg-slate-800 border-slate-700'):
                    ui.label('数字人主播').classes('text-sm font-bold text-white mb-3')
                    with ui.row().classes('w-full gap-4 justify-center'):
                        self._build_avatar_option("man", "男主播",
                                                  "/storage/avatars/man.gif", "/storage/avatars/man_static.png")
                        self._build_avatar_option("woman", "女主播",
                                                  "/storage/avatars/girl.gif", "/storage/avatars/girl_static.png")
        status = "已选择" if self.digital_human == avatar_id else "已取消"
        msg_type = "positive" if self.digital_human == avatar_id else "info"
        ui.notify(f"{status}: {avatar_id}", type=msg_type)

    # 预览、合成、下载、发布（完全保持原有逻辑）
    def _do_rough_cut(self):
        seg_ids = [s.segment_id for s in self.segments if s.video_url]
        if not seg_ids:
            ui.notify("请先上传至少一个视频片段", type="warning")
            return
        if self._is_product_mode():
            if (self.tts_voice and not self.digital_human) or (not self.tts_voice and self.digital_human):
                ui.notify("TTS音色和数字人形象需要同时选择或同时不选", type="warning")
                return
        self._is_rendering = True
        if hasattr(self, 'btn_preview'):
            self.btn_preview.disable()
        ui.notify("开始合成，请稍候...", type="info", timeout=10000)
        asyncio.create_task(self._run_rough_cut_task(seg_ids))

    async def _run_rough_cut_task(self, seg_ids: List[str]):
        try:
            resp = await self.state.backend.rough_cut(
                project_id=self.state.current_project.project_id,
                segment_sequence=seg_ids,
                bgm_url=self.bgm_url,
                bgm_volume=self.bgm_volume,
                subtitle_enabled=self.subtitle_on,
                tts_voice=self.tts_voice,
                digital_human=self.digital_human,
                mode=self.state.current_project.mode.value
            )
            self._is_rendering = False
            if hasattr(self, 'btn_preview'):
                self.btn_preview.enable()
            if resp.get("success"):
                self.preview_url = self._fixed_preview_url
                self._update_preview()
                duration = resp.get('duration', 0)
                features = resp.get('features_applied', {})
                msg = f"合成完成！时长: {duration:.1f}秒"
                if features.get('tts'):
                    msg += " | 已启用TTS+数字人"
                elif features.get('subtitle'):
                    msg += " | 已启用字幕"
                ui.notify(msg, type="positive", timeout=8000)
            else:
                ui.notify(f"合成失败: {resp.get('message')}", type="negative")
        except Exception as ex:
            self._is_rendering = False
            if hasattr(self, 'btn_preview'):
                self.btn_preview.enable()
            ui.notify(f"合成异常: {str(ex)}", type="negative")

    def _update_preview(self):
        if not self.preview_card:
            return
        self.preview_card.clear()
        with self.preview_card:
            if self.preview_url:
                url = f"{self.preview_url}?t={int(datetime.now().timestamp())}"
                ui.video(url).classes('w-full h-full')
            else:
                with ui.column().classes('items-center justify-center h-full'):
                    ui.icon('play_circle_outline', size='48px').classes('text-slate-600 mb-2')
                    ui.label('粗剪成片预览区').classes('text-slate-500 text-lg')
                    ui.label('点击"预览合成"生成真实视频').classes('text-slate-600 text-sm mt-2')



    def _publish_work(self):
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
                ui.button('确认发布', on_click=lambda: asyncio.create_task(self._do_publish(
                    dlg, title_inp.value, desc_inp.value, tags_inp.value
                ))).classes('bg-green-500 text-black font-bold')
        dlg.open()

    async def _do_publish(self, dlg: ui.dialog, title: str, description: str, tags_str: str):
        tags = [t.strip() for t in tags_str.split(',') if t.strip()]
        try:
            resp = await self.state.backend.publish_video(
                project_id=self.state.current_project.project_id,
                title=title,
                description=description,
                tags=tags
            )
            if resp.get("success"):
                ui.notify("发布成功！已存入个人作品库", type="positive")
                await dlg.close()
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

    async def get_data_source(self) -> List[VideoWork]:
        """从后端获取公共案例数据"""
        try:
            resp = await self.state.backend.get_case_library()
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

    async def _load_works(self) -> None:
        self.works_grid.clear()
        try:
            resp = await self.state.backend.get_personal_works(
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

    async def get_data_source(self) -> List[VideoWork]:
        """从后端获取个人作品"""
        if not self.state.is_authenticated():
            return []

        try:
            resp = await self.state.backend.get_personal_works(
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


def main() -> None:
    state = AppState()
    # state.set_backend(MockBackendAPI())
    # 生产环境切换：
    from hybrid_backend import HybridBackendAPI
    state.set_backend(HybridBackendAPI(base_url="http://localhost:8000"))

    ui.run(
        title=APP_TITLE,
        favicon='🌱',
        dark=True,
        reload=False,
        # uvicorn_reload_dirs="../frontend",
        port=3000,
        show=True
    )


if __name__ in {"__main__","__mp_main__"}:
    main()


