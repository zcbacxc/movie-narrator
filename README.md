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
- 🖥️ Web UI (`mn web` — local FastAPI + React browser app with form inputs, cooperative cancel, artifact download, and real-time progress via WebSocket)
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

# WhisperX + semantic search (requires PyTorch; Python < 3.14)
pip install "movie-narrator[ml]"

# Web UI (FastAPI + React)
pip install "movie-narrator[web]"

# Everything
pip install "movie-narrator[full]"
```

> **Note on Python 3.14+**: The `[ml]` extra (WhisperX + sentence-transformers) is currently gated to Python < 3.14 due to upstream dependency wheel availability. On Python 3.14+, `pip install "movie-narrator[full]"` will install all other extras and **silently skip** the ML components. The `align` and `match` pipeline steps will soft-degrade (see [Soft steps](#soft-steps)) instead of failing.

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

```bash
# Basic usage
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60
```

All 18 CLI flags are documented in [`examples/cli-usage.sh`](examples/cli-usage.sh) with usage examples for every scenario: basic, video/library, research/BGM/clips, multi-language subtitles, and YAML config. Key flags: `--movie/-m`, `--style/-s`, `--duration/-d`, `--voice/-v`, `--format/-f`, `--video/-V`, `--library-dir`, `--research`, `--bgm`, `--no-bgm`, `--no-clips`, `--strict`, `--keep-cache`, `--retry`, `--subtitle-lang`, `--subtitle-mode`, `--config`.

### Job YAML config

```bash
# Drive a job from YAML (movie may live only in the file)
mn create --config examples/job.example.yaml

# CLI flags still win over YAML
mn create --config examples/job.example.yaml --movie "OtherTitle" --no-clips
```

When `--config` is not passed, the CLI auto-discovers a YAML config in priority order:
1. `cwd/job.yaml` (project-level user config)
2. Packaged `examples/job.example.yaml` (sensible defaults for new users)
3. None (pure CLI args)

This means new users can run `mn create --movie X` without creating any config file — the example YAML provides default steps/params automatically.

See [`examples/job.example.yaml`](examples/job.example.yaml) for the full whitelist: soft-step toggles under `steps:` (`research`, `align`, `scene`, `match`, `bgm`, `export`, `translate`), all 32 `params:` keys (scene detection, match, BGM, TTS pacing, translate, research, WhisperX, render, async, video sizes), and the multi-language subtitle top-level keys `subtitle_lang` / `subtitle_mode`. Relative `video` / `bgm` / `library_dir` paths resolve against the YAML file's directory. LLM credentials stay in `.env` / `MN_*` only.

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

### Web UI

```bash
# Install with web extra
pip install "movie-narrator[web]"

# Launch local browser app (default: http://127.0.0.1:8760)
mn web

# Or with custom host/port
mn web --host 0.0.0.0 --port 8080

# Production: build frontend, then mn web serves it
cd webui && npm install && npm run build
mn web  # serves web_api/static/ + API on http://127.0.0.1:8760

# Development: two terminals
mn web --reload                    # FastAPI on :8760
cd webui && npm run dev            # Vite dev server on :5173 (proxies API)
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
| 2 | `当前目录/.env` | Project-level |
| 3 | `~/.movie-narrator/.env` | User-level, never lost on pip install/upgrade/uninstall |
| 4 | Built-in defaults | Local Ollama |

### Full reference

See [`.env.example`](.env.example) for the complete list of all 21 environment variables (LLM + TTS infrastructure only). All pipeline behavior is configured via [`examples/job.example.yaml`](examples/job.example.yaml) — 32 params keys covering scene detection, match, render, translate, BGM, WhisperX, async, and video sizes.

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

15-step sequential pipeline (see [Architecture](docs/ARCHITECTURE.md)):

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → translate_subtitles → generate_subtitle →
render_video → validate_deliverable → export_clips
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
│   │   ├── runner.py        # 15-step pipeline orchestrator
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
│   │   ├── render.py        # MoviePy 2.x video rendering
│   │   ├── qa.py            # Post-render deliverable QA (hard step)
│   │   ├── export_clips.py  # Per-segment clip export (direct ffmpeg)
│   │   ├── preflight.py     # Pre-run LLM/TTS validation (fail-fast)
│   │   └── errors.py        # PipelineStrictError, PipelineCancelled, RunController, StepAction
│   ├── workflow/
│   │   ├── schema.py        # JobConfig / JobSteps / JobParams
│   │   ├── load.py          # YAML loader + validation
│   │   ├── merge.py         # CLI > YAML > Settings merge
│   │   └── errors.py        # JobConfigError
│   ├── tts/                     # TTS abstraction layer
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
│   │   ├── retention.py     # Log file retention
│   │   ├── audio_mix.py     # Audio normalize + BGM ducking (pydub)
│   │   ├── deliverable_qa.py # ffprobe/ffmpeg media probing + QA rules
│   │   └── video_layout.py  # Cover/contain crop+resize geometry
│   └── web_api/                 # FastAPI + WebSocket backend (default Web UI; requires [web] extra)
│       ├── __init__.py          # lazy launch_web_api export
│       ├── __main__.py          # python -m movie_narrator.web_api
│       ├── server.py            # FastAPI app factory (CORS, static mount, ws route)
│       ├── routes.py            # REST API endpoints (create / status / cancel / artifacts)
│       ├── ws.py                # WebSocket endpoint (real-time progress + logs)
│       ├── tasks.py             # TaskManager (background task lifecycle)
│       ├── form.py              # FormData + validate_form + form_to_context_args
│       ├── console.py           # WebSocketConsole (thread-safe broadcast)
│       ├── controller.py        # RunController (cooperative cancel flag)
│       ├── models.py            # RunStatus enum + WebRun per-session state
│       └── utils.py             # upload handling + collect_artifacts + sanitize_filename
├── webui/                       # React 18 + Vite + TypeScript frontend (default Web UI)
│   ├── package.json             # React 18 + Vite + TypeScript + Tailwind
│   ├── vite.config.ts           # dev proxy → :8760, build → dist/
│   ├── index.html               # Vite entry
│   ├── src/                     # React app (App.tsx, components/, hooks/, lib/, types/, styles/)
│   └── dist/                    # production build output (served by mn web)
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
│   ├── test_workflow_steps.py
│   ├── test_audio_mix.py
│   ├── test_deliverable_qa.py
│   ├── test_qa.py
│   ├── test_text_image.py
│   └── test_video_layout.py
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
- [x] Web UI (Gradio local browser app via `mn web`; cooperative cancel; requires `[web]` extra) (v0.4.10: refactored to FastAPI + React)

### v0.4.x — TTS Abstraction & Infrastructure ✅

- [x] Web UI rewrite: Gradio → FastAPI + React 18 + WebSocket (v0.4.10)
- [x] TTS provider abstraction (`TTSProvider` protocol, Edge + OpenAI + MiMo backends)
- [x] Provider selection via `MN_TTS_PROVIDER` (`edge` / `openai` / `mimo`)
- [x] OpenAI TTS support (voice whitelist, credential fallback, lazy SDK import)
- [x] MiMo TTS support (3 models: named voice, voice clone, voice design; limited-time free)
- [x] Cache key upgrade (sha256, 7 dimensions, two-level fan-out, per-provider version map)
- [x] CI temp-file isolation (silent audio never enters cache)
- [x] `is_ci()` single source of truth for CI detection
- [x] `ConfigError` cross-cutting error class
- [x] MoviePy 1.x → 2.x upgrade (Python 3.13+ compatibility)
- [x] Preflight LLM/TTS validation before pipeline execution
- [x] Step-level retry mechanism (`--retry` flag, `StepAction` enum)
- [x] Auto-create `~/.movie-narrator/.env` on first run
- [x] `export_clips` direct ffmpeg subprocess (design choice, not workaround)
- [x] Config system overhaul: strict env/yaml boundary — `.env` (Settings) contains 21 LLM + TTS infrastructure fields only; `job.yaml` (params) contains all 32 pipeline behavior keys; YAML auto-discovery (`--config` not passed → `cwd/job.yaml` → packaged example); `.env.example` and `job.example.yaml` are the single sources of truth; no code constants module — inline literals match example files

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
- [LLM Provider Guides](docs/LLM_PROVIDERS.md)
- [Contributing](docs/CONTRIBUTING.md)

---

## License

Licensed under the [AGPL-3.0](LICENSE) License.
