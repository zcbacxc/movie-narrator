# Quickstart: Plugin Development

This guide walks you through creating, packaging, and distributing a
movie-narrator plugin from scratch in under 10 minutes.

## Prerequisites

```bash
pip install movie-narrator
```

Verify installation:

```bash
mn version
# movie-narrator 0.5.4 (contract 0.5.1)
```

## Step 1: Create the plugin package

Create a new directory for your plugin:

```
my-plugin/
├── pyproject.toml
└── my_plugin/
    └── __init__.py
```

### `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "my-plugin"
version = "0.1.0"
description = "My custom movie-narrator plugin."
requires-python = ">=3.10"
dependencies = ["movie-narrator>=0.5.0"]

[project.entry-points."movie_narrator.plugins"]
my-plugin = "my_plugin:MyPlugin"

[tool.setuptools.packages.find]
where = ["."]
```

The `[project.entry-points]` section is critical — it registers your
plugin for auto-discovery. The key (`my-plugin`) is the plugin name
shown by `mn plugin list`, and the value (`my_plugin:MyPlugin`) is the
import path to your plugin class.

### `my_plugin/__init__.py`

```python
from movie_narrator import Context, PluginContext, register_step


class MyPlugin:
    name = "my-plugin"

    def register(self, ctx: PluginContext) -> None:
        ctx.steps.register(
            "my_step",
            _my_step,
            soft=True,
            status_field="my_step",
            consequence="my step skipped — output unaffected",
            after="render_video",
        )


def _my_step(ctx: Context) -> Context:
    """Add a custom processing step after video rendering."""
    console = ctx.services.console
    console.info(f"MyPlugin: processing {ctx.video_path}")

    # Your custom logic here
    ctx.metadata["my_step_ran"] = True

    ctx.step_state.result = ctx.step_state.result.__class__("success")
    ctx.step_state.message = "my step completed"
    return ctx
```

## Step 2: Install and test locally

```bash
cd my-plugin
pip install -e .
```

Verify the plugin is discovered:

```bash
mn plugin list
# my-plugin (entry_point: my_plugin:MyPlugin)
```

Load it and verify registration:

```bash
mn plugin discover
# Discovered: my-plugin
# Registered steps: my_step (after render_video, soft)
```

## Step 3: Run with the plugin active

The plugin is auto-loaded when `discover_plugins()` is called. In the
CLI, this happens automatically:

```bash
mn create --movie "Inception" --video movie.mp4 --output-dir output/
```

Your step will execute after `render_video` and log its message.

## Step 4: Choose your extension type

The Plugin SDK supports four extension points. Pick the one that
matches your goal:

| Goal | Decorator | Factory signature | Example |
|------|-----------|-------------------|---------|
| Add a pipeline step | `register_step` | `(ctx: Context) -> Context` | `examples/plugins/watermark/` |
| Add a TTS provider | `register_tts` | `(settings) -> TTSProvider` | Built-in `edge`, `openai`, `mimo` |
| Add an LLM provider | `register_llm` | `() -> ContextManager[LLMClient]` | Built-in `openai` |
| Add a research provider | `register_research` | `(ctx, settings) -> ResearchInfo` | `examples/plugins/research-wiki/` |
| Add a vision captioner | `register_vision` | `(**kwargs) -> VisionCaptioner` | Built-in `stub` |

### Research provider example

```python
from movie_narrator import Context, ResearchInfo, register_research

@register_research("my_research")
def _research(ctx: Context, settings) -> ResearchInfo:
    # Fetch data from your source (API, database, etc.)
    return ResearchInfo(
        title=ctx.movie_name,
        summary="A custom summary from my provider.",
        genres=["Action"],
        cast=[],
        keywords=["custom"],
    )
```

Select it in your job config:

```yaml
params:
  research_provider: my_research
```

### TTS provider example

```python
from movie_narrator import register_tts

@register_tts("my_tts")
def _make_tts(settings) -> "TTSProvider":
    from my_tts_impl import MyTTSProvider
    return MyTTSProvider(settings)
```

Select it via environment variable:

```bash
MN_TTS_PROVIDER=my_tts mn create ...
```

## Step 5: Package and distribute

### Build the wheel

```bash
pip install build
python -m build
```

This produces `dist/my_plugin-0.1.0-py3-none-any.whl`.

### Publish to PyPI

```bash
pip install twine
twine upload dist/*
```

### Users install and use

```bash
pip install my-plugin
mn plugin list          # confirms discovery
mn create --movie ...   # plugin auto-loads
```

## Debugging

### Plugin not discovered?

Check:
1. The entry point group is exactly `movie_narrator.plugins`
2. The entry point value matches `package:ClassName`
3. The package is installed (`pip show my-plugin`)
4. Run `mn plugin list` to see all discovered entry points

### Step not executing?

Check:
1. `mn plugin registries` shows your step in the registry
2. The `after`/`before` insertion point matches a built-in step name
3. The step is not disabled in `job.yaml` under `steps:`
4. For soft steps, check `_degraded_steps` in metadata if it failed silently

### Protocol validation error?

TTS and Vision providers must return instances that satisfy the
protocol ABC (`TTSProvider` / `VisionCaptioner`). If you see a
`TypeError` from `create()`, ensure your provider class inherits from
or implements all required methods.

## Reference examples

| Plugin | Extension type | Location |
|--------|---------------|----------|
| Watermark | Pipeline step | `examples/plugins/watermark/` |
| Template | Pipeline step (skeleton) | `examples/plugins/template/` |
| Research Wiki | Research provider | `examples/plugins/research-wiki/` |

## Next steps

- Read the [Plugin Development Guide](PLUGIN_DEVELOPMENT.md) for the
  full API reference
- Check the [Architecture](ARCHITECTURE.md) to understand pipeline flow
- Review [PACKAGING.md](PACKAGING.md) for publishing best practices
