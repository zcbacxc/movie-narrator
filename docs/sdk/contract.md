# SDK API Reference

This section is auto-generated from the source code docstrings using
[mkdocstrings](https://mkdocstrings.github.io/). To build the docs
locally:

```bash
pip install mkdocs mkdocstrings[python]
mkdocs serve
```

Then open <http://localhost:8000>.

## Contract module

The `contract.py` module is the single import surface for external
consumers (web package, plugins, SDK users).

::: movie_narrator.contract

## Models

Core data models: `Context`, `Services`, `ResearchInfo`, `StepState`,
`StepResult`, etc.

::: movie_narrator.models

## Provider registries

The `ProviderRegistry` class and global registry instances for TTS,
Vision, LLM, and Research providers.

::: movie_narrator.providers.registry

## Pipeline runner

The `build_context` and `run_pipeline` functions, plus the
`StepRegistry` and `PARAM_WHITELIST` exports.

::: movie_narrator.pipeline.runner
