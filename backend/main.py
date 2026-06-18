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

        # 初始化短视频创作助手智能体系统（启动时加载 Agent1）
        self.vas = Video_Assistant_System(lamb=2e-1)
        self.vas.idea_history=[{"role":"system","content":idea_sys}]
        self.vas.abstract_history=[{"role":"system","content":abstract_sys}]
        self.vas.scene_history=[{"role":"system","content":scene_sys}]

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

        # 重置智能体记忆
        self.vas.idea_history=[{"role":"system","content":idea_sys}]
        self.vas.abstract_history=[{"role":"system","content":abstract_sys}]
        self.vas.scene_history=[{"role":"system","content":scene_sys}]

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
            self.vas._load_abstract_agent()
        elif agent_name == "scene":
            self.vas._load_scene_agent()

    async def agent1_chat(self, req: Agent1ChatRequest) -> Dict[str, Any]:
        await self._ensure_agent("abstract")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_agent1_chat, req)

    def _sync_agent1_chat(self, req: Agent1ChatRequest) -> Dict[str, Any]:
        self.vas.mode_id=req.mode_id
        self.vas.idea_history.append({"role":"user","content":req.message})
        response = self.vas.generate_response("",max_new_tokens=768,choice="abstract",history=self.vas.idea_history)
        print(f"Agent: {response}")
        self.vas.idea_history.append({"role":"assistant","content":response})
        total_words=sum([len(turn["content"]) for turn in self.vas.idea_history[1:]])
        agent_words=sum([len(turn["content"]) for turn in self.vas.idea_history[2::2]])
        print(f'History conversation length:{total_words}.Agent\'s answer has {agent_words} words.')

        return {
            "success": True,
            "reply": response,
            "summary_draft": "",
            "suggested_questions": ["请详细描述种植过程", "产品与竞品的主要区别是什么？"],
            "turn_id": len(req.history) + 1
        }

    async def agent1_summary(self, req: Agent1SummaryRequest) -> Dict[str, Any]:
        await self._ensure_agent("abstract")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_agent1_summary, req)

    def _sync_agent1_summary(self, req: Agent1SummaryRequest) -> Dict[str, Any]:
        # 也可以直接使用self.vas.idea_history[-1]["content"]，因为用户无法修改、补充、删减聊天区域的内容
        self.vas.abstract_history.append({"role":"user","content":req.highlights})
        summary = self.vas.generate_response("",768,"abstract",self.vas.abstract_history)
        self.vas.abstract_history.append({"role":"assistant","content":summary})
        print(f'Agent1 Summary:{summary}')

        return {
            "success": True,
            "summary": summary,
            "keywords": ["农产品", "新鲜", "有机"],
            "emotion_tags": ["朴实", "真诚"],
            "target_audience": "一二线城市注重健康的年轻消费者"
        }


    def escape_json_quotes(self, s: str) -> str:
        """
        将字符串中所有未转义的双引号转为 \"，不考虑字符串内有字符串的情况（如果有，这个函数会雪上加霜，将影响json解析）
        比如Agent2输出的字符串如果里面还有双引号就会失败（但是单引号可以）: \\\"scene\\\": \\\"视频展示了\\\\\"乡村户外庭院\\\\\"场景，阳光明媚。
        保留已转义的 \"
        """
        # 保护已有的 \"，去掉换行符
        protected = s.replace('\\"', '\x00').replace('\\n', '').replace('\n', '')
        # 替换剩余的 "（不考虑字典字符串值内有用于概念性阐释的双引号）
        escaped = protected.replace('"', '\\"')
        # 每个片段前后双引号不转义
        escaped = escaped.replace('\\"{', '"{').replace('}\\"', '}"')

        # 恢复 \"
        return escaped.replace('\x00', '\\"')


    async def agent2_generate(self, req: Agent2ShotsRequest) -> Dict[str, Any]:
        await self._ensure_agent("scene")
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_agent2_generate, req)

    def _sync_agent2_generate(self, req: Agent2ShotsRequest) -> Dict[str, Any]:
        # 不能直接使用self.vas.abstract_history[-1]["content"]，因为可能用户有修改、补充、删减
        self.vas.scene_history.append({"role":"user","content":req.summary})
        shots=self.vas.generate_response("",12288,"scene",self.vas.scene_history)
        self.vas.scene_history.append({"role":"assistant","content":shots})
        print(f'Agent2 Shots Suggestion:{shots}')

        self.vas.log_chat()
        try:
            shots=json.loads(self.escape_json_quotes(shots.strip().removeprefix('```json').removeprefix('```').removesuffix('```').strip()))
        except json.JSONDecodeError as e:
            message='JSON decoding error.'
            print(message)
            logger.error(f'{message}{e}')
            return {"success":False,"shots":[],"total_duration":0,"storyline_arc":message}


        if isinstance(shots,list) and len(shots)>0:
            shots=[json.loads(s) for s in shots]
            try:
                if not isinstance(shots[0].get("text"), list):
                    # 不能使用数字人和TTS、字幕烧录
                    message = "文案格式不是字典列表。"
                    print(message)
                    return {
                        "success": True,
                        "shots": shots,
                        "total_duration": sum(s.get("duration_hint", 5) for s in shots),
                        "storyline_arc": message
                    }
            except Exception as e:
                print(f'Internal json decode error:{e}')


        elif isinstance(shots,dict):
            message="JSON decodes string into dictionary but not list."
            print(message)
            shots=[shots]
            return {
                "success": True,
                "shots": shots,
                "total_duration": sum(s.get("duration_hint", 5) for s in shots),
                "storyline_arc": message
            }


        return {
            "success": True,
            "shots": shots,
            "total_duration": sum(s.get("duration_hint", 5) for s in shots),
            "storyline_arc": "正常生成片段拍摄内容和方法建议"
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
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False, workers=1)


