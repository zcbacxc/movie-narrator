[![English](https://img.shields.io/badge/English-README-blue)](README.md)
[![简体中文](https://img.shields.io/badge/简体中文-README-green)](README.zh-CN.md)

# 🎬 Movie Narrator

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-AGPL--3.0-blue)
![CI](https://github.com/zcbacxc/movie-narrator/actions/workflows/ci.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/movie-narrator)
![Downloads](https://img.shields.io/pypi/dm/movie-narrator)

> 一个提示 → 一个带解说的电影视频

Movie Narrator 是一个开源工具包，可通过简单命令自动生成带有解说、字幕和渲染输出的电影解说视频。

---

## 功能特性

- 🎬 LLM 驱动的电影解说脚本生成
- 🔊 文字转语音解说（默认使用 Edge-TTS）
- 💬 自动生成 SRT 字幕文件
- 🌐 多语言字幕（LLM 翻译）
- 🏁 多候选赛马 — 同输入跑 N 套变体，打分排名，自动选优
- 🎯 参考片模仿 — 从爆款解说提取风格
- 👁️ VLM 视觉场景描述（云端 VLM API）
- 🎭 解说视角（全知 / 角色 / 悬疑）
- 🎨 渲染模板系统（标题卡、水印、口号）
- 🔍 TMDB 事实验证
- 🖥️ Web UI（独立 `movie-narrator-web` 包 — FastAPI + React）
- 🎞️ 使用 MoviePy 和 FFmpeg 渲染视频
- 📝 脚本 Markdown 导出
- 🎵 背景音乐集成
- 📦 元数据导出
- 🔌 可扩展的插件架构
- ☁️ 异步任务队列（本地 + 远程任务提交、进度轮询、重试）
- 🌐 通过 REST API 远程推理

---

## 安装

### 环境要求

- Python 3.10+
- FFmpeg

### 安装 FFmpeg

#### macOS

```bash
brew install ffmpeg
```

#### Ubuntu / Debian

```bash
sudo apt install ffmpeg
```

#### Windows

```bash
# 方式一：winget
winget install Gyan.FFmpeg

# 方式二：chocolatey
choco install ffmpeg

# 方式三：从官网下载 https://ffmpeg.org/
```

验证安装：

```bash
ffmpeg -version
```

---

## 安装 Movie Narrator

### 从 PyPI 安装

```bash
pip install movie-narrator
```

### 从源码安装

```bash
git clone https://github.com/zcbacxc/movie-narrator.git
cd movie-narrator
pip install -e .
```

#### 可选扩展

```bash
# 场景检测（PySceneDetect）
pip install "movie-narrator[media]"

# WhisperX + 语义搜索（需要 PyTorch，Python < 3.14）
pip install "movie-narrator[ml]"

# Web UI（FastAPI + React）— 独立包
pip install movie-narrator-web

# 全部
pip install "movie-narrator[full]"
```

> **Python 3.14+ 注意**：`[ml]` 扩展（WhisperX + sentence-transformers）因上游依赖 wheel 可用性限制，目前仅支持 Python < 3.14。在 Python 3.14+ 上，`pip install "movie-narrator[full]"` 会安装其他所有扩展并**静默跳过** ML 组件。`align` 和 `match` 步骤会软降级（见[软步骤](#流水线)）而非报错。

开发模式安装：

```bash
pip install -e ".[dev]"
```

---

## 快速开始

### 前置条件

- **LLM**：默认使用本地 Ollama（先运行 `ollama serve`）。也可通过 `.env` 文件配置远程 LLM。
- **FFmpeg**：视频渲染必需。

### 基本用法

```bash
# 生成带解说的电影视频
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60

# 自定义音色和视频比例
mn create --movie "飞驰人生" --voice "zh-CN-XiaoxiaoNeural" --format "9:16"
```

### 更多命令

```bash
mn create --config examples/job.example.yaml     # 通过 YAML 配置驱动
mn create --subtitle-lang en --subtitle-mode bilingual  # 多语言字幕
mn race --movie "飞驰人生" --video movie.mp4 --candidates 3  # 多候选赛马
mn imitate --reference viral_ref.mp4 --movie "飞驰人生"  # 参考片模仿
mn serve               # 启动远程推理 API 服务 (v0.6.1+)
mn submit -m <movie>   # 提交异步任务
mn tasks               # 列出最近任务
mn version             # 查看版本
mn --help              # 完整帮助（含全部 24 个 CLI 参数）
```

全部 24 个 CLI 参数及各场景用法示例请参考 [`examples/cli-usage.sh`](examples/cli-usage.sh)。

---

## 配置

所有配置项使用 `MN_` 前缀，避免与其他工具冲突。

### 通过 `.env` 文件（推荐）

`~/.movie-narrator/.env` 在首次运行时自动创建并填入默认值 — 编辑此文件配置 LLM、TTS 等设置。该文件位于包目录之外，`pip install/upgrade/uninstall` 均不会触碰。也可在工作目录创建 `.env` 进行项目级覆盖。

```bash
MN_LLM_BASE_URL=http://localhost:11434/v1
MN_LLM_API_KEY=ollama
MN_LLM_MODEL=qwen2.5:7b
MN_DEFAULT_VOICE=zh-CN-YunxiNeural
```

### 通过环境变量

```powershell
# PowerShell
$env:MN_LLM_BASE_URL="http://localhost:11434/v1"
$env:MN_LLM_MODEL="qwen2.5:7b"
mn create --movie "飞驰人生" --duration 60
```

```bash
# Linux / macOS
export MN_LLM_BASE_URL=http://localhost:11434/v1
export MN_LLM_MODEL=qwen2.5:7b
mn create --movie "飞驰人生" --duration 60
```

### 配置查找顺序

| 优先级 | 位置 | 说明 |
|--------|------|------|
| 1 | 环境变量（`MN_*`） | 最高优先 |
| 2 | `cwd/.env` | 项目级 |
| 3 | `~/.movie-narrator/.env` | 用户级，pip install/upgrade/uninstall 均不会丢失 |
| 4 | 内置默认值 | 本地 Ollama |

### 完整配置项

完整环境变量列表（仅 LLM + TTS 基础配置）及默认值和说明，请查看 [`.env.example`](.env.example)。所有流水线行为参数通过 [`examples/job.example.yaml`](examples/job.example.yaml) 配置，涵盖场景检测、匹配、渲染、翻译、BGM、WhisperX、异步、视频分辨率等。

### LLM 服务商导航

Movie Narrator 支持任何 OpenAI 兼容的 LLM。新用户不知道选哪个？查看 [LLM 服务商导航](docs/LLM_PROVIDERS.md)，含注册流程和免费额度说明：

| 服务商 | 免费额度 | 适合场景 |
|--------|---------|---------|
| [Ollama](docs/llm-providers/ollama.md) | 完全免费（本地） | 隐私、离线使用 |
| [智谱 GLM](docs/llm-providers/zhipu.md) | glm-4-flash 永久免费 | 零成本、无需显卡 |
| [阿里云百炼](docs/llm-providers/alibaba-bailian.md) | 每模型 100 万 Tokens | 通义千问旗舰模型 |
| [小米 MiMo](docs/llm-providers/xiaomi-mimo.md) | 限时免费 + 邀请码 ¥10 奖励 | LLM + TTS 一站式 |
| [硅基流动](docs/llm-providers/siliconflow.md) | 免费模型 + 赠送额度 | 多模型灵活切换 |

---

## 输出结构

| 文件 | 说明 |
|------|------|
| `narration.mp3` | AI 生成的解说音频 |
| `mixed.mp3` | 解说 + BGM 混音（启用 BGM 时；否则直接使用 `narration.mp3`） |
| `subtitle.srt` | 同步字幕文件（原版解说） |
| `subtitle.<lang>.srt` | 翻译字幕（设置 `--subtitle-lang` 时输出） |
| `subtitle.bilingual.srt` | 双语字幕（设置 `--subtitle-lang` 时输出） |
| `script.md` | 人类可读的脚本 |
| `research.json` | 电影调研数据（使用 `--research` 时） |
| `metadata.json` | 片段时间戳、流水线状态、配置 |
| `final.mp4` | 渲染的视频（16:9 或 9:16） |
| `matches.json` | 场景-片段匹配结果（提供视频时） |
| `clips/` | 逐片段剪辑 .mp4 文件（未设置 `--no-clips` 时） |

---

## 流水线

16 步顺序流水线（详见[架构设计](docs/ARCHITECTURE.md)）：

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → translate_subtitles → generate_subtitle →
run_qa_gate → render_video → validate_deliverable → export_clips
```

**软步骤**（research、align、scene detect、scene match、BGM、translate、QA gate、clip export）在缺少可选依赖或上游数据缺失时**优雅跳过**或**软降级**。使用 `--strict` 改为直接中断。

---

## 项目结构

```text
movie-narrator/
├── src/movie_narrator/
│   ├── cli.py               # Typer CLI 入口
│   ├── config.py            # Pydantic 配置
│   ├── models.py            # 数据模型（Context、Status 等）
│   ├── contract.py          # 稳定 API 契约层
│   ├── pipeline/            # 16 步流水线（runner、steps、errors）
│   ├── cloud/               # 任务队列 + 远程推理 (v0.6.x)
│   ├── workflow/            # YAML 任务配置（schema、loader、merge）
│   ├── tts/                 # TTS provider 抽象层
│   └── utils/               # 共享工具（console、log、font 等）
├── tests/                   # 单元 + 集成测试
├── docs/                    # 架构、指南、路线图
├── examples/                # Job YAML、CLI 用法、插件
└── .github/workflows/       # CI/CD
```

---

## 文档

- [路线图](docs/ROADMAP.zh-CN.md)
- [架构设计](docs/ARCHITECTURE.md)
- [LLM 服务商导航](docs/LLM_PROVIDERS.md)
- [贡献指南](docs/CONTRIBUTING.md)
- [AI 编程工具指南](docs/AI_GUIDE.md)

---

## 许可证

基于 [AGPL-3.0](LICENSE) 许可证发布。
