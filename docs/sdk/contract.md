# SDK API Reference

Auto-generated from source docstrings using [mkdocstrings](https://mkdocstrings.github.io/). Build locally with `mkdocs serve`.

## Contract module

The `contract.py` module is the single import surface for external
consumers (web package, plugins, SDK users). It re-exports all public
symbols from internal modules and defines `CONTRACT_VERSION` for
import-time compatibility checks.

::: movie_narrator.contract

## Related modules

The following modules have dedicated reference pages:

- [Models](models.md) — `Context`, `Services`, `PipelineStatus`, `StepState`, etc.
- [Provider registries](registries.md) — `ProviderRegistry`, `register_tts` / `register_vision` / `register_llm` / `register_research`
- [Pipeline runner](pipeline.md) — `build_context`, `run_pipeline`, `StepRegistry`, `STEPS`
- [Presets](presets.md) — `Preset` protocol, `list_presets()`, `get_preset()`
- [TTS](tts.md) — `TTSProvider` ABC, `BaseTTSProvider`
- [Cloud](cloud.md) — `TaskQueue`, `LocalTaskQueue`, `TaskAPIServer`, `WorkerDaemon`
