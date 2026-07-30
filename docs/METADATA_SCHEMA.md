# Metadata Schema Reference

> `metadata.json` is the audit and diagnostics file written by every pipeline run. This document describes the schema, organized by functional domain. For architecture context, see [ARCHITECTURE.md](ARCHITECTURE.md).

## Top-level structure

```json
{
  "movie": "string",
  "style": "string",
  "content_language": "string",
  "run_id": "string (8-char)",
  "duration_metrics": { ... },
  "script_truncated": { ... },
  "match_summary": { ... },
  "footage_coverage": { ... },
  "align_word_segments": [...],
  "alignment_qa": { ... },
  "match_quality": { ... },
  "subtitle_qa": { ... },
  "translation_glossary": { ... },
  "audio_quality": { ... },
  "bgm_transitions": { ... },
  "video_qa": { ... },
  "quality_dashboard": { ... },
  "qa_report": { ... },
  "beats_meta": [...],
  "warnings": [...],
  "bgm_error": "string (absent on success)"
}
```

---

## Match domain

### `match_summary`

Records the match-quality breakdown for manual QA verification and downstream consumers.

| Field | Type | Description |
|-------|------|-------------|
| `version` | int | Schema version, currently = 1 |
| `status` | str | `"success"` / `"failed"` |
| `segments` | int | Total matched narration segments |
| `scenes_in` | int | Original scene count (before merge/drop) |
| `scenes_after_merge` | int | Scene count after merge, before drop |
| `scenes_after_drop` | int | Final scene count after drop |
| `merge_min_duration` | float | Short-scene merge threshold (seconds) |
| `drop_min_duration` | float | Tiny-scene drop threshold (seconds) |
| `min_score` | float | Embedding low-score fallback threshold (default 0.25) |
| `speed_clamp` | [float, float] | Speed factor clamp range [min, max] |
| `source_counts` | object | Segment count per source (`embedding_topk`, `embedding_top1`, `heuristic`) |
| `heuristic_ratio` | float | Heuristic segment ratio (0.0–1.0) |
| `embedding_ratio` | float | Embedding segment ratio (0.0–1.0) |
| `score` | object\|null | Stats for **adopted** embedding scores (`min`, `max`, `avg`) |
| `raw_score` | object\|null | Stats for **all attempted** embedding scores (includes fallbacks; `n` = attempts) |
| `speed_factor` | object\|null | Speed factor stats (`src_duration / narr_duration`) |
| `low_score_fallback_count` | int | Segments that fell back to heuristic due to score < min_score |
| `captioning` | object | WhisperX captioning status (`used`, `usable_label_ratio`, `cached`, `language`, `model`) |
| `embedding_model` | str | Embedding model name used |
| `degraded_reason` | str\|null | `"fake_captions"` / `"all_heuristic"` / null |
| `diversity` | object | WP3 diversity post-processing audit (`swaps`, `swaps_log`, `window`, `max_reuse`) |
| `timeline` | object | EP1/EP2 timeline audit (`mode`, `act_weights`, `segments_per_act`, `anchored_count`) |
| `topk` | object | EP3 top-K rerank audit (`k`, `reuse_penalty`, `topk_count`, `top1_count`) |

**Back-compat fields** (legacy consumers): `total` = `segments`, `embedding` = `source_counts.embedding`, `heuristic` = `source_counts.heuristic`, `captions_fake` = (`degraded_reason == "fake_captions"`).

**`score` vs `raw_score`**: `score.avg` reflects only "good" embedding hits (adopted); `raw_score.avg` includes "bad-but-fell-back" scores.

**`MatchedClip.source` values**: `"embedding_topk"` (top-K ran, k > 1), `"embedding_top1"` (top-K disabled, k ≤ 1), `"heuristic"` (low-score fallback or no captions), `"scene"` (no embedding model), `"fallback"` (no scenes).

**Reuse penalty mechanics**: when a scene was used in the last `reuse_window` (default 3) segments, its raw cosine score gets a `reuse_penalty` deduction before greedy selection. This lets a lower-ranked but unused candidate win over a recently-used top-1, without forcing a hard diversity swap.

### `match_quality`

Composite match quality aggregation across embedding, rhythm, and diversity dimensions.

| Field | Type | Description |
|-------|------|-------------|
| `avg_composite` | float | Average composite score across all clips |
| `min_composite` | float | Minimum composite score |
| `max_composite` | float | Maximum composite score |
| `low_quality_count` | int | Clips with composite score < 0.4 |
| `diversity_penalty_count` | int | Clips penalized for scene reuse |

### `footage_coverage`

| Field | Type | Description |
|-------|------|-------------|
| `ratio` | float | Fraction of narration segments with real footage (vs text-only fallback) |
| `segments_with_footage` | int | Count of segments with footage |
| `total_segments` | int | Total narration segments |

---

## Align domain

### `status.align` semantics

| Value | Meaning | Timestamps | In `_degraded_steps`? |
|-------|---------|------------|----------------------|
| `success` | Alignment succeeded (word-level or segment-level). Check `align_fallback` to distinguish. | Word-level or segment-level | No |
| `failed` | WhisperX forced alignment raised — fell back to transcript-level timestamps. | Segment-level | Yes |
| `skipped` | ASR returned empty or single-segment drift too large. | TTS-estimated | Yes |
| `disabled` | Neither whisperx nor faster_whisper importable. | TTS-estimated | No (uses `skipped` step result) |

### Align diagnostic fields

| Field | Type | Description |
|-------|------|-------------|
| `align_fallback` | bool | True if alignment is segment-level only (no word-level forced alignment) |
| `align_degraded` | bool | True if alignment is degraded (fallback, empty ASR, or single-segment drift) |
| `align_segments` | int | Number of ASR segments returned by the backend |
| `align_backward_skipped` | int | Segments that kept TTS estimates because monotonic clamp would have crushed them to 100ms |
| `align_backend_used` | str | Actual backend: `"whisperx"` / `"faster_whisper"` / `"none"` |
| `align_backend_reason` | str | Why this backend was selected |
| `align_backend_attempted` | list | Failed backend attempts before fallback |

**`align_backward_skipped > 0`** means some segments' timestamps are TTS estimates (not WhisperX-aligned) because the wx segment mapped far behind the previous segment's end. This is preferable to a 100ms flash on screen.

### `alignment_qa`

| Field | Type | Description |
|-------|------|-------------|
| `low_confidence_count` | int | Segments with confidence < 0.6 |
| `total_segments` | int | Total aligned segments |
| `low_confidence_ratio` | float | `low_confidence_count / total_segments` |

---

## Script domain

### `script_truncated`

Absent (not null) when no truncation occurred — zero overhead when LLM respects `prompt_max_chars_per_sentence`.

| Field | Type | Description |
|-------|------|-------------|
| `count` | int | Number of segments truncated by `_truncate_to_max_chars()` |
| `max_chars` | int | The max_chars limit used |
| `details` | list | `[{original_len, truncated_len}]` for each truncated segment |

### `beats_meta`

Per-beat metadata from structured LLM output. Absent when two-phase script generation is not used.

| Field | Type | Description |
|-------|------|-------------|
| `text` | str | Beat text |
| `act` | int | Act number (1–4) |
| `approx_ratio` | float | Time anchor ratio (0–1) for time-anchored scene search |

**EP2 beat anchor priority**: when beat metadata is available, the heuristic baseline uses `approx_ratio` as the primary time anchor. Priority chain: EP2 beat anchor > EP1 weighted acts > uniform proportional mapping.

---

## Audio domain

### `duration_metrics`

Absent (not null) when no target duration is set — zero overhead when `--duration` is not specified or narration is within 15% of target.

| Field | Type | Description |
|-------|------|-------------|
| `target_sec` | int/float | Target narration duration from `--duration` |
| `narration_sec` | float | Actual narration duration after TTS assembly |
| `ratio_vs_target` | float | `narration_sec / target_sec` |
| `pause_ms_original` | int | Original pause_ms from config |
| `pause_ms_applied` | int | Actual pause_ms used (reduced if adjustment triggered) |
| `adjusted` | bool | Whether pause was reduced to fit target |

### `audio_quality`

Per-segment audio quality metrics and pipeline-level summary.

| Field | Type | Description |
|-------|------|-------------|
| `segments` | list | Per-segment `SegmentAudioMetrics` (clipping ratio, SNR, silence fraction) |
| `summary` | object | Aggregate stats across all segments |

### `bgm_transitions`

| Field | Type | Description |
|-------|------|-------------|
| `zones` | list | Detected emotion zones with start/end timestamps |
| `transitions` | list | Per-zone gain adjustments applied |

**`bgm_error`** (top-level): error message when `mix_bgm` fails; absent when BGM succeeds.

---

## Render domain

### `video_qa`

Video encoding quality validation results.

| Field | Type | Description |
|-------|------|-------------|
| `codec` | str | Detected video codec |
| `resolution` | [int, int] | Video resolution (width, height) |
| `fps` | float | Frame rate |
| `bitrate` | int | Video bitrate (kbps) |
| `audio_codec` | str | Audio codec |
| `issues` | list | Detected quality issues with recommendations |

### `render_profile`

| Field | Type | Description |
|-------|------|-------------|
| `render_profile` | str | `"publish"` (default) or `"draft"` (fast iteration: crf=28, preset=ultrafast) |
| `render_title_card_sec` | float\|absent | Title card duration in seconds; 0 or absent = disabled |

---

## Quality dashboard

### `quality_dashboard`

Cross-step quality score aggregation across 8 dimensions: script, audio, alignment, match, subtitle, translation, deliverable, video_encoding.

| Field | Type | Description |
|-------|------|-------------|
| `overall_score` | float | Weighted average across all dimensions (0–1) |
| `dimensions` | object | Per-dimension breakdown with score, issues, weight |
| `issue_count` | int | Total issues across all dimensions |
| `regression` | object\|null | Comparison with baseline metadata (if provided) |

### `qa_report`

Structured QA report exported as `qa_report.json` and `qa_report.txt` alongside deliverables.

| Field | Type | Description |
|-------|------|-------------|
| `overall_score` | float | Same as `quality_dashboard.overall_score` |
| `dimensions` | object | Per-dimension breakdown |
| `recommendations` | list | Actionable recommendations based on issues |
| `raw_data` | object | Full QA data for programmatic consumption |

---

## TTS cache

**`TTSCacheKey`** includes `style_prompt` (not `pause_ms`). `CACHE_SCHEMA_VERSION` = 3 — all pre-v0.4.23 cache files are automatically re-generated on first run. Atomic write (`.partial` → `os.replace`) prevents corrupt cache files; if a corrupt file is detected at load time, it is deleted and re-synthesized transparently.
