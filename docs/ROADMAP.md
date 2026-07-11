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

- [ ] Research agent for movie plot research
- [ ] WhisperX audio-text alignment
- [ ] Scene detection from movie videos
- [ ] Automatic clip matching based on script
- [ ] Semantic scene search (embedding-based)
- [ ] Background music integration (BGM mixing)

## v0.3.x — Platform & Workflow

- [ ] Workflow DSL for pipeline customization
- [ ] YAML-based pipeline configuration
- [ ] Web UI (Gradio / FastAPI)
- [ ] Multi-language subtitle support

## v0.4.x — Extensibility

- [ ] Plugin system for custom pipeline steps
- [ ] Python SDK for programmatic usage
- [ ] Third-party extension support
