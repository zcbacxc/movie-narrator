[![English](https://img.shields.io/badge/English-Roadmap-blue)](ROADMAP.md)
[![简体中文](https://img.shields.io/badge/简体中文-路线图-green)](ROADMAP.zh-CN.md)

# Roadmap

> Per-release details in [CHANGELOG.md](../CHANGELOG.md). Configuration reference in [`.env.example`](../.env.example) and [`job.example.yaml`](../examples/job.example.yaml).

## v0.1.x — Core Pipeline

- [x] CLI interface (`mn create`, `mn version`)
- [x] LLM script generation with JSON output
- [x] Edge-TTS narration with concurrent generation
- [x] SRT subtitle generation with millisecond precision
- [x] MoviePy video rendering (16:9 / 9:16)
- [x] TTS result caching with content-addressable keys
- [x] Metadata export (JSON)
- [x] CI pipeline (unit tests + smoke test)

## v0.2.x — Scene & Media

- [x] Research agent for movie plot research (`--research`)
- [x] WhisperX audio-text alignment
- [x] Scene detection from movie videos
- [x] Automatic clip matching based on script
- [x] Semantic scene search (embedding-based)
- [x] Background music integration (BGM mixing)
- [x] Script markdown export (`script.md`)
- [x] Scene-level clip output (`clips/`)
- [x] Graceful degradation — soft steps skip silently when optional deps missing

## v0.3.x — Platform & Workflow

- [x] Declarative workflow config for soft-step toggles + params
- [x] YAML-based job configuration (`mn create --config`)
- [x] Console / structured-step-state logging refactor (`ctx.services.console`, `StepState`)
- [x] Multi-language subtitle support (`--subtitle-lang` / `--subtitle-mode`; LLM translation with retry-then-soft-degrade; three-file SRT output)
- [x] Web UI (Gradio; superseded by v0.4.x FastAPI + React refactor; subsequently split into the independent repo [movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web))

## v0.4.x — TTS Abstraction & Infrastructure

> 28 patch releases (v0.4.0 – v0.4.27). Major themes: TTS provider abstraction, config system overhaul, WebUI refactor, core engine production quality, L2 readiness & hand-test passed, match intelligence, effect portfolio, extensibility, contract layer.

### Infrastructure

- [x] TTS provider abstraction (Edge / OpenAI / MiMo via `MN_TTS_PROVIDER`)
- [x] Content-addressable cache (sha256, 7 dimensions, per-provider version map)
- [x] Config system overhaul — strict `.env` / `job.yaml` boundary
- [x] MoviePy 1.x → 2.x upgrade (Python 3.13+ compatibility)
- [x] Preflight LLM/TTS validation before pipeline execution
- [x] Step-level retry mechanism (`--retry` flag, `StepAction` enum)

### Web UI

> The Web UI work below has since been split into the independent repository [movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web). The core repo no longer ships `web_api/` or `webui/`; install the Web UI separately via `pip install movie-narrator-web` and launch it with `mn-web`.

- [x] Gradio → FastAPI + React SPA refactor (Vite + TypeScript + shadcn/ui)
- [x] WebSocket real-time progress (`/ws/jobs/{id}`)
- [x] pip-installable WebUI packaging — now shipped from the separate `movie-narrator-web` repo (`pip install movie-narrator-web`, command `mn-web`)

### Core Engine Quality

- [x] Post-render deliverable QA step (`validate_deliverable`)
- [x] Audio normalize + BGM ducking with attack/release smoothing
- [x] Video cover/contain layout; bottom-safe subtitle layout (CJK wrapping + backdrop bar)
- [x] Render encode quality — CRF 18, preset `slow`, `+faststart`
- [x] Narration preset system (`douyin-fast` / `mainstream-dry` / `bilibili-long`)
- [x] Two-phase script generation with dynamic sentence count
- [x] Draft profile mode for fast iteration (`render_profile: draft`)

### L2 Readiness & Validation

- [x] `match_summary` full schema (21+ fields) in `metadata.json` for L2 jq queries
- [x] Degradation visibility — `_degraded_steps` + CLI summary for all soft-step failures
- [x] faster-whisper backend (Windows CPU compatibility; unlocks embedding re-rank)
- [x] L2 hand-test passed — O1-O10 100% (G1 满江红 + G3 飞驰人生3)
- [x] L2 G2 cross-movie validation — 西虹市首富
- [x] L2+ hand-test toolkit (checklist + `compare_runs.py` + SOP)

### Match Intelligence

- [x] EP1 act-weighted timeline partitioning (4-act dramatic pacing)
- [x] EP3 top-K rerank with order-backtrack reuse penalty
- [x] EP2 beat time anchor (structured beats with `act` + `approx_ratio`)
- [x] Diversity post-processing (sliding-window scene reuse limit)
- [x] Footage coverage gate (warn-only when below threshold)

### Effect Portfolio

- [x] EP4 hook templates & set pieces (genre-appropriate scroll-stoppers + named-scene injection)
- [x] EP5 title card + cover.jpg export + vertical safe area (9:16 subtitle margin tightening)
- [x] EP6 duck curve & RMS-based loudness normalization

### Extensibility & Pipeline

- [x] EP8 VisionCaptioner abstraction (`vision/` package; stub + `http_vlm` OpenAI-compatible provider)
- [x] EP9 pause/resume (`--pause-at` + `mn resume` + `pipeline_state.json`)
- [x] Contract layer (`contract.py` — stable API boundary; now the surface consumed by the external [movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web) package, `CONTRACT_VERSION = (0, 5, 0)`)
- [x] Stage E productization (CLI match summary + RS-07/08/09 render fixes)

## v0.5.x — Ecosystem

> **Goal**: Freeze the public API surface (Pipeline, Workflow, Plugin, SDK) before Cloud features depend on it.

- [ ] Plugin API for custom pipeline steps (step registration, lifecycle hooks, dependency declaration)
- [ ] Python SDK for programmatic usage (`from movie_narrator import ...`)
- [ ] Custom pipeline step registration (`@register_step`)
- [ ] Third-party provider extensions (TTS, LLM, research backends via Plugin API)
- [ ] Community extension discovery and packaging conventions

> **Design note**: SDK and Plugin API are designed together — the SDK is the primary consumer of the Plugin API, so both must stabilize in the same release to avoid compatibility pressure.

## v0.6.x — Cloud

- [ ] Remote inference (offload LLM / TTS / rendering to cloud workers)
- [ ] Distributed rendering (split video segments across nodes)
- [ ] Task queue (async job submission, progress polling, retry)
- [ ] Web service deployment (REST API, authentication, multi-tenant) — note: the Web UI itself is now an independent package ([movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)); this item covers cloud deployment/hosting concerns, not the UI codebase
