# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Neutral plugin package for movie-narrator (M1 boundary cleanup).

This package hosts the plugin extension points that used to live in
``contract`` / ``plugin_loader``, so those two modules no longer form
an import cycle:

- :mod:`movie_narrator.plugins.contracts` — ``Plugin``, ``PluginContext``,
  ``load_plugin``. Imports registry types from their source modules
  (``pipeline.registry``, ``providers.registry``), never from ``contract``.
- :mod:`movie_narrator.plugins.discovery` — entry-points discovery
  (``discover_plugins``, ``list_available_plugins``, ``PluginLoadResult``).

``movie_narrator.plugin_loader`` remains as a thin compatibility
wrapper re-exporting these symbols. Public import paths through
``movie_narrator.contract`` and ``movie_narrator`` are unchanged.
"""

from .contracts import Plugin, PluginContext, load_plugin
from .discovery import (
    ENTRY_POINT_GROUP,
    PluginLoadResult,
    discover_plugins,
    list_available_plugins,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "Plugin",
    "PluginContext",
    "PluginLoadResult",
    "discover_plugins",
    "list_available_plugins",
    "load_plugin",
]
