# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Narration preset system — pluggable style modes.

A preset bundles a set of default parameter values (match cadence, BGM
ducking, subtitle layout, prompt shaping) that together produce a
recognisable narration style.  Built-in presets cover three popular
recap styles; community presets (v1.5.1) are installed YAML data files
(never code — see ``presets/community.py`` and ADR-018) managed via
``mn presets install``.

Usage::

    from movie_narrator.presets import get_preset, BUILTIN_PRESETS

    preset = get_preset("mainstream-dry")
    params = preset.params()          # dict of JobParams keys
    prompt_tags = preset.prompt_tags() # dict of prompt shaping labels
"""

from .base import Preset, PresetParam
from .community import (
    CommunityPresetError,
    InstalledPreset,
    install_preset,
    list_installed,
    load_community_preset,
    uninstall_preset,
)
from .registry import get_preset, list_presets, BUILTIN_PRESETS

__all__ = [
    "Preset",
    "PresetParam",
    "get_preset",
    "list_presets",
    "BUILTIN_PRESETS",
    # v1.5.1 — community preset sharing (data-only YAML presets)
    "CommunityPresetError",
    "InstalledPreset",
    "install_preset",
    "list_installed",
    "load_community_preset",
    "uninstall_preset",
]
