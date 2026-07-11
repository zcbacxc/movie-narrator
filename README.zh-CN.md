[![English](https://img.shields.io/badge/English-README-blue)](README.md)
[![简体中文](https://img.shields.io/badge/简体中文-README-green)](README.zh-CN.md)

# 🎬 Movie Narrator

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/github/license/yourusername/movie-narrator)
![CI](https://github.com/yourusername/movie-narrator/actions/workflows/test.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/movie-narrator)

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

从官网下载安装：[https://ffmpeg.org/](https://ffmpeg.org/)

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
git clone https://github.com/yourusername/movie-narrator.git
cd movie-narrator
pip install -e .
```

开发模式安装：

```bash
pip install -e ".[dev]"
```

---

## 快速开始

生成你的第一个带解说的电影视频：

```bash
mn create \
  --movie "飞驰人生" \
  --style "热血搞笑" \
  --duration 60
```

查看版本：

```bash
mn version
```

查看帮助：

```bash
mn --help
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

未来工作流：

```text
电影 → 调研 → 脚本 → TTS → 字幕 →
场景检测 → 场景匹配 → BGM → 渲染
```

---

## 项目结构

```text
movie-narrator/
├── src/movie_narrator/
│   ├── cli.py              # Typer CLI 入口
│   ├── config.py           # Pydantic 配置
│   ├── models.py           # 数据模型
│   ├── pipeline/
│   │   ├── runner.py       # 流水线协调器
│   │   ├── script.py       # LLM 脚本生成
│   │   ├── tts.py          # Edge-TTS 语音合成（带缓存）
│   │   ├── subtitle.py     # SRT 字幕生成
│   │   └── render.py       # MoviePy 视频渲染
│   └── utils/
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

### v0.1.x

- [x] CLI 命令行工具
- [x] 脚本生成（LLM）
- [x] Edge-TTS 语音合成
- [x] 字幕生成
- [x] 视频渲染
- [ ] 调研 Agent
- [ ] 背景音乐
- [ ] WhisperX 对齐

### v0.2.x

- [ ] 场景检测
- [ ] 自动素材匹配
- [ ] 语义化场景搜索

### v0.3.x

- [ ] 工作流 DSL
- [ ] YAML 流水线执行
- [ ] Web UI

### v0.4.x

- [ ] 插件系统
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
