"""Example out-of-tree plugin: watermark step.

This plugin demonstrates the v0.5 SDK by registering a custom pipeline
step that burns a watermark image into the final video after rendering.

Installation::

    cd examples/plugins/watermark
    pip install -e .

After installation, the plugin is auto-discovered via entry points when
``movie_narrator.discover_plugins()`` is called (or when the pipeline
runner initializes plugins).

Usage in a pipeline::

    from movie_narrator import discover_plugins
    discover_plugins()  # loads all installed plugins

Or load manually::

    from movie_narrator import load_plugin
    from movie_narrator_watermark import WatermarkPlugin
    load_plugin(WatermarkPlugin())
"""

from .plugin import WatermarkPlugin

__all__ = ["WatermarkPlugin"]
