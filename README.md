[![English](https://img.shields.io/badge/English-README-blue)](README.md)
[![简体中文](https://img.shields.io/badge/简体中文-README-green)](README.zh-CN.md)

# 🎬 Movie Narrator

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/github/license/yourusername/movie-narrator)
![CI](https://github.com/yourusername/movie-narrator/actions/workflows/test.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/movie-narrator)

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
git clone https://github.com/yourusername/movie-narrator.git
cd movie-narrator
pip install -e .
```

For development:

```bash
pip install -e ".[dev]"
```

---

## Quick Start

Generate your first narrated movie video:

```bash
mn create \
  --movie "飞驰人生" \
  --style "热血搞笑" \
  --duration 60
```

Check version:

```bash
mn version
```

Show help:

```bash
mn --help
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

Future workflow:

```text
Movie → Research → Script → TTS → Subtitle →
Scene Detect → Scene Match → BGM → Render
```

---

## Project Structure

```text
movie-narrator/
├── src/movie_narrator/
│   ├── cli.py              # Typer CLI entry point
│   ├── config.py           # Pydantic settings
│   ├── models.py           # Data models
│   ├── pipeline/
│   │   ├── runner.py       # Pipeline orchestrator
│   │   ├── script.py       # LLM script generation
│   │   ├── tts.py          # Edge-TTS with caching
│   │   ├── subtitle.py     # SRT generation
│   │   └── render.py       # MoviePy video rendering
│   └── utils/
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

### v0.1.x

- [x] CLI
- [x] Script generation (LLM)
- [x] Edge-TTS narration
- [x] Subtitle generation
- [x] Video rendering
- [ ] Research Agent
- [ ] Background music
- [ ] WhisperX alignment

### v0.2.x

- [ ] Scene detection
- [ ] Automatic clip matching
- [ ] Semantic scene search

### v0.3.x

- [ ] Workflow DSL
- [ ] YAML pipeline execution
- [ ] Web UI

### v0.4.x

- [ ] Plugin system
- [ ] SDK
- [ ] Third-party extensions

---

## Documentation

- [Roadmap](docs/ROADMAP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Contributing](docs/CONTRIBUTING.md)

---

## License

Licensed under the [AGPL-3.0](LICENSE) License.
