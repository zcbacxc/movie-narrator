[![English](https://img.shields.io/badge/English-README-blue)](README.md)
[![简体中文](https://img.shields.io/badge/简体中文-README-green)](README.zh-CN.md)

# 🎬 Movie Narrator

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/github/license/zcbacxc/movie-narrator)
![CI](https://github.com/zcbacxc/movie-narrator/actions/workflows/ci.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/movie-narrator)
![Downloads](https://img.shields.io/pypi/dm/movie-narrator)

> One Prompt → One Narrated Movie Video

Movie Narrator is an open-source toolkit that automatically generates movie recap videos with narration, subtitles, and rendered output from a simple command.

---

## Features

- 🎬 Generate movie recap scripts with LLMs
- 🔊 Text-to-Speech narration (Edge-TTS by default)
- 💬 Automatic SRT subtitle generation
- 🌐 Multi-language subtitles (`--subtitle-lang en` translates narration cues via LLM and writes `subtitle.<lang>.srt` + `subtitle.bilingual.srt`)
- 🖥️ Web UI (`mn web` — local Gradio browser app with form inputs, cooperative cancel, and artifact download)
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

# Web UI (Gradio)
pip install "movie-narrator[web]"

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
| `--subtitle-lang` | Target language tag (`en`, `ja`, `zh-TW`, ...); empty = feature off | - |
| `--subtitle-mode` | Overlay mode: `original` / `translated` / `bilingual` | `original` |
| `--config` | Path to job YAML (movie/steps/params); CLI flags override YAML | - |

### Job YAML config (v0.3)

```bash
# Drive a job from YAML (movie may live only in the file)
mn create --config examples/job.example.yaml

# CLI flags still win over YAML
mn create --config examples/job.example.yaml --movie "OtherTitle" --no-clips
```

See [`examples/job.example.yaml`](examples/job.example.yaml) for the full whitelist: soft-step toggles under `steps:` (`research`, `align`, `scene`, `match`, `bgm`, `export`, `translate`), `params:` (`scene_threshold`, `match_min_score`, `research_provider`, `translate_provider`, `translate_retries`, `translate_chunk_chars`, `translate_chunk_size`), and the multi-language subtitle top-level keys `subtitle_lang` / `subtitle_mode`. Relative `video` / `bgm` / `library_dir` paths resolve against the YAML file's directory. LLM credentials stay in `.env` / `MN_*` only.

### Multi-language subtitles

```bash
# Translate narration cues to English and overlay them on the video
mn create --movie "Inception" --subtitle-lang en --subtitle-mode bilingual

# Or just write the translated SRT files (no on-screen change)
mn create --movie "Inception" --subtitle-lang en
```

When `--subtitle-lang` is set, `generate_subtitle` always writes three SRT files:

- `subtitle.srt` — original narration (always present, `subtitle_path` invariant)
- `subtitle.<lang>.srt` — translated (e.g. `subtitle.en.srt`)
- `subtitle.bilingual.srt` — cue body `f"{original}\n{translation}"` (LF between lines)

`--subtitle-mode` chooses which file `render_video` reads:

| Mode | Overlay text source |
|------|---------------------|
| `original` (default) | `subtitle.srt` |
| `translated` | `subtitle.<lang>.srt` (falls back to `subtitle.srt` with a warn if missing) |
| `bilingual` | `subtitle.bilingual.srt` (same fallback) |

Setting `subtitle_mode=translated|bilingual` without `subtitle_lang` raises `JobConfigError` at merge time. Failure policy: LLM retries `MN_TRANSLATE_RETRIES` times, then soft-degrades to filling the translation track with the original text and surfacing a warning.

### Web UI (v0.3.5)

```bash
# Install with web extra
pip install "movie-narrator[web]"

# Launch local browser app
mn web

# Or with custom host/port
mn web --host 0.0.0.0 --port 8080

# Create a public Gradio share link (temporary)
mn web --share
```

The Web UI provides a form-based interface to all CLI options: movie name, style, duration, voice, format, video/BGM upload, subtitle settings, and advanced params. A Cancel button allows cooperative cancellation at step boundaries. Artifacts (video, subtitles, script, metadata) are available for download at all terminal states — including after cancellation.

**empty = no override**: Advanced form fields left blank do NOT override Settings (`.env` / `MN_*`) defaults. Only fill a field if you want to explicitly override.

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
| `MN_SUBTITLE_LANG` | Default target language tag; empty = feature off | - |
| `MN_SUBTITLE_MODE` | Default overlay mode (`original` / `translated` / `bilingual`) | `original` |
| `MN_TRANSLATE_PROVIDER` | Translation backend (v0.3: `llm` only) | `llm` |
| `MN_TRANSLATE_RETRIES` | LLM translation retries before soft-degrade | `3` |
| `MN_TTS_PROVIDER` | TTS backend: `edge` (default), `openai`, or `mimo` | `edge` |
| `MN_OPENAI_TTS_MODEL` | OpenAI TTS model (when `MN_TTS_PROVIDER=openai`) | `tts-1` |
| `MN_OPENAI_TTS_API_KEY` | OpenAI TTS API key (falls back to `MN_LLM_API_KEY`) | - |
| `MN_OPENAI_TTS_BASE_URL` | OpenAI TTS base URL (falls back to `MN_LLM_BASE_URL`) | - |
| `MN_MIMO_TTS_MODEL` | MiMo TTS model (`mimo-v2.5-tts`, `mimo-v2.5-tts-voiceclone`, `mimo-v2.5-tts-voicedesign`) | `mimo-v2.5-tts` |
| `MN_MIMO_API_KEY` | MiMo API key (falls back to `MN_LLM_API_KEY`) | - |
| `MN_MIMO_BASE_URL` | MiMo base URL | `https://api.xiaomimimo.com/v1` |
| `MN_MIMO_STYLE_PROMPT` | Style description for `mimo-v2.5-tts` (optional) | - |

---

## Output

```text
output/
└── 飞驰人生/
    ├── narration.mp3       # TTS narration audio
    ├── mixed.mp3            # Narration + BGM mix (when BGM enabled)
    ├── subtitle.srt
    ├── subtitle.<lang>.srt    # (when --subtitle-lang set; e.g. subtitle.en.srt)
    ├── subtitle.bilingual.srt # (when --subtitle-lang set; original + LF + translation per cue)
    ├── script.md
    ├── script.json
    ├── research.json        # (when --research)
    ├── scenes.json          # (when video provided)
    ├── matches.json         # (when video provided)
    ├── metadata.json
    ├── final.mp4
    └── clips/               # (when --no-clips not set)
```

| File | Description |
|------|-------------|
| `narration.mp3` | AI-generated narration audio |
| `mixed.mp3` | Narration + BGM overlay (when BGM enabled; otherwise `narration.mp3` used directly) |
| `subtitle.srt` | Synchronized subtitle file (original narration) |
| `subtitle.<lang>.srt` | Translated subtitle (when `--subtitle-lang` set) |
| `subtitle.bilingual.srt` | Bilingual subtitle (when `--subtitle-lang` set; cue body `f"{src}\n{dst}"`) |
| `script.md` | Human-readable script |
| `script.json` | Machine-readable script segments |
| `research.json` | Movie research data (when `--research`) |
| `scenes.json` | Detected scene boundaries (when video provided) |
| `metadata.json` | Segment timings, pipeline status, config |
| `final.mp4` | Rendered video (16:9 or 9:16) |
| `matches.json` | Scene-to-segment clip matching (when video provided) |
| `clips/` | Per-segment clip .mp4 files (when `--no-clips` not set) |

---

## Pipeline

14-step sequential pipeline (see [Architecture](docs/ARCHITECTURE.md)):

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → translate_subtitles → generate_subtitle →
render_video → export_clips
```

**Soft steps** (research, align, scene detect, scene match, BGM, translate, clip export) gracefully skip or soft-degrade when optional dependencies are missing or upstream data is unavailable. Use `--strict` to abort instead.

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
│   │   ├── runner.py        # 14-step pipeline orchestrator
│   │   ├── resolve.py       # Source video resolution
│   │   ├── assets.py        # Asset validation
│   │   ├── research.py      # LLM movie research
│   │   ├── script.py        # LLM script generation
│   │   ├── script_export.py # Script markdown export
│   │   ├── tts.py           # TTS orchestration (uses tts/ package; caching + concurrency)
│   │   ├── align.py         # WhisperX audio alignment
│   │   ├── scenes.py        # PySceneDetect scene detection
│   │   ├── match.py         # Heuristic clip matching
│   │   ├── bgm.py           # Background music mixing
│   │   ├── translate.py     # Multi-language subtitle translation (LLM)
│   │   ├── subtitle.py      # SRT generation (single / translated / bilingual)
│   │   ├── render.py        # MoviePy video rendering
│   │   ├── export_clips.py  # Per-segment clip export
│   │   └── errors.py        # PipelineStrictError
│   ├── workflow/
│   │   ├── schema.py        # JobConfig / JobSteps / JobParams
│   │   ├── load.py          # YAML loader + validation
│   │   ├── merge.py         # CLI > YAML > Settings merge
│   │   └── errors.py        # JobConfigError
│   ├── tts/                     # TTS abstraction layer (v0.4)
│   │   ├── __init__.py          # re-exports public API
│   │   ├── protocol.py          # TTSProvider ABC
│   │   ├── base.py              # BaseTTSProvider (CI silent fallback), is_ci()
│   │   ├── edge.py              # EdgeTTSProvider
│   │   ├── openai_provider.py   # OpenAITTSProvider (voice whitelist, lazy SDK)
│   │   ├── mimo_provider.py     # MimoTTSProvider (3 models: named voice, voice clone, voice design)
│   │   ├── factory.py           # get_tts_provider(settings)
│   │   └── cache.py             # TTSCacheKey, cache_path_for, PROVIDER_CACHE_VERSIONS
│   ├── utils/
│   │   ├── async_utils.py   # Sync/async bridge
│   │   ├── console.py       # Console Protocol + PlainConsole + build_console
│   │   ├── environment.py   # Environment collection
│   │   ├── errors.py        # ConfigError (cross-cutting config-error class)
│   │   ├── font.py          # CJK font fallback
│   │   ├── json_parser.py   # LLM JSON extraction (with truncation recovery)
│   │   ├── llm.py           # OpenAI client wrapper
│   │   ├── log.py           # AppLogger (file logging layer)
│   │   ├── metadata_export.py # metadata.json builder
│   │   ├── optional_deps.py # Optional dependency probing
│   │   ├── prompts.py       # Prompt templates
│   │   └── retention.py     # Log file retention
│   └── web/                     # Gradio browser UI (v0.3.5; requires [web] extra)
│       ├── __init__.py          # lazy launch_web export
│       ├── __main__.py          # python -m movie_narrator.web
│       ├── app.py               # Gradio Blocks layout + event handlers
│       ├── bridge.py            # form → background thread → yield UI updates
│       ├── form.py              # FormData + validate_form + form_to_context_args
│       ├── console.py           # GradioConsole (thread-safe via threading.Lock)
│       ├── controller.py        # GradioController (cooperative cancel flag)
│       ├── models.py            # RunStatus enum + WebRun per-session state
│       └── utils.py             # upload handling + collect_artifacts + sanitize_filename
├── tests/
│   ├── test_context.py
│   ├── test_settings.py
│   ├── test_errors.py
│   ├── test_align.py
│   ├── test_assets.py
│   ├── test_bgm.py
│   ├── test_cli_config.py
│   ├── test_cli_resolve.py
│   ├── test_match.py
│   ├── test_optional_deps.py
│   ├── test_render_real.py
│   ├── test_research.py
│   ├── test_resolve.py
│   ├── test_runner_strict.py
│   ├── test_runner_workflow_metadata.py
│   ├── test_scenes.py
│   ├── test_script_export.py
│   ├── test_translate.py
│   ├── test_json_parser.py
│   ├── test_pipeline_cancel.py
│   ├── test_web_console.py
│   ├── test_web_controller.py
│   ├── test_web_form.py
│   └── test_workflow_steps.py
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

### v0.3.x — Platform & Workflow ✅

- [x] Declarative workflow config for soft-step toggles + params
- [x] YAML-based job configuration (`mn create --config`)
- [x] Console / structured-step-state logging refactor (`ctx.services.console`, `StepState`)
- [x] Multi-language subtitle support (`--subtitle-lang` / `--subtitle-mode`; LLM translation with retry-then-soft-degrade; `subtitle.<lang>.srt` + `subtitle.bilingual.srt` outputs)
- [x] Web UI (Gradio local browser app via `mn web`; cooperative cancel; requires `[web]` extra)

### v0.4.x — TTS Abstraction & Infrastructure

- [x] TTS provider abstraction (`TTSProvider` protocol, Edge + OpenAI + MiMo backends)
- [x] Provider selection via `MN_TTS_PROVIDER` (`edge` / `openai` / `mimo`)
- [x] OpenAI TTS support (voice whitelist, credential fallback, lazy SDK import)
- [x] MiMo TTS support (3 models: named voice, voice clone, voice design; limited-time free)
- [x] Cache key upgrade (sha256, 7 dimensions, two-level fan-out, per-provider version map)
- [x] CI temp-file isolation (silent audio never enters cache)
- [x] `is_ci()` single source of truth for CI detection
- [x] `ConfigError` cross-cutting error class

### v0.5.x — Ecosystem (Planned)

> **Goal**: Freeze the public API surface (Pipeline, Workflow, Plugin, SDK) before Cloud features depend on it.

- [ ] Plugin API for custom pipeline steps (step registration, lifecycle hooks, dependency declaration)
- [ ] Python SDK for programmatic usage (`from movie_narrator import ...`)
- [ ] Custom pipeline step registration (`@register_step`)
- [ ] Third-party provider extensions (TTS, LLM, research backends via Plugin API)
- [ ] Community extension discovery and packaging conventions

> SDK and Plugin API are designed together — both must stabilize in the same release.

### v0.6.x — Cloud (Planned)

- [ ] Remote inference (offload LLM / TTS / rendering to cloud workers)
- [ ] Distributed rendering (split video segments across nodes)
- [ ] Task queue (async job submission, progress polling, retry)
- [ ] Web service deployment (REST API, authentication, multi-tenant)

---

## Documentation

- [Roadmap](docs/ROADMAP.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Contributing](docs/CONTRIBUTING.md)

---

## License

Licensed under the [AGPL-3.0](LICENSE) License.
