"""Stable API contract between the core engine and external consumers.

This module is the **single import surface** that the Web API layer
(and future consumers) should depend on. It re-exports the types,
protocols, and functions that form the engine's public API, plus
defines the plugin extension points (``Step``, ``Plugin``,
``PluginContext``).

By centralizing the contract here:

- web_api imports ``from ..contract import ...`` instead of reaching
  into internal modules (``..pipeline.runner``, ``..pipeline.errors``,
  ``..utils.console``, ``..utils.sanitize``).
- ``PARAM_WHITELIST`` is accessible without importing the full runner
  module, eliminating the highest drift-risk coupling point.
- External plugins import ``from movie_narrator import register_step,
  register_tts, register_vision, Context`` to extend the engine.
- When the project is eventually split into separate repositories,
  this module becomes the natural package boundary.

Nothing is *moved* from its current location — this module only
re-exports. Internal modules keep their definitions for backward
compatibility with existing CLI and test code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional, Protocol, runtime_checkable

# ── Re-exports: console abstraction ────────────────────────

from .utils.console import BaseConsole, Console
from .utils.sanitize import sanitize_filename

# ── Re-exports: pipeline errors ────────────────────────────

from .pipeline.errors import (
    PipelineCancelled,
    PipelineStrictError,
    RunController,
    StepAction,
    check_cancelled,
)

# ── Re-exports: engine entry points ────────────────────────

from .pipeline.runner import PARAM_WHITELIST, build_context, run_pipeline

# ── Re-exports: models ─────────────────────────────────────

from .models import Context, Services

# ── Re-exports: registries ─────────────────────────────────

from .pipeline.registry import StepRegistry, register_step, step, step_registry
from .providers import (
    ProviderRegistry,
    register_tts,
    register_vision,
    tts_registry,
    vision_registry,
)

# ── Step type alias ────────────────────────────────────────

#: A pipeline step function: ``Context -> Context``.
Step = Callable[[Context], Context]


# ── PipelineResult protocol ────────────────────────────────


@runtime_checkable
class PipelineResult(Protocol):
    """Read-only view of a completed pipeline's output.

    Formalizes the implicit ``Context`` duck-typing that web_api's
    ``collect_artifacts`` and ``TaskManager._run_task`` previously
    relied on without type annotations.

    The core engine's ``Context`` model satisfies this protocol
    structurally. Future consumers (CLI GUI, mobile, etc.) can depend
    on this protocol instead of the full ``Context`` model.
    """

    @property
    def video_path(self) -> Optional[str]: ...

    @property
    def audio_path(self) -> Optional[str]: ...

    @property
    def clips_dir(self) -> Optional[str]: ...

    @property
    def output_dir(self) -> str: ...

    @property
    def subtitle_paths(self) -> Optional[Any]: ...


# ── Plugin extension points ────────────────────────────────


@dataclass
class PluginContext:
    """Context passed to a plugin's ``register`` method.

    Gives plugins access to the global registries so they can
    register custom steps, TTS providers, vision providers, etc.

    Plugins should NOT hold long-lived references to this object —
    it exists only during the registration phase.
    """

    steps: StepRegistry
    tts: ProviderRegistry
    vision: ProviderRegistry

    @classmethod
    def default(cls) -> "PluginContext":
        """Create a PluginContext backed by the global registries."""
        return cls(
            steps=step_registry,
            tts=tts_registry,
            vision=vision_registry,
        )


@runtime_checkable
class Plugin(Protocol):
    """A plugin that extends the movie-narrator pipeline.

    Plugins implement a ``name`` attribute and a ``register`` method
    that receives a :class:`PluginContext` and registers its
    components (steps, providers, etc.) with the appropriate registries.

    Example::

        class WatermarkPlugin:
            name = "watermark"

            def register(self, ctx: PluginContext) -> None:
                ctx.steps.register(
                    "add_watermark",
                    add_watermark,
                    after="render_video",
                )
    """

    name: str

    def register(self, ctx: PluginContext) -> None: ...


def load_plugin(plugin: Plugin) -> None:
    """Register a plugin with the global registries.

    Calls ``plugin.register(PluginContext.default())``, giving the
    plugin access to ``step_registry``, ``tts_registry``, and
    ``vision_registry``.

    Args:
        plugin: An object implementing the :class:`Plugin` protocol.

    Raises:
        TypeError: if *plugin* does not implement the Plugin protocol.
    """
    if not isinstance(plugin, Plugin):
        raise TypeError(
            f"{plugin!r} does not implement the Plugin protocol "
            f"(missing 'name' attribute or 'register' method)."
        )
    plugin.register(PluginContext.default())


# ── Plugin discovery (entry_points) ───────────────────────
# Imported at the bottom of the module to avoid circular import:
# plugin_loader imports from contract (Plugin, load_plugin), so we
# import it after those symbols are defined.
# The actual import is done lazily below to keep the top-level
# contract module clean.

# ── Public API ─────────────────────────────────────────────

__all__ = [
    # Console
    "BaseConsole",
    "Console",
    "SilentConsole",
    # Errors
    "PipelineCancelled",
    "PipelineStrictError",
    "RunController",
    "StepAction",
    "check_cancelled",
    # Engine
    "PARAM_WHITELIST",
    "build_context",
    "run_pipeline",
    # Utilities
    "sanitize_filename",
    # Protocols
    "PipelineResult",
    # Models
    "Context",
    "Services",
    # Registries
    "StepRegistry",
    "ProviderRegistry",
    "step_registry",
    "tts_registry",
    "vision_registry",
    # Registration decorators
    "register_step",
    "register_tts",
    "register_vision",
    "step",  # alias for register_step
    # Plugin system
    "Step",
    "PluginContext",
    "Plugin",
    "load_plugin",
    "discover_plugins",
    "list_available_plugins",
    # Presets (re-exported for web package and external consumers)
    "list_presets",
    "get_preset",
]


# SilentConsole is imported here (not at top) to avoid circular import:
# utils/console.py imports from utils/log.py and utils/retention.py,
# which are safe but we keep the import explicit for contract clarity.
from .utils.console import SilentConsole  # noqa: E402

# Plugin discovery is imported here (not at top) to avoid circular import:
# plugin_loader imports from contract (Plugin, load_plugin), so we import
# it after those symbols are defined.
from .plugin_loader import (  # noqa: E402
    discover_plugins,
    list_available_plugins,
)

# Presets are imported here (not at top) to avoid circular import:
# presets modules import from models and config, which are safe but we
# keep the import explicit for contract clarity.
from .presets import get_preset, list_presets  # noqa: E402
