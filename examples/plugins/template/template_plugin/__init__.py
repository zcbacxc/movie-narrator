"""PLUGIN_NAME — template plugin for movie-narrator.

This template demonstrates the standard structure for a movie-narrator
plugin. Copy this directory, replace PLUGIN_NAME / PLUGIN_PACKAGE /
PLUGIN_CLASS with your plugin's identifiers, and implement your custom
logic in ``_my_step``.

Available registration methods on PluginContext:

    ctx.steps.register(name, func, soft=True, after="render_video")
    ctx.tts.register(name, factory)
    ctx.vision.register(name, factory)
    ctx.llm.register(name, factory)
    ctx.research.register(name, factory)
"""

from __future__ import annotations

from movie_narrator import Context, PluginContext, register_step


class TemplatePlugin:
    """Template plugin — registers a custom pipeline step."""

    name = "template"

    def register(self, ctx: PluginContext) -> None:
        """Register the template step with the step registry."""
        ctx.steps.register(
            "template_step",
            _template_step,
            soft=True,
            status_field="template",
            consequence="template step skipped — no impact on output",
            after="render_video",
        )


def _template_step(ctx: Context) -> Context:
    """Custom pipeline step — replace with your logic.

    This step runs after ``render_video`` and has access to the full
    Context, including ``ctx.video_path``, ``ctx.output_dir``, and
    ``ctx.metadata``.
    """
    # Example: log a message
    console = ctx.services.console if ctx.services else None
    if console and hasattr(console, "info"):
        console.info(f"TemplatePlugin: video_path={ctx.video_path}")

    ctx.step_state.result = ctx.step_state.result.__class__("success")
    ctx.step_state.message = "template step executed"
    return ctx
