from typing import Optional,List,Dict

# ==============================================================================
# SECTION 1: 前后端共享 Pydantic Schemas (API DTO)
# ==============================================================================
# 生产环境建议: 将这些模型提取到 ../common/schemas.py，前后端共同导入
# 此处内联定义以保证单文件可运行性

try:
    from pydantic import BaseModel, Field, field_validator
except ImportError:
    # 降级：若未安装 pydantic，使用简单 dataclass 替代（仅类型提示，无运行时校验）
    from dataclasses import dataclass as _dataclass

    class BaseModel:  # type: ignore
        def __init__(self, **data): self.__dict__.update(data)
        def model_dump(self): return self.__dict__
        def dict(self): return self.__dict__

    def Field(*args, **kwargs): return None  # type: ignore


class RegisterRequest(BaseModel):
    """用户注册请求体"""
    username: str = Field(..., min_length=3, max_length=20, description="登录账号，3-20位字母数字下划线")
    password: str = Field(..., min_length=8, description="明文密码，至少8位")
    phone: Optional[str] = Field(None, description="手机号，可选但如提供则全局唯一")
    nickname: Optional[str] = Field(None, description="用户昵称，默认同 username")


class LoginRequest(BaseModel):
    """用户登录请求体"""
    username: str = Field(..., description="账号名或手机号")
    password: str = Field(..., description="密码")


class CreateProjectRequest(BaseModel):
    user_id: str
    mode: int
    title: str
    description: str
    province: Optional[str] = None

# 智能体（大模型）并没有智能区分用户的能力（用户画像和农业知识库属于“规划”板块，不属于“创作”板块）
class Agent1ChatRequest(BaseModel):
    """Agent1 多轮对话请求体"""
    project_id: str = Field(..., description="项目唯一标识")
    message: str = Field(..., description="用户当前轮次输入消息")
    history: List[Dict[str, str]] = Field(default_factory=list, description="历史消息列表，格式 [{'role': 'user'|'assistant', 'content': '...'}]")
    mode_id: int = Field(..., ge=0, le=1, description="创作模式: 0=PRODUCT_INTRO, 1=STORY_DESIGN")
    temperature: float = Field(0.7, ge=0.0, le=1.0, description="采样温度")


class Agent1SummaryRequest(BaseModel):
    """Agent1 摘要生成请求体"""
    project_id: str = Field(..., description="项目唯一标识")
    highlights: str = Field(..., min_length=1, description="用户确认的亮点短语列表")
    custom_notes: Optional[str] = Field(None, description="用户补充要求，如'生成适合短视频口播的摘要'")
    mode_id: int = Field(..., ge=0, le=1, description="创作模式")


class Agent2ShotsRequest(BaseModel):
    """Agent2 拍摄建议生成请求体"""
    project_id: str = Field(..., description="项目唯一标识")
    summary: str = Field(..., min_length=10, description="Agent1 生成的摘要全文")
    mode_id: int = Field(..., ge=0, le=1, description="创作模式")
    style_preference: Optional[str] = Field(None, description="风格偏好，如'朴实'、'温馨'、'促销感'")


"""
class ReorderSegmentsRequest(BaseModel):
    segment_id_list: List[str]  # 新的顺序


class AddSegmentRequest(BaseModel):
    after_segment_id: str = ""  # ""加到尾部


class UpdateSegmentRequest(BaseModel):
    segment_id: str
    scene: Optional[str] = None
    audio: Optional[str] = None
    text: Optional[str] = None
"""


class RoughCutRequest(BaseModel):
    """视频粗剪请求体"""
    project_id: str = Field(..., description="项目唯一标识")
    segment_sequence: List[str] = Field(..., description="按顺序排列的 segment_id 列表")
    bgm_url: Optional[str] = Field(None, description="背景音乐文件 URL，None 则无 BGM")
    bgm_volume: float = Field(0.3, ge=0.0, le=1.0, description="背景音量 0.0-1.0")
    subtitle_enabled: bool = Field(True, description="是否基于 copy 字段自动生成 SRT 字幕")
    tts_voice: Optional[str] = Field(None, description="TTS 音色 ID，仅在剧情模式(mode=1)有效")
    digital_human: Optional[str] = Field(None, description="数字人形象 ID，仅在剧情模式(mode=1)有效")


class PublishRequest(BaseModel):
    """视频发布请求体"""
    project_id: str = Field(..., description="项目唯一标识")
    title: str = Field(..., description="作品标题")
    description: str = Field(..., description="作品描述")
    tags: List[str] = Field(default_factory=list, description="标签列表，如 ['农产品', 'AI创作']")
    cover_frame: int = Field(0, description="封面截取帧序号，默认第0帧")
