#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
=============================================================================
FHFP HTTP Backend Client (Async Version)
前端异步 HTTP 客户端实现，对接 FastAPI 后端服务
=============================================================================

文件位置: frontend/http_backend.py
职责:
    - 实现 BackendAPI Protocol（接口契约）
    - 使用 httpx.AsyncClient 发送异步 HTTP 请求到 FastAPI 后端
    - 处理请求序列化、错误处理、响应解析

使用方式:
    from http_backend import HttpBackendAPI
    from main import AppState

    state = AppState()
    state.set_backend(HttpBackendAPI(base_url="http://localhost:8000"))

    # 在页面中 await 调用
    resp = await state.backend.agent1_chat(...)
=============================================================================
"""

import logging
from typing import Any, Dict, List, Optional

import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import httpx
from common.schema import *

# 从前端主模块导入 Protocol（确保路径正确）
# 如果分离成独立文件，需要调整导入路径
try:
    from main_kimi import BackendAPI
except ImportError:
    # 独立运行时定义 Protocol（避免循环导入）
    from typing import Protocol

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
                                 content_type: str="video/mp4") -> Dict[str, Any]: ...

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

logger = logging.getLogger("FHFP-HTTP-Client")


class HttpBackendAPI(BackendAPI):
    """
    BackendAPI 的异步 HTTP 客户端实现。

    使用 Pydantic Schema 构建请求体，确保前后端字段一致。
    基于 httpx.AsyncClient，所有网络操作均为异步非阻塞。
    """
    ENDPOINTS = {
        "register": "/api/auth/register",
        "authenticate": "/api/auth/login",
        "check_username": "/api/auth/check-username",
        "check_phone": "/api/auth/check-phone",
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
        # httpx.AsyncClient 是异步 HTTP 客户端
        self.client = httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=10.0),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    async def close(self):
        """优雅关闭 HTTP 连接池（应用退出时调用）"""
        await self.client.aclose()

    async def _post(self, endpoint_key: str, payload: dict,
              use_json: bool = True,
              files: Optional[dict] = None) -> dict:
        """统一异步 POST 请求"""
        url = f"{self.base_url}{self.ENDPOINTS[endpoint_key]}"
        try:
            if files:
                # 文件上传：移除 Content-Type header，让 httpx 自动设置 multipart boundary
                headers = {k: v for k, v in self.client.headers.items()
                           if k.lower() != "content-type"}
                resp = await self.client.post(
                    url, files=files,
                    headers=headers
                )
            else:
                resp = await self.client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            return {"success": False, "message": "请求超时"}
        except httpx.ConnectError:
            return {"success": False, "message": "无法连接到后端"}
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error: {url}, status: {e.response.status_code}, detail: {e.response.text[:500]}")
            return {"success": False, "message": f"HTTP {e.response.status_code}: {e.response.text[:200]}"}
        except Exception as e:
            logger.error(f"Request failed: {url}, error: {e}")
            return {"success": False, "message": str(e)}

    async def _get(self, endpoint_key: str, params: Optional[dict] = None) -> dict:
        """统一异步 GET 请求"""
        url = f"{self.base_url}{self.ENDPOINTS[endpoint_key]}"
        try:
            resp = await self.client.get(url, params=params)
            resp.raise_for_status()
            return resp.json()
        except httpx.TimeoutException:
            return {"success": False, "message": "请求超时"}
        except httpx.ConnectError:
            return {"success": False, "message": "无法连接到后端"}
        except Exception as e:
            logger.error(f"GET failed: {url}, error: {e}")
            return {"success": False, "message": str(e)}

    # ==============================================================================
    # 1. 认证接口
    # ==============================================================================

    async def register(self, username: str, password: str,
                 phone: Optional[str] = None,
                 nickname: Optional[str] = None) -> Dict[str, Any]:
        req = RegisterRequest(username=username, password=password,
                              phone=phone, nickname=nickname)
        payload = req.model_dump(exclude_none=True)
        return await self._post("register", payload)

    async def check_username_exists(self, username: str) -> Dict[str, Any]:
        return await self._get("check_username", {"username": username})

    async def check_phone_exists(self, phone: str) -> Dict[str, Any]:
        return await self._get("check_phone", {"phone": phone})

    async def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        req = LoginRequest(username=username, password=password)
        payload = req.model_dump()
        return await self._post("authenticate", payload)

    async def create_project(self, user_id: str, mode: int, title: str, description: str,
                       province: Optional[str] = None) -> Dict[str, Any]:
        req = CreateProjectRequest(
            user_id=user_id,
            mode=mode,
            title=title,
            description=description,
            province=province
        )
        payload = req.model_dump(exclude_none=True)
        return await self._post("create_project", payload)

    # ==============================================================================
    # 2. Agent 1 接口
    # ==============================================================================

    async def agent1_chat(self, project_id: str, message: str,
                    history: List[Dict[str, str]], mode_id: int = 0) -> Dict[str, Any]:
        req = Agent1ChatRequest(project_id=project_id, message=message,
                                history=history, mode_id=mode_id)
        payload = req.model_dump()
        return await self._post("agent1_chat", payload)

    async def agent1_generate_summary(self, project_id: str,
                                 highlights: str,
                                 custom_notes: Optional[str] = None, mode_id: int = 0) -> Dict[str, Any]:
        req = Agent1SummaryRequest(project_id=project_id, highlights=highlights,
                                   custom_notes=custom_notes, mode_id=mode_id)
        payload = req.model_dump(exclude_none=True)
        return await self._post("agent1_summary", payload)

    # ==============================================================================
    # 3. Agent 2 接口
    # ==============================================================================

    async def agent2_generate_shots(self, project_id: str, summary: str,
                              mode_id: int = 0,
                              style_preference: Optional[str] = None) -> Dict[str, Any]:
        req = Agent2ShotsRequest(project_id=project_id, summary=summary,
                                 mode_id=mode_id, style_preference=style_preference)
        payload = req.model_dump(exclude_none=True)
        return await self._post("agent2_shots", payload)

    # ==============================================================================
    # 4. 片段管理接口（与后端 API 对齐）
    # ==============================================================================

    async def get_project_segments(self, project_id: str) -> Dict[str, Any]:
        """从后端获取项目的片段数据"""
        url = f"{self.base_url}/api/projects/{project_id}/segments"
        try:
            resp = await self.client.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Get segments failed: {e}")
            return {"success": False, "message": str(e)}

    async def reorder_segments(self, project_id: str, segment_id_list: List[str]) -> Dict[str, Any]:
        url = f"{self.base_url}/api/projects/{project_id}/segments/reorder"
        try:
            payload = {"segment_id_list": segment_id_list}
            resp = await self.client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Reorder failed: {e}")
            return {"success": False, "message": str(e)}

    async def add_segment(self, project_id: str, after_segment_id: Optional[str] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/api/projects/{project_id}/segments"
        try:
            payload = {"after_segment_id": after_segment_id or ""}
            resp = await self.client.post(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Add segment failed: {e}")
            return {"success": False, "message": str(e)}

    async def update_segment(self, project_id: str, segment_id: str,
                             scene: Optional[str] = None,
                             audio: Optional[str] = None,
                             text: Optional[str] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/api/projects/{project_id}/segments/{segment_id}"
        try:
            payload = {k: v for k, v in {
                "scene": scene,
                "audio": audio,
                "text": text
            }.items() if v is not None}
            resp = await self.client.patch(url, json=payload)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Update segment failed: {e}")
            return {"success": False, "message": str(e)}

    async def delete_segment(self, project_id: str, segment_id: str) -> Dict[str, Any]:
        """
        DELETE 请求没有请求体，project_id 和 segment_id 都从 URL path 传。
        """
        url = f"{self.base_url}/api/projects/{project_id}/segments/{segment_id}"
        try:
            resp = await self.client.delete(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Delete segment failed: {e}")
            return {"success": False, "message": str(e)}

    async def upload_segment_video(self, project_id: str, segment_id: str,
                                   file_data: bytes, file_name: str,
                                   content_type: str = "video/mp4") -> Dict[str, Any]:
        """【修复】异步文件上传（multipart），所有字段通过 files 参数传递"""
        files = {
            "project_id": (None, project_id),
            "segment_id": (None, segment_id),
            "file": (file_name, file_data, content_type)
        }
        return await self._post("upload_segment", {}, use_json=False, files=files)

    # ==============================================================================
    # 【修复】BGM 上传接口
    # ==============================================================================
    async def upload_bgm(self, project_id: str, file_data: bytes, file_name: str,
                         content_type: str = "audio/mpeg") -> Dict[str, Any]:
        """
        【修复】上传背景音乐到后端。
        所有字段通过 files 参数传递，避免 httpx data 参数在 multipart 中的兼容性问题。
        """
        files = {
            "project_id": (None, project_id),
            "file": (file_name, file_data, content_type)
        }
        return await self._post("upload_bgm", {}, use_json=False, files=files)

    async def rough_cut(self, project_id: str, segment_sequence: List[str],
                   bgm_url: Optional[str] = None,
                   bgm_volume: float = 0.3,
                   subtitle_enabled: bool = True,
                   tts_voice: Optional[str] = None,
                   digital_human: Optional[str] = None,
                   mode: int = 0) -> Dict[str, Any]:
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
        # 注意：mode 字段可能需要手动加入，如果 Schema 里没有
        payload["mode"] = mode
        return await self._post("rough_cut", payload)

    async def publish_video(self, project_id: str, title: str,
                      description: str, tags: List[str],
                      cover_frame: int = 0) -> Dict[str, Any]:
        req = PublishRequest(project_id=project_id, title=title,
                             description=description, tags=tags,
                             cover_frame=cover_frame)
        payload = req.model_dump()
        return await self._post("publish", payload)

    # ==============================================================================
    # 5. 查询接口
    # ==============================================================================

    async def get_case_library(self, category: Optional[str] = None, keyword: Optional[str] = None,
                         sort_by: str = "views", page: int = 1,
                         page_size: int = 12) -> Dict[str, Any]:
        params = {k: v for k, v in {
            "category": category,
            "keyword": keyword,
            "sort_by": sort_by,
            "page": page,
            "page_size": page_size
        }.items() if v is not None}
        return await self._get("case_library", params)

    async def get_personal_works(self, user_id: str, keyword: Optional[str] = None,
                           sort_by: str = "date", page: int = 1,
                           page_size: int = 12) -> Dict[str, Any]:
        params = {k: v for k, v in {
            "user_id": user_id,
            "keyword": keyword,
            "sort_by": sort_by,
            "page": page,
            "page_size": page_size
        }.items() if v is not None}
        return await self._get("personal_works", params)

    # ==============================================================================
    # 【新增】文件存在性检查接口
    # ==============================================================================
    async def check_file_exists(self, url_path: str) -> bool:
        """
        通过后端 API 检查文件是否存在，不直接访问文件系统。
        """
        if not url_path:
            return False
        try:
            resp = await self.client.get(
                f"{self.base_url}/api/storage/check",
                params={"url_path": url_path},
                timeout=5.0
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("success") and data.get("exists", False)
        except Exception:
            return False


# ==============================================================================
# 使用示例
# ==============================================================================

async def main():
    """异步测试入口"""
    client = HttpBackendAPI(base_url="http://localhost:8000")

    # 测试健康检查
    try:
        resp = await httpx.AsyncClient().get("http://localhost:8000/health", timeout=5)
        print(f"Backend health: {resp.json()}")
    except Exception as e:
        print(f"Backend not available: {e}")
        await client.close()
        return

    # 测试注册
    result = await client.register(
        username="test_farmer",
        password="Farm123!@#",
        phone="13800138000",
        nickname="老农张三"
    )
    print(f"Register: {result}")

    # 测试登录
    result = await client.authenticate("test_farmer", "Farm123!@#")
    print(f"Login: {result}")

    await client.close()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())

