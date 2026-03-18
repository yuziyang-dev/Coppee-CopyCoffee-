"""
项目配置：API密钥、模型参数、存储路径等。

所有敏感配置通过环境变量读取，不硬编码。
"""

import os
from dataclasses import dataclass, field


@dataclass
class ASRConfig:
    """腾讯云 ASR 语音识别配置"""
    secret_id: str = field(default_factory=lambda: os.getenv("TENCENT_SECRET_ID", ""))
    secret_key: str = field(default_factory=lambda: os.getenv("TENCENT_SECRET_KEY", ""))
    app_id: str = field(default_factory=lambda: os.getenv("TENCENT_APP_ID", ""))
    engine_type: str = "16k_zh"  # 16k采样率中文
    result_text_format: int = 0  # 0-每句带时间戳


@dataclass
class LLMConfig:
    """大语言模型配置（兼容 OpenAI API 格式）"""
    api_key: str = field(default_factory=lambda: os.getenv("LLM_API_KEY", ""))
    base_url: str = field(default_factory=lambda: os.getenv("LLM_BASE_URL", "https://api.openai.com/v1"))
    model: str = field(default_factory=lambda: os.getenv("LLM_MODEL", "gpt-4o"))
    max_tokens: int = 4096
    temperature: float = 0.3


@dataclass
class ParserConfig:
    """链接解析器配置"""
    user_agent: str = (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.0 Mobile/15E148 Safari/604.1"
    )
    request_timeout: int = 30
    max_retries: int = 3
    max_images: int = 10  # 最多处理的图片数量


@dataclass
class StorageConfig:
    """存储配置"""
    output_dir: str = field(default_factory=lambda: os.getenv("OUTPUT_DIR", "./output"))
    temp_dir: str = field(default_factory=lambda: os.getenv("TEMP_DIR", "./temp"))


@dataclass
class AppConfig:
    """应用总配置"""
    asr: ASRConfig = field(default_factory=ASRConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    parser: ParserConfig = field(default_factory=ParserConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)

    def ensure_dirs(self):
        os.makedirs(self.storage.output_dir, exist_ok=True)
        os.makedirs(self.storage.temp_dir, exist_ok=True)
