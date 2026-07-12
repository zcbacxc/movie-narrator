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
- 📝 Script markdown export (`script.md`)
- 🎵 Background music integration (BGM)
- 🎬 Scene-level clip export
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

```bash
# Option 1: winget
winget install Gyan.FFmpeg

# Option 2: chocolatey
choco install ffmpeg

# Option 3: Manual download from https://ffmpeg.org/
```

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

#### Optional extras

```bash
# Scene detection (PySceneDetect)
pip install "movie-narrator[media]"

# WhisperX + semantic search (requires PyTorch)
pip install "movie-narrator[ml]"

# Everything
pip install "movie-narrator[full]"
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
| `--video, -V` | Source movie file path | - |
| `--library-dir` | Movie library directory | - |
| `--research` | Enable plot research via LLM | `false` |
| `--no-research` | Disable plot research | - |
| `--bgm` | Background music file path | - |
| `--no-bgm` | Disable BGM even if default is set | `false` |
| `--no-clips` | Skip scene-level clip export | `false` |
| `--strict` | Abort pipeline on soft step failure | `false` |
| `--keep-cache` | Keep TTS cache files | `false` |

### Offline Demo (No LLM Required)

```bash
# CI=1 uses silent audio fallback, bypasses LLM and Edge-TTS
CI=1 mn create --movie "Demo" --duration 10
```

### Other Commands

```bash
mn version   # Show version
mn --help    # Show help
```

---

## Configuration

All settings use the `MN_` prefix to avoid conflicts with other tools.

### Via `.env` file (recommended)

Create `.env` in your project directory (or `~/.movie-narrator/.env` for global config — this file lives outside the package, so `pip install/upgrade/uninstall` never touches it):

```bash
MN_LLM_BASE_URL=http://localhost:11434/v1
MN_LLM_API_KEY=ollama
MN_LLM_MODEL=qwen2.5:7b
MN_DEFAULT_VOICE=zh-CN-YunxiNeural
MN_DEFAULT_FORMAT=16:9
```

### Via environment variables

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

### Config lookup order

| Priority | Location | Notes |
|----------|----------|-------|
| 1 | Environment variables (`MN_*`) | Highest |
| 2 | `当前目录/.env` | Project-level |
| 3 | `~/.movie-narrator/.env` | User-level, never lost on pip install/upgrade/uninstall |
| 4 | Built-in defaults | Local Ollama |

### Full reference

| Variable | Description | Default |
|----------|-------------|---------|
| `MN_LLM_BASE_URL` | LLM API endpoint | `http://localhost:11434/v1` |
| `MN_LLM_API_KEY` | LLM API key | `ollama` |
| `MN_LLM_MODEL` | LLM model name | `qwen2.5:7b` |
| `MN_DEFAULT_VOICE` | Edge-TTS voice | `zh-CN-YunxiNeural` |
| `MN_DEFAULT_FORMAT` | Video aspect ratio | `16:9` |
| `MN_LIBRARY_DIR` | Movie library path | - |
| `MN_DEFAULT_BGM` | Default BGM file | - |
| `MN_RESEARCH_ENABLED` | Auto-enable research | `false` |
| `MN_RESEARCH_PROVIDER` | Research backend | `llm` |
| `MN_SCENE_THRESHOLD` | PySceneDetect threshold | `27.0` |
| `MN_SCENE_FRAME_SKIP` | Frames to skip in scene detection | `10` |
| `MN_MATCH_MIN_SCORE` | Minimum match score | `0.25` |
| `MN_EXPORT_CLIPS_DEFAULT` | Auto-export clips | `true` |

---

## Output

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

| File | Description |
|------|-------------|
| `narration.mp3` | AI-generated narration audio |
| `final_audio.mp3` | Narration + BGM mix (when BGM enabled) |
| `subtitle.srt` | Synchronized subtitle file |
| `script.md` | Human-readable script |
| `script.json` | Machine-readable script segments |
| `research.json` | Movie research data (when `--research`) |
| `metadata.json` | Segment timings, pipeline status, config |
| `final.mp4` | Rendered video (16:9 or 9:16) |
| `matches.json` | Scene-to-segment matching (when video provided) |
| `clips/` | Per-segment clip files |

---

## Pipeline

13-step sequential pipeline (see [Architecture](docs/ARCHITECTURE.md)):

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → generate_subtitle → render_video →
export_clips
```

**Soft steps** (research, align, scene detect, scene match, BGM, clip export) gracefully skip when optional dependencies are missing. Use `--strict` to abort instead.

---

## Project Structure

```text
movie-narrator/
├── src/movie_narrator/
│   ├── __init__.py          # Package metadata (__version__)
│   ├── cli.py               # Typer CLI entry point
│   ├── config.py            # Pydantic settings
│   ├── models.py            # Data models (Context, Status, etc.)
│   ├── pipeline/
│   │   ├── runner.py        # 13-step pipeline orchestrator
│   │   ├── resolve.py       # Source video resolution
│   │   ├── assets.py        # Asset validation
│   │   ├── research.py      # LLM movie research
│   │   ├── script.py        # LLM script generation
│   │   ├── script_export.py # Script markdown export
│   │   ├── tts.py           # Edge-TTS with caching
│   │   ├── align.py         # WhisperX audio alignment
│   │   ├── scenes.py        # PySceneDetect scene detection
│   │   ├── match.py         # Heuristic clip matching
│   │   ├── bgm.py           # Background music mixing
│   │   ├── subtitle.py      # SRT generation
│   │   ├── render.py        # MoviePy video rendering
│   │   ├── export_clips.py  # Per-segment clip export
│   │   └── errors.py        # PipelineStrictError
│   └── utils/
│       ├── async_utils.py   # Sync/async bridge
│       ├── environment.py   # Environment collection
│       ├── font.py          # CJK font fallback
│       ├── json_parser.py   # LLM JSON extraction
│       ├── llm.py           # OpenAI client wrapper
│       ├── optional_deps.py # Optional dependency probing
│       └── prompts.py       # Prompt templates
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

### v0.2.x — Scene & Media ✅

- [x] Research agent for movie plot research (`--research`)
- [x] WhisperX audio-text alignment
- [x] Scene detection from movie videos
- [x] Automatic clip matching based on script
- [x] Semantic scene search (embedding-based, requires `[ml]`)
- [x] Background music integration (BGM mixing)
- [x] Script markdown export (`script.md`)
- [x] Scene-level clip output (`clips/`)

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
