"""
内容处理管线：串联视频/图片/文本处理器，将解析后的原始内容
转换为可供AI总结的结构化文本。
"""

from __future__ import annotations

import logging

from get_notes.config import AppConfig
from get_notes.models import ContentType, ParsedContent, ProcessedContent
from .video import VideoProcessor
from .image import ImageProcessor
from .text import TextProcessor

logger = logging.getLogger(__name__)


class ContentPipeline:
    """
    多模态内容处理管线

    根据内容类型自动选择处理路径：
    - 视频内容 → 音频提取 → ASR转录
    - 图文内容 → OCR文字提取 + 视觉理解
    - 文章内容 → HTML清洗 + 文本提取
    """

    def __init__(self, config: AppConfig):
        self.config = config
        self.video_processor = VideoProcessor(config)
        self.image_processor = ImageProcessor(config)
        self.text_processor = TextProcessor()

    def process(self, parsed: ParsedContent) -> ProcessedContent:
        """
        处理解析后的内容，返回处理结果。
        """
        result = ProcessedContent()
        logger.info(
            "开始处理内容 [%s / %s]: %s",
            parsed.platform.value,
            parsed.content_type.value,
            parsed.title or parsed.content_id,
        )

        # 处理视频内容
        if parsed.content_type == ContentType.VIDEO and parsed.video:
            result = self._process_video(parsed, result)

        # 处理图文内容
        if parsed.content_type == ContentType.IMAGE_TEXT and parsed.images:
            result = self._process_images(parsed, result)

        # 处理文本内容
        if parsed.description:
            result.clean_text = self.text_processor.clean(parsed.description)

        logger.info("内容处理完成")
        return result

    def _process_video(
        self, parsed: ParsedContent, result: ProcessedContent
    ) -> ProcessedContent:
        """处理视频：提取音频 + ASR转录"""
        video = parsed.video
        if not video:
            logger.warning("无视频信息，跳过视频处理")
            return result

        video_url = video.url if video.url else None
        video_path = video.local_path if video and video.local_path else None

        if not video_url and not video_path:
            logger.warning("无视频URL也无本地文件，跳过视频处理")
            return result

        try:
            transcript, audio_path = self.video_processor.process(
                video_path=video_path,
                video_url=video_url,
            )
            result.transcript = transcript
            result.audio_path = audio_path
            logger.info("视频转录完成，文本长度: %d", len(transcript))
        except Exception as e:
            logger.error("视频处理失败: %s", e)

        return result

    def _process_images(
        self, parsed: ParsedContent, result: ProcessedContent
    ) -> ProcessedContent:
        """处理图片列表：OCR + 视觉理解"""
        image_paths = [
            img.local_path for img in parsed.images
            if img.local_path
        ]

        if not image_paths:
            logger.warning("没有已下载的图片，跳过图片处理")
            return result

        logger.info("处理 %d 张图片", len(image_paths))
        ocr_texts, descriptions = self.image_processor.process_batch(image_paths)
        result.ocr_texts = ocr_texts
        result.image_descriptions = descriptions

        return result

    def aggregate(self, parsed: ParsedContent, processed: ProcessedContent) -> str:
        """聚合所有内容为LLM可处理的文本"""
        return self.text_processor.aggregate_content(parsed, processed)
