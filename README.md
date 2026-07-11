[![English](https://img.shields.io/badge/English-README-blue)](README.md)
[![简体中文](https://img.shields.io/badge/简体中文-README-green)](README.zh-CN.md)

# 🎬 Movie Narrator

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/github/license/zcbacxc/movie-narrator)
![CI](https://github.com/zcbacxc/movie-narrator/actions/workflows/test.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/movie-narrator)
![Downloads](https://img.shields.io/pypi/dm/movie-narrator)

> One Prompt → One Narrated Movie Video

Movie Narrator is an open-source toolkit that automatically generates movie recap videos with narration, subtitles, and rendered output from a simple command.

---

## Features

- 🎬 Generate movie recap scripts with LLMs
- 🔊 Text-to-Speech narration (Edge-TTS by default)
- 💬 Automatic SRT subtitle generation
- 🎞️ Video rendering with MoviePy and FFmpeg
- 📦 Metadata export
- 🔌 Extensible pipeline architecture
- 🐍 Pure Python implementation

---

## Installation

### Requirements

- Python 3.10+
- FFmpeg

### Install FFmpeg

#### macOS

```bash
brew install ffmpeg
```

#### Ubuntu / Debian

```bash
sudo apt install ffmpeg
```

#### Windows

Download and install from: [https://ffmpeg.org/](https://ffmpeg.org/)

Verify installation:

```bash
ffmpeg -version
```

---

## Install Movie Narrator

### From PyPI

```bash
pip install movie-narrator
```

### From Source

```bash
git clone https://github.com/zcbacxc/movie-narrator.git
cd movie-narrator
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

---

## Quick Start

### Prerequisites

- **LLM**: Default uses local Ollama (`ollama serve` to start). Or configure remote LLM via `.env` file.
- **FFmpeg**: Required for video rendering.

### Basic Usage

```bash
# Generate a narrated movie video
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60

# With custom voice and format
mn create --movie "飞驰人生" --voice "zh-CN-XiaoxiaoNeural" --format "9:16"

# Keep TTS cache for debugging
mn create --movie "飞驰人生" --keep-cache
```

### CLI Options

| Option | Description | Default |
|--------|-------------|---------|
| `--movie, -m` | Movie name (required) | - |
| `--style, -s` | Narration style | `热血搞笑` |
| `--duration, -d` | Target duration (seconds) | `60` |
| `--voice, -v` | Edge-TTS voice | `zh-CN-YunxiNeural` |
| `--format, -f` | Video format (`16:9` or `9:16`) | `16:9` |
| `--keep-cache` | Keep TTS cache files | `false` |

### Offline Demo (No LLM Required)

```bash
# CI mode uses silent audio fallback, no network needed
mn create --movie "Demo" --duration 10
```

### Other Commands

```bash
mn version   # Show version
mn --help    # Show help
```

---

## Output

```text
output/
└── 飞驰人生/
    ├── narration.mp3
    ├── subtitle.srt
    ├── metadata.json
    └── final.mp4
```

| File | Description |
|------|-------------|
| `narration.mp3` | AI-generated narration audio |
| `subtitle.srt` | Synchronized subtitle file |
| `metadata.json` | Segment timings and video config |
| `final.mp4` | Rendered video (16:9 or 9:16) |

> Future versions will add `script.md` and `clips/` for scene-level output.

---

## Pipeline

Current workflow:

```text
Movie → Script → TTS → Subtitle → Render
```

Future workflow (see [Roadmap](docs/ROADMAP.md)):

```text
Movie → Research → Script → TTS → Subtitle →
Scene Detect → Scene Match → BGM → Render
```

---

## Project Structure

```text
movie-narrator/
├── src/movie_narrator/
│   ├── __init__.py         # Package metadata (__version__)
│   ├── cli.py              # Typer CLI entry point
│   ├── config.py           # Pydantic settings
│   ├── models.py           # Data models
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── runner.py       # Pipeline orchestrator
│   │   ├── script.py       # LLM script generation
│   │   ├── tts.py          # Edge-TTS with caching
│   │   ├── subtitle.py     # SRT generation
│   │   └── render.py       # MoviePy video rendering
│   └── utils/
│       ├── __init__.py
│       ├── async_utils.py  # Sync/async bridge
│       ├── font.py         # CJK font fallback
│       ├── llm.py          # OpenAI client wrapper
│       ├── prompts.py      # Prompt templates
│       └── json_parser.py  # LLM JSON extraction
├── tests/
│   └── test_context.py
├── docs/
├── assets/
└── .github/workflows/
```

---

## Roadmap

### v0.1.x — Core Pipeline ✅

- [x] CLI interface (`mn create`, `mn version`)
- [x] LLM script generation with JSON output
- [x] Edge-TTS narration with concurrent generation
- [x] SRT subtitle generation with millisecond precision
- [x] MoviePy video rendering (16:9 / 9:16)
- [x] TTS result caching with content-addressable keys
- [x] Metadata export (JSON)
- [x] CI pipeline (unit tests + smoke test)

### v0.2.x — Scene & Media

- [ ] Research agent for movie plot research
- [ ] WhisperX audio-text alignment
- [ ] Scene detection from movie videos
- [ ] Automatic clip matching based on script
- [ ] Semantic scene search (embedding-based)
- [ ] Background music integration (BGM mixing)

### v0.3.x — Platform & Workflow

- [ ] Workflow DSL for pipeline customization
- [ ] YAML-based pipeline configuration
- [ ] Web UI (Gradio / FastAPI)
- [ ] Multi-language subtitle support

### v0.4.x — Extensibility

- [ ] Plugin system for custom pipeline steps
- [ ] Python SDK for programmatic usage
- [ ] Third-party extension support

---

## Documentation

- [Roadmap](docs/ROADMAP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Contributing](docs/CONTRIBUTING.md)

---

## License

Licensed under the [AGPL-3.0](LICENSE) License.
