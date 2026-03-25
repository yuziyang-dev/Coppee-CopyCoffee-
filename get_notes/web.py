"""
Web 服务：为 Get笔记 提供 API 和前端界面。

使用 FastAPI + SSE 实现实时进度推送。
启动方式: python -m get_notes.web
"""

from __future__ import annotations

import asyncio
import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from get_notes.app import GetNotesApp, _load_dotenv
from get_notes.config import AppConfig

_load_dotenv()

app = FastAPI(title="Get笔记", version="0.1.0")

STATIC_DIR = Path(__file__).parent / "static"
STATIC_DIR.mkdir(exist_ok=True)

notes_app: Optional[GetNotesApp] = None


def get_notes_app() -> GetNotesApp:
    global notes_app
    if notes_app is None:
        notes_app = GetNotesApp()
    return notes_app


class ProgressHandler(logging.Handler):
    """Captures log messages and forwards them to a queue as SSE events."""

    def __init__(self, q: queue.Queue):
        super().__init__()
        self.q = q

    def emit(self, record: logging.LogRecord):
        msg = self.format(record)
        step = None
        if "Step 1" in msg or "解析链接" in msg:
            step = "parse"
        elif "Step 2" in msg or "处理内容" in msg:
            step = "process"
        elif "Step 3" in msg or "聚合" in msg:
            step = "aggregate"
        elif "Step 4b" in msg or "AI补全" in msg:
            step = "infer"
        elif "Step 4" in msg or "AI生成" in msg or "AI总结" in msg or "AI精准提取" in msg:
            step = "summarize"
        elif "笔记生成完成" in msg or "笔记已保存" in msg:
            step = "done"

        if step:
            self.q.put({"type": "progress", "step": step, "message": msg})


tasks: dict[str, dict] = {}


def _run_task(task_id: str, link: str, instruction: Optional[str], model: Optional[str] = None):
    """Run the processing pipeline in a background thread."""
    q = tasks[task_id]["queue"]

    handler = ProgressHandler(q)
    handler.setLevel(logging.INFO)
    root_logger = logging.getLogger("get_notes")
    root_logger.addHandler(handler)
    root_logger.setLevel(logging.INFO)

    try:
        q.put({"type": "progress", "step": "start", "message": "开始处理..."})
        app_instance = get_notes_app()
        if model:
            app_instance.config.llm.model = model
        card = app_instance.process_link(link, instruction)

        from dataclasses import asdict
        result = asdict(card)
        result.pop("raw_content", None)
        q.put({"type": "result", "data": result})
    except Exception as e:
        q.put({"type": "error", "message": str(e)})
    finally:
        root_logger.removeHandler(handler)
        q.put({"type": "end"})


@app.post("/api/process")
async def process_link(request: Request):
    body = await request.json()
    link = body.get("link", "").strip()
    instruction = body.get("instruction", "").strip() or None
    model = body.get("model", "").strip() or None

    if not link:
        return {"error": "请提供链接"}

    task_id = uuid.uuid4().hex[:12]
    tasks[task_id] = {"queue": queue.Queue(), "created": time.time()}

    thread = threading.Thread(
        target=_run_task, args=(task_id, link, instruction, model), daemon=True
    )
    thread.start()

    return {"task_id": task_id}


@app.get("/api/stream/{task_id}")
async def stream_progress(task_id: str):
    if task_id not in tasks:
        return {"error": "任务不存在"}

    q = tasks[task_id]["queue"]

    async def event_generator():
        while True:
            try:
                event = q.get_nowait()
            except queue.Empty:
                await asyncio.sleep(0.3)
                continue

            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

            if event.get("type") in ("end", "error"):
                break

        tasks.pop(task_id, None)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


ENV_PATH = Path(__file__).resolve().parent.parent / ".env"


def _try_write_env(key_map: dict[str, str]):
    """Best-effort write to .env; silently skip if read-only filesystem."""
    try:
        lines = []
        if ENV_PATH.exists():
            lines = ENV_PATH.read_text(encoding="utf-8").splitlines()

        found_keys: set[str] = set()
        new_lines = []
        for line in lines:
            updated = False
            for k, v in key_map.items():
                if line.startswith(f"{k}=") or line.startswith(f"{k} ="):
                    new_lines.append(f"{k}={v}")
                    found_keys.add(k)
                    updated = True
                    break
            if not updated:
                new_lines.append(line)

        for k, v in key_map.items():
            if k not in found_keys:
                new_lines.append(f"{k}={v}")

        ENV_PATH.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    except OSError:
        pass


@app.get("/api/settings")
async def get_settings():
    """返回当前配置（API Key 脱敏显示）"""
    app_inst = get_notes_app()
    key = app_inst.config.llm.api_key or ""
    masked = key[:6] + "***" + key[-4:] if len(key) > 10 else ("*" * len(key) if key else "")
    return {
        "api_key_masked": masked,
        "api_key_set": bool(key and "填入" not in key),
        "base_url": app_inst.config.llm.base_url,
        "model": app_inst.config.llm.model,
    }


@app.post("/api/settings")
async def save_settings(request: Request):
    """保存 API Key 并热更新运行时配置"""
    global notes_app
    body = await request.json()
    api_key = body.get("api_key", "").strip()
    base_url = body.get("base_url", "").strip()

    if not api_key:
        return {"error": "请填写 API Key"}

    if not base_url:
        base_url = "https://api.jiekou.ai/openai/v1"

    _try_write_env({"LLM_API_KEY": api_key, "LLM_BASE_URL": base_url})

    app_inst = get_notes_app()
    app_inst.config.llm.api_key = api_key
    app_inst.config.llm.base_url = base_url

    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


@app.get("/retro", response_class=HTMLResponse)
async def index_retro():
    html_path = STATIC_DIR / "index-retro.html"
    return HTMLResponse(html_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("get_notes.web:app", host="0.0.0.0", port=8000, reload=True)
