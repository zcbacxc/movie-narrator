# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.5.6] - 2026-07-28

### Added (v0.5.6 — Narrative Quality & External Data)

- **Narrative five-principles + anti-AI-tone** (NA-M1-S1, `prompts.py`): injected five narrative principles (hook, pacing, emotion arc, specificity, variety) and anti-AI-tone guidelines into LLM prompt templates to produce more natural, engaging narration.
- **Platform tone adaptation** (NA-M1-S2, `prompts.py` / `models.py`): new `target_platform` param (`douyin` / `bilibili` / `youtube`) that adjusts tone, pacing, and vocabulary to match platform conventions.
- **Rhythm zone & emotion marking** (NA-M1-S3, `script.py`): plot beats now carry `rhythm_zone` (intro / build / climax / resolution) and `emotion` fields, enabling downstream rhythm-aware matching.
- **Rhythm-zone match scoring** (NA-M1-S3+, `match.py`): match score now includes a soft bonus for scenes whose timeline position aligns with the beat's rhythm zone, improving narrative pacing.
- **Narrator perspective & character anchor** (NA-M1-S4, `script.py` / `models.py`): new `narrator_perspective` (`omniscient` / `character` / `detective`) and `focus_character` params that shift narration viewpoint for creative variety.
- **CLI perspective flags** (NA-M1-S4+, `cli.py`): `--narrator-perspective` and `--focus-character` CLI flags override YAML values.
- **Two-phase script self-check judge** (NA-M1-S5, `script.py` / `errors.py`): after Phase-2 expansion, a judge LLM call evaluates the script against quality criteria; low-quality scripts trigger a retry with feedback.
- **Judge feedback loop** (NA-M1-S5+, `script.py`): on judge rejection, specific quality issues are injected into the retry prompt for targeted correction, improving convergence.
- **Structured movie card** (NA-M2-S1, `research.py` / `models.py`): LLM now produces a structured `MovieCard` (title, year, genre, director, cast, plot summary, themes) instead of freeform text, reducing hallucination and enabling downstream fact verification.
- **TMDB external data source** (NA-M2-S1+, `providers/tmdb.py`): optional TMDB integration for fact verification — cross-checks LLM-generated movie cards against TMDB data; available as `research_provider: "tmdb"` or as an enrichment layer over LLM research.
- **BGM emotion-based selection** (NA-M4-S1, `bgm.py`): when BGM metadata YAML is provided (`bgm_metadata_path`), BGM tracks are selected based on beat emotion labels for mood-appropriate scoring.
- **Emotion-weighted BGM selection** (NA-M4-S1+, `bgm.py`): uses full emotion distribution across all beats (not just dominant emotion) for BGM selection, with energy alignment to match narrative intensity.
- **Render template system** (NA-M6-S1, `render.py` / `models.py`): new `render_template` param provides per-preset styling options (title card text, disclaimer, watermark, slogan, end card); `{movie}` placeholder auto-replaced at render time.
- **Language chain consistency** (R2-NA-LANG, `models.py` / `script.py` / `translate.py`): single `lang` param as source of truth for narration language, propagated consistently through script generation, TTS, and translation.
- **Retryable error codes** (R2-NA-ORCH, `errors.py`): network-type failures (timeout, connection error) now carry `RetryableError` classification, enabling the orchestrator to distinguish transient from permanent failures.

### Fixed (v0.5.6 — TMDB Registration & YAML Alignment)

- **TMDB provider registration** (`pipeline/research.py`): added explicit `from ..providers import tmdb` import at module level to ensure `@register_research("tmdb")` executes at import time. Without this, the tmdb provider was only registered when `enrich_movie_card_with_tmdb` was lazily imported inside `_research_via_llm`, causing `research_provider: "tmdb"` to fail with "unknown provider" because the registry check runs before the lazy import.
- **YAML whitelist comments** (`examples/job.example.yaml`): added 12 missing keys to the params whitelist documentation — `match_skip_intro_sec`, `match_drop_dark_luma`, `match_source_window`, `bgm_loudnorm`, `bgm_metadata_path`, `hook_templates`, `set_pieces`, `target_platform`, `lang`, `render_template`, `narrator_perspective`, `focus_character`.
- **L2 YAML outdated comments** (`examples/l2/job.l2.douyin.yaml`): updated WP1 short-key comments from "不生效" to "已生效"; corrected the false `prompt_target_segment_duration` comment (it is a valid JobParams field, not a load-failure risk).
- **Lang params backward compatibility** (`models.py`): `lang` is only added to params when explicitly set by user, preventing unintended behavior changes for existing configs.

### Changed (v0.5.6)

- `README.md` / `README.zh-CN.md`: updated params count (52→77) and CLI flags count (18→24) to reflect current codebase.
- `examples/cli-usage.sh`: added `--narrator-perspective` and `--focus-character` to the complete parameter list; completed table to 24 entries (added `--output-dir` / `--pause-at`, merged `--no-research` into `--research`).
- `docs/ROADMAP.md` / `ROADMAP.zh-CN.md`: added v0.5.6 narrative quality & external data section.
- `docs/AI_GUIDE.md` / `docs/ARCHITECTURE.md` / `docs/ARCHITECTURE.zh-CN.md`: updated env var count (24→32) and params count (48/52→77) to match current codebase; simplified inline param lists to reference `examples/job.example.yaml`.

### Notes

- `CONTRACT_VERSION` remains `(0, 5, 1)` — no SDK surface changes.
- All 853 tests pass (23 skipped in CI mode, 0 failures).

## [0.5.5] - 2026-07-27

### Added (v0.5.5 — Logging Improvements)

- **Configurable log levels** (`--log-level DEBUG|INFO|WARNING|ERROR`): new CLI option on `mn create`, `mn resume`, and `mn imitate` commands to control file logging verbosity (default: DEBUG).
- **Verbose console mode** (`--verbose`): new CLI flag that mirrors DEBUG-level log messages to the console for real-time debugging.
- **RotatingFileHandler**: log files now rotate at 10MB with 5 backups, preventing unbounded log growth.
- **JSON format logging**: optional structured JSON log lines (`json_format=True` in `build_console`) for ELK/Loki aggregation.
- **Run ID correlation**: each pipeline run generates a short 8-char run ID, prepended to log messages and stored in `metadata.json` for cross-referencing log files.
- **Sub-step timing** (`step_timing`): context manager that logs elapsed time for external API calls — LLM plot beats/expansion, TTS batch synthesis, ffmpeg mux, and LLM research.
- **Services.logger integration**: `AppLogger` instance auto-injected into `ctx.services.logger`, enabling plugins to emit structured log messages without importing a logging framework.
- **error() defaults to `exc_info=True`**: tracebacks are always captured on errors, eliminating silent stack trace loss.

### Changed

- `utils/log.py`: rewrote `AppLogger` with `RotatingFileHandler`, `_JsonFormatter`, `_TextFormatter` (run_id prefix), and configurable level.
- `utils/console.py`: `build_console` accepts `log_level`/`verbose`/`json_format`/`run_id` parameters; `PlainConsole.debug` prints to console in verbose mode.
- `pipeline/runner.py`: `build_context` passes log config to `build_console`; `Services.logger` wired to `AppLogger`.
- `pipeline/script.py`, `pipeline/research.py`, `pipeline/tts.py`, `pipeline/render.py`: added `step_timing` around external calls.
- `utils/metadata_export.py`: `run_id` field added to `metadata.json`.
- `docs/AI_GUIDE.md`: added logging system section; fixed TTS cache key description (`pause_ms` → `style_prompt`).
- `docs/ARCHITECTURE.zh-CN.md`: synced with English version (diversity fields, align diagnostics, cache key).
- `docs/CONTRIBUTING.zh-CN.md`: fixed project structure (`workflow.py` → `workflow/` directory).
- `docs/QUICKSTART.md`: updated version reference to 0.5.5.
- `docs/ROADMAP.md` / `ROADMAP.zh-CN.md`: fixed `@register_provider` → specific decorator names.
- `examples/cli-usage.sh`: removed deprecated `mn web`; added `--log-level`/`--verbose` examples.
- `examples/l2/README.md`: updated WP1 limitations (short keys now effective).
- `examples/l2/job.l2.douyin.no_align.yaml`: deleted (temporary diagnostic, v0.4.27 issue resolved).
- `examples/l2/tools/llm_check.py`: fixed dependency description.
- `.gitignore`: replaced `docs/规划/` with `docs-nocommit/` for local-only docs.

### Notes

- `CONTRACT_VERSION` remains `(0, 5, 1)` — no SDK surface changes.
- All 779 tests pass (23 skipped in CI mode, 0 failures).

## [0.5.4] - 2026-07-27

### Added (v0.5.4 — Quality Uplift)

- **VLM caption provider** (`vision/vlm.py`, Q-M5): real visual scene descriptions via cloud VLM API (GPT-4o, Qwen-VL, etc.). Extracts keyframes from each scene midpoint, sends to OpenAI-compatible vision API, and returns concise captions that unlock embedding re-rank in `match.py`. Gracefully handles individual scene failures with fallback labels. Configured via `MN_VLM_*` environment variables; registered as `"vlm"` provider in `vision_registry`.
- **Multi-candidate horse race** (`race.py` + `mn race` CLI command, Q-P2): runs N variations of the same input with different presets, scores each candidate by pacing density, footage coverage, and duration alignment, then ranks and optionally auto-picks the best. Supports custom preset lists and `--auto-pick` to copy the winner to the output root.
- **Reference video imitation** (`imitate.py` + `mn imitate` CLI command, Q-P7): analyzes a viral narration video's sentence density, cut density, and pacing, then auto-generates a temporary preset and runs the standard pipeline to produce a same-style new narration. Supports `--analyze-only` mode for reference analysis without generation.
- **Layer 0 runbook** (`examples/l2/RUNBOOK.md`, Q-X1~X6): zero-code quality improvement guide covering preset selection, match tuning, render quality, and degradation diagnostics. Includes auxiliary tools for batch comparison and metric extraction.

### Changed

- `cli.py`: added `race` and `imitate` CLI commands.
- `vision/factory.py`: registered `"vlm"` provider in `vision_registry` at import time.
- `ROADMAP.md` / `ROADMAP.zh-CN.md`: added v0.5.4 Quality Uplift section.
- `.env.example`: added `MN_VLM_*` environment variable documentation.
- `examples/job.example.yaml`: documented `vision_captioner` param.
- `README.md`: added `mn race` and `mn imitate` command examples.

### Notes

- `CONTRACT_VERSION` remains `(0, 5, 1)` — no new SDK exports; Q-M5/Q-P2/Q-P7 are CLI-level features, not SDK surface additions.
- All 779 tests pass (23 skipped in CI mode, 0 failures).

## [0.5.3] - 2026-07-27

### Added (v0.5.3 — Hardening)

- **SDK API reference** (`mkdocs.yml` + `docs/sdk/`): mkdocs + mkdocstrings configuration for auto-generating API documentation from source docstrings. SDK reference pages cover `contract.py`, `models.py`, `providers/registry.py`, `pipeline/runner.py`, `tts/`, and `presets/`.
- **Performance benchmark** (`benchmarks/profile_pipeline.py`): per-step profiling script that runs the full 15-step pipeline in CI mode (mock LLM + silent TTS) and reports timing for each step. Supports multiple runs with averaging and JSON output.
- **Quickstart guide** (`docs/QUICKSTART.md`): end-to-end plugin development tutorial covering package creation, installation, testing, all four extension types (step/TTS/LLM/research), and PyPI publishing.
- **Research provider example** (`examples/plugins/research-wiki/`): out-of-tree plugin demonstrating `@register_research` with a Wikipedia REST API-based research provider. Includes comparison table with the built-in `llm` provider.
- **`ResearchInfo` SDK export**: `ResearchInfo` model added to the public SDK surface (`contract.py` + `__init__.py`), enabling research providers to import it directly from `movie_narrator`.
- **`docs` optional dependency** (`pyproject.toml`): `pip install movie-narrator[docs]` installs mkdocs + mkdocstrings for local doc building.

### Changed

- `docs/PLUGIN_DEVELOPMENT.md`: added LLM Providers and Research Providers sections with code examples; updated SDK Surface table with `register_llm`, `register_research`, `llm_registry`, `research_registry`, `ResearchInfo`, and `check_version`; updated stability guarantees to include LLM/Research interfaces.
- `docs/ROADMAP.md`: added v0.5.3 Hardening section.

## [0.5.2] - 2026-07-26

### Added (M5 — Community & Packaging)

- **CLI plugin commands** (`cli.py`): `mn plugin list` (entry_points discovery), `mn plugin discover` (load all plugins), `mn plugin registries` (show all registered steps and providers), `mn plugin version` (show CONTRACT_VERSION).
- **Plugin template** (`examples/plugins/template/`): minimal copy-and-go template with README quick-start guide, demonstrating step registration, provider registration, and version checking.
- **`check_version()` helper** (`contract.py`): import-time version validation for external consumers. Raises `ImportError` with a clear upgrade message when `CONTRACT_VERSION < required`.
- **`ProviderRegistry.info()` method** (`providers/registry.py`): returns structured metadata (name, category, protocol_validated) for each registered provider.
- **Packaging guide** (`docs/PACKAGING.md`): versioning conventions, entry point declarations, CLI plugin commands, publishing workflow for core engine and third-party plugins.

### Changed

- `contract.py`: added `check_version` to `__all__` and public exports.
- `__init__.py`: added `check_version` to SDK surface.
- `ROADMAP.md` / `ROADMAP.zh-CN.md`: added M5 section.

## [0.5.1] - 2026-07-26

### Added (M4 — Provider Migration)

- **LLM registry** (`llm_registry`): `utils/llm.py` migrated to the registry pattern. Built-in `openai` provider registered via `@register_llm("openai")`. External plugins can now register custom LLM providers through the Plugin API.
- **Research registry** (`research_registry`): `pipeline/research.py` migrated to the registry pattern. Built-in `llm` provider registered via `@register_research("llm")`. External plugins can register custom research backends.
- **Protocol validation**: `tts_registry` and `vision_registry` now enforce ABC conformance at `create()` time — factories returning non-`TTSProvider` / non-`VisionCaptioner` instances raise `TypeError`.
- **`PluginContext` extension**: `PluginContext` now includes `llm` and `research` fields, giving plugins access to all four provider registries.
- **SDK exports**: `register_llm`, `register_research`, `llm_registry`, `research_registry` added to `contract.py` and `__init__.py`.
- **`CONTRACT_VERSION` bumped** to `(0, 5, 1)` — backward compatible (new exports only, no removals or signature changes).
- **`llm_provider` setting** added to `config.py` — allows selecting LLM provider by name (default: `"openai"`).

### Changed

- `tts/factory.py`: removed legacy fallback code — provider dispatch is now registry-only.
- `vision/factory.py`: removed legacy fallback code — provider dispatch is now registry-only.
- `providers/registry.py`: added `set_protocol()` method for deferred protocol binding (avoids circular imports); added `contains()` method to `ProviderRegistry`.
- `providers/__init__.py`: exports `llm_registry`, `research_registry`, `register_llm`, `register_research`.
- `workflow/schema.py`: updated `vision_captioner` comment to reflect plugin-based providers.

### Documentation

- Updated `ROADMAP.md` / `ROADMAP.zh-CN.md`: added M4 section.
- Updated `ARCHITECTURE.md` / `ARCHITECTURE.zh-CN.md`: provider registry table now includes LLM/Research; extension points updated.
- Updated `CONTRIBUTING.md` / `CONTRIBUTING.zh-CN.md`: directory tree reflects new provider registrations.
- Updated `AI_GUIDE.md`: directory tree and contract version updated.
- Fixed `http_vlm` references across all docs — `http_vlm` was never merged; vision providers are now extensible via Plugin API.

## [0.5.0] - 2026-07-26

### Added (v0.5 Ecosystem — Plugin API, SDK, Scene Filter, WebUI Split)

#### M1 — Plugin registry infrastructure (#91)

- **StepRegistry** (`plugin_loader.py`): central registry for pipeline step plugins with decorator-based registration (`@register_step`). Enables third-party steps to extend the pipeline without forking (#91).
- **ProviderRegistry** (`providers/registry.py`): unified provider registration system with `@register_provider` decorator. Supports TTS, LLM, and research provider extensions (#91).
- **UnifiedParamSchema** (`schema.py`): single source of truth for job parameters — `PARAM_WHITELIST` is now auto-derived from `JobParams` model fields, eliminating manual sync across 4 files (#91).
- **SDK surface** (`contract.py`, `__init__.py`): public API exports (`list_presets`, `get_preset`) for external consumers and the web package (#91).
- **58 tests** covering registry operations, param schema derivation, and SDK contract (#91).

#### M2 — SDK freeze (#92)

- **Plugin discovery via entry_points** (`plugin_loader.py`): automatic discovery of installed plugins through Python `importlib.metadata` entry points under the `movie_narrator.plugins` group (#92).
- **Services extension** (`models.py`): `Services` model now includes optional `logger` field for structured logging in plugins. `SilentConsole` remains the fallback when not provided (#92).
- **Out-of-tree example plugin** (`examples/plugins/watermark/`): reference implementation showing step + provider registration, entry point declaration, and plugin lifecycle (#92).
- **SDK examples** (`examples/plugins/watermark/`): plugin development reference included in the example plugin README, covering `@register_step`, `@register_tts`, `Plugin` protocol, and entry point declaration (#92).
- **EntryPoint immutability fix**: tests updated to use `MagicMock` for `EntryPoint` on Python 3.11+ where `EntryPoint` objects became immutable (#92).

#### WP6 — Scene filtering (#93)

- **Intro skip** (`pipeline/scene_filter.py`): automatically detects and skips intro/logo sequences at the start of videos using luminance and motion analysis (#93).
- **Dark frame detection** (`pipeline/scene_filter.py`): filters out near-black frames that would waste narration budget on non-content segments (#93).
- **Highlight window** (`pipeline/scene_filter.py`): configurable time-window-based scene prioritization — bias scene selection toward user-specified highlight ranges (#93).
- **5-file param sync** — `scene_skip_intro`, `scene_dark_threshold`, `scene_highlight_window` added to `schema.py`, `base.py`, `load.py`, `merge.py`, `runner.py` PARAM_WHITELIST (#93).

#### WebUI split — Dual repository separation

- **`movie-narrator-web` standalone repo** ([github.com/zcbacxc/movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)): WebUI (FastAPI + React) extracted into a separate GitHub repository and PyPI package. Core engine is now a pure CLI package with no web dependencies (#94).
- **`mn-web` entry point**: standalone launcher for the web UI — `pip install movie-narrator-web` + `mn-web` starts the server (#94).
- **Contract versioning** (`contract.py`): `CONTRACT_VERSION = (0, 5, 0)` semver tuple added. External consumers (movie-narrator-web, plugins) can verify API compatibility at import time (#94).
- **Version alignment**: web package version tracks core engine version (both 0.5.0). Web package declares `movie-narrator>=0.5.0` as dependency (#94).
- **Contract layer preserved**: `contract.py` re-exports `list_presets` and `get_preset` so the web package imports only from the stable public API (#94).
- **Test split**: `TaskController` tests moved to the web repo; core `test_pipeline_cancel.py` retains only `check_cancelled` mechanism tests (#94).

### Changed

- Core `pyproject.toml`: removed `fastapi`, `uvicorn`, `python-multipart` from dependencies. Web extras removed — web is now a separate repo/package (#94).
- `conftest.py`: cleaned up `collect_ignore` — removed `packages`, `node_modules` entries; `webui` retained to guard against phantom directories left on developer machines after the monorepo split (#94).
- `.gitignore`: removed `packages/web/`-specific entries (web code lives in separate repo) (#94).
- `src/movie_narrator/__init__.py`: added `list_presets`, `get_preset`, and `CONTRACT_VERSION` to public exports (#91, #94).

### Fixed

- **faster-whisper fallback** (`pipeline/align.py`): S6 L2+ hand-test revealed WhisperX unavailable in some environments — faster-whisper fallback now enabled for scene captioning (d48cb09).
- **S6 test script cleanup**: one-off realcap test script removed (a86bc18).

## [0.4.27] - 2026-07-25

### Added (EP5 completion + L2+ hand-test materials)

#### EP5 — Cover export & vertical safe area (PR #87)

- **Cover.jpg export** (`pipeline/render.py`): `render_cover_export` param exports the highest-score frame as `cover.jpg` with movie title overlay. Non-blocking — failures log a warning and continue, never aborting the render. Presets `douyin-fast` and `bilibili-long` enable this by default (#87).
- **Vertical safe area** (`pipeline/render.py`): `render_vertical_safe_area` param auto-tightens subtitle margins for 9:16 mobile output — `bottom_margin_ratio ≥ 0.15` and `subtitle_max_width_ratio ≤ 0.82`. Prevents subtitles from being clipped by platform UI overlays (#87).
- **5-file param sync** — `render_cover_export` + `render_vertical_safe_area` added to `schema.py`, `base.py`, `load.py`, `merge.py`, `runner.py` PARAM_WHITELIST. Presets updated with sensible defaults (#87).
- **21 new tests** — cover export logic, vertical safe area clamping, preset injection, param whitelist coverage (#87).

#### L2+ hand-test preparation (PR #86)

- **L2+ checklist** (`docs/checklists/L2_HANDTEST_PLUS.md`): EP-specific objective gates (EP2 beat anchor accuracy, EP4 hook CTR proxy, EP5 cover/vertical, EP6 loudness consistency) + subjective bonus items (#86).
- **Run comparison tool** (`scripts/compare_runs.py`): CLI tool to diff `metadata.json` files across runs — highlights score deltas, source-count changes, and degradation differences. Supports before/after A/B comparison for effect verification (#86).
- **L2+ SOP** (`docs/checklists/L2_PLUS_SOP.md`): step-by-step standard operating procedure for executing the L2+ hand test, including sample selection, run configuration, and pass/fail criteria (#86).

### Changed

- ROADMAP.md: v0.4.26 `EP5 remaining` and `L2+ hand test` items marked complete.

## [0.4.26] - 2026-07-25

### Added (Stage E contract + Effect uplift + EP8/EP9)

#### Stage E — Productization contract (PR #83)

- **E.5 CLI match summary** (`cli.py`): `mn create` now prints a one-line match summary at pipeline end — segment count, embedding/heuristic ratio, average score, and degradation reason if any. Provides immediate visibility without opening `metadata.json` (#83).
- **RS-07 segment duration floor** (`pipeline/render.py`): `_SEG_DURATION_FLOOR = 0.1` prevents division-by-zero in `with_speed_scaled()` when segment duration approaches zero. Eliminates silent overflow that could cover adjacent segments (#83).
- **RS-08 mux timeout whitelist** (`pipeline/render.py`): `_DEFAULT_MUX_TIMEOUT = 600` is now overridable by `render_ffmpeg_timeout` param. Previously the whitelist entry was ignored by a hardcoded 600s in the mux call (#83).
- **RS-09 tmp directory cleanup** (`pipeline/render.py`): `shutil.rmtree(tmp_dir, ignore_errors=True)` replaces single-file cleanup, preventing `.tmp/` empty directory residue on subclip failures (#83).
- **M2 documentation** (`examples/job.example.yaml`): `prompt_target_segment_duration` YAML param path documented as fully connected (`JobParams` + `load` + `merge` + `PARAM_WHITELIST`) (#83).

#### EP4 — Hook templates & set pieces (PR #84)

- **`hook_templates`** (`presets/*`): genre-appropriate scroll-stopping hook sentence templates injected into `EXPAND_PROMPT` via `build_hook_hint()`. Each preset gets 5–8 templates with `{movie}` slot (#84).
- **`set_pieces`** (`presets/*`): named-scene injection into `BEATS_PROMPT` via `build_set_pieces_hint()`. Lets presets seed known set pieces (e.g. "公交车战", "封神桥段") into Phase 1 beat generation (#84).
- **2 new params** — `hook_templates` + `set_pieces` added to whitelist across all 4 files (`schema.py`, `merge.py`, `load.py`, `runner.py`) (#84).

#### EP2 — Beat time anchor (PR #84)

- **Structured beats output** (`utils/prompts.py`): `BEATS_PROMPT` now requests JSON with `act` (1–4) + `approx_ratio` (0–1) per beat, enabling time-anchored scene search (#84).
- **Beat metadata** (`pipeline/script.py`): parses structured beats, stores `ctx.metadata["beats_meta"]` with act + approx_ratio per segment (#84).
- **Time-anchored heuristic** (`pipeline/match.py`): heuristic baseline uses `approx_ratio` as primary time anchor — priority: EP2 beat anchor > EP1 weighted acts > uniform mapping. `match_summary.timeline` records `beat_anchor` mode and `anchored_count` (#84).

#### EP5 — Title card overlay (PR #84)

- **`render_title_card_sec`** (`pipeline/render.py`): overlays centered movie name with 1.4x font size and fade in/out (0.3s). Duration configurable per preset: douyin-fast=1.0s, bilibili-long=1.2s, mainstream-dry=0 (disabled) (#84).

#### EP6 — Audio duck curve & loudnorm (PR #84)

- **Proportional duck curve** (`utils/audio_mix.py`): `duck_bgm` now scales duck depth with narration energy — louder narration segments get deeper ducking, quieter segments allow BGM to rise. Replaces flat `bgm_duck_db` with dynamic curve (#84).
- **`bgm_loudnorm`** (`pipeline/bgm.py`): `normalize_loudnorm()` provides RMS-based loudness normalization as alternative to peak normalization. `bgm_loudnorm: true` in douyin-fast preset for consistent short-form loudness (#84).
- **1 new param** — `bgm_loudnorm` added to whitelist across all 4 files (#84).

#### EP8 — VisionCaptioner abstraction (PR #85)

- **`vision/` package** (new): `VisionCaptioner` ABC (`protocol.py`) with `caption_scenes()` contract; `StubVisionCaptioner` (`stub.py`) returning placeholder labels; `get_vision_captioner()` factory (`factory.py`) dispatching by `vision_captioner` param (#85).
- **EP8 integration** (`pipeline/match.py`): vision captions supplement audio-transcript captions. Stub labels flagged as fake (`is_stub=True`) so existing fake-caption guard behavior is unchanged. Real VLM provider can be plugged in via factory without touching match logic (#85).
- **1 new param** — `vision_captioner` added to whitelist (values: `"none"` default | `"stub"` | future provider names) (#85).
- **15 new vision tests** (`tests/test_vision.py`): ABC contract, stub behavior, factory dispatch, placeholder pattern compatibility (#85).

#### EP9 — Human-in-the-loop pause/resume (PR #85)

- **`PipelinePaused` exception** (`pipeline/errors.py`): carries `completed_step` attribute for resume targeting (#85).
- **State serialization** (`pipeline/runner.py`): `_save_pipeline_state()` serializes `Context` to `pipeline_state.json` (excludes non-serializable `services`); `_load_pipeline_state()` reconstructs `Context` (auto-injects `SilentConsole` via `model_validator`); `_next_step_after()` returns next step name (#85).
- **`--pause-at` CLI option** (`cli.py`): `mn create --pause-at script|match` pauses after the specified step; `mn resume <output_dir>` loads state file, re-injects real console, resumes from next step (#85).
- **`start_step` param** (`run_pipeline()`): skips completed steps when resuming (#85).
- **24 new pause/resume tests** (`tests/test_pipeline_pause.py`): exception, state serialization round-trip, resume from various steps, console re-injection (#85).

### Changed

- **`PARAM_WHITELIST`** (`pipeline/runner.py`): added `vision_captioner`, `bgm_loudnorm`, `hook_templates`, `set_pieces`, `render_title_card_sec` — all 4-file whitelist sync maintained (#83, #84, #85).
- **All 3 presets** (`presets/douyin_fast.py`, `mainstream_dry.py`, `bilibili_long.py`): added `hook_templates`, `set_pieces`, `render_title_card_sec`, `bgm_loudnorm` (douyin only) with genre-appropriate values (#84).
- **`BEATS_PROMPT`** (`utils/prompts.py`): upgraded to request structured JSON output with `act` + `approx_ratio` fields (#84).

### Verified

- Full test suite: 566 passed, 0 failures (CI verified).
- Merge conflict in `runner.py` `PARAM_WHITELIST` resolved: `vision_captioner` (EP8) + `bgm_loudnorm` (EP6) both kept — independent entries, no semantic conflict.

## [0.4.25] - 2026-07-24

### Added (Contract layer — stable API boundary)

- **`contract.py`** (new): single import surface that web_api and future consumers depend on. Re-exports 13 symbols from 4 internal modules (`pipeline.runner`, `pipeline.errors`, `utils.console`, `utils.sanitize`) + defines `PipelineResult` protocol (`runtime_checkable`) formalizing the implicit `Context` duck-typing previously used by web_api (#82).
- **18 new contract tests** (`tests/test_contract.py`): re-export identity (13 tests), `__all__` completeness (2 tests), `PipelineResult` protocol satisfaction (6 tests), web_api import isolation (1 test) (#82).

### Changed

- **`web_api/console.py`**: `from ..utils.console` → `from ..contract` (#82).
- **`web_api/tasks.py`**: `from ..utils.sanitize` + `from ..pipeline.errors` + `from ..pipeline.runner` → unified `from ..contract` (#82).
- **`web_api/utils.py`**: `collect_artifacts` ctx parameter documented as `PipelineResult` protocol (#82).
- **`web_api/form.py`**: `form_to_context_args` docstring documents `PARAM_WHITELIST` contract constraint (#82).

## [0.4.24] - 2026-07-24

### Added (EP3 — Top-K rerank with order-backtrack reuse penalty)

- **EP3 top-K rerank** (`pipeline/match.py`): new `_greedy_topk_assign()` replaces the previous top-1 embedding assignment. For each narration segment, computes top-K candidate scenes via `_cosine_topk()` (O(n) argpartition + sort K winners), then picks the candidate with the highest *adjusted* score — where scenes used in the last `reuse_window` segments get a `reuse_penalty` deduction. This lets a lower-ranked but unused scene win over a recently-used top-1, breaking the "same scene back-to-back" pattern without forcing a hard diversity swap (#80).
- **`MatchedClip.source` expanded** (`models.py`): Literal now includes `"embedding_topk"` (top-K ran) and `"embedding_top1"` (top-K disabled, k ≤ 1), in addition to the existing `"embedding"`, `"heuristic"`, `"scene"`, `"fallback"` (#80).
- **2 new params**: `match_topk` (default 5, 0/1 = top-1 mode) + `match_topk_reuse_penalty` (default 0.15) added to whitelist across all 4 files (`schema.py`, `merge.py`, `load.py`, `runner.py`) (#80).
- **EP3 audit fields** (`match_summary.topk` + `match_summary.source_counts`): `topk.{k, reuse_penalty, topk_count, top1_count}` records the rerank configuration and how many segments used top-K vs top-1. `source_counts.{embedding_topk, embedding_top1}` breaks down the embedding sub-path (#80).
- **9 new EP3 tests** (`tests/test_match.py`): reuse penalty swap, disabled top-1 mode, zero-penalty no-swap, audit fields (topk + top1), `_cosine_topk` unit (sorted descending, k exceeds candidates, empty matrix), `_greedy_topk_assign` unit (#80).

### L2 Hand-Test G2 (2026-07-24)

EP3 cross-movie validation on second feature film (西虹市首富, 4.45 GB comedy):

| Verification | G1 (飞驰人生3) | G2 (西虹市首富) | Result |
|---|---|---|---|
| `embedding_topk` count | 18/18 | 18/18 | ✅ 100% top-K path |
| `top1_count` (degraded) | 0 | 0 | ✅ no top-1 fallback |
| `heuristic_count` | 0 | 0 | ✅ no heuristic fallback |
| `qa_report.ok` | true | true | ✅ no P0 defects |
| `degraded_reason` | null | null | ✅ no degradation |
| `footage_coverage.ratio` | 1.0 | 1.0 | ✅ full coverage |

L2 exit §12.2 §1 "≥2 样片两轮连续无 P0" achieved. See [`docs/checklists/L2_HANDTEST_G2_20260724.md`](docs/checklists/L2_HANDTEST_G2_20260724.md) for full report.

## [0.4.23] - 2026-07-23

### Added (Performance contract closure + audit cleanup)

- **ST-07 TTS cache atomic write** (`pipeline/tts.py`): synthesize to `.partial` then `os.replace()` for atomic commit. Corrupt cache file detection — if `AudioSegment.from_mp3()` fails on a cached file, delete + re-synthesize automatically. Prevents cache poisoning from interrupted writes (#79).
- **ST-08 style_prompt in TTSCacheKey** (`tts/cache.py`): replaced `pause_ms` (not audio-affecting) with `style_prompt` (affects MiMo TTS output) in cache key. `CACHE_SCHEMA_VERSION` bumped 2 → 3, auto-invalidating all old cache entries (#79).
- **AQ-10 bgm_error metadata** (`pipeline/runner.py`): `mix_bgm` failure now writes `ctx.metadata["bgm_error"]` with the error message for downstream audit (#79).

### Changed

- **ST-09 Phase1 max_tokens scaling** (`pipeline/script.py`): `max_tokens = max(settings.research_max_tokens, target_count * 60)`. Prevents JSON truncation on high-segment presets (e.g. douyin 120s → n=36 segments would exceed the old fixed cap) (#79).
- **AQ-07 duck_bgm O(n²) → O(n)** (`utils/audio_mix.py`): replaced pydub chunk slicing + `+` concatenation with numpy array multiplication. Per-sample amplitude envelope applied in one operation. Expected speedup: 300s audio ~53s → <2s (#79).
- **MS-10 min_score comment** (`examples/job.example.yaml`): corrected from "低于此值丢弃" to "低于此值回退 heuristic 基线，不丢弃" — matches actual code behavior (#79).

### Verified (no code change needed)

- **AQ-08 empty ASR status**: `align.py` lines 189-197, 241-248, 286-293 already set `status.align="skipped"` on empty WhisperX/faster-whisper results — not `"success"`.
- **ST-10 CI mock segments**: tests use dynamic `range(n)` segment counts, not fixed — verified across `test_match.py`, `test_render.py`, `test_align.py`.

## [0.4.22] - 2026-07-23

### Added (EP1 — Act-weighted timeline partitioning)

- **EP1 act-weighted match** (`pipeline/match.py`): new `match_timeline_mode="weighted_acts"` partitions scenes into 4 equal-time buckets and assigns narration segments to acts by weight. Default weights `[0.15, 0.25, 0.40, 0.20]` concentrate 40% of segments in act 3 (climax), replacing the flat "fast-forward browse" feel with dramatic pacing. Both heuristic and embedding paths are act-constrained — embedding candidates restricted to act bucket ± adjacent overflow. Falls back to `uniform` when < 8 scenes or < 4 segments (#78).
- **3 new match helpers** (`pipeline/match.py`):
  - `_partition_scenes_by_act()` — divides scenes into N equal-time buckets by `scene.start`
  - `_assign_segments_to_acts()` — allocates segments to acts by weight, counts sum exactly to n_segments
  - `_get_act_candidate_indices()` — returns scene indices for act + adjacent overflow
- **Timeline audit** (`match_summary.timeline`): `{mode, act_weights, segments_per_act}` records which timeline mode was used and how segments were distributed across acts (#78).
- **2 new params**: `match_timeline_mode` + `match_act_weights` added to whitelist across all 4 files (`schema.py`, `merge.py`, `load.py`, `runner.py`) (#78).

### Changed

- **Heuristic loop O(n²) → O(n)**: pre-computed `act_seg_map` dict before the loop instead of `list(enumerate).filter` per segment (#78).
- **`job.example.yaml`**: added `match_timeline_mode` + `match_act_weights` examples + whitelist comment update (#78).

### Verified

- 18 new EP1 tests: 3 unit test classes (partition, assign, candidates) + 6 integration tests (act-constrained heuristic, fallback gating, uniform default, custom weights, src_start distribution).
- Full test suite: 495 passed (+18), 23 skipped, 0 failures.
- User code review verified: gating, empty bucket fallback, `_cosine_top1 < 0` fallback, 4-file whitelist sync, `scene.index` safety in fancy indexing.

## [0.4.21] - 2026-07-23

### Added (Stage D remaining — WP5 pause feedback, ST-06 tail protection, WP7 draft profile)

- **WP5 duration pause feedback** (`pipeline/tts.py`): after TTS assembly, if narration exceeds target `--duration` by >15%, automatically reduces inter-segment pause_ms and rebuilds audio. Writes `metadata.duration_metrics` with `{target_sec, narration_sec, ratio_vs_target, pause_ms_original, pause_ms_applied, adjusted}`. Zero overhead when within target — field is absent, not null. Extracted `_build_audio()` helper to avoid code duplication between initial build and rebuild (#77).
- **WP7 draft profile** (`pipeline/runner.py`): new `render_profile` param (`publish` | `draft`). When `draft`, overrides `render_crf: 28`, `render_preset: ultrafast`, `render_faststart: true` for fast iteration. User-supplied params always take precedence — draft only fills gaps. Configurable via `job.yaml` (#77).
- **ST-06 tail climax protection** (`pipeline/script.py`): `_trim_segments` now locks the last segment (tail climax/outro) in addition to the first 3 hooks, preventing the ending from being trimmed away. Only activates when `target > hook_count + 1 and len(segments) > target + 1` — no behavior change for small target counts (#77).

### Changed

- **`_build_audio()` extraction** (`pipeline/tts.py`): audio assembly logic extracted from inline `generate_voice` into a standalone helper function, used by both initial build and pause-feedback rebuild (#77).
- **`render_profile` added to whitelist** (`workflow/schema.py`, `workflow/load.py`, `workflow/merge.py`): new param aligned across all 4 whitelist files, consistent with PR #72 pattern (#77).
- **`job.example.yaml`**: added `render_profile` example + whitelist comment update (#77).

### Verified

- D-3 AQ-02 (soft-step degraded_steps): confirmed F3 patch in `runner.py` already covers soft-step internal failures — no code change needed.
- Full test suite: 499 passed, 1 skipped, 0 failed (user-verified, 40 min).

## [0.4.20] - 2026-07-23

### Added (Stage D quality consolidation — audit fields, param whitelist, test isolation)

- **WP3 diversity post-processing** (`pipeline/match.py`): `_apply_diversity()` reduces consecutive scene reuse. If a scene index appears more than `match_max_scene_reuse` times (default 2) within a sliding window of `match_diversity_window` segments (default 3), the latest occurrence is swapped to the nearest unused scene. Only `scene_index`/`src_start`/`src_end` are changed — score and source remain unchanged. Configurable via `job.yaml` (#70).
- **WP5 max_chars hard truncation** (`pipeline/script.py`): `_truncate_to_max_chars()` post-processes each segment after LLM expansion. Truncates at the last punctuation mark before `prompt_max_chars_per_sentence` for natural breaks, or hard-cuts at the limit if no punctuation found (#70).
- **WP4 footage coverage gate** (`pipeline/render.py`): calculates what fraction of narration segments have real footage (vs text-only fallback). Writes `metadata.footage_coverage.ratio` + `metadata.footage_coverage.segments_with_footage` / `total_segments`. Warn-only gate — flags low coverage in `_degraded_steps` but does not abort (video already rendered by this point). Configurable via `render_require_footage` + `render_min_footage_coverage` in `job.yaml` (#69).
- **AQ-04 `ensure_final_audio()`** (`pipeline/bgm.py`): unified BGM output normalization across all 4 exit points. Idempotent guard — already-mixed audio is a no-op. Render step additionally calls it as a safety net (#69).
- **WP3 diversity audit log**: `match_summary.diversity.swaps_log` records `[{segment_index, old_scene, new_scene}]` for each swap, enabling downstream consumers to distinguish original embedding scores from post-swap scores (#71).
- **WP5 truncation audit**: `metadata.script_truncated` records `{count, max_chars, details: [{original_len, truncated_len}]}` when truncation occurs. Zero overhead when LLM respects max_chars — field is absent, not null (#71).
- **Metadata export**: `footage_coverage`, `duration_metrics`, and `script_truncated` added to `metadata_export.py` (#69, #71).
- **Audit integration tests** (`tests/test_audit_integration.py`): 4 CI-friendly tests replacing the 50-min L2+ handtest. Verify `diversity.swaps_log` triggers with small scene pool (3 scenes × 10 segments × max_reuse=1) and `script_truncated` populates when LLM exceeds max_chars. Runs in 15 seconds vs 50 minutes (#75).

### Fixed

- **Param whitelist desync** (4 files, 12 params): `schema.py`, `merge.py`, `load.py`, `runner.py` had drifted out of sync over multiple PRs. 8 params silently dropped by runner (`match_diversity_window`, `match_max_scene_reuse`, `render_require_footage`, `render_min_footage_coverage`, `align_backend`, `translate_provider`, `translate_retries`, `research_provider`), 3 dropped by merge (`prompt_target_sentences`, `prompt_max_chars_per_sentence`, `prompt_hook_seconds`), 1 missing from schema (`prompt_target_segment_duration`). `align_backend` was a hard failure in `load.py` (JobConfigError), others were silent drops. All 4 files aligned to union set (#72).
- **Test isolation: `.env` pollution** (`tests/test_tts_providers.py`): `Settings()` reads `.env` via pydantic-settings. Tests asserting defaults (`test_default_tts_provider_is_edge`, `test_mimo_defaults`) failed when `.env` set `MN_TTS_PROVIDER=mimo`. Fixed with `_env_file=None` (#73).
- **Test isolation: faster_whisper probe** (`tests/test_align.py`, `tests/test_cli_debug.py`): tests only patched `align.probe` but `select_align_backend()` uses `_align_backend.probe` (separate import). On machines with faster_whisper installed, backend was auto-selected as faster_whisper instead of whisperx, causing 12 test failures. Fixed by patching both probe references (#74, #76).
- **Test isolation: lru_cache settings** (`tests/test_tts_providers.py`): 5 CI/cache tests calling `generate_voice()` → `get_settings()` inherited cached `.env` mimo config. Fixed with `get_settings.cache_clear()` + `MN_TTS_PROVIDER=edge` env override (#74).

### Changed

- **WP4 render.py docstring**: explicitly documented as "WARN-ONLY gate, not an abort gate" — the video is already rendered by the time coverage is calculated, so the gate can only flag, not prevent (#71).
- **ROADMAP.md**: added v0.4.20 section.

### L2+ Hand-Test Results (2026-07-23)

Stage D audit + PR #72 whitelist trigger verified end-to-end:

| Verification | Result |
|--------------|--------|
| PR #72 whitelist end-to-end | ✅ `max_reuse:1` + `max_chars:3` reached `ctx.metadata` |
| WP5 script_truncated | ✅ `count:18` (all 18 segments truncated from 6-10 chars to 3) |
| WP3 diversity.swaps | 0 (2424 scenes — naturally diverse, not a bug) |
| WP4 footage_coverage | ✅ `ratio:1.0` |
| Total wall-clock | 50 min (PySceneDetect on 130-min movie = 45 min) |

See [`docs/checklists/L2_HANDTEST_20260723.md`](docs/checklists/L2_HANDTEST_20260723.md) for full report.

## [0.4.19] - 2026-07-22

### Added (WhisperX compatibility blocker resolved — faster-whisper backend + L2 hand-test passed)

- **faster-whisper backend** (`pipeline/_align_backend.py`): environment-adaptive backend selection for audio alignment. WhisperX (pyannote VAD → speechbrain → k2-fsa) has no prebuilt Windows CPU wheel and torch 2.8 `weights_only=True` rejects pyannote's omegaconf pickle. faster-whisper (CTranslate2) has none of these dependencies. L2 handtest: `faster-whisper small` transcribes 60s Chinese audio in 1.9s on CPU.
- **`select_align_backend()`**: auto-detects GPU/CPU + OS + importability. GPU or Linux CPU → whisperx (word-level alignment); Windows CPU → faster_whisper (k2-fsa unavailable); neither importable → skip. User can override via `align_backend: whisperx|faster_whisper` in `job.yaml`.
- **`transcribe_with_faster_whisper()`**: shared transcription function for both `align.py` (narration audio) and `match.py` (video audio track). Returns segment-level `{"start", "end", "text"}` dicts.
- **`_remap_segments()` + `_detect_drift()`**: extracted from duplicated code in WhisperX and faster-whisper paths. Thresholds centralized as module constants (`_DRIFT_THRESHOLD`, `_BACKWARD_JUMP_RATIO`, `_MIN_SEGMENT_DURATION`).
- **faster-whisper in `[ml]` and `[full]` extras**: `faster-whisper>=1.0` added alongside `whisperx>=3.0`.
- **`align_backend` param**: added to `job.yaml` whitelist (`merge.py`, `load.py`, `schema.py`).
- **3 new metadata fields**: `align_backend_used`, `align_backend_reason`, `align_backend_attempted`.
- **`status.align` semantics table** in `docs/ARCHITECTURE.md`: documents `success`/`failed`/`skipped`/`disabled` + `align_fallback` flag + `_degraded_steps` membership.
- **CI trigger fix**: feature/hotfix branch pushes no longer trigger CI (only `main` push + PR events). Eliminates duplicate CI runs from push + PR sync.
- **`collect_artifacts` clips collection**: per-segment `.mp4` clips from `export_clips` step are now included in artifact list for Web UI download.

### Fixed

- **WhisperX CPU blocker (align.py)**: `align_audio` now uses `select_align_backend()` instead of hardcoded `import whisperx`. On Windows CPU, automatically falls back to faster-whisper.
- **WhisperX CPU blocker (match.py)**: `_transcribe_video_audio` had a second hardcoded `import whisperx` call site (PR #65 missed it). Added faster-whisper fallback. This was the root cause of `embedding_ratio=0%` — without video audio transcription, all scene captions were placeholders → `fake_ratio=1.0 > 0.7` → forced heuristic match.
- **faster-whisper success path status**: `status.align` now reports `success` (not `failed`) when faster-whisper transcription + remapping succeeds. `align_fallback=True` still marks segment-level only, but the step no longer pollutes `_degraded_steps`. L2 handtest confirmed: `embedding_ratio=1.0` with faster-whisper, output quality is not degraded.
- **CI duplicate runs**: feature branch push + PR sync triggered 2 batches of CI runs (different concurrency groups). Fixed by removing `feature/*` and `hotfix/*` from push trigger.

### Changed

- **3 stale remote branches deleted**: `feature/core-engine-production-quality` (merged via PR #37), `feature/docs-sync-with-code` (merged via PR #51), `release/v0.4.11` (merged via PR #32, version long superseded).
- **`probe()` supports `faster_whisper`**: added to `_HINTS` and `module_names` in `utils/optional_deps.py`.

### L2 Hand-Test Results (2026-07-21/22)

4-PR fix chain resolved the WhisperX compatibility blocker and unlocked O10 (`embedding_ratio > 0`):

| PR | Fix | Key metric change |
|----|-----|-------------------|
| #65 | align.py faster-whisper backend | `align_segments`: 0 → 9 |
| #66 | extract `_remap_segments` + document semantics | (refactor, no behavior change) |
| #67 | match.py faster-whisper fallback | `embedding_ratio`: 0.00 → 1.00 |
| #68 | faster-whisper success path → `success` | `degraded_steps`: `["align_audio"]` → `[]` |

L2 objective exit criteria (O1-O10) **100% achieved** across two films (G1 满江红 + G3 飞驰人生3):
- `align_status: success`, `degraded_steps: []`, `embedding_ratio: 1.00`, `qa.ok: true`

See [`docs/checklists/L2_HANDTEST_20260721.md`](docs/checklists/L2_HANDTEST_20260721.md) for full v1→v4 progression.

## [0.4.18] - 2026-07-19

### Added (Core engine hardening — 8 PRs, L2-ready observability + degradation visibility)
- **`match_summary` full schema (21 fields + 4 back-compat)**: `metadata.json` now records complete match-quality breakdown — `version`, `status`, `segments`, `scenes_in/after_merge/after_drop`, `merge_min_duration`, `drop_min_duration`, `min_score`, `speed_clamp`, `source_counts`, `heuristic_ratio`, `embedding_ratio`, `score` (adopted), `raw_score` (all attempted, with `n`), `speed_factor`, `low_score_fallback_count`, `captioning`, `embedding_model`, `degraded_reason`, `diversity` (reserved). Legacy fields (`total`/`embedding`/`heuristic`/`captions_fake`) preserved for back-compat.
- **`align_backward_skipped` metadata**: count of segments that kept TTS estimates because the monotonic clamp would have crushed them to 100ms (F4 backward-jump detection).
- **Runner `_degraded_steps` for non-exception paths**: soft steps that internally catch exceptions and set `status='failed'` + `step_state.result=WARNING` (e.g. `align_fallback`) are now accumulated into `_degraded_steps` — visible in CLI summary, not just metadata.
- **CI concurrency control**: stale CI runs are cancelled on PR amend + force-push; main-branch pushes run to completion.
- **`docs/ARCHITECTURE.md`**: canonical `match_summary` schema table for L2 hand-test jq queries.

### Fixed
- **C1: `align_fallback` status visibility** — `whisperx.align()` exception now sets `status.align='failed'` (was `'success'`), so users/CLI/metadata all see the alignment degradation. Remapping still runs (segment-level timestamps > TTS estimates).
- **F4: align backward-jump crushing** — segments mapping far behind `prev_end` (>50% of original duration) are now skipped (TTS estimate kept) instead of being clamped to a 100ms flash on screen.
- **MS-01: 0-scene fallback** — `ContentDetector` returning 0 scenes now synthesizes one full-length Scene + sets `scene_detection_degraded=True` (was silent empty list → text-only video).
- **MS-02: fake caption detection** — `_build_scene_captions` returns `List[Tuple[str, bool]]` with explicit `is_fake` flag, replacing fragile `label.startswith("scene ")` string heuristic. Forces heuristic match when >70% of captions are placeholders.
- **AQ-01: align drift detection** — single-segment WhisperX output with duration drift >50% is skipped (was silently accepted as "success").
- **AQ-05: volume_unknown fail-closed** — audio stream present but `volumedetect` failed now reports `volume_unknown` issue (was silently skipping silence check).
- **M1: align comment accuracy** — C1 comment no longer falsely claims `_degraded_steps` accumulates; accurately describes F3's runner upgrade.

### Changed
- **B3: 100ms segment floor documented** — `align.py` now explains why 100ms (minimum audible word duration; doesn't skew QA duration ratio which compares total length, not per-segment).
- **B5: silent except blocks now log** — `scenes.py` + `runner.py` best-effort `try/except: pass` blocks now emit `console.debug()` so disk-full/readonly failures are visible in verbose logs.
- **CI: `cancel-in-progress` only for PR events** — main-branch pushes represent release-ready verification and run to completion.

## [0.4.17] - 2026-07-18

### Added (Dynamic sentence count + L2 E2E tests)
- **Dynamic sentence count by duration (方案 B)**: `generate_script()` now computes `n = round(duration / prompt_target_segment_duration)` when preset defines `prompt_target_segment_duration`. Longer videos get more sentences, not longer sentences — keeping per-sentence length in the natural 19-25 char range. At 60s, dynamic count equals preset's `prompt_target_sentences` (backward compatible).
- **New preset field**: `prompt_target_segment_duration` added to all three presets (douyin=3.3s, mainstream=5.0s, bilibili=7.5s). Added to `ALLOWED_PARAM_KEYS` + `PARAM_WHITELIST`.
- **`script_target_count` metadata**: records the calculated target count n, distinguishing "requested 16" from "got 16" for debugging.
- **L2 automated E2E smoke tests**: CI-runnable end-to-end pipeline tests that verify the full `generate_script` → `synthesize_tts` → render chain contract. See Added section below.

### Fixed (max_chars correction based on R5b real TTS data)
- **max_chars_per_sentence corrected** based on R5b measured speech rate 3.8 chars/sec:
  - `mainstream-dry`: 18 → 22 (5.0s × 3.8 = 19.0 chars, 16% margin)
  - `bilibili-long`: 22 → 32 (7.5s × 3.8 = 28.5 chars, 12% margin)
  - `douyin-fast`: 15 (unchanged, 3.3s × 3.8 = 12.5 chars)

### Changed
- Preset `max_chars` comments now document the R5b speech-rate calculation.
- `generate_script` priority: `seg_duration > 0` → dynamic count; `base_count is int` → fixed count; else → default 18.

## [0.4.16] - 2026-07-17

### Added (Two-phase script generation + CLI/Config improvements)
- **Two-phase script generation**: `generate_script()` now splits into Phase 1 (plot beat extraction at low temperature) + Phase 2 (beat expansion at moderate temperature) + fallback trim. Decouples count control from style expression, making `prompt_target_sentences` actually enforceable.
- **First-run config notice**: `ensure_user_config()` now prints a one-time informational message to stderr when creating `~/.movie-narrator/.env`, telling the user which fields to edit. Non-interactive (no prompt), CI mode skips the notice.
- **CLI help improvements**: `no_args_is_help=True` (bare `mn` now shows help instead of "Missing command"), `rich_markup_mode="rich"` for colored output, all 9 commands now have bilingual (中文/English) help text and docstrings.
- **`-h` conflict resolved**: `web` command's `--host` no longer binds `-h` short option, freeing `-h` for `--help` across all commands.
- New config field: `script_expand_temperature=0.5` (Phase 2 temperature, separate from `script_temperature=0.7`).

### Fixed
- **Phase 1 None silent conversion**: `str(None)="None"` was truthy and passed filtering, producing meaningless "None" beats. Now explicitly filters None/empty/"None" strings.
- **Phase 2 empty text segments**: Whitespace-only segments were accepted, would produce silent TTS audio. Now strips and filters.
- **Retry failure debug logging**: Last error now logged via `console.debug()` before raising RuntimeError.

### Changed
- `prompts.py`: Added `BEATS_PROMPT` (Phase 1) and `EXPAND_PROMPT` (Phase 2). `SCRIPT_PROMPT` retained for backward reference.
- `config.py`: `research_retries=3` + `research_retry_delay=1.5` (Research step now retries, matching script.py pattern).
- `pipeline/runner.py`: `SOFT_STEP_CONSEQUENCES` map — soft-step failures now append human-readable consequence messages. Pipeline-end degradation summary warns about reduced quality.
- `pipeline/tts.py`: Per-segment TTS retry (3 attempts, 1s delay) — single network hiccup no longer kills entire batch.

## [0.4.15] - 2026-07-17

### Added (Narration preset system — Stage 0.5)
- **Pluggable preset framework**: new `src/movie_narrator/presets/` package with `Preset` Protocol, closed-vocabulary validation (`ALLOWED_PARAM_KEYS` + `ALLOWED_PROMPT_TAGS`), and registry. Preset params are the BASELINE; user params (CLI / YAML) always override.
- **Three built-in presets** covering the most popular recap styles:
  - `douyin-fast` (default, backward-compatible): 18 sentences × 3.3s, fast cuts, deep BGM ducking (-10dB), brisk cadence
  - `mainstream-dry`: 12 sentences × 5s, slow cuts (speed_clamp 0.90–1.05), light BGM (-15dB), measured cadence (谷阿莫/影视飓风 rhythm)
  - `bilibili-long`: 8 sentences × 7.5s, large scene merge (5s), very light BGM (-18dB), languid cadence (粉丝留存型长解说)
- **CLI**: `--narration-preset` / `-p` flag on `mn create`; new `mn preset` command to list presets or show full params/tags (`mn preset mainstream-dry`).
- **YAML config**: `narration_preset` top-level field in `job.yaml` (CLI flag overrides YAML).
- **Web API**: `FormData.narration_preset` field.
- **Prompt shaping**: closed-vocabulary tags (`prompt_cadence` / `prompt_register` / `prompt_connectors`) injected into `SCRIPT_PROMPT` as conditional hints via `build_cadence_hint()`. Three new `JobParams` keys: `prompt_target_sentences`, `prompt_max_chars_per_sentence`, `prompt_hook_seconds`.

### Changed
- **Single-source whitelist**: `PARAM_WHITELIST` frozenset in `runner.py` is now the authoritative param whitelist. `build_context` iterates it instead of a hardcoded tuple. `ALLOWED_PARAM_KEYS` in `presets/base.py` is validated as a subset at registry build time.
- `prompts.py` `SCRIPT_PROMPT` now uses `{target_sentences}`, `{max_chars}`, `{hook_seconds}`, `{cadence_hint}` placeholders instead of hardcoded "15-20 sentences" / "15 characters" / "3 seconds".

### Hand-test findings (2026-07-17 满江红对比实验)
- **Preset prompt tags verified effective**: cadence/register/connectors all produced perceptible style differences (brisk → 61% exclamation sentences; written → literary vocabulary; interjection → interactive calls).
- **Known limitation**: `prompt_target_sentences` constraint is weak — LLM output 18 sentences for all three presets regardless of the 12/8 targets. Style differences were carried by sentence length and rhetoric instead of sentence count. Planned fix: stronger prompt wording + post-processing truncation.

## [0.4.14] - 2026-07-17

### Added (Publishable bottom subtitle)
- **Backdrop bar + thicker stroke**: bottom-subtitle layout (`_render_bottom` in `text_image.py`) now draws a semi-transparent black backdrop bar (65% alpha, 16px horizontal / 12px vertical padding, clamped inside frame) behind the text block, plus a 4px black stroke (was 2px) around white fill — matches the standardized recap style consumed by short-video platforms and stays legible on bright footage.
- Two new unit tests: `test_bottom_layout_has_semi_transparent_backdrop_bar` (verifies dark pixel band spans ≥50% width in bottom ~22% of canvas) and `test_bottom_layout_full_canvas_outside_textband_is_transparent` (verifies alpha=0 outside subtitle band so source footage is preserved).

### Fixed
- **Empty wrapped list edge case**: `_render_bottom` now returns early when wrapping produces zero lines, instead of computing `line_spacing * (0-1)`.
- **Line height measurement**: each line's height is re-measured via `textbbox` instead of reusing the first line's metric — more accurate for mixed CJK/Latin text.
- **Cross-platform test threshold**: backdrop bar width assertion lowered from 60% to 50% to accommodate Linux CI's narrower font metrics (PIL renders text narrower on Linux than Windows).

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
- **QA duration tolerance**: `qa_max_duration_ratio` default 1.15 → 1.25 — TTS natural output routinely exceeds the target duration by 10-20%; the tighter 1.15 threshold falsely rejected valid renders in hand-testing.
- Pipeline is now **15 steps** (was 14): `validate_deliverable` inserted between `render_video` and `export_clips`. Frontend `PIPELINE_STEPS` and `STEP_LABELS` synced (`成片质检`).

### Fixed (Hand-test P0 bugs on Windows + Python 3.14)
- **Two-stage render** (`render.py`): MoviePy 2.x `write_videofile` on Windows + Python 3.14 raises `OSError [Errno 22] Invalid argument` partway through the rawvideo stdin pipe when encoding video+audio together, leaving `final.mp4` with a corrupted ftyp/mdat layout (no moov atom — ffprobe returns empty, file unplayable). Split into stage 1 (MoviePy writes video-only mp4, stable in isolation) + stage 2 (ffmpeg muxes audio with `-c:v copy` + applies `+faststart` atomically). Eliminates the stdin pipe failure mode entirely.
- **UTF-8 subprocess decoding** (`deliverable_qa.py`): `subprocess.run` with `text=True` alone uses the system locale (GBK on Chinese Windows), which crashed when ffprobe/ffmpeg emitted non-GBK bytes and left stdout/stderr empty — causing `validate_deliverable` to fail every render with false `silent_audio` / `no_audio_stream`. Added `_run()` helper forcing `encoding="utf-8", errors="replace"` so probe parsing always sees real text.
- **`load.py` param whitelist**: 12 production-quality knobs (`render_fit_mode`, `render_crf`, `render_preset`, `render_faststart`, `render_subtitle_position`, `render_subtitle_max_width_ratio`, `render_subtitle_bottom_margin_ratio`, `qa_enabled`, `qa_max_silence_db`, `qa_min_duration_ratio`, `qa_max_duration_ratio`, `match_drop_scene_min_duration`, `bgm_duck_db`, `bgm_normalize`, `audio_target_dbfs`) were missing from `_ALLOWED_PARAMS` — job configs setting these were silently rejected at load time.

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

[Unreleased]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.17...HEAD
[0.4.17]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.16...v0.4.17
[0.4.16]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.15...v0.4.16
[0.4.15]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.14...v0.4.15
[0.4.14]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.13...v0.4.14
[0.4.13]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.12...v0.4.13
[0.4.12]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.11...v0.4.12
[0.4.11]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.10...v0.4.11
[0.4.10]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.9...v0.4.10
[0.4.9]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.8...v0.4.9
[0.4.8]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.7...v0.4.8
[0.4.7]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.6...v0.4.7
[0.4.6]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.5...v0.4.6
[0.4.5]: https://github.com/zcbacxc/movie-narrator/compare/v0.4.2...v0.4.5
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
