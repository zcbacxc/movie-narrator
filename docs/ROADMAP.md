[![English](https://img.shields.io/badge/English-Roadmap-blue)](ROADMAP.md)
[![简体中文](https://img.shields.io/badge/简体中文-路线图-green)](ROADMAP.zh-CN.md)

# Roadmap

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

### New CLI flags (v0.2)

- `--video` — Source movie file path
- `--library-dir` — Movie library directory
- `--research` / `--no-research` — Toggle plot research
- `--bgm` — Background music file
- `--no-bgm` — Disable BGM
- `--no-clips` — Skip clip export
- `--strict` — Abort on soft step failure

### Extras install

```bash
pip install "movie-narrator[media]"  # scenedetect
pip install "movie-narrator[ml]"     # whisperx + sentence-transformers
pip install "movie-narrator[full]"   # everything
```

### Graceful degradation

Soft pipeline steps (research, align, scene detect, scene match, BGM, clip export) skip silently when optional dependencies are missing. Pipeline continues end-to-end. Use `--strict` to fail instead.

## v0.3.x — Platform & Workflow

- [x] Declarative workflow config for soft-step toggles + params
- [x] YAML-based job configuration (`mn create --config`)
- [x] Console / structured-step-state logging refactor (`ctx.services.console`, `StepState`)
- [x] Multi-language subtitle support (`--subtitle-lang` / `--subtitle-mode`; LLM translation with retry-then-soft-degrade; three-file SRT output)
- [x] Web UI (Gradio local browser app via `mn web`; requires `[web]` extra) — *superseded by the FastAPI + React refactor in v0.4.x, see below*

### v0.3 New CLI flags

- `--subtitle-lang` — Target language tag (`en`, `ja`, `zh-TW`, ...); empty = feature off
- `--subtitle-mode` — Overlay mode: `original` / `translated` / `bilingual` (default `original`)

## v0.4.x — TTS Abstraction & Infrastructure

> 28 patch releases (v0.4.0 – v0.4.27). Major themes: TTS provider abstraction, config system overhaul, WebUI refactor (Gradio → FastAPI + React), core engine production quality, L2 readiness & hand-test passed, match intelligence (EP1/EP2/EP3), effect portfolio (EP4/EP5/EP6), extensibility (EP8/EP9), and contract layer. Per-release details in [CHANGELOG.md](../CHANGELOG.md).

### Infrastructure

- [x] TTS provider abstraction (`TTSProvider` protocol; Edge / OpenAI / MiMo providers)
- [x] Provider selection via `MN_TTS_PROVIDER` (`edge` / `openai` / `mimo`)
- [x] Cache key upgrade (sha256, 7 dimensions, two-level fan-out, per-provider version map)
- [x] Config system overhaul — strict `.env` / `job.yaml` boundary (24 infra fields / 48+ pipeline params)
- [x] MoviePy 1.x → 2.x upgrade (Python 3.13+ compatibility)
- [x] Preflight LLM/TTS validation before pipeline execution
- [x] Step-level retry mechanism (`--retry` flag, `StepAction` enum)
- [x] Auto-create `~/.movie-narrator/.env` on first run

### Web UI

- [x] Gradio → FastAPI + React SPA refactor (`web_api/` package, Vite + TypeScript + shadcn/ui)
- [x] WebSocket real-time progress (`/ws/jobs/{id}` streams `Console.snapshot()`)
- [x] pip-installable WebUI packaging (SPA bundled in wheel)
- [x] Legacy Gradio `web/` package removed

### Core Engine Quality

- [x] Post-render deliverable QA step (`validate_deliverable` — ffprobe + ffmpeg fallback)
- [x] Audio normalize + BGM ducking with attack/release smoothing
- [x] Video cover/contain layout; bottom-safe subtitle layout (CJK wrapping + backdrop bar)
- [x] Render encode quality — CRF 18, preset `slow`, `+faststart`
- [x] Narration preset system (3 built-in: `douyin-fast`, `mainstream-dry`, `bilibili-long`)
- [x] Two-phase script generation (beats → expansion) with dynamic sentence count
- [x] Performance contract closure (TTS cache atomic write, style_prompt in cache key, numpy duck rewrite)

### L2 Readiness & Validation

- [x] `match_summary` full schema (21+ fields) in `metadata.json` for L2 jq queries
- [x] Degradation visibility — `_degraded_steps` + CLI summary for all soft-step failures
- [x] faster-whisper backend (Windows CPU compatibility; unlocks embedding re-rank without WhisperX)
- [x] L2 hand-test passed — O1-O10 100% (G1 满江红 + G3 飞驰人生3, `embedding_ratio=1.00`, `degraded_steps=[]`)
- [x] L2 G2 cross-movie validation — 西虹市首富 (`embedding_topk=18/18`, `qa_report.ok=true`, `degraded_reason=null`)
- [x] L2+ hand-test toolkit (checklist + `compare_runs.py` + SOP)

### Match Intelligence

- [x] EP1 act-weighted timeline partitioning (4-act dramatic pacing, `match_timeline_mode="weighted_acts"`)
- [x] EP3 top-K rerank with order-backtrack reuse penalty (`match_topk` + `match_topk_reuse_penalty`)
- [x] EP2 beat time anchor (structured beats with `act` + `approx_ratio`, time-anchored heuristic)
- [x] Diversity post-processing (sliding-window scene reuse limit)
- [x] Footage coverage gate (warn-only when below `render_min_footage_coverage`)

### Effect Portfolio

- [x] EP4 hook templates & set pieces (genre-appropriate scroll-stoppers + named-scene injection)
- [x] EP5 title card overlay + cover.jpg export + vertical safe area (9:16 subtitle margin tightening)
- [x] EP6 duck curve & RMS-based loudness normalization

### Extensibility & Pipeline

- [x] EP8 VisionCaptioner abstraction (`vision/` package; stub + `http_vlm` OpenAI-compatible provider)
- [x] EP9 pause/resume (`--pause-at` + `mn resume` + `pipeline_state.json` serialization)
- [x] Contract layer (`contract.py` — stable API boundary between web_api and core engine)
- [x] Stage E productization (CLI match summary + RS-07/08/09 render fixes)

### Environment Variables

- `MN_TTS_PROVIDER` — `edge` (default), `openai`, or `mimo`
- `MN_DEFAULT_VOICE` — Default voice identifier for the selected TTS provider; each provider interprets this string (Edge: `zh-CN-YunxiNeural`, OpenAI: `alloy`, MiMo: voice name / file path / description depending on model)
- `MN_OPENAI_TTS_MODEL` — OpenAI TTS model (default `tts-1`)
- `MN_OPENAI_TTS_API_KEY` — OpenAI TTS API key (falls back to `MN_LLM_API_KEY`)
- `MN_OPENAI_TTS_BASE_URL` — OpenAI TTS base URL (falls back to `MN_LLM_BASE_URL`)
- `MN_MIMO_TTS_MODEL` — MiMo TTS model (default `mimo-v2.5-tts`; also `mimo-v2.5-tts-voiceclone`, `mimo-v2.5-tts-voicedesign`)
- `MN_MIMO_API_KEY` — MiMo API key (falls back to `MN_LLM_API_KEY`)
- `MN_MIMO_BASE_URL` — MiMo base URL (default `https://api.xiaomimimo.com/v1`)
- `MN_MIMO_STYLE_PROMPT` — Style description for `mimo-v2.5-tts` user message (default empty)

### Config boundary (`.env` / `job.yaml`)

Strict separation: `.env` contains ONLY LLM + TTS infrastructure (24 fields); `job.yaml` contains ALL pipeline behavior (48+ params). See [`.env.example`](../.env.example) and [`job.example.yaml`](../examples/job.example.yaml) as single sources of truth.

**`.env` (Settings) — 24 fields:**
- LLM (14): `MN_LLM_BASE_URL`, `MN_LLM_API_KEY`, `MN_LLM_MODEL`, `MN_LLM_TIMEOUT`, `MN_SCRIPT_TEMPERATURE`, `MN_SCRIPT_EXPAND_TEMPERATURE`, `MN_SCRIPT_MAX_TOKENS`, `MN_SCRIPT_RETRIES`, `MN_SCRIPT_RETRY_DELAY`, `MN_RESEARCH_TEMPERATURE`, `MN_RESEARCH_MAX_TOKENS`, `MN_RESEARCH_RETRIES`, `MN_RESEARCH_RETRY_DELAY`, `MN_TRANSLATE_MAX_TOKENS`
- TTS (10): `MN_DEFAULT_VOICE`, `MN_TTS_PROVIDER`, `MN_TTS_CACHE_MAX_MB`, `MN_OPENAI_TTS_*`(3), `MN_MIMO_*`(4)

**`job.yaml` (params) — 48+ keys:** See [`job.example.yaml`](../examples/job.example.yaml) for the full list (Scene, Match, BGM, TTS pacing, Translate, Research, WhisperX, Render, QA, Async, Video sizes, Prompt shaping, Effect portfolio, Vision).

### Provider env-var naming convention

Future TTS providers (Azure, ElevenLabs, FishAudio, CosyVoice, ...) follow a uniform pattern:

```
MN_<PROVIDER>_TTS_MODEL   — model name
MN_<PROVIDER>_API_KEY     — API key (falls back to MN_LLM_API_KEY)
MN_<PROVIDER>_BASE_URL    — base URL (provider-specific default)
```

Provider-specific extras (e.g. `MN_MIMO_STYLE_PROMPT`) are appended as needed.

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
- [ ] Web service deployment (REST API, authentication, multi-tenant)
