#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
FHFP HTTP Backend Client
前端 HTTP 客户端实现，对接 FastAPI 后端服务
=============================================================================

文件位置: frontend/http_backend.py
职责:
    - 实现 BackendAPI Protocol（接口契约）
    - 使用 requests 库发送同步 HTTP 请求到 FastAPI 后端
    - 处理请求序列化、错误处理、响应解析

使用方式:
    from http_backend import HttpBackendAPI
    from main import AppState

    state = AppState()
    state.set_backend(HttpBackendAPI(base_url="http://localhost:8000"))
=============================================================================
"""

import logging
from typing import Any, Dict, List, Optional

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
import requests
from common.schema import *

# 从前端主模块导入 Protocol（确保路径正确）
# 如果分离成独立文件，需要调整导入路径
try:
    from main_kimi import BackendAPI
except ImportError:
    # 独立运行时定义 Protocol（避免循环导入）
    from typing import Protocol

    class BackendAPI(Protocol):
        def authenticate(self, username: str, password: str) -> Dict[str, Any]: ...

        def create_project(self, user_id: str, mode: int, title: str, description: str,
                           province: Optional[str] = None) -> Dict[str, Any]: ...

        def agent1_chat(self, project_id: str, message: str, history: List[Dict[str, str]]) -> Dict[str, Any]: ...

        def agent1_generate_summary(self, project_id: str, highlights: List[str], custom_notes: Optional[str] = None) -> \
        Dict[str, Any]: ...

        def agent2_generate_shots(self, project_id: str, summary: str, mode: int,
                                  style_preference: Optional[str] = None) -> Dict[str, Any]: ...

        def upload_segment_video(self, project_id: str, segment_id: str, file_data: bytes, file_name: str,
                                 content_type: str) -> Dict[str, Any]: ...

        def rough_cut(self, project_id: str, segment_sequence: List[str], bgm_url: Optional[str] = None,
                      bgm_volume: float = 0.3, subtitle_enabled: bool = True, tts_voice: Optional[str] = None,
                      digital_human: Optional[str] = None) -> Dict[str, Any]: ...

        def publish_video(self, project_id: str, title: str, description: str, tags: List[str],
                          cover_frame: Optional[int] = 0) -> Dict[str, Any]: ...

        def get_case_library(self, category: Optional[str] = None, keyword: Optional[str] = None,
                             sort_by: str = "views", page: int = 1, page_size: int = 12) -> Dict[str, Any]: ...

        def get_personal_works(self, user_id: str, keyword: Optional[str] = None, sort_by: str = "date", page: int = 1,
                               page_size: int = 12) -> Dict[str, Any]: ...

logger = logging.getLogger("FHFP-HTTP-Client")


class HttpBackendAPI(BackendAPI):
    """
    BackendAPI 的 HTTP 客户端实现。

    使用 Pydantic Schema 构建请求体，确保前后端字段一致。
    """

    ENDPOINTS = {
        "register": "/api/auth/register",
        "authenticate": "/api/auth/login",
        "create_project": "/api/projects",
        "agent1_chat": "/api/agent1/chat",
        "agent1_summary": "/api/agent1/summary",
        "agent2_shots": "/api/agent2/shots",
        "upload_segment": "/api/upload/segment",
        "upload_bgm": "/api/upload/bgm",
        "rough_cut": "/api/video/rough-cut",
        "publish": "/api/video/publish",
        "case_library": "/api/cases",
        "personal_works": "/api/works",
    }

    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self.timeout = 120

    def _post(self, endpoint_key: str, payload: dict,
              use_json: bool = True,
              files: Optional[dict] = None,
              data: Optional[dict] = None) -> dict:
        """统一 POST 请求（保持原有实现）"""
        url = f"{self.base_url}{self.ENDPOINTS[endpoint_key]}"
        try:
            if files or data:
                headers = {k: v for k, v in self.session.headers.items()
                           if k.lower() != "content-type"}
                resp = self.session.post(url, data=data, files=files,
                                        headers=headers, timeout=self.timeout)
            else:
                resp = self.session.post(url, json=payload, timeout=self.timeout)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            return {"success": False, "message": "请求超时"}
        except requests.exceptions.ConnectionError:
            return {"success": False, "message": "无法连接到后端"}
        except Exception as e:
            logger.error(f"Request failed: {url}, error: {e}")
            return {"success": False, "message": str(e)}

    def _get(self, endpoint_key: str, params: Optional[dict] = None) -> dict:
        """统一 GET 请求"""
        url = f"{self.base_url}{self.ENDPOINTS[endpoint_key]}"
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"GET failed: {url}, error: {e}")
            return {"success": False, "message": str(e)}

    # ==============================================================================
    # 认证接口（使用 Schema）
    # ==============================================================================

    def register(self, username: str, password: str,
                 phone: Optional[str] = None,
                 nickname: Optional[str] = None) -> Dict[str, Any]:
        """
        用户注册。

        Schema: RegisterRequest(username, password, phone, nickname)
        后端接收: POST /api/auth/register
        """
        req = RegisterRequest(username=username, password=password,
                              phone=phone, nickname=nickname)
        payload = req.model_dump(exclude_none=True)
        return self._post("register", payload)

    def check_username_exists(self, username: str) -> Dict[str, Any]:
        """
        检查账号名是否已存在（注册时实时查重）

        对应后端: GET /api/auth/check-username?username=xxx

        Returns:
            Dict: {success, exists: bool}
        """
        return self._get("check_username", {"username": username})

    def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        """
        用户登录。

        Schema: LoginRequest(username, password)
        后端接收: POST /api/auth/login
        """
        req = LoginRequest(username=username, password=password)
        payload = req.model_dump()

        return self._post("authenticate", payload)

    def create_project(self, user_id: str, mode: int, title: str, description: str,
                       province: Optional[str] = None) -> Dict[str, Any]:
        """
        创建新项目。

        Schema: CreateProjectRequest(user_id, mode, title, description, province)
        后端接收: POST /api/projects
        """
        req = CreateProjectRequest(
            user_id=user_id,
            mode=mode,
            title=title,
            description=description,
            province=province
        )
        payload = req.model_dump(exclude_none=True)
        return self._post("create_project", payload)

    # ==============================================================================
    # Agent 1 接口（使用 Schema）
    # ==============================================================================

    def agent1_chat(self, project_id: str, message: str,
                    history: List[Dict[str, str]],mode_id:int) -> Dict[str, Any]:
        """
        Agent1 多轮对话。

        Schema: Agent1ChatRequest(project_id, message, history, mode_id, temperature)
        后端接收: POST /api/agent1/chat
        """
        req = Agent1ChatRequest(project_id=project_id, message=message,
                                history=history, mode_id=mode_id)
        payload = req.model_dump()

        return self._post("agent1_chat", payload)


    def agent1_generate_summary(self, project_id: str,
                                 highlights: List[str],
                                 custom_notes: Optional[str] = None,mode_id:int=0) -> Dict[str, Any]:
        """
        Agent1 摘要生成。

        Schema: Agent1SummaryRequest(project_id, highlights, custom_notes, mode_id)
        后端接收: POST /api/agent1/summary
        """
        req = Agent1SummaryRequest(project_id=project_id, highlights=highlights,
                                   custom_notes=custom_notes, mode_id=mode_id)
        payload = req.model_dump(exclude_none=True)

        return self._post("agent1_summary", payload)

    # ==============================================================================
    # Agent 2 接口（使用 Schema）
    # ==============================================================================

    def agent2_generate_shots(self, project_id: str, summary: str,
                              mode: int,
                              style_preference: Optional[str] = None) -> Dict[str, Any]:
        """
        Agent2 拍摄建议生成。

        Schema: Agent2ShotsRequest(project_id, summary, mode_id, style_preference)
        后端接收: POST /api/agent2/shots
        """
        req = Agent2ShotsRequest(project_id=project_id, summary=summary,
                                 mode_id=mode, style_preference=style_preference)
        payload = req.model_dump(exclude_none=True)


        return self._post("agent2_shots", payload)

    # ==============================================================================
    # 粗剪与发布（使用 Schema）
    # ==============================================================================

    def rough_cut(self, project_id: str, segment_sequence: List[str],
                   bgm_url: Optional[str] = None,
                   bgm_volume: float = 0.3,
                   subtitle_enabled: bool = True,
                   tts_voice: Optional[str] = None,
                   digital_human: Optional[str] = None) -> Dict[str, Any]:
        """
        视频粗剪。

        Schema: RoughCutRequest(...)
        后端接收: POST /api/video/rough-cut
        """
        req = RoughCutRequest(
            project_id=project_id,
            segment_sequence=segment_sequence,
            bgm_url=bgm_url,
            bgm_volume=bgm_volume,
            subtitle_enabled=subtitle_enabled,
            tts_voice=tts_voice,
            digital_human=digital_human
        )
        payload = req.model_dump(exclude_none=True)

        return self._post("rough_cut", payload)

    def publish_video(self, project_id: str, title: str,
                      description: str, tags: List[str],
                      cover_frame: int = 0) -> Dict[str, Any]:
        """
        发布视频。

        Schema: PublishRequest(...)
        后端接收: POST /api/video/publish
        """
        req = PublishRequest(project_id=project_id, title=title,
                             description=description, tags=tags,
                             cover_frame=cover_frame)
        payload = req.model_dump()


        return self._post("publish", payload)


    def upload_segment_video(self, project_id: str, segment_id: str,
                              file_data: bytes, file_name: str,
                              content_type: str) -> Dict[str, Any]:
        """文件上传（multipart，不使用 JSON Schema）"""
        files = {"file": (file_name, file_data, content_type)}
        data = {"project_id": project_id, "segment_id": segment_id}
        return self._post("upload_segment", {}, use_json=False, files=files, data=data)

    # ------------------------------------------------------------------
    # TODO:7. 双数据库查询接口
    # ------------------------------------------------------------------

    def get_case_library(self, **kwargs) -> Dict[str, Any]:
        return self._get("case_library", {k: v for k, v in kwargs.items() if v is not None})

    def get_personal_works(self, **kwargs) -> Dict[str, Any]:
        return self._get("personal_works", {k: v for k, v in kwargs.items() if v is not None})




# ==============================================================================
# 使用示例
# ==============================================================================

if __name__ == "__main__":
    # 快速测试客户端
    client = HttpBackendAPI(base_url="http://localhost:8000")

    # 测试健康检查
    try:
        resp = requests.get("http://localhost:8000/health", timeout=5)
        print(f"Backend health: {resp.json()}")
    except Exception as e:
        print(f"Backend not available: {e}")
        exit(1)

    # 测试注册
    result = client.register(
        username="test_farmer",
        password="Farm123!@#",
        phone="13800138000",
        nickname="老农张三"
    )
    print(f"Register: {result}")

    # 测试登录
    result = client.authenticate("test_farmer", "Farm123!@#")
    print(f"Login: {result}")

    # 测试查重
    result = client.check_username_exists("test_farmer")
    print(f"Username exists: {result}")




