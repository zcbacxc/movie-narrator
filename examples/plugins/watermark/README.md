# movie-narrator-watermark

Example out-of-tree plugin demonstrating the v0.5 Plugin SDK.

## What it does

Registers a soft pipeline step `add_watermark` that runs immediately
after `render_video`. The step overlays a watermark image (PNG with
alpha) onto the final video using ffmpeg's `overlay` filter at 50%
opacity in the bottom-right corner.

## Installation

```bash
cd examples/plugins/watermark
pip install -e .
```

## Usage

### Auto-discovery (recommended)

After installation, the plugin is automatically discovered when
`discover_plugins()` is called:

```python
from movie_narrator import discover_plugins
discover_plugins()
```

### Manual loading

```python
from movie_narrator import load_plugin
from movie_narrator_watermark import WatermarkPlugin
load_plugin(WatermarkPlugin())
```

### Configuration

Set the watermark image path in your job config:

```yaml
assets:
  watermark: /path/to/watermark.png
```

If no watermark is configured, the step is skipped (soft step, no error).
