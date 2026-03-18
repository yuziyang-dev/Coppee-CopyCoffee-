"""
平台路由器：根据URL自动识别平台并分派给对应解析器。
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from get_notes.config import AppConfig
from get_notes.models import ParsedContent, Platform
from .base import BaseParser
from .douyin import DouyinParser
from .xiaohongshu import XiaohongshuParser

logger = logging.getLogger(__name__)

# 从用户粘贴的文本中提取URL
URL_PATTERN = re.compile(r'https?://[^\s<>"\']+')


class PlatformRouter:
    """
    平台识别路由：接受用户输入的文本（可能包含分享文案），
    自动提取URL并分派给对应平台的解析器。
    """

    def __init__(self, config: Optional[AppConfig] = None):
        self.config = config or AppConfig()
        self._parsers: list[BaseParser] = [
            DouyinParser(self.config),
            XiaohongshuParser(self.config),
        ]

    def extract_url(self, text: str) -> Optional[str]:
        """从分享文案中提取URL"""
        match = URL_PATTERN.search(text)
        if match:
            return match.group(0).rstrip(")")
        return None

    def identify_platform(self, url: str) -> Platform:
        """根据URL域名识别平台"""
        domain_map = {
            "douyin.com": Platform.DOUYIN,
            "iesdouyin.com": Platform.DOUYIN,
            "xiaohongshu.com": Platform.XIAOHONGSHU,
            "xhslink.com": Platform.XIAOHONGSHU,
            "bilibili.com": Platform.BILIBILI,
            "b23.tv": Platform.BILIBILI,
            "weixin.qq.com": Platform.WECHAT,
        }
        for domain, platform in domain_map.items():
            if domain in url:
                return platform
        return Platform.UNKNOWN

    def parse(self, user_input: str) -> ParsedContent:
        """
        完整解析流程：
        1. 从用户输入中提取URL
        2. 识别平台
        3. 分派给对应解析器
        """
        url = self.extract_url(user_input)
        if not url:
            raise ValueError(f"无法从输入中提取有效URL: {user_input!r}")

        platform = self.identify_platform(url)
        logger.info("识别平台: %s, URL: %s", platform.value, url)

        for parser in self._parsers:
            if parser.can_handle(url):
                return parser.parse(url)

        raise ValueError(f"暂不支持该平台的链接解析: {url} (平台: {platform.value})")
