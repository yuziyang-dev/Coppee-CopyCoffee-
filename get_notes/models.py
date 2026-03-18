"""
数据模型：定义解析结果、内容载体和笔记结构。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class Platform(Enum):
    DOUYIN = "douyin"
    XIAOHONGSHU = "xiaohongshu"
    BILIBILI = "bilibili"
    WECHAT = "wechat"
    UNKNOWN = "unknown"


class ContentType(Enum):
    VIDEO = "video"
    IMAGE_TEXT = "image_text"
    ARTICLE = "article"


@dataclass
class MediaItem:
    """单个媒体资源（图片或视频）"""
    url: str
    media_type: str  # "image" | "video"
    local_path: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None


@dataclass
class ParsedContent:
    """从平台解析出的原始内容"""
    platform: Platform
    content_type: ContentType
    content_id: str
    title: str = ""
    description: str = ""
    author: str = ""
    tags: list[str] = field(default_factory=list)
    images: list[MediaItem] = field(default_factory=list)
    video: Optional[MediaItem] = None
    source_url: str = ""


@dataclass
class ProcessedContent:
    """经过多模态处理后的内容"""
    transcript: str = ""       # ASR转录文本（视频）
    ocr_texts: list[str] = field(default_factory=list)  # OCR识别的图片文字
    image_descriptions: list[str] = field(default_factory=list)  # 图片语义描述
    clean_text: str = ""       # 清洗后的原文文本
    audio_path: Optional[str] = None


@dataclass
class PourStep:
    """单次注水阶段"""
    stage: str = ""            # 阶段名：闷蒸 / 第一段 / 第二段 ...
    water_ml: str = ""         # 注水量或累计水量，如 "30ml" "注至150ml"
    time: str = ""             # 时间，如 "0:00-0:30" "30s"
    technique: str = ""        # 手法：中心注水 / 绕圈 / 搅拌 等


@dataclass
class BrewCard:
    """手冲咖啡方案参数卡"""
    # 基本信息
    title: str = ""
    summary: str = ""          # 一句话概括这个方案的特点/风格

    # 咖啡豆
    bean_name: str = ""        # 豆子名称/品牌
    origin: str = ""           # 产地
    roast_level: str = ""      # 烘焙度：浅 / 中浅 / 中 / 中深 / 深
    process: str = ""          # 处理法：水洗 / 日晒 / 蜜处理 等
    bean_notes: str = ""       # 豆子补充说明

    # 研磨
    grind_size: str = ""       # 研磨度描述：如 "中细" "C40 24格" "EK43 9.0"

    # 冲煮核心参数
    dose: str = ""             # 粉量 (g)
    water_amount: str = ""     # 水量 (ml)
    ratio: str = ""            # 粉水比，如 "1:15"
    water_temp: str = ""       # 水温 (°C)
    total_time: str = ""       # 总萃取时间

    # 器具
    dripper: str = ""          # 滤杯：V60 / Kalita / Origami 等
    filter_paper: str = ""     # 滤纸
    equipment_notes: str = ""  # 其他器具说明

    # 冲煮步骤
    pour_steps: list[dict] = field(default_factory=list)

    # 风味
    flavor_notes: str = ""     # 预期风味描述
    tips: str = ""             # 作者的关键技巧/心得

    # AI 补全标记：记录哪些字段是推理补全的（非原文提取）
    inferred_fields: list[str] = field(default_factory=list)

    # 来源
    author: str = ""
    source_platform: str = ""
    source_url: str = ""
    raw_content: str = ""


@dataclass
class NoteSummary:
    """AI生成的笔记摘要（通用，保留兼容）"""
    title: str = ""
    summary: str = ""
    key_points: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    chapters: list[dict] = field(default_factory=list)
    source_platform: str = ""
    source_url: str = ""
    raw_content: str = ""
