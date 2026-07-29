# Plugin Development Guide

This guide covers how to write, package, and distribute plugins for
movie-narrator using the v0.5 Plugin SDK.

## Overview

movie-narrator v0.5 introduces a plugin system that allows external
code to extend the pipeline without modifying the core source. Plugins
can:

- **Add pipeline steps** — inject custom processing logic at any point
- **Register TTS providers** — add new text-to-speech backends
- **Register Vision captioners** — add new scene captioning backends

All extensions use the same registry pattern: register at import time,
discover at runtime.

## Quick Start

### 1. Create a plugin class

A plugin is any object with a `name` attribute and a `register` method
that accepts a `PluginContext`:

```python
from movie_narrator import PluginContext, register_step, Context

class MyPlugin:
    name = "my-plugin"

    def register(self, ctx: PluginContext) -> None:
        ctx.steps.register(
            "my_step",
            my_step_func,
            after="render_video",
            soft=True,
        )

def my_step_func(ctx: Context) -> Context:
    # Your custom logic here
    return ctx
```

### 2. Package it with an entry point

In your `pyproject.toml`:

```toml
[project.entry-points."movie_narrator.plugins"]
my-plugin = "my_package:MyPlugin"
```

### 3. Install and discover

```bash
pip install my-plugin-package
```

```python
from movie_narrator import discover_plugins
discover_plugins()  # auto-loads all installed plugins
```

## SDK Surface

The public SDK is importable from `movie_narrator`:

| Symbol | Purpose |
|--------|---------|
| `Context` | Pipeline context model (read/modify during steps) |
| `Services` | Infrastructure container (console, logger) |
| `Plugin` | Protocol your plugin class must satisfy |
| `PluginContext` | Passed to `register()`, provides registry access |
| `load_plugin()` | Manually load a plugin instance |
| `discover_plugins()` | Auto-discover via entry_points |
| `list_available_plugins()` | List available plugins without loading |
| `register_step` | Decorator to register a pipeline step |
| `register_tts` | Decorator to register a TTS provider factory |
| `register_vision` | Decorator to register a Vision captioner factory |
| `register_llm` | Decorator to register an LLM provider factory |
| `register_research` | Decorator to register a research provider factory |
| `step_registry` | Global step registry instance |
| `tts_registry` | Global TTS provider registry instance |
| `vision_registry` | Global Vision provider registry instance |
| `llm_registry` | Global LLM provider registry instance |
| `research_registry` | Global research provider registry instance |
| `ResearchInfo` | Model returned by research providers |
| `check_version` | Import-time contract version validation |

## Pipeline Steps

### Registration

```python
from movie_narrator import register_step, Context

@register_step("my_step", after="render_video", soft=True)
def my_step(ctx: Context) -> Context:
    ctx.metadata["my_step_ran"] = True
    return ctx
```

### Ordering

Plugin steps must declare an insertion point:

- `after="render_video"` — inserted immediately after the named step
- `before="validate_deliverable"` — inserted immediately before the named step

Built-in steps (no `after`/`before`) maintain their fixed order. Plugin
steps without an insertion point are appended to the end.

### Soft vs Hard Steps

- **Soft steps** (`soft=True`): exceptions are caught and rendered as
  warnings. The pipeline continues with degraded output. Requires a
  `status_field` name and a `consequence` message.
- **Hard steps** (`soft=False`, default): exceptions abort the pipeline.

### Built-in Step Names

The 16 built-in steps in execution order:

1. `resolve_video`
2. `prepare_assets`
3. `research_plot` (soft)
4. `generate_script`
5. `export_script_md`
6. `generate_voice`
7. `align_audio` (soft)
8. `detect_scenes` (soft)
9. `match_clips` (soft)
10. `mix_bgm` (soft)
11. `translate_subtitles` (soft)
12. `generate_subtitle`
13. `run_qa_gate` (soft)
14. `render_video`
15. `validate_deliverable`
16. `export_clips` (soft)

## TTS Providers

```python
from movie_narrator import register_tts, Settings

@register_tts("elevenlabs")
def make_elevenlabs(settings: Settings) -> TTSProvider:
    from .elevenlabs import ElevenLabsProvider
    return ElevenLabsProvider(settings)
```

The factory receives the project `Settings` and must return an object
satisfying the `TTSProvider` protocol (with `synthesize` method).

## Vision Captioners

```python
from movie_narrator import register_vision

@register_vision("blip")
def make_blip(**kwargs) -> VisionCaptioner:
    from .blip import BlipCaptioner
    return BlipCaptioner(**kwargs)
```

The factory receives keyword arguments and must return an object
satisfying the `VisionCaptioner` protocol (with `caption_frame` and
`caption_scenes` methods).

## LLM Providers

```python
from contextlib import contextmanager
from movie_narrator import register_llm

@register_llm("anthropic")
def make_anthropic():
    @contextmanager
    def _cm():
        from .anthropic_client import AnthropicLLMClient
        yield AnthropicLLMClient(model="claude-3-opus", api_key=...)
    return _cm()
```

The factory takes no arguments and must return a **context manager**
that yields an `LLMClient`-compatible object (with `.client` and
`.model` attributes). The context manager pattern ensures proper
resource cleanup.

Select the provider via `llm_provider` in `.env` or settings.

## Research Providers

```python
from movie_narrator import Context, ResearchInfo, register_research

@register_research("web_search")
def make_web_search(ctx: Context, settings) -> ResearchInfo:
    # Fetch from your data source (API, database, web scraper, etc.)
    return ResearchInfo(
        title=ctx.movie_name,
        year=2024,
        summary="A custom summary from my provider.",
        genres=["Action", "Drama"],
        cast=["Actor 1", "Actor 2"],
        keywords=["keyword1", "keyword2"],
    )
```

The factory receives `(ctx, settings)` and must return a `ResearchInfo`
instance. The pipeline's `research_plot` step calls this factory and
writes the result to `research.json`.

Select the provider in your job config:

```yaml
params:
  research_provider: web_search
```

See `examples/plugins/research-wiki/` for a complete reference
implementation using Wikipedia's REST API.

## Services

The `Services` container provides infrastructure to steps:

```python
def my_step(ctx: Context) -> Context:
    # Console output (always available)
    ctx.services.console.info("Processing...")

    # Logger (optional, may be None)
    if ctx.services.logger:
        ctx.services.logger.info("my_step started")

    return ctx
```

`Services` fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `console` | `Console` | required | Console output abstraction |
| `logger` | `Optional[Any]` | `None` | Duck-typed logger (`.info/.warning/.error`) |

## Entry Points

Plugins are discovered via the `movie_narrator.plugins` entry point
group. Declare in `pyproject.toml`:

```toml
[project.entry-points."movie_narrator.plugins"]
my-plugin = "my_package:MyPlugin"
```

The entry point value can be:

- A class path (`my_package:MyPlugin`) — instantiated with no args
- A module path (`my_package`) — must have a top-level `plugin` or `Plugin` attribute

## Compatibility Strategy

### What's stable in v0.5

- The `Plugin` protocol (`name` + `register(ctx)`)
- The `PluginContext` interface (`steps`, `tts`, `vision`, `llm`, `research`)
- `register_step` decorator signature
- `register_tts` / `register_vision` / `register_llm` / `register_research` decorator signatures
- Built-in step names and execution order
- `discover_plugins()` / `load_plugin()` function signatures
- `Services` field names (`console`, `logger`)
- `ResearchInfo` model fields (`title`, `year`, `summary`, `genres`, `cast`, `keywords`)
- `check_version()` function for import-time compatibility validation

### What may change in v0.6+

- New `Services` fields may be added (always optional, never break existing code)
- New registry categories may be added (e.g. `subtitles_registry`)
- Built-in step list may grow (new steps inserted, existing order preserved)
- `PluginContext` may gain new fields (additive, not breaking)

### What won't change

- Existing built-in step names will not be renamed
- The `Context -> Context` step function signature will not change
- The `register(name, func, *, soft, ...)` signature will not change
