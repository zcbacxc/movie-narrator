# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.4.13] - 2026-07-17

### Added (Core engine production quality)
- **Post-render deliverable QA step** (`validate_deliverable`): new hard pipeline step inserted after `render_video`. Probes the final `final.mp4` with ffprobe (falling back to `ffmpeg -i` when ffprobe is absent) and fails the pipeline on missing/silent audio, missing video stream, duration mismatch, or tiny output. CI runs skip QA by default (fast smoke); local runs enable it by default. Configurable via `qa_enabled` / `qa_max_silence_db` / `qa_min_duration_ratio` / `qa_max_duration_ratio`.
- **Audio normalize + BGM ducking** (`utils/audio_mix.py`): `normalize_peak()` targets a loudness floor; `duck_bgm()` attenuates BGM under narration with windowed envelope + linear attack/release smoothing. The `mix_bgm` skip path now normalizes narration for loudness consistency; the success path ducks then normalizes. MP3 export falls back to WAV when the bundled ffmpeg lacks libmp3lame.
- **Video cover/contain layout** (`utils/video_layout.py`): `compute_fit_box()` returns crop+resize geometry so source footage fills the canvas (cover) or letterboxes (contain). Render applies `cover` by default.
- **Bottom-safe subtitle layout**: `text_image.create_text_image` gains `position="bottom"`, `max_width_ratio`, `bottom_margin_ratio`, `max_lines` with CJK-aware wrapping and ellipsis truncation. Render now always draws subtitle overlays for all segments (including footage-covered ones) using the bottom position by default.
- **Deliverable QA probes** (`utils/deliverable_qa.py`): `probe_media()` + `evaluate_deliverable()` with structured `QAReport` / `QAIssue` output.
- 15 new `JobParams` fields plumbed through schema → merge → runner metadata for render fit/encode/subtitle, BGM duck/normalize, QA, and match drop.
- 44 new unit tests across `test_video_layout`, `test_text_image`, `test_audio_mix`, `test_deliverable_qa`, `test_qa`, extended `test_match` (tiny-scene drop + merge defaults) and `test_bgm` (ducking/normalize + WAV fallback).

### Changed (Production defaults tightened without config)
- **Match defaults**: `match_speed_clamp_min` 0.5 → 0.85, `match_speed_clamp_max` 3.0 → 1.25, `scene_merge_min_duration` 0.0 → 2.0 — keeps pacing publishable. Scenes shorter than `match_drop_scene_min_duration` (default 0.4s) are dropped after merge with re-indexing (last-resort keeps all if every scene would be dropped).
- **Render encode**: CRF 18, preset `slow`, `+faststart` movflag by default for VOD-friendly output.
- Pipeline is now **15 steps** (was 14): `validate_deliverable` inserted between `render_video` and `export_clips`. Frontend `PIPELINE_STEPS` and `STEP_LABELS` synced (`成片质检`).

## [0.4.12] - 2026-07-16

### Changed
- Removed legacy Gradio UI (`src/movie_narrator/web/`, 9 files)
- Removed `gradio>=4.44,<7` from `[full]` extra
- Migrated `test_web_form.py` to `web_api.form`
- Migrated `test_pipeline_cancel.py` from `GradioController` to `TaskController`
- Deleted `test_web_console.py` and `test_web_controller.py` (covered by `test_web_api.py`)

### Added (Web upload hardening)
- Upload size limits: video 2 GB, BGM 50 MB — streaming chunk read enforces limit and deletes partial file on violation (HTTP 413)
- Extension whitelist: video (mp4/mkv/mov/webm/avi), BGM (mp3/wav/m4a/flac/ogg) — rejects unknown extensions (HTTP 415)
- Best-effort cleanup of uploaded source files after task completion (success/failure/cancel)
- 9 new tests covering extension rejection/acceptance, size enforcement, streaming, case-insensitive extensions, cleanup utility, and UploadError status codes

### Fixed (CI)
- Branch protection required check `test` now matches an actual CI job: matrix job renamed to `test-matrix`, new `test` summary job (`needs: [test-matrix]`, `if: always()`) gates on all matrix results — future PRs merge without admin override

## [0.4.11] - 2026-07-16

### Fixed (WebUI deliverable for pip users)
- **Ship React SPA in the wheel**: Vite now builds into `src/movie_narrator/web_api/static/`; `[tool.setuptools.package-data]` includes `static/**`; `server.py` serves package-relative `static/` (works after `pip install`, not only from a git checkout).
- **Track frontend config in git**: root `.gitignore` `*.json` was excluding `webui/package.json` / `tsconfig.json` / `package-lock.json`. Added `!webui/...` exceptions and committed lockfile for reproducible installs.
- **CI / Publish build the frontend**: new `webui` CI job; Publish runs `npm ci && npm run build` before `python -m build`, then asserts the wheel contains `movie_narrator/web_api/static/index.html` + hashed JS/CSS (guards against silent package-data omission).
- **`create_app` version** now uses `movie_narrator.__version__` (was hardcoded `"0.1.0"`).

> `v0.4.10` on PyPI predated the packaging fix — upgrade to **0.4.11** for a usable `mn web` after `pip install "movie-narrator[web]"`.

## [0.4.10] - 2026-07-16

### Added (WebUI rewrite — React + FastAPI)
- **New Web UI** replacing Gradio: React 18 + Vite + TypeScript + shadcn/ui frontend, FastAPI + WebSocket backend
- React SPA with dark OLED theme (slate-900 bg, pink-500 primary, blue-600 accent), Inter font, Lucide icons
- Form sections: Movie info, Voice, Assets (video/BGM upload), Subtitles, Advanced (research/strict/scene-threshold/etc.)
- Real-time pipeline progress via WebSocket: 14-step timeline with active/done/failed/skipped states, auto-scrolling log stream
- Video player with inline playback (new `/api/video/{id}` endpoint), artifact zip download
- Cooperative task cancellation via DELETE `/api/tasks/{id}` or WS cancel message
- Task state preserved across "New Run" — form values survive reset
- 22 backend unit tests (WebSocketConsole, TaskController, TaskManager, Pydantic models, form validation, utils)

### Fixed (security + robustness)
- **ws.py**: `json.JSONDecodeError` from malformed client messages no longer kills the WebSocket connection
- **utils.py**: `save_upload()` strips directory components from uploaded filenames to prevent path traversal (e.g. `../../etc/passwd`)
- **tasks.py**: Dead code `TaskInfo.update_step()` and `TaskManager.update_step()` removed; `to_status_dict()` now reads `current_step` from `console.snapshot()`

### Changed
- CLI port changed: 7860 (Gradio) → 8760 (FastAPI)
- `[web]` extra: `gradio` → `fastapi` + `uvicorn[standard]` + `python-multipart` (Gradio retained in `[full]` for legacy `web/` module)

## [0.4.9] - 2026-07-16

### Added
- **WhisperX scene captioning**: `match_clips` now transcribes the video's audio track with WhisperX (when `[ml]` extra is installed) and uses the real dialogue text as scene labels for embedding matching. Previously, scenes used fake labels like `"scene 0 from 0.0s to 5.2s"` — essentially random for semantic matching. Transcript results are cached per video file hash (`transcript_<hash>.json`) to avoid re-transcription on re-runs. Falls back to fake labels when WhisperX is unavailable or transcription fails.

### Fixed (quality audit — fake data / silent failures)
- **match.py**: `score < min_score` no longer drops the segment — falls back to heuristic instead of silently losing video footage for that narration segment (30~70% random clip loss).
- **match.py**: WhisperX scene captioning fallback unified — both "not installed" and "transcription failed" now produce `inline_warn` (was silent / debug-level).
- **script.py**: Removed silent `MOCK_SEGMENTS` fake movie fallback for real users. LLM failure after all retries now raises `RuntimeError` with diagnostic message. CI environment (`CI=1`) retains mock fallback with explicit `inline_warn` to allow full pipeline testing.
- **align.py**: WhisperX alignment switched from index-based (1:1) to time-overlap matching. Index-based caused drift on long videos when WhisperX produced a different number of segments than the script (silence/music sections).
- **render.py**: `VideoFileClip` open failure upgraded from `debug` to `inline_warn` with clear message: "Falling back to text-only video — no footage will be shown."

### Refactored (code audit optimizations)
- **utils/sanitize.py**: Extracted `sanitize_filename()` to shared module (was duplicated in `cli.py` and `web/utils.py`).
- **runner.py**: Added module-level assertion enforcing `SOFT_STATUS_STEPS` ↔ `STATUS_FIELD_FOR_STEP` key consistency.
- **match.py**: `SentenceTransformer` model loading cached via `lru_cache` (was reloaded twice per `match_clips` call, saving 1-3 seconds).
- **subtitle.py**: SRT files now written with UTF-8 BOM (`utf-8-sig`) — prevents CJK mojibake on older players that default to system ANSI.
- **resolve.py**: Added `elif` + comments to make `--video` vs `--library-dir` fallthrough explicit.

### Fixed (QA audit — cache correctness + test coverage)
- **match.py**: Transcript cache key now includes `model_name` and `language` (was file-hash only; switching model/language silently reused wrong transcript).
- **align.py**: Empty WhisperX segments now produces `inline_warn` instead of silently returning success with no alignment done.
- **script.py**: Unified CI detection via `is_ci()` from `tts/base.py` (was duplicating `os.environ.get("CI")` logic).
- Added 10 new tests covering: score < min_score heuristic fallback, cache write/hit/corrupt recovery, cache key model+language isolation, align midpoint distance, align unequal segment counts, align empty segments warning, render VideoFileClip failure inline_warn, CI mock fallback path, CI mock not used in production.

## [0.4.8] - 2026-07-16

### Fixed
- Documentation: corrected "30 params" to "32 params" across all 5 docs (actual JobParams field count is 32, not 30).
- Documentation: removed inline version annotations (v0.3, v0.3.5, v0.4, v0.4.7) from README and ARCHITECTURE section headers.
- Documentation: added `examples/cli-usage.sh` — dedicated CLI example file (like `.env.example` for env and `job.example.yaml` for yaml).
- Documentation: simplified README CLI section from 18-row table to brief description + link to example file.
- `job.example.yaml`: `subtitle_lang` changed from `en` to `""` and `steps.translate` from `true` to `false` — safe defaults for new users.
- `config.py`: removed `MN_DEFAULT_FORMAT` from `_read_example_env()` fallback template (field was deleted from Settings).

### Changed (env/yaml boundary refactor)
- **Breaking**: `.env` (Settings) now contains ONLY 21 LLM + TTS infrastructure fields. All 32 pipeline behavior params moved to `job.yaml` (params). Previously 60 `MN_*` env vars mixed LLM/TTS + pipeline behavior.
- **Breaking**: `default_format` removed from Settings (was dead code, never read). `library_dir`, `default_bgm`, `research_enabled`, `export_clips_default`, `subtitle_lang`, `subtitle_mode` also removed — these are CLI/YAML fields, not env vars.
- Deleted `defaults.py` — no code constants module. Pipeline modules use inline literals in `ctx.metadata.get()` calls, matching example files.
- `.env.example` rewritten: 21 LLM + TTS vars only (was 60).
- `job.example.yaml` expanded: all 32 params with inline comments.
- Settings `extra="ignore"` added so old `.env` vars don't break on upgrade.

## [0.4.7] - 2026-07-15

### Added (v0.4.7 — config system overhaul)
- 33 hardcoded constants promoted to configurable Settings fields with `MN_*` env var support:
  - LLM call tuning: `llm_timeout`, `script_temperature`, `script_max_tokens`, `script_retries`, `script_retry_delay`, `research_temperature`, `research_max_tokens`, `translate_max_tokens`
  - TTS: `tts_max_concurrent`, `tts_audio_format`, `tts_audio_bitrate`
  - WhisperX: `whisperx_device`, `whisperx_model`, `whisperx_language`
  - Translate: `translate_source_lang`
  - Render: `render_bg_color`, `render_font_size`, `render_output_name`, `render_ffmpeg_timeout`
  - Async: `async_timeout`, `async_max_workers`
  - Match: `embedding_model_name`, `match_speed_clamp_min`, `match_speed_clamp_max`, `scene_merge_min_duration`
  - BGM: `bgm_gain_db`
  - TTS pacing: `tts_pause_ms`
  - Video: `video_sizes` (JSON string)
- YAML config auto-discovery: `--config` not passed → `cwd/job.yaml` → packaged `examples/job.example.yaml` → none. New users can run `mn create --movie X` without creating any config file
- `ensure_user_config()` now reads `.env.example` as single source of truth (was divergent inline template)
- `examples/job.example.yaml` updated with all 14 whitelisted params + inline comments

### Fixed (v0.4.7)
- `translate_chunk_chars` / `translate_chunk_size` were in Settings + YAML whitelist but never copied to `ctx.metadata` by `build_context`; `_translate_via_llm` used hardcoded module constants. User YAML config was silently ignored — now properly connected
- `export_clips.py` hardcoded `libx264` / `aac` codecs instead of using `settings.render_video_codec` / `render_audio_codec`
- `scene_frame_skip` missing from `runner.py` params copy loop — YAML value silently ignored, always fell back to Settings default
- `.env.example` missing 5 Settings fields (lost during reorg): `MN_TTS_CACHE_MAX_MB`, `MN_TTS_PAUSE_MS`, `MN_BGM_GAIN_DB`, `MN_EMBEDDING_MODEL_NAME`, `MN_VIDEO_SIZES`
- `JobParams` model (uses `extra="forbid"`) missing 3 fields added to `load.py` whitelist — would cause `AttributeError` when accessed
- `_match_clips_impl` referenced undefined `settings` variable — now uses `get_settings()`

### Changed (v0.4.7)
- `.env.example` verified: 60 Settings fields = 60 `MN_*` env vars (perfect match)
- `runner.py` params copy loop now includes all 12 parametric fields (2 others handled separately in metadata init)
- `merge.py` `_STYLE/DURATION/FORMAT_DEFAULT` documented as typer sentinels (not user-configurable Settings)

## [0.4.6] - 2026-07-15

### Fixed
- render_video: `temp_audiofile` extension now derived from `audio_codec` (`.aac` not `.wav`) to prevent AAC-in-WAV mismatch causing silent final.mp4 output

## [0.4.5] - 2026-07-14

### Changed
- Version bump to reflect accumulated documentation and robustness changes since 0.4.2

## [0.4.2] - 2026-07-14

### Added
- Preflight LLM/TTS validation (`pipeline/preflight.py`): probes LLM connectivity (1-token completion) and TTS provider construction before any pipeline step runs. CI mode skips LLM probe. `PreflightError` extends `ConfigError` with remediation hints
- Step-level retry mechanism: `StepAction` enum (RETRY/SKIP/ABORT) in `pipeline/errors.py`; runner wraps step execution in retry loop; `--retry` CLI flag enables `InteractiveCLIController` that prompts [R]etry / [S]kip / [A]bort on hard step failure. Retry preserves ctx state (TTS cache, partial results). Backward compatible — controllers without `on_step_error` get ABORT
- Auto-create `~/.movie-narrator/.env` on first run: `ensure_user_config()` in `config.py` writes default template (27 fields) if file is missing. Never overwrites existing files
- `audioop-lts>=0.2.0` dependency for Python 3.13+ (stdlib `audioop` removed)
- Python 3.13 added to CI test matrix

### Changed
- MoviePy 1.x → 2.x upgrade: `moviepy>=2.0,<3.0` (was `>=1.0.3,<2.0`). API migration in `render.py`: `subclip`→`subclipped`, `speedx`→`with_speed_scaled`, `set_start`→`with_start`, `set_duration`→`with_duration`, `set_audio`→`with_audio`, `ImageClip(transparent=True)`→`ImageClip(is_mask=False)`
- Gradio constraint widened from `>=4.44,<5` to `>=4.44,<7` for Python 3.13+ compatibility
- `whisperx` and `sentence-transformers` gated with `python_version < "3.14"` (torch lacks 3.14 wheels)
- Python 3.13 classifier added to `pyproject.toml`
- `export_clips.py`: direct `subprocess.run(["ffmpeg", ...])` — now documented as a design choice (not a MoviePy 1.x workaround), since export_clips only does seek+cut+encode
- `.env.example` reorganized into clear sections matching `config.py` field set
- README.md: document auto-creation behavior and Python 3.13+ `[ml]` extras note
- `errors.py` description updated: now contains `PipelineStrictError`, `PipelineCancelled`, `RunController`, `StepAction`

### Fixed
- `test_render_real.py`: mock API names updated for MoviePy 2.x (`subclipped`, `with_speed_scaled`, `with_start`, `with_duration`, `with_position`, `with_audio`, `resized`)

## [0.4.1] - 2026-07-14

### Added
- MiMo TTS provider (`tts/mimo_provider.py`): Xiaomi MiMo TTS via OpenAI-compatible `chat.completions` API. Three models supported (all limited-time free):
  - `mimo-v2.5-tts`: Named voice (e.g. "Chloe") with optional style prompt (`MN_MIMO_STYLE_PROMPT`)
  - `mimo-v2.5-tts-voiceclone`: Voice cloning from audio file (base64 data URI, cached per path)
  - `mimo-v2.5-tts-voicedesign`: Voice design from text description
- New Settings: `mimo_tts_model`, `mimo_api_key` (falls back to `llm_api_key`), `mimo_base_url` (default `https://api.xiaomimimo.com/v1`), `mimo_style_prompt`
- MiMo registered in `PROVIDER_CACHE_VERSIONS` and `TTSProviderType` enum
- Tests: 11 new MiMo cases (constructor, credential fallback, named voice mode, voiceclone encoding + cache, voicedesign mode, unsupported model error, missing file error, factory, settings defaults, env prefix)

### Changed
- `pipeline/tts.py`: cache key `model` field now resolves `mimo_tts_model` when provider is MiMo
- MiMo provider converts wav→mp3 internally (API returns wav; pipeline expects mp3)
- Roadmap restructured: Plugin/SDK/Extension moved from v0.4 to new v0.5 (Ecosystem); v0.4 retitled "TTS Abstraction & Infrastructure"; added v0.6 (Cloud) long-term direction
- v0.5 design goal added: freeze public API surface before Cloud features depend on it
- Provider env-var naming convention documented (`MN_<PROVIDER>_TTS_MODEL` / `_API_KEY` / `_BASE_URL`)
- `MN_DEFAULT_VOICE` documented as cross-provider voice setting in v0.4 env vars; README descriptions updated from "Edge-TTS voice" to provider-agnostic wording

## [0.4.0] - 2026-07-14

### Added
- TTS abstraction layer (`src/movie_narrator/tts/`): `TTSProvider` protocol, `BaseTTSProvider` with CI silent fallback, `EdgeTTSProvider`, `OpenAITTSProvider`, factory, and `TTSCacheKey` with two-level fan-out cache layout
- `TTSProviderType` enum in `config.py`: `edge` (default) and `openai`
- New Settings fields: `tts_provider`, `openai_tts_model` (default `tts-1`), `openai_tts_api_key` (falls back to `llm_api_key`), `openai_tts_base_url` (falls back to `llm_base_url`)
- `ConfigError` in `utils/errors.py` — cross-cutting config-error class for missing credentials, invalid voice, unsupported provider
- `is_ci()` single source of truth for CI detection in `tts/base.py` (replaces scattered `os.getenv("CI")`)
- `PROVIDER_CACHE_VERSIONS` dict in `tts/cache.py` — extensible per-provider cache version mapping (Open/Closed Principle)
- `tts_provider` field in `metadata.json` output
- `.env.example` updated with v0.3 subtitle and v0.4 TTS keys

### Changed
- `pipeline/tts.py`: refactored to use `tts/` package. Removed module-level `DEFAULT_VOICE = get_settings().default_voice` (triggered Settings at import time). `generate_voice` now reads Settings lazily at function-call time
- CI silent fallback: synthesize to temp path (`output/.ci_<hash>.mp3`), probe, then delete — silent-audio files never enter cache, preventing pollution of subsequent non-CI runs
- Cache key upgraded from md5 to sha256 with 7 dimensions (`schema_version`, `provider`, `provider_version`, `model`, `voice`, `text`, `pause_ms`). `CACHE_SCHEMA_VERSION = 2` — first v0.4 run regenerates all TTS audio
- CI duration calibration: `_EST_CHARS_PER_SEC = 10.0` (was 2.86) — closer to real speech rate
- OpenAI TTS voice whitelist validation (`alloy`, `echo`, `fable`, `onyx`, `nova`, `shimmer`); Edge-TTS does not validate (lets API report)
- OpenAI SDK lazy import in `OpenAITTSProvider.__init__` — keeps startup lighter and allows future optional packaging

### Tests
- `tests/test_tts_providers.py` (47 cases): TTSCacheKey, cache_path_for, is_ci, _estimate_duration_s, BaseTTSProvider CI routing, EdgeTTSProvider delegation, OpenAITTSProvider constructor/voice validation, factory, Settings TTS fields, ConfigError, metadata_export tts_provider

## [0.3.5] - 2026-07-13

### Added
- Web UI: `mn web` launches a local Gradio browser app (requires `pip install "movie-narrator[web]"`). Thin shell over `build_context` + `run_pipeline` — no second implementation. Supports all CLI form fields (movie, style, duration, voice, format, video upload, BGM upload, subtitles, advanced params), cooperative cancel at step boundaries, and best-effort artifact download at all terminal states
- `build_context()` / `run_pipeline(ctx)` split: pipeline orchestrator no longer a 20-param god function. `build_context` handles Settings merge + Services injection; `run_pipeline(ctx, *, controller=None)` runs the 14-step loop. Both CLI and Web share the same entry points
- Cooperative cancellation: `RunController` Protocol + `PipelineCancelled` exception + `check_cancelled()` at step boundaries. `controller=None` (CLI) never fires. Cancel is a distinct terminal path — not warn, not error, does not trip `--strict`
- `Console.cancelled()` method on Console Protocol (`PlainConsole`, `_SilentConsole`, `GradioConsole`)
- New `[web]` optional extra: `gradio>=4.44,<5`
- Tests: `test_pipeline_cancel.py` (6 cases), `test_web_form.py` (16 cases), `test_web_console.py` (9 cases), `test_web_controller.py` (6 cases)

### Changed
- `pipeline/runner.py`: `run_pipeline` signature changed from `run_pipeline(movie, style, ...)` to `run_pipeline(ctx, *, controller=None)`. Callers must use `build_context(...)` first
- `cli.py`: `create` command now calls `build_context` + `run_pipeline` (thin shell). New `web` command added
- `pyproject.toml`: version bumped to 0.3.5; `[full]` extra now includes `gradio>=4.44,<5`

## [0.3.4] - 2026-07-13

### Added
- Multi-language subtitle support (v0.3): `--subtitle-lang` / `--subtitle-mode` plus YAML `subtitle_lang` / `subtitle_mode` / `steps.translate` / `params.translate_*`
- New soft step `translate_subtitles` (LLM provider, pluggable). Failure policy: retry-then-soft-degrade (translate provider returns the original text on chunk failure; warnings surfaced via `metadata.warnings`)
- Three-file subtitle output: `subtitle.srt` (original, invariant) + `subtitle.<lang>.srt` (translated) + `subtitle.bilingual.srt` (original + LF + translation per cue)
- `render_subtitle_path` field picks the overlay track per `subtitle_mode`; `subtitle_path` remains original-only for backward compatibility
- `content_language` / `subtitle_mode` / `translate_provider` / `subtitle_paths` / `warnings` exported to `metadata.json`
- `JobConfigError` raised when `subtitle_mode ∈ {translated, bilingual}` is set without `subtitle_lang`
- Tests: `tests/test_translate.py` covering disabled/skipped/empty/provider-unknown/CI-passthrough/length-mismatch/blank-item paths; subtitle SRT tests extended for translated + bilingual file outputs and render_subtitle_path resolution

### Changed
- 移除 `render.py` 中重复的自定义 tqdm 进度条；MoviePy 内部 `logger="bar"` 接管进度展示（commit `7980ccd`）

## [0.3.2] - 2026-07-13

### Added
- `workflow_dispatch` 手动触发 CI（GitHub Actions UI 可手动跑测试，无需 push）

### Changed
- 控制台日志重构补完：`workflow_steps` 键统一为 step 函数名（`research_plot` / `align_audio` / `detect_scenes` / `match_clips` / `mix_bgm` / `export_clips`）
- `console.done()` 取代裸 `print(f"\nDone in {elapsed}s")`（commit `36436d8`）

## [0.3.1] - 2026-07-13

### Added
- 简化 Gitflow 工作流：feature + hotfix 双分支模型（无 develop / release 长期分支）
- `importlib.metadata` 动态版本读取（消除双写失配）
- TestPyPI 支持（tag 带 `-test` 后缀时自动发测试源）
- CI 自动 PyPI 发布 + GitHub Release 创建
- 控制台日志重构落地：`utils/console.py` + `utils/log.py` + `utils/retention.py` 引入 Console Protocol / AppLogger / `build_console()`；`models.py` 新增 `StepResult` / `StepState` / `Services`
- `runner.py` 统一状态渲染：每步只设置 `ctx.step_state`，runner 负责 console 输出；soft/hard try/except 分叉；`STATUS_FIELD_FOR_STEP` 提升为模块级常量
- MoviePy 临时音频路由到 `output/.tmp/`（避免污染源码目录）
- 14 个 pipeline step 全部迁移：裸 `print()` → `ctx.services.console`；`match.py` 补 try/except
- CI smoke test（`mn create --config` YAML 配置样例测试）

### Changed
- CI 拆分为 ci.yml（PR/push 测试）+ publish.yml（tag 触发发布）

### Fixed
- `runner.py` 补充缺失的 `Dict, Any` import
- 修复 ci.yml 在插入 smoke test 后的 YAML 语法错误

## [0.3.0] - 2026-07-05

### Added
- `mn create --config` 支持 YAML 配置文件
- workflow_steps 和 params 元数据注入
- 控制台日志重构设计

[Unreleased]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.11...HEAD
[0.4.12]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.11...v0.4.12
[0.4.11]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.10...v0.4.11
[0.4.10]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.9...v0.4.10
[0.4.9]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.8...v0.4.9
[0.4.8]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.7...v0.4.8
[0.4.7]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.6...v0.4.7
[0.4.6]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.5...v0.4.6
[0.4.5]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.3...v0.4.5
[0.4.3]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.2...v0.4.3
[0.4.2]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.1...v0.4.2
[0.4.1]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/zcbacxc/movie-narrator/compare/v0.3.5...v0.4.0
[0.3.5]: https://github.com/zcbacxc/movie-narrator/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/zcbacxc/movie-narrator/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/zcbacxc/movie-narrator/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/zcbacxc/movie-narrator/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/zcbacxc/movie-narrator/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/zcbacxc/movie-narrator/compare/v0.2.2...v0.3.0
