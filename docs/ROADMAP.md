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
- [x] WebSocket real-time progress (`/ws/task/{task_id}`)
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

- [x] EP8 VisionCaptioner abstraction (`vision/` package; stub provider, extensible via Plugin API)
- [x] EP9 pause/resume (`--pause-at` + `mn resume` + `pipeline_state.json`)
- [x] Contract layer (`contract.py` — stable API boundary; now the surface consumed by the external [movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web) package, `CONTRACT_VERSION = (0, 5, 1)`)
- [x] Stage E productization (CLI match summary + RS-07/08/09 render fixes)

## v0.5.x — Ecosystem

> **Goal**: Freeze the public API surface (Pipeline, Workflow, Plugin, SDK) before Cloud features depend on it.

### M1 — Plugin registry infrastructure (#91)

- [x] Plugin API for custom pipeline steps (step registration, lifecycle hooks, dependency declaration)
- [x] StepRegistry + ProviderRegistry with `@register_step` / `@register_tts` / `@register_vision` / `@register_llm` / `@register_research` decorators
- [x] UnifiedParamSchema — `PARAM_WHITELIST` auto-derived from `JobParams` model fields
- [x] SDK surface exports (`list_presets`, `get_preset`) in `contract.py` and `__init__.py`

### M2 — SDK freeze (#92)

- [x] Python SDK for programmatic usage (`from movie_narrator import ...`)
- [x] Custom pipeline step registration (`@register_step`)
- [x] Plugin discovery via `importlib.metadata` entry points (`movie_narrator.plugins` group)
- [x] Third-party provider extensions (TTS, LLM, research backends via Plugin API)
- [x] `Services.logger` optional field for structured logging in plugins
- [x] Out-of-tree example plugin (`examples/plugins/watermark/`)

### WP6 — Scene filtering (#93)

- [x] Intro skip — auto-detect and skip intro/logo sequences via luminance + motion analysis
- [x] Dark frame detection — filter near-black frames that waste narration budget
- [x] Highlight window — configurable time-window-based scene prioritization

### WebUI split — Dual repository separation (#94, #95)

- [x] WebUI (FastAPI + React) extracted into standalone repo [movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)
- [x] Core engine is now a pure CLI package with no web dependencies
- [x] Contract versioning (`CONTRACT_VERSION = (0, 5, 0)`) for import-time compatibility checks

### M4 — Provider migration

- [x] LLM registry (`llm_registry`) — `utils/llm.py` migrated to registry pattern with built-in `openai` provider
- [x] Research registry (`research_registry`) — `pipeline/research.py` migrated to registry pattern with built-in `llm` provider
- [x] TTS/Vision factory legacy fallback cleanup — removed dead code, registry-only dispatch
- [x] Protocol validation — `tts_registry` and `vision_registry` enforce ABC conformance at `create()` time
- [x] `PluginContext` extended with `llm` and `research` fields
- [x] `CONTRACT_VERSION` bumped to `(0, 5, 1)` — backward compatible (new exports only)
- [x] SDK exports: `register_llm`, `register_research`, `llm_registry`, `research_registry` added to `contract.py` and `__init__.py`

### M5 — Community & packaging

- [x] CLI plugin commands (`mn plugin list|discover|registries|version`)
- [x] Plugin template (`examples/plugins/template/`) with README quick-start guide
- [x] `check_version()` helper for import-time version validation by external consumers
- [x] `ProviderRegistry.info()` method for structured provider metadata
- [x] Packaging guide (`docs/PACKAGING.md`) — versioning, entry points, publishing workflow

> **Design note**: SDK and Plugin API are designed together — the SDK is the primary consumer of the Plugin API, so both must stabilize in the same release to avoid compatibility pressure.

### v0.5.3 — Hardening

- [x] SDK API reference (`mkdocs.yml` + `docs/sdk/` — auto-generated from docstrings via mkdocstrings)
- [x] Performance benchmark script (`benchmarks/profile_pipeline.py` — per-step profiling in CI mode)
- [x] Quickstart guide (`docs/QUICKSTART.md` — end-to-end plugin tutorial)
- [x] Research provider example (`examples/plugins/research-wiki/` — Wikipedia API-based research provider)
- [x] `ResearchInfo` added to SDK exports (`contract.py` + `__init__.py`)
- [x] `PLUGIN_DEVELOPMENT.md` updated with LLM and Research provider sections
- [x] `docs` optional dependency group (mkdocs + mkdocstrings)

### v0.5.4 — Quality Uplift

- [x] VLM caption provider (`vision/vlm.py` — cloud VLM API for real visual scene descriptions, Q-M5)
- [x] Multi-candidate horse race (`race.py` + `mn race` CLI — run N variations, score, rank, Q-P2)
- [x] Reference video imitation (`imitate.py` + `mn imitate` CLI — extract style from viral narration, Q-P7)
- [x] Layer 0 runbook (`examples/l2/RUNBOOK.md` — zero-code quality improvement guide, Q-X1~X6)

### v0.5.5 — Logging Improvements

- [x] Configurable log levels (`--log-level DEBUG|INFO|WARNING|ERROR` on `mn create`/`resume`/`imitate`)
- [x] Verbose console mode (`--verbose` flag for real-time debug output)
- [x] RotatingFileHandler (10MB, 5 backups) preventing unbounded log growth
- [x] JSON format logging option for structured log aggregation (ELK/Loki)
- [x] Run ID correlation (8-char ID in log prefix + metadata.json)
- [x] Sub-step timing (`step_timing` for LLM/TTS/ffmpeg call profiling)
- [x] Services.logger integration (AppLogger auto-injected for plugin use)
- [x] Docs/examples alignment with v0.5.4 project state

### v0.5.6 — Narrative Quality & External Data

- [x] Narrative five-principles + anti-AI-tone in prompt templates (NA-M1-S1)
- [x] Platform tone adaptation — `target_platform` for douyin/bilibili/youtube (NA-M1-S2)
- [x] Rhythm zone & emotion marking on plot beats (NA-M1-S3)
- [x] Rhythm-zone influence on match scoring (NA-M1-S3+)
- [x] Narrator perspective & character anchor — `narrator_perspective` / `focus_character` (NA-M1-S4)
- [x] CLI flags for perspective — `--narrator-perspective` / `--focus-character` (NA-M1-S4+)
- [x] Two-phase script self-check judge with retry (NA-M1-S5)
- [x] Judge feedback loop — inject issues into retry prompt (NA-M1-S5+)
- [x] Structured movie card for reduced hallucination (NA-M2-S1)
- [x] TMDB external data source for fact verification (NA-M2-S1+)
- [x] BGM emotion-based selection using beat metadata (NA-M4-S1)
- [x] Emotion-weighted BGM selection with energy alignment (NA-M4-S1+)
- [x] Render template system with per-preset styling (NA-M6-S1)
- [x] Language chain consistency — single `lang` source of truth (R2-NA-LANG)
- [x] Retryable error codes for network-type failures (R2-NA-ORCH)
- [x] TMDB provider import-time registration fix (`pipeline/research.py`)
- [x] YAML whitelist comments sync — 12 missing params documented (`examples/job.example.yaml`)
- [x] L2 YAML comment fixes — WP1 short keys, prompt_target_segment_duration (`examples/l2/job.l2.douyin.yaml`)
- [x] README / cli-usage.sh / ROADMAP alignment with current codebase

## v0.6.x — Cloud

- [ ] Remote inference (offload LLM / TTS / rendering to cloud workers)
- [ ] Distributed rendering (split video segments across nodes)
- [ ] Task queue (async job submission, progress polling, retry)
- [ ] Web service deployment (REST API, authentication, multi-tenant) — note: the Web UI itself is now an independent package ([movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)); this item covers cloud deployment/hosting concerns, not the UI codebase
