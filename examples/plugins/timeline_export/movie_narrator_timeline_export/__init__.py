# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Timeline export plugin for movie-narrator.

Registers a soft pipeline step ``timeline_export`` that runs after
``render_video``. The step builds a unified intermediate representation
of the edit (matched clips on the video track + timed segments on the
text track + preset title/end/watermark/disclaimer overlays) and writes
it out as a timeline draft for manual fine-tuning in an NLE.

Two backends are provided:

- ``otio`` — OpenTimelineIO (Apache-2.0), the professional interchange
  standard. Requires the optional extra ``[otio]``.
- ``jianying`` — a Jianying (CapCut domestic) draft JSON, generated
  entirely by this plugin (self-authored; no third-party code is copied).

Installation::

    cd examples/plugins/timeline_export
    pip install -e .            # base plugin
    pip install -e ".[otio]"    # + OTIO backend

After installation the plugin is auto-discovered via entry points when
``movie_narrator.discover_plugins()`` is called (or the pipeline runner
initializes plugins).

Usage in a pipeline::

    from movie_narrator import discover_plugins
    discover_plugins()

Backend selection is driven by the ``timeline_export_backend`` job
parameter (``otio`` | ``jianying``), defaulting to ``jianying``.
"""

from .plugin import TimelineExportPlugin

__all__ = ["TimelineExportPlugin"]
