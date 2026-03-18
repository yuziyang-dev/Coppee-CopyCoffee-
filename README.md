# Get笔记 — 链接解析与AI笔记总结

通过小红书和抖音链接解析视频/图文内容，利用AI进行分析和笔记总结。

本项目是对 [Get笔记](https://doc.biji.com/) 核心技术链路的研究性实现。

---

## 架构概览

```
用户粘贴链接 (Web UI / CLI)
    │
    ▼
┌─────────────────────────────────────────┐
│  Web 服务层 (web.py)                     │
│  FastAPI + SSE 实时进度推送               │
│  前端: 单页HTML, 设置面板, 模型选择        │
└──────────────┬──────────────────────────┘
               │
               ▼
┌─────────────────────────────────────────┐
│  链接解析层 (parsers/)                    │
│  ├── PlatformRouter   平台识别路由        │
│  ├── XiaohongshuParser                   │
│  │   短链重定向 → note_id提取             │
│  │   → __INITIAL_STATE__ 解析             │
│  │   → meta标签兜底                       │
│  └── DouyinParser                        │
│      短链重定向 → video_id提取             │
│      → iteminfo API 获取元数据            │
└──────────────┬──────────────────────────┘
               │ ParsedContent
               ▼
┌─────────────────────────────────────────┐
│  内容处理层 (processors/)                 │
│  ├── VideoProcessor   视频→音频→ASR转录   │
│  ├── ImageProcessor   图片→OCR/视觉理解   │
│  └── TextProcessor    文本清洗+多源聚合    │
└──────────────┬──────────────────────────┘
               │ 聚合文本
               ▼
┌─────────────────────────────────────────┐
│  AI摘要层 (ai/)                          │
│  └── NoteSummarizer                      │
│      结构化Prompt → LLM → JSON解析        │
│      输出: 标题/摘要/要点/章节/标签         │
└──────────────┬──────────────────────────┘
               │ NoteSummary
               ▼
     Web展示 / JSON文件 / CLI输出
```

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

视频处理需要 FFmpeg：

```bash
# macOS
brew install ffmpeg

# Ubuntu/Debian
sudo apt install ffmpeg
```

### 2. 启动 Web 服务

```bash
python3 -m uvicorn get_notes.web:app --port 8000
```

打开浏览器访问 http://localhost:8000 ，页面会自动弹出设置面板引导你填入 API Key。

### 3. CLI 模式（可选）

```bash
# 先配置 .env
cp .env.example .env
# 编辑 .env 填入 API Key

# 直接解析链接
python -m get_notes "https://v.douyin.com/xxxxx"

# 带补充要求
python -m get_notes "https://v.douyin.com/xxxxx" -i "整理营销方面的内容"

# 交互模式
python -m get_notes --interactive
```

---

## 项目结构

```
get_notes/
├── __init__.py          # 包初始化
├── __main__.py          # python -m 入口
├── main.py              # CLI 命令行界面
├── app.py               # 应用核心（串联完整流程）
├── config.py            # 配置管理（环境变量读取）
├── models.py            # 数据模型定义
├── web.py               # FastAPI Web 服务 + API
├── static/
│   └── index.html       # 前端单页应用
├── parsers/             # 链接解析层
│   ├── base.py          # 解析器基类（重定向、下载）
│   ├── router.py        # 平台识别路由
│   ├── douyin.py        # 抖音短视频解析
│   └── xiaohongshu.py   # 小红书图文/视频解析
├── processors/          # 内容处理层
│   ├── pipeline.py      # 处理管线（串联各处理器）
│   ├── video.py         # 视频处理（FFmpeg + ASR）
│   ├── image.py         # 图片处理（OCR + 视觉AI）
│   └── text.py          # 文本处理（清洗 + 聚合）
└── ai/                  # AI摘要层
    └── summarizer.py    # LLM笔记总结器
```

---

## 核心技术点

### 1. 链接解析 — 反爬对抗与多级回退

| 技术 | 说明 |
|------|------|
| 短链重定向跟踪 | GET + `allow_redirects=True` 跟踪完整重定向链（xhslink.com → xiaohongshu.com），HEAD 逐跳作为回退 |
| note_id 提取 | 正则匹配 `/explore/`、`/discovery/item/` 路径中的 24 位十六进制 ID，支持从 query 参数、canonical link、og:url 和 `__INITIAL_STATE__` 中兜底提取 |
| 页面数据解析 | 解析 `window.__INITIAL_STATE__` JSON 获取 `noteDetailMap`，提取标题、描述、作者、标签、图片列表、视频流地址 |
| 视频流选择 | 按 codec 优先级 h264 > h265 > av1 > h266 选择兼容性最好的视频流 |
| 认证参数保留 | 优先使用用户原始 URL（含 `xsec_token` 等认证参数）访问，提高解析成功率 |
| 三级兜底 | `__INITIAL_STATE__` → meta 标签（og:title/description/image） → edith API |

### 2. 多模态内容处理

| 模态 | 处理链路 |
|------|---------|
| 视频 | FFmpeg `-vn -acodec pcm_s16le` 提取 WAV 音频 → OpenAI Whisper 本地 ASR 转录（或腾讯云 ASR） |
| 图片 | PaddleOCR / Tesseract 文字识别 + 多模态 LLM 图片语义理解 |
| 文本 | HTML 标签清洗 → Unicode 规范化 → 空白合并 → `#标签` 提取 → 多源文本聚合（标题 + 描述 + 转录 + OCR + 图片描述） |

### 3. AI 摘要生成

| 技术 | 说明 |
|------|------|
| API 协议 | OpenAI Chat Completions 兼容格式（`/v1/chat/completions`），适配 JieKou AI、DeepSeek、OpenAI 等任意兼容服务 |
| 结构化输出 | `response_format: {"type": "json_object"}` 强制 JSON 输出，容错解析支持 markdown 代码块包裹 |
| 双 Prompt 策略 | 短内容（≤3000 字）生成 标题+摘要+要点+标签；长内容（>3000 字）额外生成章节纪要 |
| 模型选择 | 前端下拉选择，支持 DeepSeek V3/R1、Qwen3、Kimi K2、GLM 4.5、Claude、GPT-4o、Gemini、Llama 等 11 个模型 |
| 基础摘要兜底 | LLM 不可用时截取前 300 字 + 提取 `#标签` 作为基础摘要 |

### 4. Web 服务架构

| 技术 | 说明 |
|------|------|
| 后端框架 | FastAPI + Uvicorn |
| 实时进度 | Server-Sent Events (SSE)：后台线程处理 → logging Handler 捕获日志 → Queue → StreamingResponse 推送到前端 |
| 前端 | 纯 HTML/CSS/JS 单页应用，无构建步骤。Noto Serif SC 衬线字体 + 暖色调纸张质感设计 |
| 设置面板 | 前端齿轮按钮弹出 Modal，通过 `POST /api/settings` 将 API Key 写入 `.env` 并热更新运行时配置 |
| 任务模型 | 每次请求生成 task_id → 后台线程执行 → SSE 流式推送 parse/process/aggregate/summarize/done 五个阶段 |

### 5. 配置管理

| 方式 | 说明 |
|------|------|
| `.env` 文件 | python-dotenv 自动加载项目根目录 `.env`，存储 `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL` |
| Web 设置面板 | 浏览器内填写 API Key，后端读写 `.env` 文件 + 热更新内存配置 |
| 环境变量 | 所有敏感配置通过 `os.getenv()` 读取，支持 Docker / CI 环境注入 |
| dataclass | `AppConfig` 聚合 ASR / LLM / Parser / Storage 四组配置，带默认值 |

---

## 技术栈总览

| 层 | 技术 |
|----|------|
| Web 服务 | Python 3 · FastAPI · Uvicorn · SSE |
| 前端 | HTML5 · CSS3 · Vanilla JS · Google Fonts |
| 链接解析 | requests · 正则表达式 · JSON 解析 · HTTP 重定向 |
| 视频处理 | FFmpeg（音频提取） |
| 语音识别 | OpenAI Whisper（本地） · 腾讯云 ASR（云端） |
| 图片处理 | PaddleOCR · Tesseract · 多模态 LLM |
| AI 总结 | OpenAI API 兼容协议 · JieKou AI（多模型网关） |
| 配置 | python-dotenv · dataclass · 环境变量 |

---

## 可选依赖

根据使用场景按需安装：

```bash
# 本地语音识别
pip install openai-whisper

# 腾讯云ASR
pip install tencentcloud-sdk-python

# 中文OCR
pip install paddleocr paddlepaddle

# 备选OCR
pip install pytesseract Pillow
```

---

## 处理流程详解

### 小红书视频

```
分享链接/短链 → GET自动重定向 → 提取note_id(24位hex)
→ 带xsec_token访问原始URL → 解析__INITIAL_STATE__
→ 选择h264视频流 → 下载视频
→ FFmpeg提取音频 → Whisper ASR转文字
→ 聚合(标题+描述+转录) → LLM生成结构化笔记
```

### 小红书图文

```
分享链接/短链 → GET自动重定向 → 提取note_id
→ 解析__INITIAL_STATE__ → 下载图片(最多10张)
→ OCR识别文字 + 视觉AI理解
→ 聚合(标题+描述+OCR+图片描述) → LLM生成笔记
```

### 抖音短视频

```
分享链接 → 302重定向 → 提取video_id
→ iteminfo API获取元数据 → 下载无水印视频
→ FFmpeg提取音频 → ASR转文字 → LLM生成摘要
```
