[![English](https://img.shields.io/badge/English-README-blue)](README.md)
[![简体中文](https://img.shields.io/badge/简体中文-README-green)](README.zh-CN.md)

# 🎬 Movie Narrator

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/github/license/zcbacxc/movie-narrator)
![CI](https://github.com/zcbacxc/movie-narrator/actions/workflows/test.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/movie-narrator)
![Downloads](https://img.shields.io/pypi/dm/movie-narrator)

> 一个提示 → 一个带解说的电影视频

Movie Narrator 是一个开源工具包，可通过简单命令自动生成带有解说、字幕和渲染输出的电影解说视频。

---

## 功能特性

- 🎬 使用 LLM 生成电影解说脚本
- 🔊 文字转语音解说（默认使用 Edge-TTS）
- 💬 自动生成 SRT 字幕文件
- 🎞️ 使用 MoviePy 和 FFmpeg 渲染视频
- 📝 脚本 Markdown 导出（`script.md`）
- 🎵 背景音乐集成（BGM 混音）
- 🎬 场景级片段导出
- 📦 元数据导出
- 🔌 可扩展的流水线架构
- 🐍 纯 Python 实现

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

# WhisperX + 语义搜索（需要 PyTorch）
pip install "movie-narrator[ml]"

# 全部
pip install "movie-narrator[full]"
```

开发模式安装：

```bash
pip install -e ".[dev]"
```

---

## 快速开始

### 前置条件

- **LLM**: 默认使用本地 Ollama（先运行 `ollama serve`）。也可通过 `.env` 文件配置远程 LLM。
- **FFmpeg**: 视频渲染必需。

### 基本用法

```bash
# 生成带解说的电影视频
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60

# 自定义音色和视频比例
mn create --movie "飞驰人生" --voice "zh-CN-XiaoxiaoNeural" --format "9:16"

# 保留 TTS 缓存用于调试
mn create --movie "飞驰人生" --keep-cache
```

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--movie, -m` | 电影名称（必填） | - |
| `--style, -s` | 解说风格 | `热血搞笑` |
| `--duration, -d` | 目标时长（秒） | `60` |
| `--voice, -v` | Edge-TTS 音色 | `zh-CN-YunxiNeural` |
| `--format, -f` | 视频比例（`16:9` 或 `9:16`） | `16:9` |
| `--video, -V` | 源电影文件路径 | - |
| `--library-dir` | 电影库目录 | - |
| `--research` | 启用 LLM 剧情调研 | `false` |
| `--no-research` | 禁用剧情调研 | - |
| `--bgm` | 背景音乐文件路径 | - |
| `--no-bgm` | 禁用 BGM | `false` |
| `--no-clips` | 跳过场景片段导出 | `false` |
| `--strict` | 软步骤失败时中止 | `false` |
| `--keep-cache` | 保留 TTS 缓存文件 | `false` |

### 离线演示（无需 LLM）

```bash
# CI=1 使用静音回退音频，跳过 LLM 和 Edge-TTS
CI=1 mn create --movie "Demo" --duration 10
```

### 其他命令

```bash
mn version   # 查看版本
mn --help    # 查看帮助
```

---

## 配置

所有配置项使用 `MN_` 前缀，避免与其他工具冲突。

### 通过 `.env` 文件（推荐）

在项目目录创建 `.env`（或 `~/.movie-narrator/.env` 作为全局配置，`pip upgrade` 不会覆盖）：

```bash
MN_LLM_BASE_URL=http://localhost:11434/v1
MN_LLM_API_KEY=ollama
MN_LLM_MODEL=qwen2.5:7b
MN_DEFAULT_VOICE=zh-CN-YunxiNeural
MN_DEFAULT_FORMAT=16:9
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
| 2 | `当前目录/.env` | 项目级 |
| 3 | `~/.movie-narrator/.env` | 用户级，pip upgrade 不覆盖 |
| 4 | 内置默认值 | 本地 Ollama |

### 完整配置项

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MN_LLM_BASE_URL` | LLM API 地址 | `http://localhost:11434/v1` |
| `MN_LLM_API_KEY` | LLM API 密钥 | `ollama` |
| `MN_LLM_MODEL` | LLM 模型名称 | `qwen2.5:7b` |
| `MN_DEFAULT_VOICE` | Edge-TTS 音色 | `zh-CN-YunxiNeural` |
| `MN_DEFAULT_FORMAT` | 视频比例 | `16:9` |
| `MN_LIBRARY_DIR` | 电影库路径 | - |
| `MN_DEFAULT_BGM` | 默认背景音乐 | - |
| `MN_RESEARCH_ENABLED` | 自动启用调研 | `false` |
| `MN_RESEARCH_PROVIDER` | 调研后端 | `llm` |
| `MN_SCENE_THRESHOLD` | PySceneDetect 阈值 | `27.0` |
| `MN_MATCH_MIN_SCORE` | 最低匹配分数 | `0.25` |
| `MN_EXPORT_CLIPS_DEFAULT` | 自动导出片段 | `true` |

---

## 输出结构

```text
output/
└── 飞驰人生/
    ├── narration.mp3
    ├── final_audio.mp3
    ├── subtitle.srt
    ├── script.md
    ├── script.json
    ├── research.json
    ├── metadata.json
    ├── final.mp4
    ├── matches.json
    └── clips/
```

| 文件 | 说明 |
|------|------|
| `narration.mp3` | AI 生成的解说音频 |
| `final_audio.mp3` | 解说 + BGM 混合音频（启用 BGM 时） |
| `subtitle.srt` | 同步字幕文件 |
| `script.md` | 人类可读的脚本 |
| `script.json` | 机器可读的脚本分段 |
| `research.json` | 电影调研数据（使用 `--research` 时） |
| `metadata.json` | 片段时间戳、流水线状态、配置 |
| `final.mp4` | 渲染的视频（16:9 或 9:16） |
| `matches.json` | 场景-片段匹配结果（提供视频时） |
| `clips/` | 逐片段剪辑文件 |

---

## 流水线

13 步顺序流水线（详见[架构设计](docs/ARCHITECTURE.md)）：

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → generate_subtitle → render_video →
export_clips
```

**软步骤**（research、align、scene detect、scene match、BGM、clip export）在可选依赖缺失时自动跳过。使用 `--strict` 可改为中断。

---

## 项目结构

```text
movie-narrator/
├── src/movie_narrator/
│   ├── __init__.py          # 包元数据（__version__）
│   ├── cli.py               # Typer CLI 入口
│   ├── config.py            # Pydantic 配置
│   ├── models.py            # 数据模型（Context、Status 等）
│   ├── pipeline/
│   │   ├── runner.py        # 13 步流水线协调器
│   │   ├── resolve.py       # 源视频解析
│   │   ├── assets.py        # 素材验证
│   │   ├── research.py      # LLM 电影调研
│   │   ├── script.py        # LLM 脚本生成
│   │   ├── script_export.py # 脚本 Markdown 导出
│   │   ├── tts.py           # Edge-TTS 语音合成（带缓存）
│   │   ├── align.py         # WhisperX 音频对齐
│   │   ├── scenes.py        # PySceneDetect 场景检测
│   │   ├── match.py         # 启发式素材匹配
│   │   ├── bgm.py           # 背景音乐混合
│   │   ├── subtitle.py      # SRT 字幕生成
│   │   ├── render.py        # MoviePy 视频渲染
│   │   ├── export_clips.py  # 逐片段剪辑导出
│   │   └── errors.py        # PipelineStrictError
│   └── utils/
│       ├── async_utils.py   # 同步/异步桥接
│       ├── environment.py   # 环境信息收集
│       ├── font.py          # CJK 字体回退
│       ├── json_parser.py   # LLM JSON 提取
│       ├── llm.py           # OpenAI 客户端封装
│       ├── optional_deps.py # 可选依赖探测
│       └── prompts.py       # 提示词模板
├── tests/
│   ├── test_context.py
│   ├── test_settings.py
│   ├── test_errors.py
│   ├── test_align.py
│   ├── test_assets.py
│   ├── test_bgm.py
│   ├── test_cli_resolve.py
│   ├── test_match.py
│   ├── test_optional_deps.py
│   ├── test_render_real.py
│   ├── test_research.py
│   ├── test_resolve.py
│   ├── test_runner_strict.py
│   ├── test_scenes.py
│   └── test_script_export.py
├── docs/
├── assets/
└── .github/workflows/
```

---

## 路线图

### v0.1.x — 核心流水线 ✅

- [x] CLI 接口（`mn create`、`mn version`）
- [x] LLM 脚本生成（JSON 输出）
- [x] Edge-TTS 语音合成（并发生成）
- [x] SRT 字幕生成（毫秒精度）
- [x] MoviePy 视频渲染（16:9 / 9:16）
- [x] TTS 结果缓存（内容寻址）
- [x] 元数据导出（JSON）
- [x] CI 流水线（单元测试 + 冒烟测试）

### v0.2.x — 场景与媒体 ✅

- [x] 电影剧情调研 Agent（`--research`）
- [x] WhisperX 音频-文本对齐
- [x] 电影视频场景检测
- [x] 基于脚本的自动素材匹配
- [ ] 语义化场景搜索（Embedding）
- [x] 背景音乐集成（BGM 混音）
- [x] 脚本 Markdown 导出（`script.md`）
- [x] 场景级片段输出（`clips/`）

### v0.3.x — 平台与工作流

- [ ] 工作流 DSL（流水线自定义）
- [ ] YAML 流水线配置
- [ ] Web UI（Gradio / FastAPI）
- [ ] 多语言字幕支持

### v0.4.x — 可扩展性

- [ ] 插件系统（自定义流水线步骤）
- [ ] SDK
- [ ] 第三方扩展

---

## 文档

- [路线图](docs/ROADMAP.md)
- [架构设计](docs/ARCHITECTURE.md)
- [贡献指南](docs/CONTRIBUTING.md)

---

## 许可证

基于 [AGPL-3.0](LICENSE) 许可证发布。
