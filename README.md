[![English](https://img.shields.io/badge/English-README-blue)](README.md)
[![简体中文](https://img.shields.io/badge/简体中文-README-green)](README.zh-CN.md)

# 🎬 Movie Narrator

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/badge/license-AGPL--3.0--or--later-blue)
![CI](https://github.com/zcbacxc/movie-narrator/actions/workflows/ci.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/movie-narrator)
![Downloads](https://img.shields.io/pypi/dm/movie-narrator)

> One Prompt → One Narrated Movie Video

Movie Narrator is an open-source toolkit that automatically generates movie recap videos with narration, subtitles, and rendered output from a simple command.

---

## Features

- 🎬 LLM-powered movie recap script generation
- 🔊 Text-to-Speech narration (Edge-TTS by default)
- 💬 Automatic SRT subtitle generation
- 🌐 Multi-language subtitles with LLM translation
- 🏁 Multi-candidate horse race — run N variations, score and auto-pick the best
- 🎯 Reference video imitation — extract style from viral narration
- 👁️ VLM scene captioning via cloud VLM API
- 🎭 Narrator perspective (omniscient / character / detective)
- 🎨 Render template system (title cards, watermarks, slogans)
- 🔍 TMDB fact verification for movie cards
- 🖥️ Web UI (separate `movie-narrator-web` package — FastAPI + React)
- 🎞️ Video rendering with MoviePy and FFmpeg
- 📝 Script markdown export
- 🎵 Background music integration
- 📦 Metadata export
- 🔌 Extensible plugin architecture
- ☁️ Async task queue (local + remote job submission, progress polling, retry)
- 🌐 Remote inference via REST API

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

# WhisperX + semantic search (requires PyTorch; Python < 3.14)
pip install "movie-narrator[ml]"

# Web UI (FastAPI + React) — separate package
pip install movie-narrator-web

# Everything
pip install "movie-narrator[full]"
```

> **Note on Python 3.14+**: The `[ml]` extra (WhisperX + sentence-transformers) is currently gated to Python < 3.14 due to upstream dependency wheel availability. On Python 3.14+, `pip install "movie-narrator[full]"` will install all other extras and **silently skip** the ML components. The `align` and `match` pipeline steps will soft-degrade (see [Soft steps](#pipeline)) instead of failing.

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
```

### More Commands

```bash
mn create --config examples/job.example.yaml     # Drive from YAML config
mn create --subtitle-lang en --subtitle-mode bilingual  # Multi-language subtitles
mn race --movie "飞驰人生" --video movie.mp4 --candidates 3  # Multi-candidate horse race
mn imitate --reference viral_ref.mp4 --movie "飞驰人生"  # Reference video imitation
mn serve               # Start remote inference API server (v0.6.1+)
mn submit -m <movie>   # Submit async task
mn tasks               # List recent tasks
mn version             # Show version
mn --help              # Full help with all 24 CLI flags
```

All 24 CLI flags are documented in [`examples/cli-usage.sh`](examples/cli-usage.sh).

---

## Configuration

All settings use the `MN_` prefix to avoid conflicts with other tools.

### Via `.env` file (recommended)

`~/.movie-narrator/.env` is auto-created with default values on first run — edit it to configure LLM, TTS, and other settings. This file lives outside the package, so `pip install/upgrade/uninstall` never touches it. You can also create a project-level `.env` in your working directory for per-project overrides.

```bash
MN_LLM_BASE_URL=http://localhost:11434/v1
MN_LLM_API_KEY=ollama
MN_LLM_MODEL=qwen2.5:7b
MN_DEFAULT_VOICE=zh-CN-YunxiNeural
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
| 2 | `cwd/.env` | Project-level |
| 3 | `~/.movie-narrator/.env` | User-level, never lost on pip install/upgrade/uninstall |
| 4 | Built-in defaults | Local Ollama |

### Full reference

See [`.env.example`](.env.example) for the complete list of all environment variables (LLM + TTS infrastructure only). All pipeline behavior is configured via [`examples/job.example.yaml`](examples/job.example.yaml) — params keys covering scene detection, match, render, translate, BGM, WhisperX, async, and video sizes.

### LLM Provider Guides

Movie Narrator works with any OpenAI-compatible LLM. New user? Check out the [LLM Provider Guides](docs/LLM_PROVIDERS.md) for step-by-step registration and free-tier setup:

| Provider | Free Tier | Best For |
|----------|-----------|----------|
| [Ollama](docs/llm-providers/ollama.md) | Completely free (local) | Privacy, offline use |
| [Zhipu (GLM)](docs/llm-providers/zhipu.md) | glm-4-flash unlimited free | Zero-cost, no GPU |
| [Alibaba Bailian](docs/llm-providers/alibaba-bailian.md) | 1M tokens per model | Qwen flagship models |
| [Xiaomi MiMo](docs/llm-providers/xiaomi-mimo.md) | Limited-time free + ¥10 invite bonus | LLM + TTS in one platform |
| [SiliconFlow](docs/llm-providers/siliconflow.md) | Free models + voucher credits | Multi-model switching |

---

## Output

| File | Description |
|------|-------------|
| `narration.mp3` | AI-generated narration audio |
| `mixed.mp3` | Narration + BGM overlay (when BGM enabled; otherwise `narration.mp3` used directly) |
| `subtitle.srt` | Synchronized subtitle file (original narration) |
| `subtitle.<lang>.srt` | Translated subtitle (when `--subtitle-lang` set) |
| `subtitle.bilingual.srt` | Bilingual subtitle (when `--subtitle-lang` set) |
| `script.md` | Human-readable script |
| `research.json` | Movie research data (when `--research`) |
| `metadata.json` | Segment timings, pipeline status, config |
| `final.mp4` | Rendered video (16:9 or 9:16) |
| `matches.json` | Scene-to-segment clip matching (when video provided) |
| `clips/` | Per-segment clip .mp4 files (when `--no-clips` not set) |

---

## Pipeline

16-step sequential pipeline (see [Architecture](docs/ARCHITECTURE.md)):

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → translate_subtitles → generate_subtitle →
run_qa_gate → render_video → validate_deliverable → export_clips
```

**Soft steps** (research, align, scene detect, scene match, BGM, translate, QA gate, clip export) gracefully skip or soft-degrade when optional dependencies are missing or upstream data is unavailable. Use `--strict` to abort instead.

---

## Project Structure

```text
movie-narrator/
├── src/movie_narrator/
│   ├── cli.py               # Typer CLI entry point
│   ├── config.py            # Pydantic settings
│   ├── models.py            # Data models (Context, Status, etc.)
│   ├── contract.py          # Stable API contract surface
│   ├── pipeline/            # 16-step pipeline (runner, steps, errors)
│   ├── cloud/               # Task queue + remote inference (v0.6.x)
│   ├── workflow/            # YAML job config (schema, loader, merge)
│   ├── tts/                 # TTS provider abstraction layer
│   └── utils/               # Shared utilities (console, log, font, etc.)
├── tests/                   # Unit + integration tests
├── docs/                    # Architecture, guides, roadmap
├── examples/                # Job YAML, CLI usage, plugins
└── .github/workflows/       # CI/CD
```

---

## Roadmap

Current focus: cloud infrastructure (distributed rendering, API gateway, cloud storage). See the full [Roadmap](docs/ROADMAP.md) for version-by-version details from v0.6.2 to v1.0.

---

## Documentation

- [Roadmap](docs/ROADMAP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [LLM Provider Guides](docs/LLM_PROVIDERS.md)
- [Contributing](docs/CONTRIBUTING.md)
- [AI Coding Assistant Guide](docs/AI_GUIDE.md)

---

## License

Licensed under the [AGPL-3.0-or-later](LICENSE) License.
