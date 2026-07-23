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
- [x] Web UI (Gradio local browser app via `mn web`; requires `[web]` extra) — *superseded by the FastAPI + React refactor in v0.4.10, see below*

### v0.3 New CLI flags

- `--subtitle-lang` — Target language tag (`en`, `ja`, `zh-TW`, ...); empty = feature off
- `--subtitle-mode` — Overlay mode: `original` / `translated` / `bilingual` (default `original`)

### v0.3.5 Web UI (Gradio — legacy)

- `mn web` — Launch local Gradio browser app (requires `pip install "movie-narrator[web]"`)
- Cooperative cancel at step boundaries (Cancel button in UI)
- Form fields mirror CLI options; advanced params follow "empty = no override" rule (Settings defaults apply)
- Uploads go to `mn_web_*` temp dirs, never pollute `output/`

> **Note**: This Gradio-based Web UI is **legacy** and was superseded by the FastAPI + React refactor shipped in **v0.4.10** (see *v0.4.10 WebUI Refactor* below). The `web/` package was removed from the tree in **v0.4.12**.

## v0.4.x — TTS Abstraction & Infrastructure

- [x] TTS provider abstraction (`TTSProvider` protocol, `BaseTTSProvider`, `EdgeTTSProvider`, `OpenAITTSProvider`, `MimoTTSProvider`)
- [x] Provider selection via `MN_TTS_PROVIDER` (`edge` / `openai` / `mimo`)
- [x] OpenAI TTS support (sync SDK via `asyncio.to_thread`; voice whitelist; credential fallback to `MN_LLM_API_KEY`)
- [x] MiMo TTS support (3 models: named voice, voice clone, voice design; wav→mp3 conversion; style prompt)
- [x] Cache key upgrade (sha256, 7 dimensions, two-level fan-out, per-provider version map)
- [x] CI temp-file isolation (silent audio never enters cache)
- [x] `is_ci()` single source of truth for CI detection
- [x] `ConfigError` cross-cutting error class
- [x] MoviePy 1.x → 2.x upgrade (Python 3.13+ compatibility)
- [x] Preflight LLM/TTS validation before pipeline execution
- [x] Step-level retry mechanism (`--retry` flag, `StepAction` enum)
- [x] Auto-create `~/.movie-narrator/.env` on first run
- [x] `export_clips` direct ffmpeg subprocess (design choice)

### v0.4.7 Config system overhaul

- [x] Strict env/yaml boundary: `.env` = 24 LLM + TTS infrastructure fields only; `job.yaml` = 52 pipeline behavior params
- [x] YAML auto-discovery: `--config` not passed → `cwd/job.yaml` → packaged example → none
- [x] `.env.example` and `job.example.yaml` as single sources of truth (no code constants module)
- [x] All 48 YAML params properly connected through `runner.py` → `ctx.metadata` → pipeline steps
- [x] Fixed `translate_chunk_chars/size` silently ignored (never copied to `ctx.metadata`)
- [x] Fixed `export_clips` codecs hardcoded (now uses `ctx.metadata` → inline literal)
- [x] Fixed `scene_frame_skip` missing from runner copy loop

### v0.4.10 WebUI Refactor

The Web UI is rebuilt from a Gradio single-file app into a decoupled **FastAPI + React SPA** stack. The legacy `web/` (Gradio) package was removed from the tree in v0.4.12.

- [x] **FastAPI backend** — new `src/movie_narrator/web_api/` package (11 modules: `server.py`, `routes.py`, `ws.py`, `tasks.py`, `console.py`, `controller.py`, `form.py`, `models.py`, `utils.py`, `__init__.py`)
- [x] **React 18 SPA** — new `webui/` project (Vite + TypeScript); FastAPI serves the built bundle as static assets, so `mn web` is a single process
- [x] **WebSocket real-time progress** — `/ws/jobs/{id}` streams `Console.snapshot()` + status deltas; replaces the old 200ms Gradio polling generator
- [x] **`[web]` extra changed** — dropped `gradio`; now `fastapi` + `uvicorn` + `python-multipart`
- [x] **`mn web` port** — moved from `7860` (Gradio default) → `8760`
- [x] **Frontend stack** — Vite + TypeScript + shadcn/ui + Tailwind CSS

### v0.4.11 WebUI packaging (pip-installable)

> `v0.4.10` shipped the rewrite in git but the SPA was missing from the PyPI wheel. **0.4.11** closes that gap.

- [x] Vite `outDir` → `src/movie_narrator/web_api/static/`; package-data `static/**`
- [x] `server.py` serves package-relative `static/` (works after `pip install`)
- [x] Track `webui/package.json` + `package-lock.json` + `tsconfig.json` (root `*.json` gitignore exceptions)
- [x] CI `webui` job + Publish `npm ci && npm run build` before `python -m build`
- [x] Publish asserts wheel contains `static/index.html` + hashed JS/CSS

> See `docs/ARCHITECTURE.md` → *Web UI Layer* for the request/WebSocket flow and the `web_api/` module table.

### v0.4.12 Remove Legacy Gradio

- [x] Deleted `src/movie_narrator/web/` (9 files)
- [x] Removed `gradio` from `[full]` extra
- [x] Migrated tests: `test_web_form.py` → `web_api.form`, `test_pipeline_cancel.py` → `TaskController`
- [x] Deleted `test_web_console.py`, `test_web_controller.py` (covered by `test_web_api.py`)

### v0.4.13 Core Engine Production Quality

- [x] Post-render deliverable QA step (`validate_deliverable`) — hard step after `render_video`; ffprobe + ffmpeg fallback; CI skips by default, local runs enable
- [x] Audio normalize + BGM ducking (`utils/audio_mix.py`) — windowed envelope with attack/release smoothing; skip path normalizes narration
- [x] Video cover/contain layout (`utils/video_layout.py`) — source footage fitted to canvas; render defaults to `cover`
- [x] Bottom-safe subtitle layout — `text_image.create_text_image` with CJK wrapping + ellipsis; render always draws overlays (bottom by default)
- [x] Deliverable QA probes (`utils/deliverable_qa.py`) — structured `QAReport` / `QAIssue`
- [x] Match defaults tightened — clamp 0.85–1.25, merge 2.0s, drop tiny scenes <0.4s
- [x] Render encode quality — CRF 18, preset `slow`, `+faststart`
- [x] 15 new `JobParams` fields plumbed (render fit/encode/subtitle, BGM duck/normalize, QA, match drop) — actual count: render_fit_mode, render_crf, render_preset, render_faststart, render_subtitle_position, render_subtitle_max_width_ratio, render_subtitle_bottom_margin_ratio, qa_enabled, qa_max_silence_db, qa_min_duration_ratio, qa_max_duration_ratio, match_drop_scene_min_duration, bgm_duck_db, bgm_normalize, audio_target_dbfs
- [x] Pipeline 14 → 15 steps; frontend `PIPELINE_STEPS` / `STEP_LABELS` synced

### v0.4.14 Publishable Bottom Subtitle

- [x] Semi-transparent black backdrop bar (65% alpha, 16px/12px padding) behind bottom subtitle text — matches short-video recap style
- [x] Thicker stroke (2px → 4px) for legibility on bright footage
- [x] Bug fix: empty wrapped list early return; per-line height re-measurement via `textbbox`
- [x] Cross-platform test threshold (60% → 50%) for Linux CI font metrics

### v0.4.15 Narration Preset System (Stage 0.5)

- [x] Pluggable `Preset` Protocol with closed-vocabulary validation (`ALLOWED_PARAM_KEYS` + `ALLOWED_PROMPT_TAGS`)
- [x] Three built-in presets: `douyin-fast` (default), `mainstream-dry`, `bilibili-long`
- [x] CLI `--narration-preset` / `-p` flag + `mn preset` list/show command
- [x] YAML `narration_preset` top-level field + Web API `FormData.narration_preset`
- [x] Prompt shaping via closed-vocabulary tags (cadence / register / connectors)
- [x] Single-source `PARAM_WHITELIST` frozenset (runner.py) — eliminates dual-maintained whitelist
- [x] Hand-test verified: prompt tags produce perceptible style differences
- [x] Known limitation fixed in v0.4.16: two-phase generation enforces `prompt_target_sentences`
- [ ] Stage 2 (future): SPI discover via `entry_points` + opt-in local folder scan

### v0.4.16 Two-Phase Script Generation + CLI/Config Improvements

- [x] Two-phase script generation: Phase 1 (plot beats, low temp) → Phase 2 (expansion, moderate temp) → fallback trim
- [x] `prompt_target_sentences` now enforceable (was ignored by LLM in v0.4.15)
- [x] First-run config notice: `ensure_user_config()` prints one-time message to stderr
- [x] CLI help: `no_args_is_help=True`, `rich_markup_mode="rich"`, bilingual (中文/English) help
- [x] `-h` conflict resolved (web command `--host` no longer binds `-h`)
- [x] Phase 1 None/empty beat filtering + Phase 2 empty text filtering
- [x] TTS per-segment retry (3 attempts) — single failure no longer kills batch
- [x] Research step retry (was zero retry, now matches script.py pattern)
- [x] Degradation warnings: `SOFT_STEP_CONSEQUENCES` + pipeline-end summary
- [x] Retry failure debug logging

### v0.4.17 Dynamic Sentence Count + L2 E2E Tests

- [x] Dynamic sentence count by duration (方案 B): `n = round(duration / prompt_target_segment_duration)`
- [x] New preset field `prompt_target_segment_duration` (douyin=3.3s, mainstream=5.0s, bilibili=7.5s)
- [x] max_chars corrected based on R5b real TTS data (3.8 chars/sec): mainstream 18→22, bilibili 22→32
- [x] `script_target_count` metadata for debugging (distinguishes requested vs actual count)
- [x] L2 automated E2E smoke tests: CI-runnable pipeline contract verification
- [x] CI smoke assertions for preset sentence count (R4 regression guard)

### v0.4.20 Stage D Quality Consolidation

> 8 PRs landed in this release (#69–#76). Focus: visual quality guardrails (WP3 diversity, WP5 truncation), system stability (WP4 footage coverage, AQ-04 audio normalization), audit visibility (swaps_log, script_truncated), param whitelist sync, and test isolation fixes.

- [x] **WP3 diversity post-processing** — `_apply_diversity()` with sliding-window scene reuse limit (`match_diversity_window` + `match_max_scene_reuse`) (#70)
- [x] **WP5 max_chars hard truncation** — `_truncate_to_max_chars()` enforces `prompt_max_chars_per_sentence` after LLM expansion, preferring punctuation breaks (#70)
- [x] **WP4 footage coverage gate** — `metadata.footage_coverage.ratio` + warn-only `_degraded_steps` flag when coverage below `render_min_footage_coverage` (#69)
- [x] **AQ-04 `ensure_final_audio()`** — unified BGM normalization across all 4 exit points, idempotent guard (#69)
- [x] **WP3 swaps_log audit** — `match_summary.diversity.swaps_log` records each swap `{segment_index, old_scene, new_scene}` (#71)
- [x] **WP5 truncation audit** — `metadata.script_truncated` records `{count, max_chars, details}` (#71)
- [x] **Param whitelist sync** — 12 params realigned across `schema.py` / `merge.py` / `load.py` / `runner.py` (#72)
- [x] **Test isolation fixes** — `.env` pollution (`_env_file=None`), faster_whisper probe patching (dual `_align_backend.probe`), lru_cache settings clear (#73, #74, #76)
- [x] **Audit integration tests** — 4 CI tests replacing 50-min handtest, verify swaps_log trigger + script_truncated schema (#75)
- [x] **L2+ hand-test passed** — PR #72 whitelist + PR #71 audit schema verified end-to-end (2026-07-23)

### v0.4.19 faster-whisper Backend + L2 Hand-Test Passed

> 6 PRs landed in this release. Focus: resolve WhisperX CPU compatibility blocker with environment-adaptive faster-whisper backend, unlock O10 (embedding_ratio > 0), close L2 hand-test.

- [x] **faster-whisper backend** with `select_align_backend()` — environment-adaptive: GPU/Linux CPU → whisperx (word-level), Windows CPU → faster_whisper (no k2-fsa dependency)
- [x] **`align_backend` param** in `job.yaml` for explicit override
- [x] **match.py faster-whisper fallback** — second WhisperX call site (`_transcribe_video_audio`) also gets faster-whisper, unlocking O10
- [x] **`_remap_segments()` + `_detect_drift()` extraction** — eliminates ~45 lines of duplicated code between WhisperX and faster-whisper paths
- [x] **`status.align` semantics documented** — `success` for faster-whisper (not `failed`), `align_fallback` flag carries segment-level signal
- [x] **CI trigger fix** — feature/hotfix pushes no longer trigger CI (only main + PR), eliminating duplicate runs
- [x] **`collect_artifacts` clips** — per-segment `.mp4` clips now in artifact list
- [x] **3 stale remote branches deleted** — `feature/core-engine-production-quality`, `feature/docs-sync-with-code`, `release/v0.4.11`
- [x] **L2 hand-test passed** — O1-O10 100% achieved (G1 满江红 + G3 飞驰人生3, `embedding_ratio=1.00`, `degraded_steps=[]`)

### v0.4.18 Core Engine Hardening (L2-ready observability + degradation visibility)

> 8 PRs landed in this release. Focus: make degradation visible instead of silent, harden the match/align boundaries, surface F4 / C1 / MS-* bugs as metadata for L2 hand-tests.

- [x] **`match_summary` full schema (21 fields + 4 back-compat)** in `metadata.json` for L2 O9/O10 jq queries
- [x] **`align_backward_skipped` metadata** — segments that kept TTS estimates because the monotonic clamp would have crushed them to 100ms (F4)
- [x] **Runner `_degraded_steps` for non-exception paths** — soft steps that catch exceptions and set `status='failed'` + `step_state.result=WARNING` (e.g. `align_fallback`) now appear in the CLI summary
- [x] **CI concurrency**: stale runs cancelled on PR amend + force-push; main pushes run to completion
- [x] **`docs/ARCHITECTURE.md`**: canonical `match_summary` schema table
- [x] **C1**: `align_fallback` now sets `status.align='failed'` (was silently `'success'`)
- [x] **F4**: align backward-jump >50% of original duration → keep TTS estimate, don't clamp to 100ms
- [x] **MS-01**: 0-scene `ContentDetector` fallback synthesizes one full-length Scene + `scene_detection_degraded`
- [x] **MS-02**: fake caption detection via explicit `is_fake` flag (no more `label.startswith("scene ")` heuristic)
- [x] **AQ-01**: single-segment WhisperX with drift >50% is rejected
- [x] **AQ-05**: `volume_unknown` fail-closed when `volumedetect` fails
- [x] **M1**: align comment corrected to reflect F3 runner upgrade (not `_degraded_steps`)
- [x] **B3**: 100ms segment floor rationale documented
- [x] **B5**: silent `try/except: pass` blocks now `console.debug()` (scenes.py + runner.py)

### v0.4 Environment variables

- `MN_TTS_PROVIDER` — `edge` (default), `openai`, or `mimo`
- `MN_DEFAULT_VOICE` — Default voice identifier for the selected TTS provider; each provider interprets this string (Edge: `zh-CN-YunxiNeural`, OpenAI: `alloy`, MiMo: voice name / file path / description depending on model)
- `MN_OPENAI_TTS_MODEL` — OpenAI TTS model (default `tts-1`)
- `MN_OPENAI_TTS_API_KEY` — OpenAI TTS API key (falls back to `MN_LLM_API_KEY`)
- `MN_OPENAI_TTS_BASE_URL` — OpenAI TTS base URL (falls back to `MN_LLM_BASE_URL`)
- `MN_MIMO_TTS_MODEL` — MiMo TTS model (default `mimo-v2.5-tts`; also `mimo-v2.5-tts-voiceclone`, `mimo-v2.5-tts-voicedesign`)
- `MN_MIMO_API_KEY` — MiMo API key (falls back to `MN_LLM_API_KEY`)
- `MN_MIMO_BASE_URL` — MiMo base URL (default `https://api.xiaomimimo.com/v1`)
- `MN_MIMO_STYLE_PROMPT` — Style description for `mimo-v2.5-tts` user message (default empty)

### v0.4.7 env/yaml boundary (config system overhaul)

Strict separation: `.env` contains ONLY LLM + TTS infrastructure (24 fields); `job.yaml` contains ALL pipeline behavior (48 params).

**`.env` (Settings) — 24 fields:** See [`.env.example`](../.env.example)
- LLM (14): `MN_LLM_BASE_URL`, `MN_LLM_API_KEY`, `MN_LLM_MODEL`, `MN_LLM_TIMEOUT`, `MN_SCRIPT_TEMPERATURE`, `MN_SCRIPT_EXPAND_TEMPERATURE`, `MN_SCRIPT_MAX_TOKENS`, `MN_SCRIPT_RETRIES`, `MN_SCRIPT_RETRY_DELAY`, `MN_RESEARCH_TEMPERATURE`, `MN_RESEARCH_MAX_TOKENS`, `MN_RESEARCH_RETRIES`, `MN_RESEARCH_RETRY_DELAY`, `MN_TRANSLATE_MAX_TOKENS`
- TTS (10): `MN_DEFAULT_VOICE`, `MN_TTS_PROVIDER`, `MN_TTS_CACHE_MAX_MB`, `MN_OPENAI_TTS_*`(3), `MN_MIMO_*`(4)

**`job.yaml` (params) — 48 keys:** See [`job.example.yaml`](../examples/job.example.yaml)
- Scene: `scene_threshold`, `scene_frame_skip`
- Match: `match_min_score`, `match_speed_clamp_min/max`, `scene_merge_min_duration`, `match_drop_scene_min_duration`, `embedding_model_name`
- BGM: `bgm_gain_db`, `bgm_duck_db`, `bgm_normalize`, `audio_target_dbfs`
- TTS pacing: `tts_pause_ms`, `tts_max_concurrent`, `tts_audio_format`, `tts_audio_bitrate`
- Translate: `translate_source_lang`, `translate_provider`, `translate_retries`, `translate_chunk_chars`, `translate_chunk_size`
- Research: `research_provider`
- WhisperX: `whisperx_device/model/language`
- Render: `render_fps/video_codec/audio_codec/threads/bg_color/font_size/output_name/ffmpeg_timeout`, `render_fit_mode/crf/preset/faststart`, `render_subtitle_position/max_width_ratio/bottom_margin_ratio`
- QA: `qa_enabled`, `qa_max_silence_db`, `qa_min_duration_ratio`, `qa_max_duration_ratio`
- Async: `async_timeout`, `async_max_workers`
- Video: `video_sizes`

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
