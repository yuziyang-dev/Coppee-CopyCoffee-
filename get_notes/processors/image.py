"""
图片处理器：OCR文字识别 + 多模态视觉理解

处理流程：
1. OCR：使用 PaddleOCR / Tesseract 提取图片中的文字
2. 视觉理解：通过多模态LLM（GPT-4o / Claude）理解图片语义内容
3. 返回提取的文字和语义描述
"""

from __future__ import annotations

import base64
import logging
import os
from typing import Optional

import requests

from get_notes.config import AppConfig

logger = logging.getLogger(__name__)


class ImageProcessor:
    """图片内容处理器"""

    def __init__(self, config: AppConfig):
        self.config = config

    def ocr_with_paddle(self, image_path: str) -> str:
        """使用PaddleOCR进行中文文字识别"""
        try:
            from paddleocr import PaddleOCR
        except ImportError:
            raise RuntimeError("PaddleOCR未安装。请安装: pip install paddleocr paddlepaddle")

        if not os.path.exists(image_path):
            raise FileNotFoundError(f"图片不存在: {image_path}")

        ocr = PaddleOCR(use_angle_cls=True, lang="ch", show_log=False)
        result = ocr.ocr(image_path, cls=True)

        texts = []
        if result:
            for line_group in result:
                if line_group:
                    for line in line_group:
                        text = line[1][0] if isinstance(line[1], (list, tuple)) else str(line[1])
                        texts.append(text)

        combined = "\n".join(texts)
        logger.info("OCR识别完成 [%s]，文本长度: %d", image_path, len(combined))
        return combined

    def ocr_with_tesseract(self, image_path: str) -> str:
        """使用Tesseract进行OCR识别（备选方案）"""
        try:
            import pytesseract
            from PIL import Image
        except ImportError:
            raise RuntimeError(
                "Tesseract依赖未安装。请安装: "
                "pip install pytesseract Pillow && brew install tesseract"
            )

        if not os.path.exists(image_path):
            raise FileNotFoundError(f"图片不存在: {image_path}")

        img = Image.open(image_path)
        text = pytesseract.image_to_string(img, lang="chi_sim+eng")
        logger.info("Tesseract OCR完成 [%s]，文本长度: %d", image_path, len(text))
        return text.strip()

    def ocr(self, image_path: str) -> str:
        """统一OCR接口：优先PaddleOCR，回退到Tesseract"""
        try:
            return self.ocr_with_paddle(image_path)
        except (RuntimeError, ImportError):
            logger.info("PaddleOCR不可用，尝试Tesseract")
            try:
                return self.ocr_with_tesseract(image_path)
            except (RuntimeError, ImportError):
                logger.warning("所有OCR引擎不可用，跳过OCR")
                return ""

    def describe_with_vision_llm(self, image_path: str) -> str:
        """
        使用多模态LLM（GPT-4o等）理解图片语义内容。
        将图片编码为base64后通过API发送。
        """
        llm_config = self.config.llm
        if not llm_config.api_key:
            logger.warning("LLM API未配置，跳过图片语义理解")
            return ""

        if not os.path.exists(image_path):
            logger.warning("图片不存在: %s", image_path)
            return ""

        with open(image_path, "rb") as f:
            image_data = base64.b64encode(f.read()).decode()

        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png", ".gif": "gif", ".webp": "webp"}
        mime_type = mime_map.get(ext, "jpeg")

        try:
            resp = requests.post(
                f"{llm_config.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {llm_config.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": llm_config.model,
                    "max_tokens": 500,
                    "messages": [
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": (
                                        "请用中文简要描述这张图片的主要内容，包括关键信息、"
                                        "文字内容（如果有）和视觉要素。控制在200字以内。"
                                    ),
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:image/{mime_type};base64,{image_data}",
                                    },
                                },
                            ],
                        }
                    ],
                },
                timeout=60,
            )
            resp.raise_for_status()
            description = resp.json()["choices"][0]["message"]["content"]
            logger.info("图片语义描述完成 [%s]", image_path)
            return description
        except Exception as e:
            logger.error("图片语义理解失败: %s", e)
            return ""

    def process(self, image_path: str) -> tuple[str, str]:
        """
        完整图片处理：OCR + 视觉理解。
        返回 (OCR文字, 语义描述)。
        """
        ocr_text = self.ocr(image_path)
        description = self.describe_with_vision_llm(image_path)
        return ocr_text, description

    def process_batch(self, image_paths: list[str]) -> tuple[list[str], list[str]]:
        """
        批量处理图片列表。
        返回 (OCR文字列表, 语义描述列表)。
        """
        ocr_texts = []
        descriptions = []
        for path in image_paths:
            if path and os.path.exists(path):
                ocr_text, desc = self.process(path)
                ocr_texts.append(ocr_text)
                descriptions.append(desc)
        return ocr_texts, descriptions
