# Architecture

## Pipeline Overview

14-step sequential pipeline orchestrated by `pipeline/runner.py`:

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → translate_subtitles → generate_subtitle →
render_video → export_clips
```

### Step Categories

| Category | Steps | Status |
|----------|-------|--------|
| **Hard** (always run) | resolve_video, prepare_assets, generate_script, export_script_md, generate_voice, render_video | Must succeed |
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

## Job config merge layer (v0.3)

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
- Params whitelist (`scene_threshold`, `match_min_score`, `research_provider`, `translate_provider`, `translate_retries`, `translate_chunk_chars`, `translate_chunk_size`) land in `ctx.metadata`
- Multi-language subtitle top-level keys: `subtitle_lang`, `subtitle_mode` (validated in `JobConfig` — `subtitle_mode ∈ {translated, bilingual}` without `subtitle_lang` raises `JobConfigError` at merge time)
- `STEPS` remains the single source of step order; no DAG / plugins in v0.3

## Data Flow

1. **Context** (`models.Context`) — shared mutable state passed through all steps
2. **resolve_video** — locate source video from `--video`, `--library-dir`, or config
3. **prepare_assets** — validate BGM, font, intro assets exist on disk
4. **research_plot** — LLM fetches movie metadata (title, cast, keywords) → `research.json`
5. **generate_script** — LLM returns JSON → `List[ScriptSegment]`; writes `script.json`
6. **export_script_md** — renders segments to human-readable `script.md`
7. **generate_voice** — Edge-TTS async with semaphore + content-hash cache → `narration.mp3` + `List[TimedSegment]`
8. **align_audio** — (optional) WhisperX aligns narration to text → word-level timestamps
9. **detect_scenes** — (optional) PySceneDetect splits source video into `Scene` list
10. **match_clips** — (optional) maps scenes to script segments. Baseline is proportional heuristic matching against the scene span (`source="heuristic"`). When `[ml]` is installed and more than one scene exists, re-rank candidates by multilingual sentence-similarity embeddings (`source="embedding"`); falls back to heuristic on probe/modelfailure → `matches.json`
11. **mix_bgm** — (optional) mixes background music under narration → `final_audio.mp3`
12. **translate_subtitles** — (optional, v0.3) when `subtitle_lang` is set, calls the configured translation provider (default `llm`) per chunk; failure policy is retry-then-soft-degrade (fill with originals, surface a `metadata.warnings` entry). CI passthrough (`CI=1`) copies originals without network. The step produces `ctx.translated_texts` only — no files written. `subtitle_path` invariant is preserved.
13. **generate_subtitle** — pure formatter. Always writes `subtitle.srt` from `timed_segments`. When `translated_texts` is non-empty and length-aligned, additionally writes `subtitle.<lang>.srt` (translated) and `subtitle.bilingual.srt` (cue body `f"{src}\n{dst}"` with explicit LF). Bundles paths into `ctx.subtitle_paths: SubtitlePaths` and resolves `ctx.render_subtitle_path` per `subtitle_mode` (original | translated | bilingual).
14. **render_video** — MoviePy composites: solid background + text overlays (or real footage for matched segments) + audio → `final.mp4` + `metadata.json`. Overlay text comes from `ctx.render_subtitle_path`; multi-line cues auto-scale the font (`scale = 1.0 - 0.1 * (line_count - 1)`, clamped to `[0.6, 1.0]`).
15. **export_clips** — (optional) extracts per-segment clips to `clips/` directory

## Output Structure

```
output/<movie>/
├── narration.mp3          # TTS output
├── final_audio.mp3        # narration + BGM mix (when BGM enabled)
├── subtitle.srt           # SRT subtitles (original narration; always written)
├── subtitle.<lang>.srt    # translated subtitles (when --subtitle-lang set)
├── subtitle.bilingual.srt # bilingual subtitles (when --subtitle-lang set; cue body "src\ndst")
├── script.md              # human-readable script
├── script.json            # machine-readable script
├── research.json          # movie research data (when --research)
├── metadata.json          # timings, config, pipeline status, content_language
├── final.mp4              # rendered video
├── matches.json           # scene-to-segment matching (when video provided)
└── clips/                 # per-segment clip files (when --no-clips not set)
```

## Extension Points

- **New pipeline step**: append to `STEPS` in `pipeline/runner.py`. Signature must be `(ctx: Context) -> Context`.
- **Swap TTS/renderer/LLM**: replace `pipeline/tts.py`, `pipeline/render.py`, or `utils/llm.py` while keeping the step function signature.
- **New CLI command**: add `@app.command()` in `cli.py`.

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Flat sequential STEPS list | No event bus or DI container; flow is explicit and inspectable |
| Soft/hard step split | Optional deps (PySceneDetect, WhisperX) don't break core pipeline |
| Content-addressable TTS cache | Avoids redundant API calls; key includes version + pause config |
| `PipelineStatus` model | Every soft step's outcome is introspectable in `metadata.json` |
| `--strict` flag | Turns soft failures into hard aborts for CI or production use |
| `usable_clips` filter in render | Ignores accidental `source="fallback"` rows (construction default) |
