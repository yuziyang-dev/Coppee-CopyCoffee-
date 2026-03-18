"""
应用核心：串联解析 → 处理 → AI提取手冲方案的完整流程。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from pathlib import Path
from typing import Optional

from get_notes.config import AppConfig
from get_notes.models import BrewCard, ParsedContent
from get_notes.parsers import PlatformRouter
from get_notes.processors import ContentPipeline
from get_notes.ai import NoteSummarizer

logger = logging.getLogger(__name__)


def _load_dotenv():
    """自动加载项目根目录的 .env 文件"""
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            logger.info("已加载配置文件: %s", env_path)
    except ImportError:
        pass


class GetNotesApp:
    """
    手冲方案提取核心应用

    完整处理流程：
    1. 链接解析：从URL提取平台内容（视频/图文）
    2. 内容处理：ASR转录 / OCR识别 / 文本清洗
    3. AI提取：大模型提取结构化手冲方案参数卡
    """

    def __init__(self, config: Optional[AppConfig] = None):
        _load_dotenv()
        self.config = config or AppConfig()
        self.config.ensure_dirs()
        self.router = PlatformRouter(self.config)
        self.pipeline = ContentPipeline(self.config)
        self.summarizer = NoteSummarizer(self.config)

    def process_link(
        self,
        user_input: str,
        user_instruction: Optional[str] = None,
    ) -> BrewCard:
        logger.info("=" * 60)
        logger.info("Step 1: 解析链接")
        parsed = self.router.parse(user_input)
        logger.info(
            "解析完成 - 平台: %s, 类型: %s, ID: %s",
            parsed.platform.value,
            parsed.content_type.value,
            parsed.content_id,
        )

        logger.info("Step 2: 处理内容")
        processed = self.pipeline.process(parsed)

        logger.info("Step 3: 聚合内容")
        aggregated = self.pipeline.aggregate(parsed, processed)
        logger.info("聚合文本长度: %d 字符", len(aggregated))

        logger.info("Step 4: AI提取手冲方案")
        card = self.summarizer.summarize(aggregated, parsed, user_instruction)
        logger.info("方案提取完成: %s", card.title)

        self._save_card(card)
        return card

    def _save_card(self, card: BrewCard):
        output_dir = self.config.storage.output_dir
        os.makedirs(output_dir, exist_ok=True)

        safe_title = "".join(
            c if c.isalnum() or c in "._-" else "_"
            for c in (card.title or "untitled")
        )[:50]
        filepath = os.path.join(output_dir, f"{safe_title}.json")

        data = asdict(card)
        data.pop("raw_content", None)

        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        logger.info("方案已保存: %s", filepath)
