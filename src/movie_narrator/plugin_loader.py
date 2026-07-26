"""Plugin discovery via Python entry points.

This module implements automatic plugin discovery using the standard
``importlib.metadata`` entry points mechanism. Third-party packages
declare a plugin in their ``pyproject.toml``::

    [project.entry-points."movie_narrator.plugins"]
    my-plugin = "my_plugin:MyPluginClass"

At runtime, :func:`discover_plugins` scans all installed entry points
in the ``movie_narrator.plugins`` group, loads each plugin object,
and registers it via :func:`contract.load_plugin`.

Error handling is per-plugin: a broken plugin produces a warning but
does not prevent other plugins from loading.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from importlib.metadata import entry_points
from typing import Any, List

from .contract import Plugin, load_plugin

#: Entry point group name for movie-narrator plugins.
ENTRY_POINT_GROUP = "movie_narrator.plugins"


@dataclass
class PluginLoadResult:
    """Result of loading a single plugin."""

    name: str
    success: bool
    error: str = ""


def _load_entry_point(ep: Any) -> Plugin:
    """Load an entry point and return the plugin object.

    The entry point must resolve to an object implementing the
    :class:`~movie_narrator.contract.Plugin` protocol — i.e. it has
    a ``name`` attribute and a ``register`` method.

    Raises:
        TypeError: if the loaded object does not satisfy the Plugin protocol.
        Exception: any exception from the entry point load itself.
    """
    obj = ep.load()

    # If it's a class, instantiate it (no-arg constructor expected).
    # If it's already an instance, use as-is.
    if isinstance(obj, type):
        obj = obj()

    if not isinstance(obj, Plugin):
        raise TypeError(
            f"Entry point '{ep.name}' loaded object {obj!r} does not "
            f"implement the Plugin protocol (missing 'name' attribute "
            f"or 'register' method)."
        )

    return obj


def discover_plugins(*, group: str = ENTRY_POINT_GROUP) -> List[PluginLoadResult]:
    """Discover and load all plugins registered via entry points.

    Scans the ``movie_narrator.plugins`` entry point group, loads
    each plugin, and registers it with the global registries via
    :func:`~movie_narrator.contract.load_plugin`.

    Args:
        group: Entry point group name. Defaults to
            ``"movie_narrator.plugins"``.

    Returns:
        List of :class:`PluginLoadResult` describing each plugin's
        load outcome. Plugins that failed to load have
        ``success=False`` and a non-empty ``error`` message.

    This function is idempotent: calling it multiple times will
    attempt to re-register already-loaded plugins, which will raise
    ``ValueError`` from the registry (duplicate name). Those errors
    are caught and reported as failed loads.
    """
    results: List[PluginLoadResult] = []

    try:
        eps = entry_points(group=group)
    except TypeError:
        # Python 3.9 fallback (dict interface) — we require 3.10+,
        # but be defensive in case of unusual environments.
        all_eps = entry_points()
        eps = all_eps.get(group, [])

    for ep in eps:
        try:
            plugin = _load_entry_point(ep)
            load_plugin(plugin)
            results.append(PluginLoadResult(name=plugin.name, success=True))
        except Exception as exc:
            msg = f"Failed to load plugin '{ep.name}': {exc}"
            warnings.warn(msg, stacklevel=2)
            results.append(
                PluginLoadResult(name=ep.name, success=False, error=str(exc))
            )

    return results


def list_available_plugins(*, group: str = ENTRY_POINT_GROUP) -> List[str]:
    """List plugin names available via entry points (without loading).

    Args:
        group: Entry point group name.

    Returns:
        List of entry point names in the group.
    """
    try:
        eps = entry_points(group=group)
    except TypeError:
        all_eps = entry_points()
        eps = all_eps.get(group, [])

    return [ep.name for ep in eps]
