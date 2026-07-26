# Plugin Template

This is a minimal template for creating a movie-narrator plugin.

## Quick Start

1. Copy this directory:
   ```bash
   cp -r examples/plugins/template/ ~/my-plugin/
   cd ~/my-plugin/
   ```

2. Rename and replace placeholders:
   - Directory: `template_plugin/` → your package name (e.g. `my_plugin/`)
   - `pyproject.toml`: Replace `PLUGIN_NAME`, `PLUGIN_PACKAGE`, `PLUGIN_CLASS`, `PLUGIN_DESCRIPTION`
   - `__init__.py`: Rename `TemplatePlugin` → your class name, `template` → your plugin name

3. Implement your logic in `_template_step` (rename as needed).

4. Install in development mode:
   ```bash
   pip install -e .
   ```

5. Verify the plugin is discovered:
   ```bash
   mn plugin list
   mn plugin discover
   ```

## What Can a Plugin Do?

### Register a Custom Pipeline Step

```python
ctx.steps.register(
    "my_step",
    my_func,
    soft=True,           # exceptions are caught (soft-degrade)
    status_field="my",   # shows up in metadata.json
    after="render_video", # or before="export_clips"
)
```

### Register a Custom Provider

```python
# TTS provider (must implement TTSProvider ABC)
ctx.tts.register("my_tts", my_tts_factory)

# Vision provider (must implement VisionCaptioner ABC)
ctx.vision.register("my_vlm", my_vlm_factory)

# LLM provider (factory returns context manager)
ctx.llm.register("my_llm", my_llm_factory)

# Research provider (factory returns ResearchInfo)
ctx.research.register("my_research", my_research_factory)
```

## Contract Version

Your plugin should declare a minimum contract version:

```python
from movie_narrator.contract import check_version
check_version((0, 5, 1))
```

This ensures users get a clear error message if the installed core
engine is too old for your plugin.
