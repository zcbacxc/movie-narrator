# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Plugin discovery compatibility wrapper (deprecated location).

**M1 boundary cleanup.** The real implementations now live in:

- :mod:`movie_narrator.plugins.contracts` — ``Plugin``, ``PluginContext``,
  ``load_plugin``
- :mod:`movie_narrator.plugins.discovery` — ``PluginLoadResult``,
  ``discover_plugins``, ``list_available_plugins``, ``ENTRY_POINT_GROUP``,
  ``_load_entry_point``

This module only re-exports those symbols so existing imports of
``movie_narrator.plugin_loader`` keep working. It must **not** import
``contract`` (that was the cycle M1 removed).

Prefer the new paths for new code::

    from movie_narrator.plugins import discover_plugins, load_plugin
"""

from __future__ import annotations

from .plugins.contracts import Plugin, load_plugin
from .plugins.discovery import (
    ENTRY_POINT_GROUP,
    PluginLoadResult,
    _load_entry_point,  # noqa: F401  — re-exported for existing tests/compat
    discover_plugins,
    list_available_plugins,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "Plugin",
    "PluginLoadResult",
    "discover_plugins",
    "list_available_plugins",
    "load_plugin",
]
