# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

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
