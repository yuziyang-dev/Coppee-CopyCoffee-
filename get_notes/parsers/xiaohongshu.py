"""
小红书链接解析器

解析流程（基于真实链接测试验证）：
1. 短链接 xhslink.com → 302重定向 → 提取 note_id（24位十六进制）
2. 优先使用原始URL（含xsec_token等认证参数）访问页面
3. 从 window.__INITIAL_STATE__ 中提取 noteDetailMap 数据
4. 区分图文笔记和视频笔记，分别提取内容
5. 图文笔记：提取文本 + 下载图片列表
6. 视频笔记：提取描述 + 下载视频文件
7. 兜底：从 meta 标签获取基础信息
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Optional
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

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

NOTE_ID_PATTERNS = [
    re.compile(r"/explore/([0-9a-fA-F]{24})"),
    re.compile(r"/discovery/item/([0-9a-fA-F]{24})"),
]

INITIAL_STATE_PATTERN = re.compile(
    r'window\.__INITIAL_STATE__\s*=\s*(\{.*?\})\s*</script>',
    re.DOTALL,
)


class XiaohongshuParser(BaseParser):
    """小红书图文/视频解析器"""

    XHS_DOMAINS = ("xiaohongshu.com", "xhslink.com")

    def __init__(self, config: AppConfig):
        super().__init__(config)
        self.session.headers.update({
            "Referer": "https://www.xiaohongshu.com/",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })

    def can_handle(self, url: str) -> bool:
        return any(domain in url for domain in self.XHS_DOMAINS)

    def parse(self, url: str) -> ParsedContent:
        logger.info("开始解析小红书链接: %s", url[:100])

        original_input = url

        # Step 1: 处理短链接重定向
        if "xhslink.com" in url:
            url = self._follow_redirect(url)
            logger.info("重定向后URL: %s", url[:100])

        # Step 2: 提取 note_id 和 URL 参数中的类型提示
        note_id = self._extract_note_id(url)

        # 如果重定向后仍然提取不到 note_id，
        # 用页面内容中的 canonical/og:url 再试一次
        if not note_id:
            logger.info("重定向URL中未找到note_id，尝试从页面内容提取")
            note_id, url = self._resolve_note_id_from_page(url)

        if not note_id:
            raise ValueError(f"无法从URL中提取note_id: {original_input}")
        logger.info("提取到 note_id: %s", note_id)

        url_type_hint = self._extract_type_from_url(url)

        # Step 3: 获取笔记详情 — 保留原始URL参数用于认证
        note_data = self._fetch_note_detail(note_id, original_url=url)

        # Step 4: 判断内容类型（优先用API数据，URL参数作为备选）
        detected_type = note_data.get("type", url_type_hint or "image_text")
        is_video = detected_type == "video"
        content_type = ContentType.VIDEO if is_video else ContentType.IMAGE_TEXT

        content = ParsedContent(
            platform=Platform.XIAOHONGSHU,
            content_type=content_type,
            content_id=note_id,
            title=note_data.get("title", ""),
            description=note_data.get("description", ""),
            author=note_data.get("author", ""),
            tags=note_data.get("tags", []),
            source_url=url,
        )

        save_dir = os.path.join(self.config.storage.temp_dir, "xiaohongshu", note_id)

        if is_video:
            video_url = note_data.get("video_url", "")
            if video_url:
                content.video = MediaItem(url=video_url, media_type="video")
                # 不再预下载视频，pipeline 会用 URL 直接流式提取音频
        else:
            image_urls = note_data.get("image_urls", [])
            max_images = self.config.parser.max_images
            for img_url in image_urls[:max_images]:
                img_item = MediaItem(url=img_url, media_type="image")
                img_item = self._download_media(img_item, save_dir)
                content.images.append(img_item)

        return content

    def _extract_note_id(self, url: str) -> Optional[str]:
        """从URL中提取24位十六进制的note_id"""
        for pattern in NOTE_ID_PATTERNS:
            match = pattern.search(url)
            if match:
                return match.group(1)

        parsed = urlparse(url)

        # 通用匹配：从URL路径中寻找24位十六进制串
        hex_match = re.search(r'([0-9a-fA-F]{24})', parsed.path)
        if hex_match:
            return hex_match.group(1)

        # 也检查 query 参数中是否藏有 note_id
        full_url = parsed.path + "?" + parsed.query if parsed.query else parsed.path
        hex_match = re.search(r'([0-9a-fA-F]{24})', full_url)
        if hex_match:
            return hex_match.group(1)

        return None

    def _resolve_note_id_from_page(self, url: str) -> tuple[Optional[str], str]:
        """
        当重定向URL中无法直接提取note_id时，
        请求页面并从 canonical link / og:url / __INITIAL_STATE__ 中提取。
        返回 (note_id, 最终url)。
        """
        try:
            resp = self.session.get(
                url,
                timeout=self.config.parser.request_timeout,
                allow_redirects=True,
            )
            final_url = resp.url
            logger.info("最终页面URL: %s", final_url[:120])

            # 先从最终URL提取
            note_id = self._extract_note_id(final_url)
            if note_id:
                return note_id, final_url

            html = resp.text

            # 从 og:url / canonical 中提取
            for pattern in [
                r'<link\s+rel="canonical"\s+href="([^"]*)"',
                r'<meta\s+property="og:url"\s+content="([^"]*)"',
            ]:
                m = re.search(pattern, html)
                if m:
                    candidate = m.group(1)
                    nid = self._extract_note_id(candidate)
                    if nid:
                        return nid, candidate

            # 从 __INITIAL_STATE__ 中搜索 noteId
            state_match = INITIAL_STATE_PATTERN.search(html)
            if state_match:
                raw = state_match.group(1).replace("undefined", "null")
                try:
                    data = json.loads(raw)
                    note_map = data.get("note", {}).get("noteDetailMap", {})
                    for key in note_map:
                        if re.fullmatch(r'[0-9a-fA-F]{24}', key):
                            return key, final_url
                except json.JSONDecodeError:
                    pass

        except Exception as e:
            logger.warning("页面提取note_id失败: %s", e)

        return None, url

    @staticmethod
    def _extract_type_from_url(url: str) -> Optional[str]:
        """从URL查询参数中提取type提示（如 type=video）"""
        params = parse_qs(urlparse(url).query)
        type_values = params.get("type", [])
        return type_values[0] if type_values else None

    def _fetch_note_detail(self, note_id: str, original_url: str = "") -> dict:
        """
        获取笔记详情数据。
        尝试顺序：
        1. 使用原始URL（含xsec_token等认证参数）直接访问
        2. 构造 /explore/ URL 访问
        3. 从 meta 标签提取基础信息
        4. 通过API接口获取
        """
        # 优先用带认证参数的原始URL
        if original_url and "xiaohongshu.com" in original_url:
            result = self._fetch_from_web_page(note_id, page_url=original_url)
            if result.get("title") or result.get("description"):
                return result

        # 尝试 /explore/ 格式
        explore_url = f"https://www.xiaohongshu.com/explore/{note_id}"
        result = self._fetch_from_web_page(note_id, page_url=explore_url)
        if result.get("title") or result.get("description"):
            return result

        logger.info("页面解析数据不足，尝试API接口")
        return self._fetch_from_api(note_id)

    def _fetch_from_web_page(self, note_id: str, page_url: str = "") -> dict:
        """
        访问Web页面，从 __INITIAL_STATE__ 中提取笔记数据。
        如果 __INITIAL_STATE__ 数据为空，回退到 meta 标签提取。
        """
        result = self._empty_result()

        if not page_url:
            page_url = f"https://www.xiaohongshu.com/explore/{note_id}"

        try:
            resp = self.session.get(
                page_url,
                timeout=self.config.parser.request_timeout,
            )
            resp.raise_for_status()

            # 检查是否被重定向到404
            if "/404" in resp.url:
                logger.warning("页面被重定向到404: %s", resp.url[:80])
                return self._extract_from_meta_tags(resp.text, result)

            # 提取 __INITIAL_STATE__ JSON
            match = INITIAL_STATE_PATTERN.search(resp.text)
            if not match:
                logger.warning("未找到 __INITIAL_STATE__，尝试 meta 标签")
                return self._extract_from_meta_tags(resp.text, result)

            raw_json = match.group(1).replace("undefined", "null")
            data = json.loads(raw_json)

            note_detail = self._find_note_detail(data, note_id)
            if not note_detail:
                logger.warning("noteDetailMap 中无笔记数据，尝试 meta 标签")
                return self._extract_from_meta_tags(resp.text, result)

            result["title"] = note_detail.get("title", "")
            result["description"] = note_detail.get("desc", "")

            user_info = note_detail.get("user", {})
            result["author"] = user_info.get("nickname", "")

            note_type = note_detail.get("type", "normal")
            if note_type == "video":
                result["type"] = "video"
                result["video_url"] = self._extract_best_video_url(note_detail)
            else:
                result["type"] = "image_text"
                result["image_urls"] = self._extract_image_urls(note_detail)

            tag_list = note_detail.get("tagList", [])
            result["tags"] = [t.get("name", "") for t in tag_list if t.get("name")]

            # 提取互动数据
            interact = note_detail.get("interactInfo", {})
            result["stats"] = {
                "likes": interact.get("likedCount", 0),
                "collects": interact.get("collectedCount", 0),
                "comments": interact.get("commentCount", 0),
                "shares": interact.get("shareCount", 0),
            }

        except json.JSONDecodeError as e:
            logger.error("解析 __INITIAL_STATE__ JSON失败: %s", e)
        except Exception as e:
            logger.error("页面解析失败: %s", e)

        return result

    @staticmethod
    def _extract_best_video_url(note_detail: dict) -> str:
        """
        从笔记详情中提取最优视频流URL。
        优先级：h264（兼容性最好）> h265 > av1 > h266
        """
        video_info = note_detail.get("video", {})
        media = video_info.get("media", {})
        stream = media.get("stream", {})

        codec_priority = ("h264", "h265", "av1", "h266")
        for codec in codec_priority:
            streams = stream.get(codec, [])
            if not streams:
                continue
            master_url = streams[0].get("masterUrl", "")
            if master_url:
                return master_url
            # 尝试备用URL
            backups = streams[0].get("backupUrls", [])
            if backups:
                return backups[0]

        return ""

    @staticmethod
    def _extract_image_urls(note_detail: dict) -> list[str]:
        """从笔记详情中提取图片URL列表"""
        urls = []
        image_list = note_detail.get("imageList", [])
        for img in image_list:
            info_list = img.get("infoList", [])
            if not info_list:
                continue
            # 选择较高质量版本（避免选到 width=0 的默认值）
            valid_infos = [i for i in info_list if i.get("width", 0) > 0]
            if valid_infos:
                best = max(valid_infos, key=lambda x: x.get("width", 0))
            else:
                best = info_list[0]
            img_url = best.get("url", "")
            if img_url:
                if not img_url.startswith("http"):
                    img_url = "https:" + img_url
                urls.append(img_url)
        return urls

    @staticmethod
    def _extract_from_meta_tags(html: str, result: dict) -> dict:
        """
        从 HTML meta 标签中提取基础信息（兜底方案）。
        即使反爬阻止了 __INITIAL_STATE__ 数据填充，
        meta 标签通常仍包含标题和描述。
        """
        og_title = re.search(
            r'<meta\s+property="og:title"\s+content="([^"]*)"', html
        )
        if og_title:
            title = og_title.group(1).replace(" - 小红书", "").strip()
            result["title"] = title

        desc = re.search(
            r'<meta\s+name="description"\s+content="([^"]*)"', html
        )
        if desc:
            result["description"] = desc.group(1)

        og_type = re.search(
            r'<meta\s+property="og:type"\s+content="([^"]*)"', html
        )
        if og_type and og_type.group(1) == "video":
            result["type"] = "video"

        keywords = re.search(
            r'<meta\s+name="keywords"\s+content="([^"]*)"', html
        )
        if keywords:
            result["tags"] = [
                t.strip() for t in keywords.group(1).split(",") if t.strip()
            ]

        og_image = re.search(
            r'<meta\s+property="og:image"\s+content="([^"]*)"', html
        )
        if og_image:
            result["cover_image"] = og_image.group(1)

        return result

    @staticmethod
    def _empty_result() -> dict:
        return {
            "title": "",
            "description": "",
            "author": "",
            "type": "image_text",
            "image_urls": [],
            "video_url": "",
            "tags": [],
            "stats": {},
        }

    def _find_note_detail(self, data: dict, note_id: str) -> Optional[dict]:
        """从 __INITIAL_STATE__ 中定位笔记详情对象"""
        # 路径1: note.noteDetailMap.{note_id}.note
        note_map = data.get("note", {}).get("noteDetailMap", {})
        if note_id in note_map:
            return note_map[note_id].get("note", {})

        # 路径2: 遍历查找
        for key, value in note_map.items():
            if isinstance(value, dict) and "note" in value:
                return value["note"]

        # 路径3: 递归搜索包含noteId的对象
        return self._recursive_find(data, "noteId", note_id)

    def _recursive_find(self, data, key: str, value: str) -> Optional[dict]:
        """递归搜索包含指定key-value对的字典"""
        if isinstance(data, dict):
            if data.get(key) == value:
                return data
            for v in data.values():
                result = self._recursive_find(v, key, value)
                if result:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = self._recursive_find(item, key, value)
                if result:
                    return result
        return None

    def _fetch_from_api(self, note_id: str) -> dict:
        """
        通过API获取笔记详情（需要认证）。
        这是备选方案，当页面解析失败时使用。
        """
        result = {
            "title": "",
            "description": "",
            "author": "",
            "type": "image_text",
            "image_urls": [],
            "video_url": "",
            "tags": [],
        }

        try:
            api_url = "https://edith.xiaohongshu.com/api/sns/web/v1/feed"
            payload = {"source_note_id": note_id, "image_formats": ["jpg", "webp", "avif"]}

            resp = self.session.post(
                api_url,
                json=payload,
                timeout=self.config.parser.request_timeout,
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get("data", {}).get("items", [])
            if not items:
                return result

            note_card = items[0].get("note_card", {})
            result["title"] = note_card.get("title", "")
            result["description"] = note_card.get("desc", "")

            user = note_card.get("user", {})
            result["author"] = user.get("nickname", "")

            note_type = note_card.get("type", "normal")
            if note_type == "video":
                result["type"] = "video"
                video = note_card.get("video", {})
                consumer = video.get("consumer", {})
                result["video_url"] = consumer.get("origin_video_key", "")
            else:
                result["type"] = "image_text"
                for img in note_card.get("image_list", []):
                    info_list = img.get("info_list", [])
                    if info_list:
                        best = max(info_list, key=lambda x: x.get("width", 0))
                        result["image_urls"].append(best.get("url", ""))

            tag_list = note_card.get("tag_list", [])
            result["tags"] = [t.get("name", "") for t in tag_list if t.get("name")]

        except Exception as e:
            logger.error("API获取笔记详情失败: %s", e)

        return result
