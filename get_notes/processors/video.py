"""
视频处理器：音频提取 + ASR语音转文字

处理流程：
1. 使用 FFmpeg 从视频中提取音频轨道（mp3格式，体积小传输快）
2. 调用云端 ASR 或本地 Whisper 进行语音识别
3. 返回转录文本

ASR 优先级：
  云端 API（复用 LLM 的 API Key）→ 腾讯云 ASR → 本地 Whisper
"""

from __future__ import annotations

import logging
import os
import subprocess
from typing import Optional

import requests as http_requests

from get_notes.config import AppConfig

logger = logging.getLogger(__name__)


class VideoProcessor:
    """视频内容处理器"""

    def __init__(self, config: AppConfig):
        self.config = config

    def extract_audio_from_url(self, video_url: str, output_path: str) -> str:
        """
        FFmpeg 直接从视频 URL 流式提取音频，跳过下载视频文件。
        FFmpeg 只读取音频轨道数据，不会下载整个视频。
        """
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        cmd = [
            "ffmpeg", "-y",
            "-headers", "Referer: https://www.xiaohongshu.com/\r\nUser-Agent: Mozilla/5.0\r\n",
            "-i", video_url,
            "-vn",
            "-acodec", "libmp3lame",
            "-ar", "16000",
            "-ac", "1",
            "-q:a", "6",
            output_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg URL流式提取失败: {result.stderr[-200:]}")

        size_mb = os.path.getsize(output_path) / 1024 / 1024
        logger.info("音频流式提取完成（未下载视频）: %s (%.1fMB)", output_path, size_mb)
        return output_path

    def extract_audio(self, video_path: str, output_path: Optional[str] = None) -> str:
        """从本地视频文件提取音频（回退方案）。"""
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        if output_path is None:
            base = os.path.splitext(video_path)[0]
            output_path = f"{base}_audio.mp3"

        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-vn",
            "-acodec", "libmp3lame",
            "-ar", "16000",
            "-ac", "1",
            "-q:a", "6",
            output_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.error("FFmpeg错误: %s", result.stderr[-500:])
                raise RuntimeError(f"FFmpeg音频提取失败: {result.stderr[-200:]}")

            size_mb = os.path.getsize(output_path) / 1024 / 1024
            logger.info("音频提取完成: %s (%.1fMB)", output_path, size_mb)
            return output_path
        except FileNotFoundError:
            raise RuntimeError(
                "FFmpeg未安装。请安装: brew install ffmpeg (macOS) / apt install ffmpeg (Linux)"
            )

    # ── 云端 ASR：复用 LLM API Key，无需额外配置 ──

    def transcribe_with_cloud_api(self, audio_path: str) -> str:
        """
        通过 OpenAI 兼容的 /audio/transcriptions 端点转录。
        复用 LLM 的 base_url 和 api_key，无需额外配置。
        典型耗时：1 分钟音频 3-5 秒。
        """
        llm = self.config.llm
        if not llm.api_key or "填入" in llm.api_key:
            raise RuntimeError("LLM API Key 未配置")

        url = f"{llm.base_url}/audio/transcriptions"
        file_size = os.path.getsize(audio_path) / 1024 / 1024
        logger.info("云端ASR转录: %s (%.1fMB) → %s", audio_path, file_size, url)

        with open(audio_path, "rb") as f:
            resp = http_requests.post(
                url,
                headers={"Authorization": f"Bearer {llm.api_key}"},
                files={"file": (os.path.basename(audio_path), f, "audio/mpeg")},
                data={
                    "model": "whisper-1",
                    "language": "zh",
                    "response_format": "text",
                },
                timeout=180,
            )

        if resp.status_code == 404:
            raise RuntimeError("云端 ASR 端点不可用 (404)")

        resp.raise_for_status()

        # response_format=text 返回纯文本
        transcript = resp.text.strip()
        if not transcript:
            content_type = resp.headers.get("content-type", "")
            if "json" in content_type:
                import json
                data = json.loads(resp.text)
                transcript = data.get("text", "")

        logger.info("云端ASR完成，文本长度: %d", len(transcript))
        return transcript

    # ── 本地 Whisper ──

    def transcribe_with_whisper(self, audio_path: str) -> str:
        """本地 Whisper 模型转录（CPU 较慢，作为兜底方案）。"""
        try:
            import whisper
        except ImportError:
            raise RuntimeError("Whisper未安装。请安装: pip install openai-whisper")

        if not os.path.exists(audio_path):
            raise FileNotFoundError(f"音频文件不存在: {audio_path}")

        logger.info("使用本地Whisper转录（可能较慢）: %s", audio_path)
        model = whisper.load_model("base")
        result = model.transcribe(audio_path, language="zh", task="transcribe")

        transcript = result.get("text", "")
        logger.info("Whisper转录完成，文本长度: %d", len(transcript))
        return transcript

    # ── 腾讯云 ASR ──

    def transcribe_with_tencent_asr(self, audio_path: str) -> str:
        """腾讯云 ASR（需要额外配置 Secret ID/Key）。"""
        asr_config = self.config.asr
        if not asr_config.secret_id or not asr_config.secret_key:
            raise RuntimeError("腾讯云ASR未配置")

        try:
            from tencentcloud.common import credential
            from tencentcloud.asr.v20190614 import asr_client, models as asr_models
            import base64
        except ImportError:
            raise RuntimeError("腾讯云SDK未安装: pip install tencentcloud-sdk-python")

        cred = credential.Credential(asr_config.secret_id, asr_config.secret_key)
        client = asr_client.AsrClient(cred, "")

        with open(audio_path, "rb") as f:
            audio_data = base64.b64encode(f.read()).decode()

        req = asr_models.CreateRecTaskRequest()
        req.EngineModelType = asr_config.engine_type
        req.ChannelNum = 1
        req.ResTextFormat = asr_config.result_text_format
        req.SourceType = 1
        req.Data = audio_data
        req.DataLen = len(audio_data)

        logger.info("提交腾讯云ASR任务...")
        resp = client.CreateRecTask(req)
        task_id = resp.Data.TaskId

        import time
        query_req = asr_models.DescribeTaskStatusRequest()
        query_req.TaskId = task_id

        for _ in range(120):
            time.sleep(5)
            query_resp = client.DescribeTaskStatus(query_req)
            status = query_resp.Data.StatusStr
            if status == "success":
                transcript = query_resp.Data.Result
                logger.info("腾讯云ASR完成，文本长度: %d", len(transcript))
                return transcript
            elif status == "failed":
                raise RuntimeError(f"ASR任务失败: {query_resp.Data.ErrorMsg}")

        raise RuntimeError("ASR任务超时")

    # ── 统一入口 ──

    def transcribe(self, audio_path: str) -> str:
        """
        统一转录接口，按速度优先级逐级回退：
        1. 云端 API（最快，~3-5秒/分钟，复用 LLM Key）
        2. 腾讯云 ASR（需额外配置）
        3. 本地 Whisper（最慢但零依赖API）
        """
        # 优先：云端 API
        if self.config.llm.api_key and "填入" not in self.config.llm.api_key:
            try:
                return self.transcribe_with_cloud_api(audio_path)
            except Exception as e:
                logger.warning("云端ASR失败，尝试下一方案: %s", e)

        # 次选：腾讯云
        if self.config.asr.secret_id and self.config.asr.secret_key:
            try:
                return self.transcribe_with_tencent_asr(audio_path)
            except Exception as e:
                logger.warning("腾讯云ASR失败，回退Whisper: %s", e)

        # 兜底：本地 Whisper
        return self.transcribe_with_whisper(audio_path)

    def process(
        self,
        video_path: Optional[str] = None,
        video_url: Optional[str] = None,
        audio_dir: Optional[str] = None,
    ) -> tuple[str, str]:
        """
        完整视频处理流程：提取音频 → 转录。

        优先用 video_url 直接流式提取音频（不下载视频）；
        失败时回退到 video_path 本地文件提取。
        """
        audio_path = None

        # 优先：从 URL 直接流式提取（跳过视频下载）
        if video_url:
            out_dir = audio_dir or os.path.join(self.config.storage.temp_dir, "audio")
            out_path = os.path.join(out_dir, f"{hash(video_url) & 0xFFFFFFFF:08x}.mp3")
            try:
                audio_path = self.extract_audio_from_url(video_url, out_path)
            except Exception as e:
                logger.warning("URL流式提取失败，回退到本地文件: %s", e)

        # 回退：从本地视频文件提取
        if not audio_path and video_path:
            audio_path = self.extract_audio(video_path)

        if not audio_path:
            raise RuntimeError("无可用的视频源")

        transcript = self.transcribe(audio_path)
        return transcript, audio_path
