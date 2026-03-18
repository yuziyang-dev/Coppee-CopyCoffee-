"""
抖音链接解析器

解析流程：
1. 分享短链接 → 302重定向 → 提取video_id
2. 调用 iteminfo API 获取视频元数据（标题、作者、描述、vid）
3. 通过 vid 构造无水印视频播放链接
4. 下载视频文件到本地
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional

import requests

from get_notes.config import AppConfig
from get_notes.models import (
    ContentType,
    MediaItem,
    ParsedContent,
    Platform,
)
from .base import BaseParser

logger = logging.getLogger(__name__)

# 匹配抖音视频ID的正则（纯数字，通常19位）
VIDEO_ID_PATTERN = re.compile(r"/video/(\d+)")
# 从HTML页面中提取渲染数据的正则
RENDER_DATA_PATTERN = re.compile(
    r'<script\s+id="RENDER_DATA"\s+type="application/json">(.*?)</script>',
    re.DOTALL,
)


class DouyinParser(BaseParser):
    """抖音短视频解析器"""

    DOUYIN_DOMAINS = ("douyin.com", "iesdouyin.com")
    ITEM_INFO_API = "https://www.iesdouyin.com/web/api/v2/aweme/iteminfo/"
    PLAY_API = "https://www.douyin.com/aweme/v1/play/"

    def __init__(self, config: AppConfig):
        super().__init__(config)
        self.session.headers.update({
            "Referer": "https://www.douyin.com/",
            "Accept": "application/json, text/plain, */*",
        })

    def can_handle(self, url: str) -> bool:
        return any(domain in url for domain in self.DOUYIN_DOMAINS)

    def parse(self, url: str) -> ParsedContent:
        logger.info("开始解析抖音链接: %s", url)

        # Step 1: 跟踪重定向，获取真实URL
        real_url = self._follow_redirect(url)
        logger.info("真实URL: %s", real_url)

        # Step 2: 提取 video_id
        video_id = self._extract_video_id(real_url)
        if not video_id:
            # 尝试从页面源码中提取
            video_id = self._extract_video_id_from_page(real_url)
        if not video_id:
            raise ValueError(f"无法从URL中提取video_id: {real_url}")
        logger.info("提取到 video_id: %s", video_id)

        # Step 3: 获取视频元数据
        metadata = self._fetch_video_metadata(video_id)

        # Step 4: 构造解析结果
        content = ParsedContent(
            platform=Platform.DOUYIN,
            content_type=ContentType.VIDEO,
            content_id=video_id,
            title=metadata.get("title", ""),
            description=metadata.get("description", ""),
            author=metadata.get("author", ""),
            tags=metadata.get("tags", []),
            source_url=url,
        )

        # Step 5: 构造视频下载链接
        video_url = metadata.get("video_url", "")
        if video_url:
            content.video = MediaItem(
                url=video_url,
                media_type="video",
            )
            # 下载视频
            save_dir = os.path.join(self.config.storage.temp_dir, "douyin", video_id)
            content.video = self._download_media(content.video, save_dir)

        return content

    def _extract_video_id(self, url: str) -> Optional[str]:
        """从URL路径中提取video_id"""
        match = VIDEO_ID_PATTERN.search(url)
        return match.group(1) if match else None

    def _extract_video_id_from_page(self, url: str) -> Optional[str]:
        """
        访问页面并从HTML中提取video_id。
        抖音移动端页面会在源码中包含JSON数据。
        """
        try:
            resp = self.session.get(url, timeout=self.config.parser.request_timeout)
            resp.raise_for_status()

            # 尝试从 RENDER_DATA 脚本中提取
            match = RENDER_DATA_PATTERN.search(resp.text)
            if match:
                from urllib.parse import unquote
                data_str = unquote(match.group(1))
                data = json.loads(data_str)
                # 遍历寻找 aweme_id
                return self._find_aweme_id(data)

            # 尝试直接用正则从页面中提取
            id_match = re.search(r'"aweme_id"\s*:\s*"(\d+)"', resp.text)
            if id_match:
                return id_match.group(1)

        except Exception as e:
            logger.warning("从页面提取video_id失败: %s", e)
        return None

    def _find_aweme_id(self, data: dict | list) -> Optional[str]:
        """递归搜索JSON中的aweme_id字段"""
        if isinstance(data, dict):
            if "aweme_id" in data:
                return str(data["aweme_id"])
            for v in data.values():
                result = self._find_aweme_id(v)
                if result:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = self._find_aweme_id(item)
                if result:
                    return result
        return None

    def _fetch_video_metadata(self, video_id: str) -> dict:
        """
        调用抖音 iteminfo API 获取视频元数据。
        返回包含 title, description, author, video_url, tags 的字典。
        """
        result = {
            "title": "",
            "description": "",
            "author": "",
            "video_url": "",
            "tags": [],
        }

        try:
            resp = self.session.get(
                self.ITEM_INFO_API,
                params={"item_ids": video_id},
                timeout=self.config.parser.request_timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get("item_list", [])
            if not items:
                logger.warning("iteminfo API 返回空列表，尝试备用方案")
                return self._fetch_metadata_fallback(video_id)

            item = items[0]
            result["title"] = item.get("desc", "")
            result["description"] = item.get("desc", "")

            author_info = item.get("author", {})
            result["author"] = author_info.get("nickname", "")

            # 提取视频播放链接
            video_info = item.get("video", {})
            play_addr = video_info.get("play_addr", {})
            url_list = play_addr.get("url_list", [])
            if url_list:
                # 第一个通常是无水印链接
                video_play_url = url_list[0]
                # 跟踪重定向获取最终CDN链接
                result["video_url"] = self._follow_redirect(video_play_url)

            # 提取标签
            text_extra = item.get("text_extra", [])
            result["tags"] = [
                t.get("hashtag_name", "") for t in text_extra
                if t.get("hashtag_name")
            ]

        except Exception as e:
            logger.error("获取视频元数据失败: %s", e)
            return self._fetch_metadata_fallback(video_id)

        return result

    def _fetch_metadata_fallback(self, video_id: str) -> dict:
        """
        备用方案：直接访问抖音视频页面，从HTML中提取元数据。
        当 iteminfo API 不可用时使用。
        """
        result = {
            "title": "",
            "description": "",
            "author": "",
            "video_url": "",
            "tags": [],
        }

        try:
            page_url = f"https://www.douyin.com/video/{video_id}"
            resp = self.session.get(
                page_url,
                timeout=self.config.parser.request_timeout,
            )
            resp.raise_for_status()

            # 提取 title
            title_match = re.search(
                r'<title[^>]*>(.*?)</title>', resp.text, re.DOTALL
            )
            if title_match:
                result["title"] = title_match.group(1).strip().split(" - ")[0]

            # 从meta标签提取描述
            desc_match = re.search(
                r'<meta\s+name="description"\s+content="([^"]*)"', resp.text
            )
            if desc_match:
                result["description"] = desc_match.group(1)

            # 尝试从页面数据中提取视频链接
            video_match = re.search(r'"playApi"\s*:\s*"([^"]+)"', resp.text)
            if video_match:
                play_api = video_match.group(1).replace("\\u002F", "/")
                if not play_api.startswith("http"):
                    play_api = "https:" + play_api
                result["video_url"] = self._follow_redirect(play_api)

        except Exception as e:
            logger.error("备用元数据提取失败: %s", e)

        return result
