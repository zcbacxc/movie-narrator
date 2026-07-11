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
- 📦 导出元数据
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

## 输出结构

```text
output/
└── 飞驰人生/
    ├── narration.mp3
    ├── subtitle.srt
    ├── metadata.json
    └── final.mp4
```

| 文件 | 说明 |
|------|------|
| `narration.mp3` | AI 生成的解说音频 |
| `subtitle.srt` | 同步字幕文件 |
| `metadata.json` | 片段时间戳和视频配置 |
| `final.mp4` | 渲染的视频（16:9 或 9:16） |

> 未来版本将添加 `script.md` 和 `clips/` 用于场景级输出。

---

## 流水线

当前工作流：

```text
电影 → 脚本 → TTS → 字幕 → 渲染
```

未来工作流（见[路线图](docs/ROADMAP.md)）：

```text
电影 → 调研 → 脚本 → TTS → 字幕 →
场景检测 → 场景匹配 → BGM → 渲染
```

---

## 项目结构

```text
movie-narrator/
├── src/movie_narrator/
│   ├── __init__.py         # 包元数据（__version__）
│   ├── cli.py              # Typer CLI 入口
│   ├── config.py           # Pydantic 配置
│   ├── models.py           # 数据模型
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── runner.py       # 流水线协调器
│   │   ├── script.py       # LLM 脚本生成
│   │   ├── tts.py          # Edge-TTS 语音合成（带缓存）
│   │   ├── subtitle.py     # SRT 字幕生成
│   │   └── render.py       # MoviePy 视频渲染
│   └── utils/
│       ├── __init__.py
│       ├── async_utils.py  # 同步/异步桥接
│       ├── font.py         # CJK 字体回退
│       ├── llm.py          # OpenAI 客户端封装
│       ├── prompts.py      # 提示词模板
│       └── json_parser.py  # LLM JSON 提取
├── tests/
│   └── test_context.py
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

### v0.2.x — 场景与媒体

- [ ] 电影剧情调研 Agent
- [ ] WhisperX 音频-文本对齐
- [ ] 电影视频场景检测
- [ ] 基于脚本的自动素材匹配
- [ ] 语义化场景搜索（Embedding）
- [ ] 背景音乐集成（BGM 混音）
- [ ] 脚本 Markdown 导出（`script.md`）
- [ ] 场景级片段输出（`clips/`）

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
