"""
一键测试脚本：验证 .env 配置和 LLM API 连通性。
用法: python test_api.py
"""

import sys
from pathlib import Path

# 加载 .env
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from get_notes.config import AppConfig

config = AppConfig()

print("=" * 55)
print("  Get笔记 — API 连通性测试")
print("=" * 55)
print()

# 检查配置
print(f"  LLM_BASE_URL: {config.llm.base_url}")
print(f"  LLM_MODEL:    {config.llm.model}")
key_display = config.llm.api_key[:8] + "..." if len(config.llm.api_key) > 8 else "(未设置)"
print(f"  LLM_API_KEY:  {key_display}")
print()

if not config.llm.api_key or "填入" in config.llm.api_key:
    print("  [!] API Key 未设置")
    print("  请编辑 .env 文件，将 LLM_API_KEY 替换为你的真实 Key")
    print()
    print("  vim .env")
    print("  # 或")
    print("  open .env")
    sys.exit(1)

# 测试 API 调用
print("  发送测试请求...")
import requests

try:
    resp = requests.post(
        f"{config.llm.base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.llm.api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.llm.model,
            "temperature": 0.3,
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "用一句话介绍手冲咖啡"},
            ],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    content = data["choices"][0]["message"]["content"]
    model_used = data.get("model", "unknown")
    usage = data.get("usage", {})

    print()
    print(f"  [OK] API 调用成功!")
    print(f"  模型: {model_used}")
    print(f"  Token: {usage.get('prompt_tokens', '?')} + {usage.get('completion_tokens', '?')} = {usage.get('total_tokens', '?')}")
    print(f"  回复: {content}")
    print()
    print("=" * 55)
    print("  一切就绪! 你可以运行:")
    print()
    print('  python -m get_notes "你的链接" -v')
    print()
    print("  或交互模式:")
    print()
    print("  python -m get_notes --interactive")
    print("=" * 55)

except requests.exceptions.HTTPError as e:
    print()
    print(f"  [FAIL] API 返回错误: {e}")
    try:
        error_detail = resp.json()
        print(f"  详情: {error_detail}")
    except Exception:
        print(f"  响应: {resp.text[:300]}")
    sys.exit(1)

except requests.exceptions.ConnectionError:
    print()
    print("  [FAIL] 无法连接到 API 服务器")
    print(f"  请检查 BASE_URL: {config.llm.base_url}")
    sys.exit(1)

except Exception as e:
    print()
    print(f"  [FAIL] 发生错误: {e}")
    sys.exit(1)
