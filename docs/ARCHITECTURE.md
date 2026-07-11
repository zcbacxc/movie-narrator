# Architecture

## Pipeline Overview

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│ generate │ -> │ generate │ -> │ generate │ -> │ generate │ -> │ render   │
│  _script │    │  _voice  │    │ subtitle │    │  _video  │    │  _video  │
│ (LLM)    │    │ (Edge    │    │ (SRT)    │    │ (MoviePy)│    │ (MP4)    │
└──────────┘    │  TTS)    │    └──────────┘    └──────────┘    └──────────┘
                └──────────┘
```

## Data Flow

1. **Context** (`models.Context`) — shared mutable state passed through all steps
2. **Script** — LLM returns JSON, parsed into `List[ScriptSegment]`
3. **TTS** — segments converted to audio with timing, cached by content hash
4. **Subtitle** — timed segments written as SRT
5. **Render** — MoviePy composites background + text overlays + audio

## Extension Points

- Add a new pipeline step: append to `STEPS` in `pipeline/runner.py`
- Custom TTS backend: replace `pipeline/tts.py` implementation
- Custom renderer: replace `pipeline/render.py` implementation
- Custom LLM: update `utils/llm.py` client factory

## Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Context as shared mutable state | Simple sequential pipeline, no event bus needed |
| Content-addressable TTS cache | Avoids redundant API calls, key includes version + pause config |
| Async TTS with semaphore | Controls concurrency to avoid rate limits |
| Silent audio fallback in CI | Smoke tests don't depend on external network |
