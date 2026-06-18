"""
存储AppBase，Logger等NiceGUI前端必备组件
"""
from typing import Optional,Dict,Any,List,Protocol
import asyncio
import logging
import sys
from pathlib import Path
# 将项目根目录加入 sys.path（确保 common/ 可被导入）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from common.datamodel import ProjectContext,CreationMode,VideoSegment
import uuid
from datetime import datetime


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("FHFP")

# ==============================================================================
# 0. 全局配置
# ==============================================================================

APP_TITLE: str = "Farming Heart for Prosperity"
APP_VERSION: str = "1.0.0"
DEFAULT_BGM_VOLUME: float = 1
MAX_SEGMENTS: int = 100

THEME_COLORS = {
    "primary": "#4ade80",
    "secondary": "#1e293b",
    "accent": "#22c55e",
    "dark": "#0f172a",
    "text_primary": "#f1f5f9",
    "text_secondary": "#94a3b8",
    "border": "#334155",
    "danger": "#ef4444",
}





# ==============================================================================
# 2. 后端接口协议 (Protocol)
# ==============================================================================
class BackendAPI(Protocol):
    async def register(self, username: str, password: str, phone: Optional[str] = None, nickname: Optional[str] = None) -> Dict[str, Any]:...

    async def authenticate(self, username: str, password: str) -> Dict[str, Any]: ...

    async def create_project(self, user_id: str, mode: int, title: str, description: str, province: Optional[str] = None) -> \
    Dict[str, Any]: ...


    async def agent1_chat(self, project_id: str, message: str, history: List[Dict[str, str]],mode_id:int) -> Dict[str, Any]: ...

    async def agent1_generate_summary(self, project_id: str, highlights: str, custom_notes: Optional[str] = None,mode_id:int=0) -> \
    Dict[str, Any]: ...

    async def agent2_generate_shots(self, project_id: str, summary: str,mode_id:int=0,style_preference: Optional[str] = None,) -> \
    Dict[str, Any]: ...

    # 上传背景音乐
    async def upload_bgm(self, project_id: str, file_data: bytes, file_name: str) -> Dict[str, Any]: ...

    # 片段管理
    async def get_project_segments(self, project_id: str) -> Dict[str, Any]: ...

    async def reorder_segments(self, project_id: str, segment_id_list: List[str]) -> Dict[str, Any]: ...

    async def add_segment(self, project_id: str, after_segment_id: Optional[str] = "") -> Dict[str, Any]: ...

    async def delete_segment(self, project_id: str, segment_id: str) -> Dict[str, Any]: ...

    async def update_segment(self, project_id: str, segment_id: str,
                             scene: Optional[str] = None,
                             audio: Optional[str] = None,
                             text: Optional[str] = None) -> Dict[str, Any]: ...

    # 视频上传
    async def upload_segment_video(self, project_id: str, segment_id: str,
                                   file_data: bytes, file_name: str) -> Dict[str, Any]: ...

    async def rough_cut(self, project_id: str, segment_sequence: List[str], bgm_url: Optional[str] = None,
                  bgm_volume: float = 0.3, subtitle_enabled: bool = True, tts_voice: Optional[str] = None,
                  digital_human: Optional[str] = None,mode:int=0) -> Dict[str, Any]: ...

    async def publish_video(self, project_id: str, title: str, description: str, tags: List[str],
                      cover_frame: Optional[int] = 0) -> Dict[str, Any]: ...

    async def get_case_library(self, category: Optional[str] = None, keyword: Optional[str] = None, sort_by: str = "views",
                         page: int = 1, page_size: int = 12) -> Dict[str, Any]: ...

    async def get_personal_works(self, user_id: str, keyword: Optional[str] = None, sort_by: str = "date", page: int = 1,
                           page_size: int = 12) -> Dict[str, Any]: ...

    async def check_file_exists(self, url_path: str) -> bool: ...

    async def check_phone_exists(self, phone: str) -> Dict[str, Any]: ...



# ==============================================================================
# 4. 应用状态管理器 (单例)
# ==============================================================================

class AppState:
    _instance: Optional["AppState"] = None
    _lock = asyncio.Lock()

    def __new__(cls) -> "AppState":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized: return
        self._user: Optional[Dict[str, Any]] = None
        self.current_project = ProjectContext()
        self._backend: BackendAPI = None
        self.mode_id:int=0
        self._initialized = True
        logger.info("AppState singleton initialized")

    def set_backend(self, backend: BackendAPI) -> None:
        self._backend = backend
        logger.info(f"Backend injected: {backend.__class__.__name__}")

    @property
    def backend(self) -> BackendAPI:
        return self._backend

    @property
    def user(self) -> Optional[Dict[str, Any]]:
        return self._user

    def set_user(self, user: Dict[str, Any]) -> None:
        self._user = user
        self.current_project.user_id = user.get("user_id")

    def logout(self) -> None:
        self._user = None
        self.current_project = ProjectContext()
        logger.info("User logged out, state reset")

    def is_authenticated(self) -> bool:
        return self._user is not None


    def set_mode(self, mode: CreationMode) -> None:
        self.current_project.mode = mode
        self.mode_id = mode.value       # 0 或 1，自动同步
        logger.info(f"Mode set to {mode.name} (mode_id={self.mode_id})")


    def get_mode_id(self) -> int:
        """安全获取当前 mode_id，未选择模式时默认 0"""
        return self.mode_id if self.current_project.mode is not None else 0


    def set_project_id(self, pid: str) -> None:
        self.current_project.project_id = pid