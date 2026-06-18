#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
backend/main.py
===============
FastAPI 后端主程序（简化版）

只保留 AI 推理相关接口：
  - Agent1: 多轮对话 / 摘要生成
  - Agent2: 拍摄建议生成

其他所有操作（上传、片段管理、粗剪、发布）都在前端本地完成。
2026/06/18 新增大模型输出后处理函数
"""

from finetune import Video_Assistant_System, idea_sys, abstract_sys, scene_sys
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


logger = logging.getLogger("FHFP-Backend")


class BackendService:
    """只保留 AI 推理服务"""
    def __init__(self):
        self._users: Dict[str, Dict] = {}
        self._username_index: Dict[str, str] = {}
        self._phone_index: Dict[str, str] = {}
        self._projects: Dict[str, Dict] = {}
        self._works: Dict[str, List[Dict[str, Any]]] = {}

        # 初始化模型（启动时加载 Agent1）
        # self.vas = Video_Assistant_System(lamb=2e-1)
        self.vas=0
        # self.vas._load_abstract_agent()
        # self.vas.abstract_model.eval()
        self._current_agent = "abstract"  # 当前加载的模型

        # 防止并发切换模型
        self._model_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # 用户认证
    # ------------------------------------------------------------------

    async def register(self, username: str, password: str, phone: Optional[str] = None,
                       nickname: Optional[str] = None) -> \
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
        return {
            "success": True,
            "project_id": pid,
            "created_at": self._projects[pid]["created_at"],
            "status": "draft"
        }


    async def _ensure_agent(self, agent_name: str):
        if self._current_agent == agent_name:
            return
        async with self._model_lock:
            if self._current_agent == agent_name:
                return
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._sync_switch, agent_name)
            self._current_agent = agent_name

    def _sync_switch(self, agent_name: str):
        if agent_name == "abstract":
            self.vas = 0
        elif agent_name == "scene":
            self.vas = 1

    async def agent1_chat(self, req: Agent1ChatRequest) -> Dict[str, Any]:
        await self._ensure_agent("abstract")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_agent1_chat, req)

    def _sync_agent1_chat(self, req: Agent1ChatRequest) -> Dict[str, Any]:
        response = "This is a simple test to ensure functionality."
        print(f"Agent: {response}")
        print(f'History conversation length:100.')
        return {
            "success": True,
            "reply": "This is the last turn reponse from VAS.",
            "summary_draft": "",
            "suggested_questions": ["请详细描述种植过程", "产品与竞品的主要区别是什么？"],
            "turn_id": len(req.history) + 1
        }

    async def agent1_summary(self, req: Agent1SummaryRequest) -> Dict[str, Any]:
        await self._ensure_agent("abstract")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_agent1_summary, req)

    def _sync_agent1_summary(self, req: Agent1SummaryRequest) -> Dict[str, Any]:
        summary = "This is a fake summary to verify the procedure."
        return {
            "success": True,
            "summary": summary,
            "keywords": ["农产品", "新鲜", "有机"],
            "emotion_tags": ["朴实", "真诚"],
            "target_audience": "一二线城市注重健康的年轻消费者"
        }

    def escape_json_quotes(self, s:str) -> str:
        """
        将字符串中所有未转义的双引号转为 \"
        保留已转义的 \"
        """
        # 保护已有的 \"
        protected = s.replace('\\"', '\x00').replace('\\n', '').replace('\n', '')
        # 替换剩余的 "
        escaped = protected.replace('"', '\\"')
        escaped = escaped.replace('\\"{', '"{').replace('}\\"', '}"')

        # 恢复 \"
        return escaped.replace('\x00', '\\"')

    async def agent2_generate(self, req: Agent2ShotsRequest) -> Dict[str, Any]:
        await self._ensure_agent("scene")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_agent2_generate, req)

    def _sync_agent2_generate(self, req: Agent2ShotsRequest) -> Dict[str, Any]:
        """}
        shots = [
            {"scene": "全景：果园/农田清晨全景，阳光洒落", "audio": "自然环境音：鸟鸣、微风",
             "text": [{"人物":"男性","台词":"清晨的第一缕阳光，照亮了我们的果园。"}]},
            {"scene": "特写：农产品表面纹理，露水欲滴", "audio": "轻快吉他背景音乐起",
             "text": [{"人物":"男性","台词":"每一颗果实，都饱含大自然的馈赠。"}]},
            {"scene": "中景：农户采摘/包装过程，动作熟练", "audio": "包装纸摩擦声+轻快节奏",
             "text": [{"人物":"男性","台词":"从田间到餐桌，我们只追求最新鲜。"}]},
            {"scene": "近景：双手捧起产品展示，微笑", "audio": "音乐渐强，环境音淡出",
             "text": [{"人物":"男性","台词":"选择我们，就是选择健康与安心。"}]}
        ]
        """
        shots2="[\"{\\n  \\\"scene\\\": \\\"视频展示了乡村户外庭院场景，阳光明媚。一名男子（小王）手持几只鸭子向镜头介绍，并拿出手机拍摄。随后转场至室内，一位老人（爷爷）在桌前讲解腌制技巧，桌上放有盐和花椒等配料。接着是特写镜头展示手工抹酱、挂网风干的板鸭成品及成品被粉丝抢购的热闹画面。最后以切开的板鸭特写结束，展示了其晶莹的油脂和红亮的颜色。\\\",\\n  \\\"audio\\\": \\\"背景为轻快舒缓的纯音乐配乐。主要人声清晰，包含男子的开场白、老人的专业指导以及感叹词。中间穿插了商业配音的评论音效（如‘大运平台’）。结尾处有切开食物时发出的声音。\\\",\\n  \\\"text\\\": [\\n    {\\n      \\\"figure\\\": \\\"主人公\\\",\\n      \\\"script\\\": \\\"大家好，我是小王，我回老家来学习怎么制作板鸭。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"旁白/主人公\\\",\\n      \\\"script\\\": \\\"我们家养的有几种鸭子，有一种叫王大炮的，个头大，跑得也快。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"老人（爷爷）\\\",\\n      \\\"script\\\": \\\"要想做成火候大的板鸭，选材很重要。鸭子肥瘦均匀，肉质紧实。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"老人（爷爷）\\\",\\n      \\\"script\\\": \\\"配料一定要足，盐是要用花盐，还有花椒。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"老人（爷爷）\\\",\\n      \\\"script\\\": \\\"抹完酱之后把鸭子挂起来，记得要放在太阳底下。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"老人（爷爷）\\\",\\n      \\\"script\\\": \\\"很多人喜欢吃这个板鸭，香而不腻。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"旁白\\\",\\n      \\\"script\\\": \\\"很多人喜欢吃这个板鸭，香而不腻。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"旁白\\\",\\n      \\\"script\\\": \\\"很多人都喜欢吃这种板鸭。\\\"\\n    }\\n  ]\\n}\", \"{\\n  \\\"scene\\\": \\\"视频展示了传统美食板鸭的制作与销售过程。首先，在阳光明媚的室外，一只白色鸭子挂在木杆上晾晒，旁边有一桶已加工好的板鸭。接着切换到室内，一位老奶奶正在品尝产品并展示给镜头。随后是一系列特写镜头：打开包装、取出鸭掌、展示诱人的色泽以及成品。最后是顾客在直播中热情互动的画面。整体光线自然，运镜平稳，聚焦于美食细节和人物状态。\\\",\\n  \\\"audio\\\": \\\"背景播放着轻快的纯音乐。开头有清脆的敲击声。主要声音包括：老奶奶品尝时的满足感慨、展示产品的自豪语气、以及网络直播环境下热烈的弹幕与评论音效（如“秒抢光”、“又好吃又便宜”），营造出产品受欢迎的氛围。\\\",\\n  \\\"text\\\": [\\n    {\\n      \\\"figure\\\": \\\"老奶奶\\\",\\n      \\\"script\\\": \\\"哇，真香啊！\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"主播\\\",\\n      \\\"script\\\": \\\"给大家看下咱们今天的主要内容，是一种传统的板鸭，香而不腻，很多人吃过一次就忘不了。\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"观众1\\\",\\n      \\\"script\\\": \\\"又是熟悉的味道\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"观众2\\\",\\n      \\\"script\\\": \\\"每一年必买\\\"\\n    },\\n    {\\n      \\\"figure\\\": \\\"观众3\\\",\\n      \\\"script\\\": \\\"提前预定一下\\\"\\n    }\\n  ]\\n}\", \"{\\n  \\\"scene\\\": \\\"视频展示了如何切开一块深色油状物质。镜头从顶部开始，沿着横向切口进行特写拍摄。光线明亮，背景模糊，主体突出。\\\",\\n  \\\"audio\\\": \\\"背景音乐持续且节奏轻快，伴有轻微的环境杂音。没有明显的对话或人声噪音。\",\n  \\\"text\\\": []\\n}\"]"
        try:
            shots2=json.loads(self.escape_json_quotes(shots2))
            shots2=[json.loads(s) for s in shots2]
            print(shots2[0].get("text"))
        except json.JSONDecodeError as e:
            print(f"JSON decode error:{e}.")

        return {
            "success": True,
            "shots": shots2,
            "total_duration": sum(s.get("duration_hint", 5) for s in shots2),
            "storyline_arc": "起承转合：环境铺垫 -> 产品展示 -> 过程信任 -> 情感号召"
        }


backend = BackendService()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("AI Backend started")
    yield
    del backend.vas
    import gc
    gc.collect()
    import torch
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
    logger.info("AI Backend shutdown")


app = FastAPI(title="FHFP AI Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================================================================
# 用户注册、项目管理和Agent对话 路由（FastAPI 后端只保留这些）
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


@app.get("/health")
async def health_check():
    return {"status": "ok", "current_agent": backend._current_agent}


if __name__ == "__main__":
    uvicorn.run("main_with_empty_model_v2:app", host="0.0.0.0", port=8000, reload=False, workers=1)


