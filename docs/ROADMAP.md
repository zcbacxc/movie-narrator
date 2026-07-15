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
- [x] Web UI (Gradio local browser app via `mn web`; requires `[web]` extra)

### v0.3 New CLI flags

- `--subtitle-lang` — Target language tag (`en`, `ja`, `zh-TW`, ...); empty = feature off
- `--subtitle-mode` — Overlay mode: `original` / `translated` / `bilingual` (default `original`)

### v0.3.5 Web UI

- `mn web` — Launch local Gradio browser app (requires `pip install "movie-narrator[web]"`)
- Cooperative cancel at step boundaries (Cancel button in UI)
- Form fields mirror CLI options; advanced params follow "empty = no override" rule (Settings defaults apply)
- Uploads go to `mn_web_*` temp dirs, never pollute `output/`

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

- [x] Strict env/yaml boundary: `.env` = 21 LLM + TTS infrastructure fields only; `job.yaml` = 32 pipeline behavior params
- [x] YAML auto-discovery: `--config` not passed → `cwd/job.yaml` → packaged example → none
- [x] `.env.example` and `job.example.yaml` as single sources of truth (no code constants module)
- [x] All 32 YAML params properly connected through `runner.py` → `ctx.metadata` → pipeline steps
- [x] Fixed `translate_chunk_chars/size` silently ignored (never copied to `ctx.metadata`)
- [x] Fixed `export_clips` codecs hardcoded (now uses `ctx.metadata` → inline literal)
- [x] Fixed `scene_frame_skip` missing from runner copy loop

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

Strict separation: `.env` contains ONLY LLM + TTS infrastructure (21 fields); `job.yaml` contains ALL pipeline behavior (32 params).

**`.env` (Settings) — 21 fields:** See [`.env.example`](../.env.example)
- LLM (11): `MN_LLM_BASE_URL`, `MN_LLM_API_KEY`, `MN_LLM_MODEL`, `MN_LLM_TIMEOUT`, `MN_SCRIPT_TEMPERATURE`, `MN_SCRIPT_MAX_TOKENS`, `MN_SCRIPT_RETRIES`, `MN_SCRIPT_RETRY_DELAY`, `MN_RESEARCH_TEMPERATURE`, `MN_RESEARCH_MAX_TOKENS`, `MN_TRANSLATE_MAX_TOKENS`
- TTS (10): `MN_DEFAULT_VOICE`, `MN_TTS_PROVIDER`, `MN_TTS_CACHE_MAX_MB`, `MN_OPENAI_TTS_*`(3), `MN_MIMO_*`(4)

**`job.yaml` (params) — 32 keys:** See [`job.example.yaml`](../examples/job.example.yaml)
- Scene: `scene_threshold`, `scene_frame_skip`
- Match: `match_min_score`, `match_speed_clamp_min/max`, `scene_merge_min_duration`, `embedding_model_name`
- BGM: `bgm_gain_db`
- TTS pacing: `tts_pause_ms`, `tts_max_concurrent`, `tts_audio_format`, `tts_audio_bitrate`
- Translate: `translate_source_lang`, `translate_provider`, `translate_retries`, `translate_chunk_chars`, `translate_chunk_size`
- Research: `research_provider`
- WhisperX: `whisperx_device/model/language`
- Render: `render_fps/video_codec/audio_codec/threads/bg_color/font_size/output_name/ffmpeg_timeout`
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
