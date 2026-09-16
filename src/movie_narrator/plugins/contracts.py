# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Plugin contract types — ``Plugin``, ``PluginContext``, ``load_plugin``.

Moved here from ``movie_narrator.contract`` (M1) so that the public
contract module no longer defines plugin machinery, and so that
``plugin_loader`` / ``plugins.discovery`` can depend on a neutral
module instead of importing ``contract`` (which used to form a cycle).

This module must NOT import ``contract`` or ``plugin_loader``. Registry
types and singletons are taken from their source modules.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from ..pipeline.registry import StepRegistry, step_registry
from ..providers.registry import (
    ProviderRegistry,
    llm_registry,
    research_registry,
    tts_registry,
    vision_registry,
)

__all__ = [
    "Plugin",
    "PluginContext",
    "load_plugin",
]


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
    llm: ProviderRegistry
    research: ProviderRegistry

    @classmethod
    def default(cls) -> "PluginContext":
        """Create a PluginContext backed by the global registries."""
        return cls(
            steps=step_registry,
            tts=tts_registry,
            vision=vision_registry,
            llm=llm_registry,
            research=research_registry,
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

    def register(self, ctx: PluginContext) -> None:
        """Register this plugin's components with the provided registries.

        Args:
            ctx: The plugin context exposing the global registries.
        """
        ...


def load_plugin(plugin: Plugin) -> None:
    """Register a plugin with the global registries.

    Calls ``plugin.register(PluginContext.default())``, giving the
    plugin access to ``step_registry``, ``tts_registry``,
    ``vision_registry``, ``llm_registry``, and ``research_registry``.

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
