#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
common/models.py
================
前后端共享的数据模型（纯 Python，无框架依赖）

可被：
    - frontend/core.py 导入（用于 AppState）
    - backend/main.py 导入（用于数据库操作）
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pathlib import Path
from pydantic import BaseModel


class CreationMode(Enum):
    PRODUCT_INTRO = 0
    STORY_DESIGN = 1



@dataclass
class VideoSegment:
    segment_id: str = field(default_factory=lambda: f"seg_{uuid.uuid4().hex[:8]}")
    project_id: str = ""
    order: int = 0
    video_url: Optional[str] = None
    video_duration: Optional[float] = None
    scene: Optional[str] = None
    audio: Optional[str] = None
    # 序列化JSON字符串
    text: Optional[str] = None


@dataclass
class ProjectContext:
    """项目上下文（前后端共享）"""
    project_id: Optional[str] = None
    user_id: Optional[str] = None
    mode: Optional[CreationMode] = None
    title: str = ""
    description: str = ""
    province: Optional[str] = None
    agent1_history: List[Dict[str, str]] = field(default_factory=list)
    agent1_highlights: str = ""
    agent1_summary: str = ""
    segments: List[Any] = field(default_factory=list)
    bgm_url: Optional[str] = None
    bgm_volume: float = 0.3
    subtitle_enabled: bool = True
    tts_voice: Optional[str] = None
    digital_human: Optional[str] = None
    preview_url: Optional[str] = None


class VideoWork(BaseModel):
    """统一的作品数据模型"""
    work_id: str
    title: str
    description: str
    thumbnail: str
    video_url: str
    duration: str
    duration_seconds: int
    author: str
    category_level1: str
    category_level2: str
    region: str
    publish_date: str
    views: int
    likes: int
    shares: int
    comments: int
    summary: str
    highlights: List[str]
    shot_descriptions: List[Dict[str, Any]]
    tags: List[str]
    is_public: bool = True

