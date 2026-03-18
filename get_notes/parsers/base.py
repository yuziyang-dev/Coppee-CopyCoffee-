"""
解析器基类：定义所有平台解析器的统一接口。
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod

import requests

from get_notes.config import AppConfig
from get_notes.models import ParsedContent, MediaItem

logger = logging.getLogger(__name__)


class BaseParser(ABC):
    """平台解析器基类"""

    def __init__(self, config: AppConfig):
        self.config = config
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.parser.user_agent,
        })

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        """判断此解析器是否能处理给定的URL"""
        ...

    @abstractmethod
    def parse(self, url: str) -> ParsedContent:
        """解析链接，返回结构化内容"""
        ...

    def _follow_redirect(self, url: str, max_hops: int = 10) -> str:
        """
        跟踪重定向链，返回最终URL。
        先尝试 GET + allow_redirects（最可靠），
        失败时回退到逐跳 HEAD。
        """
        # 方式1: GET 自动跟踪所有重定向，取最终 resp.url
        try:
            resp = self.session.get(
                url,
                allow_redirects=True,
                timeout=self.config.parser.request_timeout,
            )
            if resp.url and resp.url != url:
                logger.info("重定向（GET）: %s -> %s", url, resp.url)
                return resp.url
        except requests.RequestException as e:
            logger.warning("GET 重定向失败: %s, 错误: %s", url, e)

        # 方式2: 逐跳 HEAD，最多 max_hops 次
        current = url
        for _ in range(max_hops):
            try:
                resp = self.session.head(
                    current,
                    allow_redirects=False,
                    timeout=self.config.parser.request_timeout,
                )
                if resp.status_code in (301, 302, 303, 307, 308):
                    location = resp.headers.get("Location", "")
                    if not location:
                        break
                    if location.startswith("/"):
                        from urllib.parse import urlparse
                        parsed = urlparse(current)
                        location = f"{parsed.scheme}://{parsed.netloc}{location}"
                    logger.info("重定向（HEAD）: %s -> %s", current, location)
                    current = location
                else:
                    break
            except requests.RequestException:
                break
        return current

    def _download_file(self, url: str, save_path: str) -> str:
        """下载文件到本地路径"""
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        try:
            resp = self.session.get(
                url,
                stream=True,
                timeout=self.config.parser.request_timeout,
            )
            resp.raise_for_status()
            with open(save_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info("文件已下载: %s", save_path)
            return save_path
        except requests.RequestException as e:
            logger.error("下载失败 %s: %s", url, e)
            raise

    def _download_media(self, item: MediaItem, directory: str) -> MediaItem:
        """下载单个媒体资源并更新local_path"""
        ext = ".mp4" if item.media_type == "video" else ".jpg"
        filename = f"{hash(item.url) & 0xFFFFFFFF:08x}{ext}"
        save_path = os.path.join(directory, filename)
        try:
            item.local_path = self._download_file(item.url, save_path)
        except Exception:
            logger.warning("跳过下载失败的媒体: %s", item.url)
        return item
