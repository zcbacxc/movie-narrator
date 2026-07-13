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
- [ ] Web UI (Gradio / FastAPI)
- [ ] Multi-language subtitle support

## v0.4.x — Extensibility

- [ ] Plugin system for custom pipeline steps
- [ ] Python SDK for programmatic usage
- [ ] Third-party extension support
