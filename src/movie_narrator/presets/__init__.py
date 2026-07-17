"""Narration preset system — pluggable style modes.

A preset bundles a set of default parameter values (match cadence, BGM
ducking, subtitle layout, prompt shaping) that together produce a
recognisable narration style.  Built-in presets cover three popular
recap styles; third-party presets can be added later via entry-points.

Usage::

    from movie_narrator.presets import get_preset, BUILTIN_PRESETS

    preset = get_preset("mainstream-dry")
    params = preset.params()          # dict of JobParams keys
    prompt_tags = preset.prompt_tags() # dict of prompt shaping labels
"""

from .base import Preset, PresetParam
from .registry import get_preset, list_presets, BUILTIN_PRESETS

__all__ = [
    "Preset",
    "PresetParam",
    "get_preset",
    "list_presets",
    "BUILTIN_PRESETS",
]
