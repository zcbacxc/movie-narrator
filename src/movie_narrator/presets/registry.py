"""Preset registry — registration, validation, and lookup.

Stage 0.5: only built-in presets are registered (no SPI discover yet).
The :func:`discover_presets` hook is reserved for Stage 2 (entry-points
+ local folder scan) and currently returns an empty list.
"""

from __future__ import annotations

from typing import Dict, List

from .base import (
    ALLOWED_PARAM_KEYS,
    ALLOWED_PROMPT_TAGS,
    Preset,
    PresetParam,
)

# ── Built-in presets ────────────────────────────────────────
from .mainstream_dry import MainstreamDryPreset
from .douyin_fast import DouyinFastPreset
from .bilibili_long import BilibiliLongPreset


def _validate(preset: Preset) -> PresetParam:
    """Validate a preset's params and tags against the closed vocabularies.

    Raises :class:`ValueError` with a descriptive message if any key or
    value is out of bounds.
    """
    raw_params = preset.params()
    raw_tags = preset.prompt_tags()

    # Validate param keys
    bad_keys = set(raw_params) - ALLOWED_PARAM_KEYS
    if bad_keys:
        raise ValueError(
            f"Preset '{preset.name}': param keys not in ALLOWED_PARAM_KEYS: {bad_keys}"
        )

    # Validate prompt tag keys and values
    for tag_key, tag_val in raw_tags.items():
        if tag_key not in ALLOWED_PROMPT_TAGS:
            raise ValueError(
                f"Preset '{preset.name}': prompt tag '{tag_key}' is not in "
                f"ALLOWED_PROMPT_TAGS. Allowed: {sorted(ALLOWED_PROMPT_TAGS)}"
            )
        allowed_vals = ALLOWED_PROMPT_TAGS[tag_key]
        if tag_val not in allowed_vals:
            raise ValueError(
                f"Preset '{preset.name}': prompt tag '{tag_key}' value '{tag_val}' "
                f"is not allowed. Allowed values: {sorted(allowed_vals)}"
            )

    return PresetParam(
        name=preset.name,
        param_dict=dict(raw_params),
        tag_dict=dict(raw_tags),
        desc=preset.description(),
    )


def _build_registry() -> Dict[str, PresetParam]:
    """Build the validated preset registry.

    Built-in presets are always loaded.  Future SPI discover results
    are merged on top (Stage 2).
    """
    registry: Dict[str, PresetParam] = {}

    # 1. Built-in presets (hardcoded — Stage 0.5)
    builtin = [
        DouyinFastPreset(),
        MainstreamDryPreset(),
        BilibiliLongPreset(),
    ]

    # 2. SPI discover (Stage 2 — currently returns [])
    external = discover_presets()

    for preset in builtin + external:
        validated = _validate(preset)
        if validated.name in registry:
            raise ValueError(
                f"Duplicate preset name '{validated.name}' — "
                f"built-in and external presets must have unique names"
            )
        registry[validated.name] = validated

    return registry


def discover_presets() -> List[Preset]:
    """Discover external presets via SPI.

    Stage 0.5 stub — returns empty list.  Stage 2 will implement:
    1. ``importlib.metadata.entry_points(group="movie_narrator.presets")``
    2. Opt-in local folder scan (``~/.movie-narrator/presets/*.py``)
    """
    return []


# ── Public API ──────────────────────────────────────────────

_REGISTRY: Dict[str, PresetParam] | None = None


def _get_registry() -> Dict[str, PresetParam]:
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = _build_registry()
    return _REGISTRY


def get_preset(name: str) -> PresetParam:
    """Look up a validated preset by name.

    Raises :class:`KeyError` if not found.  Use :func:`list_presets`
    to see available names.
    """
    reg = _get_registry()
    if name not in reg:
        available = ", ".join(sorted(reg))
        raise KeyError(
            f"Unknown narration preset '{name}'. Available: {available}"
        )
    return reg[name]


def list_presets() -> Dict[str, str]:
    """Return ``{name: description}`` for all registered presets."""
    return {name: p.desc for name, p in _get_registry().items()}


# Lazy-loaded constant for __init__ re-export
def _build_builtin_dict():
    return _get_registry()


class _LazyPresets:
    """Lazy proxy so BUILTIN_PRESETS doesn't trigger registry build at import."""

    def __iter__(self):
        return iter(_get_registry())

    def __len__(self):
        return len(_get_registry())

    def __contains__(self, item):
        return item in _get_registry()

    def items(self):
        return _get_registry().items()

    def keys(self):
        return _get_registry().keys()

    def values(self):
        return _get_registry().values()


BUILTIN_PRESETS = _LazyPresets()
