# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

`movie-narrator` — a Python CLI (`mn`) that turns one prompt into a narrated movie-recap video. Pipeline: **Resolve → Assets → Research → Script → Script Export → TTS → Align → Scenes → Match → BGM → Translate → Subtitle → QA Gate → Render → QA → Export Clips** (16 steps), orchestrated over a shared mutable `Context` model. Targets Python 3.10+. Current version: **0.7.3** (CONTRACT_VERSION `0, 7, 2`). v0.5 ecosystem (M1–M5 + hardening + narrative quality + script quality + voice & audio quality + subtitle & translation quality + match & alignment precision + holistic QA & quality dashboard) complete; v0.6 Cloud task queue (v0.6.0) and remote inference (v0.6.1) complete; v0.7.x output experience (GPU encoding, cost tracking, preview mode, scene transitions, text animation) complete; next is service deployment (v0.8.0).

## Common Commands

```bash
# Install (dev mode with test deps)
pip install -e ".[dev]"

# Run unit tests (fast, no network or LLM needed)
pytest -v

# Single test
pytest tests/test_context.py::test_format_time -v

# Generate a video (requires LLM endpoint reachable; defaults to local Ollama)
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60

# Generate with source video, BGM, and research enabled
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60 --video ./movie.mp4 --bgm ./bgm.mp3 --research

# Use a job YAML config file
mn create --config examples/job.example.yaml

# Debug sub-commands
mn resolve --movie "飞驰人生" --library-dir D:/movies
mn research --movie "飞驰人生"
mn scenes --video ./movie.mp4
mn align --audio ./narration.mp3 --script ./script.md
mn clips --video ./movie.mp4 --scenes ./scenes.json

# Plugin commands (v0.5+)
mn plugin list          # list discovered entry_points
mn plugin discover      # load all plugins
mn plugin registries    # show registered steps + providers
mn plugin version       # show CONTRACT_VERSION

# Task queue commands (v0.6+)
mn submit -m "飞驰人生" -p douyin-fast    # submit async task
mn submit -m "飞驰人生" --wait --timeout 600  # submit and wait
mn status <task_id>     # show task status
mn tasks                # list tasks
mn tasks --status running  # filter by status
mn cancel <task_id>     # cancel running task
mn wait <task_id> -t 600  # wait for completion
mn cleanup              # clean up terminal tasks
mn cleanup --all        # clean up all tasks
mn serve --port 8765 --max-workers 2  # start daemon + REST API server
mn download <task_id> -r http://worker:8765  # fetch artifacts from remote

# Pipeline control
mn create --movie "飞驰人生" --pause-at script  # pause after script generation
mn resume --state output/<movie>/pipeline_state.json  # resume from checkpoint

# Performance benchmark (v0.5.3+)
python benchmarks/profile_pipeline.py --runs 3 --output benchmark.json

# CLI smoke checks
mn --help
mn version

# CI-mode run (uses silent fallback audio, no Edge-TTS network calls)
CI=1 mn create --movie "CI-Test" --style "热血搞笑" --duration 10 --keep-cache
```

External prerequisites: **FFmpeg** on `PATH` (`brew install ffmpeg` / `apt install ffmpeg` / Windows installer). The renderer locates it via `shutil.which`.

Optional extras for advanced features:
- `pip install "movie-narrator[media]"` — scene detection (PySceneDetect), clip export
- `pip install "movie-narrator[ml]"` — audio alignment (WhisperX), embedding-based clip matching (sentence-transformers)
- `pip install "movie-narrator[full]"` — all extras
- `pip install "movie-narrator[docs]"` — mkdocs + mkdocstrings for local API doc generation
- `pip install "movie-narrator[dev]"` — pytest

## Architecture (Big Picture)

The pipeline is a **flat sequence of pure functions** `step(ctx: Context) -> Context` defined in `src/movie_narrator/pipeline/runner.py:STEPS`. Each step reads from and writes back to the shared `Context` (defined in `models.py`). The step list is managed by `StepRegistry` (`pipeline/registry.py`) — built-in steps are registered at import time, and external plugins can inject steps via `@register_step` with `after=`/`before=` ordering hints.

The 16-step pipeline:
```
cli.create() → run_pipeline()
→ resolve_video     (find source video: --video arg or library dir fuzzy search)
→ prepare_assets    (validate BGM path, resolve to absolute)
→ research_plot     (LLM movie info lookup, writes research.json)          [soft]
→ generate_script   (LLM two-phase: beats → expansion → List[ScriptSegment])
→ export_script_md  (writes script.md markdown)
→ generate_voice    (TTS provider async w/ semaphore, content-hash cache)
→ align_audio       (WhisperX transcription alignment)                     [soft]
→ detect_scenes     (PySceneDetect threshold-based scene cuts)             [soft]
→ match_clips       (heuristic + optional embedding re-rank of narration→scene) [soft]
→ mix_bgm           (pydub overlay narration + background music)           [soft]
→ translate_subtitles (LLM translation, retry-then-soft-degrade)           [soft]
→ generate_subtitle (writes subtitle.srt from TimedSegment list)
→ run_qa_gate       (intermediate product QA validation before render)     [soft]
→ render_video      (MoviePy composite with video footage + text overlays + audio)
→ validate_deliverable (ffprobe QA: audio, duration, stream checks + video encoding QA + quality dashboard + QA report export)
→ export_clips      (export scene subclips as .mp4 files)                  [soft]
```

Steps marked **[soft]** are optional — they can fail or be disabled without aborting the entire pipeline. Their status is tracked in `ctx.status` (one of `disabled` / `skipped` / `success` / `failed`). Pass `--strict` to make soft failures fatal.

### Data model (`models.py`)

`Context` is the unit of state passed between steps. Key fields:

**Input fields** (set at creation):
- `movie_name`, `style`, `duration` — inputs from CLI/config
- `output_dir`, `library_dir` — paths

**Progressive fields** (populated by pipeline steps):
- `source_video_path` — set by `resolve_video`
- `segments: List[ScriptSegment]` — set by `generate_script`
- `timed_segments: List[TimedSegment]` — set by `generate_voice`, refined by `align_audio`
- `scenes: List[Scene]` — set by `detect_scenes` (index, start/end, clip/thumbnail paths)
- `matched_clips: List[MatchedClip]` — set by `match_clips` (narration→scene mapping with scores)
- `research: ResearchInfo` — title, year, summary, genres, cast, keywords
- `assets: Assets` — intro/bgm/watermark/font paths (validated by `prepare_assets`)
- `status: PipelineStatus` — per-soft-step state tracking

**Output fields**:
- `audio_path` — set by `generate_voice` (narration.mp3)
- `final_audio_path` — set by `mix_bgm` (mixed.mp3, or same as audio_path if BGM skipped)
- `subtitle_path` — set by `generate_subtitle` (subtitle.srt)
- `script_md_path` — set by `export_script_md` (script.md)
- `clips_dir` — set by `export_clips` (clips/ folder)
- `video_path` — set by `render_video` (final.mp4)
- `metadata: Dict[str, Any]` — free-form bag: voice, format, keep_cache, version, environment, bgm_request, strict, workflow_steps, scene_threshold, match_min_score, render_encoder, cost summary, etc.
- `cost_tracker: Optional[CostTracker]` — per-run cost tracking for LLM token usage and TTS calls (v0.7.0)

**Supporting models** (also in `models.py`):
- `MovieCard` — structured movie metadata for TMDB cross-validation (title, year, director, cast, genres, summary, set_pieces)
- `WordSegment` — word-level timestamp from WhisperX alignment (word, start, end, confidence)
- `SubtitlePaths` — resolved subtitle output paths
- `StepResult` / `StepState` — step execution outcome and per-step state
- `Services` — infrastructure container (console, logger); defaults to `SilentConsole` when not provided

### Module responsibilities

**Root-level modules** (`src/movie_narrator/`):
- `cli.py` — Typer CLI app (see CLI section below)
- `config.py` — `Settings` (pydantic-settings, see Configuration section)
- `contract.py` — stable API boundary for external consumers (see Web UI section)
- `models.py` — all data models (see Data model section above)
- `plugin_loader.py` — plugin discovery via entry points (see Plugin discovery section)
- `imitate.py` — reference video style analysis for `mn imitate` command (analyzes pacing/tone, applies matched params)
- `race.py` — multi-preset/config head-to-head comparison for `mn race` command (generates comparison output)

**Pipeline stages** (`src/movie_narrator/pipeline/`):
- `resolve.py` — finds source video: honors `--video` CLI arg first, then fuzzy-searches `--library-dir` by normalized filename. If neither, `source_video_path` stays `None` (text-only render).
- `assets.py` — validates `Assets.bgm` path exists; clears it with a warning if missing.
- `research.py` — calls registered research provider (built-in `llm`) to get structured movie info; writes `research.json` envelope. Soft step: can be disabled via `--no-research` or `workflow_steps.research=False`.
- `script.py` — two-phase LLM script generation: Phase 1 (plot beats at low temperature) → Phase 2 (beat expansion at moderate temperature) → fallback trim. Injects research context and preset tags when available. CI mode uses `_CI_MOCK_SEGMENTS` fallback.
- `script_export.py` — writes `script.md` with numbered headings per segment.
- `tts.py` — async TTS via registered provider (edge/openai/mimo), `MAX_CONCURRENT=3` semaphore, **content-addressable cache** under `{output_dir}/cache/`. When `CI` env var is set, skips network and emits silent audio.
- `align.py` — runs WhisperX on the generated audio to refine word-level timestamps. Soft step: disabled when `whisperx` package unavailable. Requires `[ml]` extra.
- `scenes.py` — uses PySceneDetect `ContentDetector` with configurable threshold/frame-skip. Soft step: disabled when `scenedetect` package unavailable. Writes `scenes.json`. Requires `[media]` extra.
- `match.py` — maps narration segments to video scenes: heuristic proportional mapping (score=1.0 baseline), then optional embedding re-rank via sentence-transformers (cosine similarity). Filters below `match_min_score`. Writes `matches.json`. Requires `[ml]` extra for embedding path.
- `scene_filter.py` — WP6 scene filtering: intro skip (luminance + motion), dark frame detection, highlight window.
- `bgm.py` — mixes background music with narration using pydub overlay. Supports optional ambient/SFX track (v0.7.1). Soft step: skips when no BGM configured.
- `translate.py` — multi-language subtitle translation via LLM. Soft step: retry-then-soft-degrade.
- `subtitle.py` — pure formatter; `_format_time` rounds ms from `seconds * 1000`.
- `render.py` — MoviePy composite with video footage + text overlays. Two-stage encode: MoviePy writes video-only stream, ffmpeg subprocess mixes audio. GPU encoder auto-detection via `utils/gpu_detect.py` (NVENC/VAAPI/VideoToolbox with CPU fallback). Honors `final_audio_path`. Supports preview mode (v0.7.2). Writes `metadata.json`.
- `qa.py` — post-render deliverable QA step (hard step). ffprobe checks + video encoding QA + quality dashboard + QA report export.
- `qa_gate.py` — intermediate QA gate (soft step, v0.5.12+). Validates script/audio/subtitle/alignment/translation quality before render. Soft gate; `--strict` to abort.
- `export_clips.py` — exports scene subclips as .mp4 files. Soft step.
- `preflight.py` — pre-run LLM/TTS validation (fail-fast). CI skips.
- `errors.py` — `PipelineStrictError`, `PipelineCancelled`, `PipelinePaused`, `RunController`, `StepAction`.
- `registry.py` — `StepRegistry` class, built-in step registration, `@register_step` decorator.
- `runner.py` — step list + orchestration + per-step `▶ / ✓ / ✗` + elapsed timing. Handles `workflow_steps` toggles, `params` injection, `--retry` flag, `--pause-at` checkpointing, `mn resume` state restoration.
- `_align_backend.py` — WhisperX / faster-whisper backend selection logic with fallback chain.

**Utils** (`src/movie_narrator/utils/`):
- `llm.py` — `get_llm_client()` context-manager yielding `LLMClient(client: OpenAI, model)` backed by settings; defaults to local Ollama. Uses managed `httpx.Client` with 60s timeout.
- `json_parser.py` — `extract_json`: tries `json.loads` → fenced ```json``` block → `{...}` substring extraction, each step applying `_clean_json` (strips `...`, trailing commas, blank lines). Raises `ValueError` on failure.
- `async_utils.py` — `run_async(coro)` bridges sync/async: if no loop is running, `asyncio.run`; otherwise submits to a 2-worker `ThreadPoolExecutor` (registered to `atexit.shutdown(wait=False)`) with a 300s timeout.
- `font.py` — CJK font fallback chain: `assets/fonts/NotoSansSC-Regular.otf` → Linux/Darwin/Windows system paths → raises a multi-line install hint if nothing matches.
- `prompts.py` — `BEATS_PROMPT` (Phase 1 plot beat extraction), `EXPAND_PROMPT` (Phase 2 beat expansion). Sentence count and style configurable via presets and `JobParams`.
- `environment.py` — `collect_environment()`: gathers Python version, platform, and FFmpeg path for metadata.
- `metadata_export.py` — `build_metadata_json()`: constructs the `metadata.json` payload with version, input params, status, environment, and segment timings.
- `optional_deps.py` — `probe(name)`: checks whether `scenedetect` / `whisperx` / `sentence_transformers` is importable, returns `(available, install_hint)`.
- `console.py` — Console Protocol + PlainConsole + build_console
- `errors.py` — ConfigError (cross-cutting config-error class)
- `log.py` — AppLogger (file logging layer) + `resolve_log_level()` (shared log-level resolution)
- `retention.py` — Log file retention
- `audio_mix.py` — Audio normalize + BGM ducking (pydub)
- `deliverable_qa.py` — ffprobe/ffmpeg media probing + QA rules
- `video_layout.py` — Cover/contain crop+resize geometry
- `text_image.py` — PIL-based text image rendering for video title cards and overlays
- `sanitize.py` — `sanitize_filename()`: handles Windows-reserved names (`CON`, `PRN`, `COM1`…) and `<>:"/\|?*` characters
- `warnings.py` — warning suppressions context manager for noisy third-party deps
- `alignment_qa.py` — word-level alignment quality: sub-segment remapping, confidence scoring, drift validation (v0.5.11)
- `audio_qa.py` — TTS output quality: clipping, SNR, silence checks per segment (v0.5.9)
- `glossary.py` — translation terminology consistency: cross-chunk glossary extraction and mismatch detection (v0.5.10)
- `match_quality.py` — composite match quality scoring: embedding + rhythm + diversity (v0.5.11)
- `prosody.py` — emotion-to-prosody mapping: speed adjustment factors from beat emotion labels (v0.5.9)
- `qa_report.py` — structured QA report export: `qa_report.json` + `qa_report.txt` (v0.5.12)
- `quality_dashboard.py` — cross-step quality score aggregation: holistic dashboard with regression baseline (v0.5.12)
- `subtitle_qa.py` — subtitle quality: CPS, overlap detection, line length, display fit (v0.5.10)
- `video_qa.py` — video encoding quality: bitrate, codec, resolution, frame rate checks (v0.5.12)
- `gpu_detect.py` — GPU encoder auto-detection (NVENC/VAAPI/VideoToolbox) with CPU fallback (v0.7.0)
- `cost_tracker.py` — per-run cost tracking for LLM token usage and TTS calls (v0.7.0)
- `transitions.py` — scene transition effects (fade, dissolve, slide) between video clips (v0.7.1)
- `text_anim.py` — text animation effects (fade, slide_up, slide_left) for subtitle overlays (v0.7.1)
- `preview.py` — 10-second preview mode for fast iteration before full render (v0.7.2)

**Workflow config** (`src/movie_narrator/workflow/`):
- `schema.py` — `JobConfig` (YAML shape with strict whitelist validation), `JobSteps` (per-step boolean toggles), `JobParams` (whitelisted fine-tuning params), `ResolvedJob` (final merged output).
- `load.py` — `load_job_config(path)`: reads YAML, validates top-level keys + steps + params whitelists, resolves relative paths against config file directory, returns validated `JobConfig`.
- `merge.py` — `merge_job(cli, job, settings)`: three-source priority merge — CLI overrides > YAML config > Settings defaults. Booleans use explicit-true detection so `--no-bgm` works correctly against YAML `no_bgm: false`.
- `errors.py` — `JobConfigError` for unknown keys, bad YAML syntax, unsupported version, etc.

**Narration presets** (`src/movie_narrator/presets/`):
- `base.py` — `Preset` Protocol (name + params() + prompt_tags() + description()), closed-vocabulary `ALLOWED_PARAM_KEYS` (subset of `PARAM_WHITELIST`), `ALLOWED_PROMPT_TAGS` (cadence/register/connectors enums).
- `registry.py` — validates preset params/tags at registration time, exposes `get_preset()` / `list_presets()`. `discover_presets()` stub reserved for Stage 2 SPI.
- `douyin_fast.py` / `mainstream_dry.py` / `bilibili_long.py` — three built-in style presets. Preset params are the BASELINE; user params always override.

**Provider registries** (`src/movie_narrator/providers/`):
- `registry.py` — `ProviderRegistry` class: category-scoped factory registry with `register()` / `create()` / `info()` / `contains()` / `set_protocol()` (deferred protocol binding to avoid circular imports). Hosts four global instances: `tts_registry`, `vision_registry`, `llm_registry`, `research_registry`. External plugins register factories via `@register_tts` / `@register_vision` / `@register_llm` / `@register_research` decorators.
- `tmdb.py` — TMDB (The Movie Database) research provider (`@register_research("tmdb")`): fetches movie metadata via TMDB API for fact verification, bypassing LLM hallucination. Uses `urllib.request` (stdlib), in-memory cache, retry with backoff. Requires `MN_TMDB_API_KEY`. Also provides `enrich_movie_card_with_tmdb()` for cross-validating LLM-sourced `MovieCard` fields.
- `__init__.py` — re-exports all registries and registration decorators.

**TTS providers** (`src/movie_narrator/tts/`):
- `protocol.py` — `TTSProvider` ABC: `create(settings) -> TTSProvider`, `synthesize(text, voice) -> bytes`.
- `base.py` — shared TTS provider base.
- `edge.py` — built-in Edge-TTS provider (default).
- `openai_provider.py` — built-in OpenAI TTS provider.
- `mimo_provider.py` — built-in MiMo (小米) TTS provider.
- `factory.py` — registry-only dispatch (legacy fallback removed in v0.5.1). Resolves provider name from `MN_TTS_PROVIDER` setting.
- `cache.py` — content-addressable cache (sha256 over 7 dimensions: text, voice, provider, rate, volume, pitch, format).

**Vision providers** (`src/movie_narrator/vision/`):
- `protocol.py` — `VisionCaptioner` ABC: `caption(image_path) -> str`.
- `stub.py` — built-in stub provider (no-op, returns empty).
- `vlm.py` — `VLMVisionCaptioner`: real vision-language model provider for scene captioning.
- `http_vlm.py` — HTTP-based VLM provider (remote inference).
- `factory.py` — registry-only dispatch (legacy fallback removed in v0.5.1).

**Plugin discovery** (`src/movie_narrator/plugin_loader.py`):
- `discover_plugins()` — scans `movie_narrator.plugins` entry point group via `importlib.metadata`, loads each plugin, registers via `load_plugin()`. Per-plugin error isolation (broken plugins warn but don't block others).
- `list_available_plugins()` — lists entry point names without loading.
- `PluginLoadResult` — dataclass tracking per-plugin load outcome (name, success, error).

**Web UI — separate repo** (`src/movie_narrator/contract.py` only):
- The FastAPI + React SPA stack lives in the external repository [`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web) (install via `pip install movie-narrator-web`, launch with `mn-web`). This core repo no longer ships `web_api/` or `webui/` directories and has no `mn web` command or `[web]` extra — `fastapi` / `uvicorn` / `python-multipart` are no longer dependencies.
- `contract.py` is the stable API boundary the external web package depends on. It re-exports symbols from internal modules (`pipeline.runner`, `pipeline.errors`, `pipeline.registry`, `utils.console`, `utils.sanitize`, `models`, `providers`, `plugin_loader`, `presets`, `cloud`), defines the `PipelineResult` protocol (`runtime_checkable`), the `Plugin` / `PluginContext` extension points, and `CONTRACT_VERSION = (0, 7, 2)` for import-time compatibility checks. See `tests/test_contract.py` for the contract guarantees.

**Cloud / Task Queue** (`src/movie_narrator/cloud/`):
- `models.py` — `TaskStatus` / `TaskPriority` enums, `TaskRequest` (mirrors `build_context` args + retry config), `TaskProgress` (step-level tracking), `TaskResult` (output paths + error capture), `Task` (central model with lifecycle timestamps). `TERMINAL_STATES` / `ACTIVE_STATES` frozensets.
- `storage.py` — `TaskStorage`: JSON-based persistence with atomic writes (`os.replace`), thread-safe via `threading.Lock`, index file for fast listing.
- `queue.py` — `TaskQueue` protocol (duck-typed interface for local/remote/cloud backends) and `LocalTaskQueue` (ThreadPoolExecutor-based in-process execution with submit, cancel, wait, cleanup).
- `remote_queue.py` — `RemoteTaskQueue`: HTTP client implementing the same `TaskQueue` protocol — swap with `LocalTaskQueue` via config, zero code changes.
- `api.py` — `TaskAPIServer`: REST API on stdlib `http.server` (no extra deps). Endpoints: POST/GET `/tasks`, GET/DELETE `/tasks/{id}`, GET `/tasks/{id}/result|artifacts|download/{file}`, `/health`, `/info`.
- `daemon.py` — `run_daemon` / `WorkerDaemon`: queue + API server + signal handling. Runs `LocalTaskQueue` + `TaskAPIServer` in a single process.
- `worker.py` — `CancelController` (cooperative cancellation via `threading.Event`, implements `RunController`), `ProgressConsole` (console wrapper for real-time progress tracking), `run_task` (pipeline execution with exponential backoff retry), `_execute_task` (single-attempt execution with result/error capture).
- `remote_provider.py` — `register_remote_llm` / `register_remote_tts`: proxy LLM/TTS calls to a remote worker via HTTP. `download_artifact` / `list_artifacts`: fetch output files from remote server.

**CLI** (`cli.py`):
- Typer app with `no_args_is_help=True`, `rich_markup_mode="rich"`, bilingual (中文/English) help text. Bare `mn` shows help; `mn --help` / `mn <command> --help` shows detailed help. `-h` works as `--help` short option across all commands (v0.4.16+).
- `create` — main command: merges CLI args + optional `--config` YAML + Settings into `ResolvedJob`, then calls `run_pipeline()`. Outputs final `video_path`. Supports `--narration-preset` / `-p`. First run without `--config` or `cwd/job.yaml` auto-creates `cwd/job.yaml` from packaged example (with stderr notice, CI skips copy).
- `race` — race multiple presets/configs head-to-head for the same movie; generates comparison output.
- `imitate` — imitate a reference video's narration style; analyzes reference and applies matched pacing/tone.
- `resume` — resume a paused pipeline from `pipeline_state.json` (created by `--pause-at`); injects `SilentConsole` then replaces with real `Console`.
- `resolve` — debug: standalone video resolution with optional `--json` output.
- `research` — debug: standalone plot research, writes `research.json`.
- `scenes` — debug: standalone scene detection, writes `scenes.json`.
- `align` — debug: standalone audio alignment (WhisperX).
- `clips` — debug: standalone clip export from `scenes.json`.
- `version` — echoes `__version__`.
- `preset` — list narration presets (`mn preset`) or show full params/tags (`mn preset <name>`).
- `plugin` — plugin management subcommands (v0.5+): `list` (entry_points discovery), `discover` (load all plugins), `registries` (show registered steps + providers), `version` (show CONTRACT_VERSION).
- `submit` / `status` / `tasks` / `cancel` / `wait` / `cleanup` — task queue commands (v0.6+): async job submission, status tracking, task listing, cancellation, blocking wait, and cleanup.
- `serve` — start task queue daemon + REST API server (v0.6+): binds host/port (default `127.0.0.1`; use `--public` for `0.0.0.0` with security warning), runs `LocalTaskQueue` + `TaskAPIServer` with signal handling.
- `download` — download task artifacts from a remote server (v0.6+): fetches output files via HTTP.
- Filename sanitizer handles Windows-reserved names (`CON`, `PRN`, `COM1`…) and `<>:"/\|?*`.

### Configuration (`config.py`)

`Settings` (pydantic-settings) loads from `.env` (project root) and `~/.movie-narrator/.env` (user global), with `MN_` env prefix. **Boundary**: `.env` (Settings) = LLM + TTS credentials, endpoints, models, and call params only. All pipeline behavior (scene, match, render, etc.) is configured via `job.yaml` params — see `examples/job.example.yaml` for defaults.

| Field | Default | Description |
|---|---|---|
| **LLM** | | |
| `llm_provider` | `openai` | Registered LLM provider name (see `llm_registry`) |
| `llm_base_url` | `http://localhost:11434/v1` | OpenAI-compatible endpoint |
| `llm_api_key` | `ollama` | API key |
| `llm_model` | `qwen2.5:7b` | Model name |
| `llm_timeout` | `60` | LLM request timeout (seconds) |
| `script_temperature` | `0.7` | Phase 1 (beats) temperature |
| `script_expand_temperature` | `0.5` | Phase 2 (expansion) temperature |
| `script_max_tokens` | `2048` | Max tokens per script LLM call |
| `script_retries` | `3` | Script generation retry count |
| `script_retry_delay` | `1.5` | Retry delay (seconds) |
| `research_temperature` | `0.3` | Research LLM temperature |
| `research_max_tokens` | `1024` | Max tokens per research call |
| `research_retries` | `3` | Research retry count |
| `research_retry_delay` | `1.5` | Retry delay (seconds) |
| `translate_max_tokens` | `4096` | Max tokens per translation call |
| **TMDB** | | |
| `tmdb_api_key` | `None` | TMDB API key for fact verification |
| `tmdb_base_url` | `https://api.themoviedb.org/3` | TMDB API base URL |
| `tmdb_language` | `zh-CN` | TMDB response language |
| **TTS** | | |
| `default_voice` | `zh-CN-YunxiNeural` | Default TTS voice (Edge/MiMo) |
| `tts_provider` | `edge` | TTS provider name (`edge` / `openai` / `mimo`) |
| `openai_tts_model` | `tts-1` | OpenAI TTS model |
| `openai_tts_api_key` | `None` | OpenAI TTS API key (falls back to `llm_api_key`) |
| `openai_tts_base_url` | `None` | OpenAI TTS base URL |
| `mimo_tts_model` | `mimo-v2.5-tts` | MiMo TTS model |
| `mimo_api_key` | `None` | MiMo API key (falls back to `llm_api_key`) |
| `mimo_base_url` | `https://api.xiaomimimo.com/v1` | MiMo API base URL |
| `mimo_style_prompt` | `""` | MiMo TTS style prompt |
| `tts_cache_max_mb` | `500` | TTS cache size limit (MB) |

`get_settings()` is `lru_cache`d. See `.env.example`.

### Soft steps and `PipelineStatus`

Eight steps are "soft" (optional): `research_plot`, `align_audio`, `detect_scenes`, `match_clips`, `mix_bgm`, `translate_subtitles`, `run_qa_gate`, `export_clips`. Each writes its outcome to `ctx.status.<field>` as one of:
- `disabled` — dependency missing or `workflow_steps` toggle set to `false`
- `skipped` — prerequisites not met (e.g. no source video)
- `success` — completed normally
- `failed` — attempted but errored; pipeline continues unless `--strict`

### Extension points

- **New pipeline stage**: register via `@register_step("name", after="step_x")` or `@step("name", before="step_y")` from `movie_narrator`. Signature must be `(ctx: Context) -> Context`. Add to `SOFT_STATUS_STEPS` if it should be skippable. Add a corresponding field to `PipelineStatus` in `models.py`.
- **Swap TTS/Vision/LLM/Research provider**: register a custom provider via `@register_tts("name")` / `@register_vision("name")` / `@register_llm("name")` / `@register_research("name")` from `movie_narrator`. Factory must return an instance satisfying the corresponding ABC (`TTSProvider` / `VisionCaptioner`). Protocol validation is enforced at `create()` time.
- **Full plugin (out-of-tree)**: create a package with `[project.entry-points."movie_narrator.plugins"]` in `pyproject.toml`, implement the `Plugin` protocol (`name` + `register(ctx: PluginContext)`), and register steps/providers inside `register()`. Discovered automatically via `discover_plugins()`. See `examples/plugins/` for templates.
- **New CLI command**: add `@app.command()` in `cli.py`.
- **Add YAML config field**: add to `JobConfig` schema in `workflow/schema.py`, add to `_ALLOWED_TOP` in `workflow/load.py`, handle in `merge_job()` in `workflow/merge.py`.

### Output structure

Fixed output under `output/<sanitized_movie>/`:
- `narration.mp3` — TTS narration audio
- `subtitle.srt` — SRT subtitle file
- `script.md` — narration script in markdown
- `metadata.json` — version, inputs, status, segment timings, environment
- `final.mp4` — rendered video

May also produce (depending on pipeline options):
- `research.json` — movie plot research envelope
- `scenes.json` — detected scene boundaries
- `matches.json` — narration-to-scene clip mappings
- `mixed.mp3` — BGM-mixed final audio
- `clips/` — exported scene subclip .mp4 files
- `cache/` — TTS cache directory (deleted unless `--keep-cache`)

Generated artifacts (`*.mp4`, `*.mp3`, `*.srt`, `*.json`, `output/`) are gitignored.

### Job YAML config (`--config`)

v0.3 feature: pass a YAML file to `mn create --config <path>` with per-field overrides. All fields optional — omit to keep Settings/CLI defaults. Relative paths (`video`, `bgm`, `library_dir`) resolve against the config file's directory.

Priority: **CLI flags > YAML config > Settings defaults**.

See `examples/job.example.yaml` for a commented template.

### Test strategy

`tests/` has 69 test files covering:
- **Unit tests**: context models, subtitle formatting, asset validation, script export, error types, optional deps probing, workflow schema/load/merge/errors, presets, settings, JSON parser, video layout, audio mix, text image
- **Pipeline step tests**: resolve, research, BGM, align, scenes, match, render, QA, translate, vision, deliverable QA (with mocks for external deps)
- **CLI integration tests**: resolve JSON/plain output, debug command degradation hints, config file loading and merge behavior
- **Runner tests**: strict mode, soft step status tracking, workflow metadata injection, pipeline cancel, pipeline pause, step retry (`--retry` flag)
- **Plugin & contract tests**: plugin discovery (entry_points), plugin registry, contract surface stability, M5 community commands
- **TTS tests**: provider cache (content-addressable), provider abstraction (edge/openai/mimo)
- **E2E smoke test**: full 16-step pipeline run in CI mode

Heavy-path tests (LLM, Edge-TTS, MoviePy render) aren't run in unit tests — the CI workflow runs them as a **smoke test** with `CI=1` to force silent-audio fallback so the pipeline is exercised end-to-end without network. New pipeline steps should follow this pattern: pure logic unit-tested, external integration covered via the smoke job.

## Web UI Development

The Web UI (React 18 SPA + FastAPI backend) is developed in a separate repository: [`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web). There is no `webui/` or `web_api/` tree in this core repo, so frontend workflows (`npm install`, `npm run dev`, `npm run build`) and the `mn web` command do not apply here. Install and launch the Web UI from the independent package instead:

```bash
pip install movie-narrator-web
mn-web            # launches the FastAPI + React SPA (port 8760)
```

When modifying the contract surface, keep `contract.py` and `tests/test_contract.py` in sync so the external web package keeps compiling against the core engine.

## Conventions

- Commit message prefixes per `docs/CONTRIBUTING.md`: `feat:`, `fix:`, `docs:`, `chore:`, `refactor:`.
- Every step in `STEPS` is run sequentially; prints `▶` on entry / `✓` on success (with elapsed time) / `✗ <err>` on failure. Soft steps may show `⏭` for disabled/skipped.
- Output structure is fixed: `output/<sanitized_movie>/` with `cache/` dir (deleted unless `--keep-cache`). MoviePy temp files go to `output/<sanitized_movie>/.tmp/`.
- Generated artifacts (`*.mp4`, `*.mp3`, `*.srt`, `*.json`, `output/`) are gitignored.

## Licensing

- **SPDX Headers**: All source files under `src/movie_narrator/` must start with:
  ```python
  # SPDX-FileCopyrightText: 2026 zcbacxc
  # SPDX-License-Identifier: AGPL-3.0-or-later
  ```
  Test files (`tests/`) are optional — SPDX headers not enforced.
- **pyproject.toml**: Use PEP 639 string format (`license = "AGPL-3.0-or-later"`), not the PEP 621 table format (`license = {text = ...}`). Build system requires `setuptools>=77.0` to parse string-form license.
- **Trove classifier**: Do NOT include license trove classifiers (e.g. `License :: OSI Approved :: ...`) — PEP 639 makes them mutually exclusive with the `license` string expression; setuptools 77+ raises `InvalidConfigError` if both are present. The `license = "AGPL-3.0-or-later"` string is the sole source of license metadata.
- **LICENSE file**: Must be unmodified AGPL-3.0 full text. The FSF copyright notice (line 4: "Copyright (C) 2007 Free Software Foundation") is the license text's own copyright and must not be altered — AGPL requires "changing it is not allowed".
- **README badge**: License badge URL uses `AGPL--3.0--or--later` (shields.io format, double dashes for single dashes). License section text uses `AGPL-3.0-or-later`.
- **Deprecated identifiers**: `AGPL-3.0` (without `-only` or `-or-later`) is deprecated in SPDX 3.0+; always use `AGPL-3.0-or-later`.

## Documentation Standards

### General
- All documentation files (`docs/*.md`, `README.md`) must maintain EN and ZH versions that are **structurally aligned** — same chapter count, same section hierarchy, same diagrams/tables. Documents requiring bilingual pairs: `README`, `docs/ARCHITECTURE`, `docs/ROADMAP`, `docs/CONTRIBUTING`, `docs/LLM_PROVIDERS`, `docs/METADATA_SCHEMA`, `docs/PACKAGING`, `docs/PLUGIN_DEVELOPMENT`, `docs/QUICKSTART`, `docs/AI_GUIDE`, and `docs/llm-providers/*` (5 provider guides). Chinese versions use `.zh-CN.md` suffix.
- Each document has a single clear focus: `ARCHITECTURE.md` = system design; `METADATA_SCHEMA.md` = field reference; `ROADMAP.md` = version planning; `README.md` = onboarding/quickstart. Cross-reference via links, never duplicate content across files.
- `docs-nocommit/` is local-only (gitignored); design documents and work-in-progress docs go there, not in `docs/`.
- `AI_GUIDE.md` is a **pure link index page** — it contains only navigation tables linking to authoritative docs, never duplicates their content.
- Entry-point code snippets (`[project.entry-points."movie_narrator.plugins"]`) have a single authoritative source: `PLUGIN_DEVELOPMENT.md`. All other files (CONTRIBUTING, QUICKSTART, PACKAGING, AI_GUIDE) must link to it rather than duplicating the snippet.

### CHANGELOG.md
- Pure English only — no mixed languages; translate all Chinese content.
- Use only standard Keep a Changelog categories: `Added` / `Changed` / `Deprecated` / `Removed` / `Fixed` / `Security`.
- Entries must be concise — one bullet per feature/change, no full test case lists, no schema field enumerations, no config constant names.
- Test additions summarized as a single bullet: `- **Tests** (\`tests/test_xxx.py\`): +N tests covering <topics>.`
- `CONTRACT_VERSION` line in `Changed` section: no blank line before it; format: `- \`CONTRACT_VERSION\` remains (X, Y, Z). All NNN tests pass (N skipped in CI, 0 failures). +M new tests vs vX.Y.Z-1.`
- Version comparison links at file bottom must be complete from `[Unreleased]` down to the earliest version.

### ARCHITECTURE.md
- Focuses on system design and component relationships — **not** an API/field reference.
- Metadata field tables belong in `docs/METADATA_SCHEMA.md`; ARCHITECTURE.md links to it with a one-paragraph summary of key domains.
- Must include a global **Component Overview** diagram (ASCII) showing entry points → workflow/contract → pipeline → subsystems → outputs.
- **Cloud Architecture** chapter required (v0.6.x+): deployment modes diagram, task lifecycle state machine, key modules table, REST API endpoints table, design rules.
- Pipeline Overview merges step responsibilities + data flow into a single table; no separate "Data Flow" or "Output Structure" sections (avoid README duplication).
- Web UI section: document only the contract boundary and the "no direct internal import" rule; do not enumerate external package internal modules (`server.py`, `routes.py`, `ws.py`, etc.).

### METADATA_SCHEMA.md
- Organized by **functional domain**: Match → Align → Script → Audio → Render → Quality → TTS cache.
- Top-level JSON structure example lists each key once (no duplicates).
- Each domain section: field table (Field / Type / Description) + relevant semantic notes (back-compat fields, value semantics, mechanics).
- EN (`METADATA_SCHEMA.md`) and ZH (`METADATA_SCHEMA.zh-CN.md`) must stay synchronized.

### SDK Reference (`docs/sdk/*.md`)
- Core content is auto-generated via mkdocstrings `:::` directives — keep English only, do not translate (API reference convention).
- Each page has 1–3 lines of hand-written intro + `:::` directives + a "Related modules" cross-reference section.
- When adding a new public module, create a `sdk/<module>.md` page and add it to `mkdocs.yml` nav.
- `contract.md` renders only `::: movie_narrator.contract`; other modules link to their dedicated pages instead of re-rendering.

### LLM Provider Guides (`docs/llm-providers/`)
- Each provider has an EN version (`xxx.md`) and a ZH version (`xxx.zh-CN.md`), with cross-links in the file header note.
- Unified section template: Introduction → Registration → Configuration → Free Quota → Pros & Cons → TTS Note → FAQ (sections may be omitted if not applicable, but naming must be consistent).
- `LLM_PROVIDERS.md` (EN) links to `xxx.md`; `LLM_PROVIDERS.zh-CN.md` (ZH) links to `xxx.zh-CN.md`.

### README.md
- No Roadmap section (lives in `docs/ROADMAP.md` only).
- Directory tree kept concise (collapsed, no full recursive listing).
- Quick Start streamlined: install → first run → config — no more than ~3 subsections.
- Output section: single representation (tree OR table, not both).
- Configuration priority table: English only, no mixed Chinese terms.
- Features list: concise one-line bullets, no multi-line explanations.
- CLI Options: reference `mn --help` rather than duplicating full option tables.

### ROADMAP.md
- Completed versions condensed into a **summary table** (version | key themes separated by `/`), not detailed per-version sections.
- Planned versions under `## Current & Planned` with `###` for major versions (e.g., v0.7.0) and `####` for patch versions (e.g., v0.7.1).
- "Nearer = more detailed, farther = more general" — immediate next version has full task breakdown; later versions have high-level goals only.
- No mixed milestone and patch version numbering in the same series.
- `CONTRACT_VERSION` noted for versions where the contract surface changes.
- EN (`ROADMAP.md`) and ZH (`ROADMAP.zh-CN.md`) must stay synchronized.

## PyPI 发布流程

> **铁律：本地测试全部通过后才能 push tag。CI 接管后续所有构建和发布。**

### 1. 本地测试

```bash
pytest -v
CI=1 mn create --movie "CI-Test" --style "测试" --duration 10 --keep-cache
```

两步均通过才可继续。

### 2. 更新版本号和 CHANGELOG

```bash
# 版本号唯一来源: pyproject.toml（__init__.py 通过 importlib.metadata 动态读取）
# 编辑 pyproject.toml: version = "X.Y.Z"
# 编辑 CHANGELOG.md: 将 unreleased 段的改动移到新版本标题下
```

### 3. 提交、打 Tag、推送

```bash
git add pyproject.toml CHANGELOG.md
git commit -m "chore: bump version to X.Y.Z"
git tag vX.Y.Z          # 正式发布用不带后缀的 tag
git push origin main --tags
```

**Tag 命名规则（CI 据此决定发布目标）：**

| Tag 格式 | 发布目标 | 示例 |
|----------|----------|------|
| `vX.Y.Z` | **正式 PyPI** | `v0.4.5` |
| `vX.Y.Z-test` | **TestPyPI** | `v0.4.5-test` |

### 4. CI 自动执行（`.github/workflows/publish.yml`）

Push tag 后 CI 自动完成以下步骤，无需本地操作：

1. 构建包（`python -m build`）
2. `twine check` 验证
3. 根据 tag 后缀发布到 TestPyPI 或正式 PyPI（OIDC 可信发布，无需 token）
4. 从 `CHANGELOG.md` 提取对应版本的 release notes
5. 创建 GitHub Release

### 5. 验证

```bash
# 正式版本
pip install --upgrade movie-narrator
# TestPyPI 预发布版本
pip install --index-url https://test.pypi.org/simple/ --extra-index-url https://pypi.org/simple/ --upgrade movie-narrator

mn --help && mn version
CI=1 mn create --movie "CI-Test" --style "测试" --duration 10 --keep-cache
```

### 版本号管理

- **唯一来源**: `pyproject.toml` 中的 `version` 字段
- `src/movie_narrator/__init__.py` 通过 `from importlib.metadata import version` 动态读取，**无需手动同步**
- 同一版本号不可重复上传，需递增

### 注意事项

- 正式发布前可先推送 `vX.Y.Z-test` 到 TestPyPI 验证，确认无误后再推送 `vX.Y.Z` 正式发布
- CI 使用 OIDC 可信发布，本地无需配置 PyPI token
- 预发布 tag（含 `-test`）创建的 GitHub Release 会标记为 prerelease
- **每一步测试失败就暂停，修复后才能继续**

## Git 操作

### 分支模型（简化 Gitflow — Feature + Hotfix）

| 分支 | 用途 |
|------|------|
| `main` | 生产发布，永远可发布 |
| `feature/*` | 功能开发，从 main 切出，PR 合并回 main |
| `hotfix/*` | 紧急修复，按需从对应 tag 切出，PR 合并回 main |

流程：
```
git checkout main && git pull
git checkout -b feature/<name>
# ... 开发 ...
git push origin feature/<name>
# → GitHub PR → main（CI 自动测试）
```

> **不使用 `release/*` 分支**。如需版本冻结或 RC 验证（多人协作场景），可短暂引入。

发布：
```
# 1. bump version in pyproject.toml (唯一版本源)
# 2. update CHANGELOG.md (unreleased → vYYYY.MM.DD[.N])
git commit -m "chore: bump version to YYYY.MM.DD[.N]"
git tag vYYYY.MM.DD[.N]
git push origin main --tags
# → CI 自动构建 + 发布到 PyPI + 创建 GitHub Release
```

紧急修复：
```
git checkout v<problematic-version>
git checkout -b hotfix/<name>
# ... 修复 ...
git commit -m "fix: <description> (vYYYY.MM.DD[.N])"
# PR → main
# 未合并的 feature 分支如受影响，按需 rebase / cherry-pick
```

### 环境配置（Windows）

```powershell
# 设置凭据助手（避免每次 push 弹框）
git config --global credential.helper wincred

# 查看当前配置
git config --global --list | findstr credential
```

### 常用命令

```bash
# 查看状态
git status

# 提交
git add .
git commit -m "feat: your message"

# 推送
git push

# 拉取
git pull

# 查看日志
git log --oneline -5
```

### 提交规范

- `feat:` 新功能
- `fix:` 修复 bug
- `docs:` 文档更新
- `chore:` 构建/工具变更
- `refactor:` 重构

### 注意事项

- Windows 环境不要使用 `export VAR=value command` 语法，直接用原生命令
- 凭据助手配置为 `wincred` 可避免每次 push 弹框
- 版本号唯一来源：`pyproject.toml`；`__init__.py` 通过 `importlib.metadata` 动态读取
- `.gitignore` 已排除：`output/`、`dist/`、`build/`、`*.egg-info`、`__pycache__/`、`.env`、`.claude/`、`.mimocode/`、`CLAUDE.md`