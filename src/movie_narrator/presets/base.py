"""Preset protocol and data structures.

A :class:`Preset` is a named bundle of parameter defaults and prompt
shaping tags.  The interface is intentionally minimal so that future
SPI discover (entry-points / local folder) can wrap any callable that
returns the right shape.

Prompt tags are a closed vocabulary — the preset may only return keys
listed in :data:`ALLOWED_PROMPT_TAGS`, and the values must be one of
the enumerated options.  This prevents prompt-template condition
branches from exploding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Protocol, runtime_checkable

# ── Closed prompt-tag vocabulary ────────────────────────────
# Each key maps to the set of allowed string values.  Presets that
# return tags outside this vocabulary will be rejected at registration
# time.
ALLOWED_PROMPT_TAGS: Dict[str, frozenset[str]] = {
    "prompt_cadence": frozenset({"measured", "brisk", "languid"}),
    "prompt_register": frozenset({"spoken", "written", "mixed"}),
    "prompt_connectors": frozenset({"interjection", "narrative", "none"}),
}

# ── Param keys that presets are allowed to set ──────────────
# Must be a subset of JobParams fields (schema.py) / _ALLOWED_PARAMS
# (load.py) / build_context key tuple (runner.py).  Validated at
# registration time.
ALLOWED_PARAM_KEYS: frozenset[str] = frozenset({
    # Match / scene
    "match_speed_clamp_min",
    "match_speed_clamp_max",
    "scene_merge_min_duration",
    "match_drop_scene_min_duration",
    "match_min_score",
    # BGM
    "bgm_gain_db",
    "bgm_duck_db",
    "bgm_normalize",
    "audio_target_dbfs",
    # Render / subtitle
    "render_subtitle_position",
    "render_subtitle_max_width_ratio",
    "render_subtitle_bottom_margin_ratio",
    "render_font_size",
    "render_fit_mode",
    "render_crf",
    "render_preset",
    "render_faststart",
    # TTS pacing
    "tts_pause_ms",
    # QA
    "qa_enabled",
    "qa_max_silence_db",
    "qa_min_duration_ratio",
    "qa_max_duration_ratio",
    # Prompt shaping (new — added to whitelist in schema/load/runner)
    "prompt_target_sentences",
    "prompt_max_chars_per_sentence",
    "prompt_hook_seconds",
})


@runtime_checkable
class Preset(Protocol):
    """A narration style preset.

    Implementations may be classes or modules; the only requirement is
    that they expose ``name``, ``params()``, ``prompt_tags()``, and
    ``description()``.
    """

    #: Unique preset identifier (kebab-case, e.g. ``"mainstream-dry"``).
    name: str

    def params(self) -> Dict[str, Any]:
        """Return JobParams-compatible key-value defaults.

        Keys must be in :data:`ALLOWED_PARAM_KEYS`.  Values must be
        valid for the corresponding :class:`JobParams` field.
        """
        ...

    def prompt_tags(self) -> Dict[str, str]:
        """Return closed-vocabulary prompt shaping labels.

        Keys must be in :data:`ALLOWED_PROMPT_TAGS` and values must be
        in the corresponding allowed set.
        """
        ...

    def description(self) -> str:
        """Human-readable description shown in ``--help`` and docs."""
        ...


@dataclass(frozen=True)
class PresetParam:
    """Materialised preset parameters ready for merge into ctx.metadata.

    Created by :func:`movie_narrator.presets.registry.get_preset` after
    validation.  ``param_dict`` is the validated ``params()`` output;
    ``tag_dict`` is the validated ``prompt_tags()`` output.
    """

    name: str
    param_dict: Dict[str, Any] = field(default_factory=dict)
    tag_dict: Dict[str, str] = field(default_factory=dict)
    desc: str = ""
