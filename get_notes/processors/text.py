"""
文本处理器：清洗和提取纯文本内容

处理流程：
1. HTML清洗：去除标签，保留纯文本
2. 格式规范化：统一换行、去除多余空白
3. 内容聚合：合并多来源文本（标题+描述+OCR+转录）
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from get_notes.models import ParsedContent, ProcessedContent

logger = logging.getLogger(__name__)


class TextProcessor:
    """文本内容处理器"""

    @staticmethod
    def clean_html(html: str) -> str:
        """去除HTML标签，保留纯文本"""
        text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
        text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<p[^>]*>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'<[^>]+>', '', text)
        # 处理HTML实体
        text = text.replace('&nbsp;', ' ')
        text = text.replace('&amp;', '&')
        text = text.replace('&lt;', '<')
        text = text.replace('&gt;', '>')
        text = text.replace('&quot;', '"')
        text = text.replace('&#39;', "'")
        return text.strip()

    @staticmethod
    def normalize(text: str) -> str:
        """规范化文本格式"""
        text = re.sub(r'\r\n', '\n', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        text = re.sub(r'[ \t]+', ' ', text)
        lines = [line.strip() for line in text.split('\n')]
        return '\n'.join(lines).strip()

    @staticmethod
    def extract_hashtags(text: str) -> list[str]:
        """提取文本中的话题标签"""
        tags = re.findall(r'#([^\s#]+)', text)
        return list(dict.fromkeys(tags))  # 去重保序

    def clean(self, text: str) -> str:
        """清洗文本：去HTML + 规范化"""
        cleaned = self.clean_html(text)
        return self.normalize(cleaned)

    def aggregate_content(
        self,
        parsed: ParsedContent,
        processed: ProcessedContent,
    ) -> str:
        """
        聚合所有来源的文本内容，构造供LLM总结的完整上下文。
        按优先级排列：标题 > 正文 > 转录 > 图片OCR > 图片描述
        """
        sections = []

        if parsed.title:
            sections.append(f"【标题】{parsed.title}")

        if parsed.author:
            sections.append(f"【作者】{parsed.author}")

        if parsed.description:
            clean_desc = self.clean(parsed.description)
            if clean_desc:
                sections.append(f"【正文】\n{clean_desc}")

        if processed.clean_text:
            sections.append(f"【文本内容】\n{processed.clean_text}")

        if processed.transcript:
            sections.append(f"【视频转录】\n{processed.transcript}")

        if processed.ocr_texts:
            non_empty = [t for t in processed.ocr_texts if t.strip()]
            if non_empty:
                ocr_section = "\n---\n".join(
                    f"图片{i+1}: {t}" for i, t in enumerate(non_empty)
                )
                sections.append(f"【图片文字识别】\n{ocr_section}")

        if processed.image_descriptions:
            non_empty = [d for d in processed.image_descriptions if d.strip()]
            if non_empty:
                desc_section = "\n---\n".join(
                    f"图片{i+1}: {d}" for i, d in enumerate(non_empty)
                )
                sections.append(f"【图片内容描述】\n{desc_section}")

        if parsed.tags:
            sections.append(f"【标签】{', '.join(parsed.tags)}")

        return "\n\n".join(sections)
