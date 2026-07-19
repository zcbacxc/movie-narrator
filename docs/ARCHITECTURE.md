# Architecture

## Pipeline Overview

15-step sequential pipeline orchestrated by `pipeline/runner.py`. Before any step executes, `preflight.py` probes LLM connectivity and TTS provider configuration — failing fast with `PreflightError` instead of silently degrading to mock content.

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → translate_subtitles → generate_subtitle →
render_video → validate_deliverable → export_clips
```

### Step Categories

| Category | Steps | Status |
|----------|-------|--------|
| **Hard** (always run) | resolve_video, prepare_assets, generate_script, export_script_md, generate_voice, render_video, validate_deliverable | Must succeed |
| **Soft** (skip on missing deps) | research_plot, align_audio, detect_scenes, match_clips, mix_bgm, translate_subtitles, export_clips | Skip gracefully / soft-degrade; `--strict` to abort |

### Pipeline Status Model

Each soft step writes to `PipelineStatus` — one of `disabled | skipped | success | failed`:

```python
class PipelineStatus(BaseModel):
    research: StepStatus   # research_plot
    align: StepStatus      # align_audio
    scene: StepStatus      # detect_scenes
    match: StepStatus      # match_clips
    bgm: StepStatus        # mix_bgm
    export: StepStatus     # export_clips
    translate: StepStatus  # translate_subtitles (default: "skipped" — feature off)
```

`translate` is the only soft status whose **default** is `skipped` rather than
`disabled` — "feature off" is semantically distinct from "explicitly disabled
via `steps.translate=false` or unknown provider" (see
`docs/superpowers/specs/2026-07-13-multi-language-subtitle-design.md` §4.1).

## Job config merge layer

Optional declarative job YAML sits **in front of** `run_pipeline`:

```text
CLI flags + optional job.yaml
        ▼
load_job_config (YAML → JobConfig)
        ▼
merge_job (CLI > YAML > Settings → ResolvedJob)
        ▼
run_pipeline(...) # STEPS order unchanged
```

- Module: `movie_narrator.workflow` (`load_job_config`, `merge_job`, `JobConfigError`)
- Soft steps honor `metadata["workflow_steps"][<field>] is False` → `status.<field> = "disabled"`
- Params whitelist (48 keys: scene_threshold, scene_frame_skip, match_min_score, match_speed_clamp_min/max, scene_merge_min_duration, match_drop_scene_min_duration, embedding_model_name, bgm_gain_db, bgm_duck_db, bgm_normalize, audio_target_dbfs, tts_pause_ms, tts_max_concurrent, tts_audio_format, tts_audio_bitrate, translate_source_lang, translate_provider, translate_retries, translate_chunk_chars, translate_chunk_size, research_provider, whisperx_device/model/language, render_fps/video_codec/audio_codec/threads/bg_color/font_size/output_name/ffmpeg_timeout, render_fit_mode/crf/preset/faststart, render_subtitle_position/max_width_ratio/bottom_margin_ratio, qa_enabled/qa_max_silence_db/qa_min_duration_ratio/qa_max_duration_ratio, prompt_target_sentences/prompt_target_segment_duration/prompt_max_chars_per_sentence/prompt_hook_seconds, async_timeout, async_max_workers, video_sizes) land in `ctx.metadata` via `build_context` copy loop
- Multi-language subtitle top-level keys: `subtitle_lang`, `subtitle_mode` (validated in `JobConfig` — `subtitle_mode ∈ {translated, bilingual}` without `subtitle_lang` raises `JobConfigError` at merge time)
- `STEPS` remains the single source of step order; no DAG / plugins in v0.3
- YAML auto-discovery: `--config` not passed → `cwd/job.yaml` → packaged `examples/job.example.yaml` → none
- `.env.example` is the single source of truth for first-run config (read by `ensure_user_config()`, not a divergent inline template)
- Strict env/yaml boundary: `.env` (Settings) = 24 LLM + TTS infrastructure fields only; `job.yaml` (params) = 48 pipeline behavior keys; no code constants module — inline literals match example files

## Web UI Layer

> Since v0.4.10 the Web UI is a **FastAPI + React SPA** architecture (served on port 8760 via `mn web`). The legacy Gradio UI (`src/movie_narrator/web/`) was removed in v0.4.12.

```text
React SPA (webui/) — form / progress / artifacts view
    ▼   REST (POST /api/jobs)  +  WebSocket (/ws/jobs/{id})
FastAPI app (web_api/server.py) — uvicorn on :8760
    ▼
routes.py (form → build_context kwargs)   ws.py (stream console snapshots)
    ▼
build_context(..., services=Services(console=BufferedConsole))
    ▼
run_pipeline(ctx, controller=RunController)   ← background task (tasks.py)
    ▼
TaskManager streams console snapshots over WebSocket → React renders live progress
```

The React SPA is built by Vite into static assets that FastAPI serves directly, so the single `mn web` process owns both the API and the frontend bundle — no separate frontend server is required in production.

### Key design rules

- **No second implementation**: Web calls `build_context` + `run_pipeline` — the same functions CLI uses
- **Cancel is runtime-only**: `RunController` / `PipelineCancelled` never enter `Context`, `PipelineStatus`, or `metadata.json`. Cancel is a distinct terminal path (not warn, not error, does not trip `--strict`)
- **empty = no override**: form fields left blank do NOT inject into `params` — Settings (`.env` / `MN_*`) defaults apply
- **Uploads to a stable dir**: uploaded files go to `output/_uploads` (FastAPI), never to ad-hoc `mn_web_*` temp dirs or the `output/` movie folder
- **Single-job per task**: re-entrancy guard via `TaskManager` (FastAPI) per task id, replacing the old `gr.State`-based `WebRun` session state

### Modules — `web_api/` (FastAPI backend, default)

| Module | Responsibility |
|--------|---------------|
| `web_api/server.py` | FastAPI app factory, static SPA mounting, `launch_web()` uvicorn entry |
| `web_api/routes.py` | REST endpoints — job submit/cancel, artifact listing, form validation |
| `web_api/ws.py` | WebSocket handler — streams `Console.snapshot()` + status deltas to the SPA |
| `web_api/tasks.py` | `TaskManager` — per-task run state, background pipeline execution, cancellation |
| `web_api/console.py` | Thread-safe buffered console (`threading.Lock`) consumed by the WebSocket loop |
| `web_api/controller.py` | `RunController` — cooperative cancel flag (`threading.Event`) |
| `web_api/form.py` | `FormData` model, `validate_form()`, `form_to_context_args()` |
| `web_api/models.py` | Pydantic request/response schemas, run status enums |
| `web_api/utils.py` | Upload handling (`output/_uploads`), `collect_artifacts()`, filename sanitization |
| `web_api/__init__.py` | Package init |

## TTS Abstraction Layer

The `tts/` package decouples TTS backend selection from pipeline orchestration:

```text
pipeline/tts.generate_voice(ctx)
    ▼
tts.factory.get_tts_provider(settings) → TTSProvider
    ▼
provider.synthesize(text, voice, output_path) → writes mp3
    ▼
pipeline probes duration via AudioSegment.from_mp3
```

### Key design rules

- **No second implementation**: pipeline owns cache, concurrency, duration probe, audio merge; providers own audio generation only
- **CI temp-file isolation**: CI synthesizes to `output/.ci_<hash>.mp3`, probes, deletes — silent-audio files never enter cache
- **`is_ci()` single source of truth**: defined in `tts/base.py`, imported by pipeline (no duplicate `os.getenv("CI")`)
- **`PROVIDER_CACHE_VERSIONS` dict**: extensible per-provider cache version (Open/Closed Principle)
- **Credential fallback**: `openai_tts_api_key` → `llm_api_key`; `openai_tts_base_url` → `llm_base_url`; `mimo_api_key` → `llm_api_key`

### Modules

| Module | Responsibility |
|--------|---------------|
| `tts/protocol.py` | `TTSProvider` ABC — `synthesize(text, voice, output_path) -> None` |
| `tts/base.py` | `BaseTTSProvider` (CI silent fallback), `is_ci()`, `_estimate_duration_s()` |
| `tts/edge.py` | `EdgeTTSProvider` — wraps `edge_tts.Communicate` |
| `tts/openai_provider.py` | `OpenAITTSProvider` — wraps sync OpenAI SDK via `asyncio.to_thread`; voice whitelist |
| `tts/mimo_provider.py` | `MimoTTSProvider` — Xiaomi MiMo TTS via `chat.completions`; 3 models (named voice, voice clone, voice design); wav→mp3 conversion |
| `tts/factory.py` | `get_tts_provider(settings)` — settings → provider instance (no singleton) |
| `tts/cache.py` | `TTSCacheKey` dataclass, `cache_path_for()` (two-level fan-out), `PROVIDER_CACHE_VERSIONS` |
| `utils/errors.py` | `ConfigError` — cross-cutting config-error class |

## Data Flow

1. **Context** (`models.Context`) — shared mutable state passed through all steps
2. **resolve_video** — locate source video from `--video`, `--library-dir`, or config
3. **prepare_assets** — validate BGM, font, intro assets exist on disk
4. **research_plot** — LLM fetches movie metadata (title, cast, keywords) → `research.json`
5. **generate_script** — LLM returns JSON → `List[ScriptSegment]`
6. **export_script_md** — renders segments to human-readable `script.md`
7. **generate_voice** — TTS provider (Edge-TTS, OpenAI, or MiMo) async with semaphore + sha256 content-addressable cache (7-dimension key, two-level fan-out) → `narration.mp3` + `List[TimedSegment]`. CI mode uses silent fallback with temp-file isolation.
8. **align_audio** — (optional) WhisperX aligns narration to text → word-level timestamps
9. **detect_scenes** — (optional) PySceneDetect splits source video into `Scene` list
10. **match_clips** — (optional) maps scenes to script segments. Baseline is proportional heuristic matching against the scene span (`source="heuristic"`). When `[ml]` is installed and more than one scene exists, re-rank candidates by multilingual sentence-similarity embeddings (`source="embedding"`); falls back to heuristic on probe/modelfailure → `matches.json`
11. **mix_bgm** — (optional) mixes background music under narration → `final_audio.mp3`
12. **translate_subtitles** — (optional, v0.3) when `subtitle_lang` is set, calls the configured translation provider (default `llm`) per chunk; failure policy is retry-then-soft-degrade (fill with originals, surface a `metadata.warnings` entry). CI passthrough (`CI=1`) copies originals without network. The step produces `ctx.translated_texts` only — no files written. `subtitle_path` invariant is preserved.
13. **generate_subtitle** — pure formatter. Always writes `subtitle.srt` from `timed_segments`. When `translated_texts` is non-empty and length-aligned, additionally writes `subtitle.<lang>.srt` (translated) and `subtitle.bilingual.srt` (cue body `f"{src}\n{dst}"` with explicit LF). Bundles paths into `ctx.subtitle_paths: SubtitlePaths` and resolves `ctx.render_subtitle_path` per `subtitle_mode` (original | translated | bilingual).
14. **render_video** — MoviePy composites: solid background + text overlays (or real footage for matched segments) + audio → `final.mp4` + `metadata.json`. Source footage is fitted to the canvas (cover by default; contain letterboxes). Subtitle overlays are always drawn (bottom position by default) including over footage segments. Encode uses CRF 18 / preset `slow` / `+faststart` by default. Overlay text comes from `ctx.render_subtitle_path`; multi-line cues auto-scale the font (`scale = 1.0 - 0.1 * (line_count - 1)`, clamped to `[0.6, 1.0]`).
15. **validate_deliverable** — (hard) probes `final.mp4` with ffprobe (falls back to `ffmpeg -i` when ffprobe is absent). Fails the pipeline on missing video/audio stream, silent audio (mean volume below `qa_max_silence_db`), duration outside `[qa_min_duration_ratio, qa_max_duration_ratio]`, or tiny output file. CI skips by default; local runs enable QA unless `qa_enabled: false`. Stores `ctx.metadata["qa_report"]`.
16. **export_clips** — (optional) extracts per-segment clips to `clips/` directory

## Output Structure

```
output/<movie>/
├── narration.mp3          # TTS output
├── mixed.mp3        # narration + BGM mix (when BGM enabled)
├── subtitle.srt           # SRT subtitles (original narration; always written)
├── subtitle.<lang>.srt    # translated subtitles (when --subtitle-lang set)
├── subtitle.bilingual.srt # bilingual subtitles (when --subtitle-lang set; cue body "src\ndst")
├── script.md              # human-readable script
├── research.json          # movie research data (when --research)
├── metadata.json          # timings, config, pipeline status, content_language
├── final.mp4              # rendered video
├── matches.json           # scene-to-segment matching (when video provided)
└── clips/                 # per-segment clip files (when --no-clips not set)
```

### `metadata.json` → `match_summary` schema (v1, PR #56)

`match_summary` records the match-quality breakdown for L2 hand-test O9/O10.
Full schema (21 fields + 4 back-compat fields):

| Field | Type | Description |
|-------|------|-------------|
| `version` | int | schema version, currently = 1 |
| `status` | str | "success" / "failed" |
| `segments` | int | total matched narration segments |
| `scenes_in` | int | original scene count (before merge/drop) |
| `scenes_after_merge` | int | scene count after merge, before drop |
| `scenes_after_drop` | int | final scene count after drop |
| `merge_min_duration` | float | short-scene merge threshold (seconds) |
| `drop_min_duration` | float | tiny-scene drop threshold (seconds) |
| `min_score` | float | embedding low-score fallback threshold (default 0.25) |
| `speed_clamp` | [float, float] | speed factor clamp range [min, max] |
| `source_counts` | {embedding, heuristic} | segment count per source |
| `heuristic_ratio` | float | heuristic segment ratio (0.0–1.0) |
| `embedding_ratio` | float | embedding segment ratio (0.0–1.0) |
| `score` | {min,max,avg} \| null | stats for **adopted** embedding scores (excludes fallbacks) |
| `raw_score` | {min,max,avg,n} \| null | stats for **all attempted** embedding scores (includes fallbacks; n=attempts) |
| `speed_factor` | {min,max,avg} \| null | speed factor stats (src_duration / narr_duration) |
| `low_score_fallback_count` | int | segments that fell back to heuristic due to score < min_score |
| `captioning` | {used, usable_label_ratio, cached, language, model} | WhisperX captioning status |
| `embedding_model` | str | embedding model name used |
| `degraded_reason` | str \| null | "fake_captions" / "all_heuristic" / null |
| `diversity` | null | reserved for EP3 |
| **— back-compat —** | | |
| `total` | int | = segments (legacy consumers) |
| `embedding` | int | = source_counts.embedding (legacy consumers) |
| `heuristic` | int | = source_counts.heuristic (legacy consumers) |
| `captions_fake` | bool | = (degraded_reason == "fake_captions") (legacy consumers) |

`score` vs `raw_score`: `score.avg` reflects only "good" embedding hits (adopted);
`raw_score.avg` includes "bad-but-fell-back" scores. If `score.avg=0.85` but
`low_score_fallback_count=5`, the first N hits were accurate and 5 failed to fallback.

### `metadata.json` → align diagnostics (v0.4.18)

| Field | Type | Description |
|-------|------|-------------|
| `status.align` | str | "success" / "failed" / "skipped" — "failed" means fallback to segment-level timestamps (C1 fix) |
| `align_fallback` | bool | True if `whisperx.align()` raised and we fell back to transcript-level timestamps |
| `align_degraded` | bool | True if alignment is degraded (fallback, empty ASR, or single-segment drift) |
| `align_segments` | int | number of WhisperX segments returned |
| `align_backward_skipped` | int | segments that kept TTS estimates because monotonic clamp would have crushed them to 100ms (F4) |

`align_backward_skipped > 0` means some segments' timestamps are TTS estimates
(not WhisperX-aligned) because the wx segment mapped far behind the previous
segment's end. This is preferable to a 100ms flash on screen.

## Extension Points

- **New pipeline step**: append to `STEPS` in `pipeline/runner.py`. Signature must be `(ctx: Context) -> Context`.
- **Swap TTS/renderer/LLM**: replace `pipeline/tts.py`, `pipeline/render.py`, or `utils/llm.py` while keeping the step function signature.
- **New CLI command**: add `@app.command()` in `cli.py`.
- **Frontend / WebUI**: the React SPA lives in `webui/` (Vite + TypeScript + shadcn/ui + Tailwind CSS). Add a route/component under `webui/src/`, talk to the backend through the REST endpoints in `web_api/routes.py` and the WebSocket in `web_api/ws.py`. During development run `cd webui && npm run dev` (Vite dev server proxies API calls to FastAPI on :8760); ship changes by rebuilding the bundle (`npm run build`) so FastAPI serves the updated static assets. See `docs/CONTRIBUTING.md` → *Frontend Development*.

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Flat sequential STEPS list | No event bus or DI container; flow is explicit and inspectable |
| Soft/hard step split | Optional deps (PySceneDetect, WhisperX) don't break core pipeline |
| Content-addressable TTS cache | Avoids redundant API calls; key includes version + pause config |
| `PipelineStatus` model | Every soft step's outcome is introspectable in `metadata.json` |
| `--strict` flag | Turns soft failures into hard aborts for CI or production use |
| `usable_clips` filter in render | Ignores accidental `source="fallback"` rows (construction default) |
