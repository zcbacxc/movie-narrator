# Architecture

## Pipeline Overview

13-step sequential pipeline orchestrated by `pipeline/runner.py`:

```
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → generate_subtitle → render_video →
export_clips
```

### Step Categories

| Category | Steps | Status |
|----------|-------|--------|
| **Hard** (always run) | resolve_video, prepare_assets, generate_script, export_script_md, generate_voice, generate_subtitle, render_video | Must succeed |
| **Soft** (skip on missing deps) | research_plot, align_audio, detect_scenes, match_clips, mix_bgm, export_clips | Skip gracefully; `--strict` to abort |

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
```

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
10. **match_clips** — (optional) heuristic matching maps scenes to script segments → `matches.json`
11. **mix_bgm** — (optional) mixes background music under narration → `final_audio.mp3`
12. **generate_subtitle** — timed segments → `subtitle.srt`
13. **render_video** — MoviePy composites: solid background + text overlays (or real footage for matched segments) + audio → `final.mp4` + `metadata.json`
14. **export_clips** — (optional) extracts per-segment clips to `clips/` directory

## Output Structure

```
output/<movie>/
├── narration.mp3          # TTS output
├── final_audio.mp3        # narration + BGM mix (when BGM enabled)
├── subtitle.srt           # SRT subtitles
├── script.md              # human-readable script
├── script.json            # machine-readable script
├── research.json          # movie research data (when --research)
├── metadata.json          # timings, config, pipeline status
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
