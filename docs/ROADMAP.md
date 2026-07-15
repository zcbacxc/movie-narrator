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

- [x] 33 hardcoded constants promoted to Settings (60 total `MN_*` env vars)
- [x] YAML auto-discovery: `--config` not passed → `cwd/job.yaml` → packaged example → none
- [x] `.env.example` as single source of truth for first-run config (replaces divergent inline template)
- [x] All 14 YAML params properly connected through `runner.py` → `ctx.metadata` → pipeline steps
- [x] Fixed `translate_chunk_chars/size` silently ignored (never copied to `ctx.metadata`)
- [x] Fixed `export_clips` codecs hardcoded (now uses `settings.render_video_codec/audio_codec`)
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

### v0.4.7 Environment variables (config system overhaul)

See [`.env.example`](../.env.example) for the complete list of all 60 `MN_*` env vars. Key additions:

- LLM tuning: `MN_LLM_TIMEOUT`, `MN_SCRIPT_TEMPERATURE`, `MN_SCRIPT_MAX_TOKENS`, `MN_SCRIPT_RETRIES`, `MN_SCRIPT_RETRY_DELAY`, `MN_RESEARCH_TEMPERATURE`, `MN_RESEARCH_MAX_TOKENS`, `MN_TRANSLATE_MAX_TOKENS`
- TTS: `MN_TTS_MAX_CONCURRENT`, `MN_TTS_AUDIO_FORMAT`, `MN_TTS_AUDIO_BITRATE`
- WhisperX: `MN_WHISPERX_DEVICE`, `MN_WHISPERX_MODEL`, `MN_WHISPERX_LANGUAGE`
- Translate: `MN_TRANSLATE_SOURCE_LANG`
- Render: `MN_RENDER_BG_COLOR`, `MN_RENDER_FONT_SIZE`, `MN_RENDER_OUTPUT_NAME`, `MN_RENDER_FFMPEG_TIMEOUT`
- Match: `MN_MATCH_SPEED_CLAMP_MIN`, `MN_MATCH_SPEED_CLAMP_MAX`, `MN_SCENE_MERGE_MIN_DURATION`, `MN_EMBEDDING_MODEL_NAME`
- BGM: `MN_BGM_GAIN_DB`
- TTS pacing: `MN_TTS_PAUSE_MS`, `MN_TTS_CACHE_MAX_MB`
- Video: `MN_VIDEO_SIZES` (JSON string)
- Async: `MN_ASYNC_TIMEOUT`, `MN_ASYNC_MAX_WORKERS`

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
