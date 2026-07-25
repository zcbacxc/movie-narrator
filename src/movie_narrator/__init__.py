from importlib.metadata import version

__version__ = version("movie-narrator")

# ── Public SDK surface (v0.5+) ─────────────────────────────
# These symbols allow external code to extend the engine:
#
#   from movie_narrator import register_step, Context
#   from movie_narrator import register_tts, register_vision
#   from movie_narrator import Plugin, PluginContext, load_plugin
#
# The contract module is the single import surface. We re-export
# here for convenience so plugins don't need to know the internal
# module path.

from .contract import (  # noqa: F401
    Context,
    Plugin,
    PluginContext,
    ProviderRegistry,
    Step,
    StepRegistry,
    build_context,
    load_plugin,
    register_step,
    register_tts,
    register_vision,
    run_pipeline,
    step,
    step_registry,
    tts_registry,
    vision_registry,
)
