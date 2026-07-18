"""Form data model, validation, and conversion to build_context kwargs.

The ``empty = no override`` rule (§5.3 of the spec) is critical:
advanced params with ``None`` values are NOT injected into the
``params`` dict, so Settings defaults take effect. This prevents the
form from shadowing ``.env`` / ``MN_*`` configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class FormData:
    """Parsed form data from the Web API."""

    movie: str
    style: str
    duration: int
    voice: str
    format: str
    video_path: Optional[str]
    library_dir: str
    research: bool
    bgm_path: Optional[str]
    no_bgm: bool
    no_clips: bool
    strict: bool
    subtitle_lang: str
    subtitle_mode: str
    # Advanced params (None = no override, let Settings decide)
    scene_threshold: Optional[float]
    match_min_score: Optional[float]
    translate_provider: str
    translate_retries: Optional[int]
    narration_preset: str = ""


def _valid_presets() -> set:
    """Return valid preset names (empty string = no preset / default).

    Dynamically fetched from the preset registry so new presets are
    automatically recognized without updating this validation.
    """
    from movie_narrator.presets import list_presets
    return {""} | set(list_presets().keys())


def validate_form(data: FormData) -> List[str]:
    """Return a list of validation error messages (empty = valid)."""
    errors: List[str] = []
    if not data.movie or not data.movie.strip():
        errors.append("Movie name is required")
    if data.duration < 5 or data.duration > 600:
        errors.append("Duration must be 5-600 seconds")
    if data.format not in ("16:9", "9:16"):
        errors.append("Format must be 16:9 or 9:16")
    if data.subtitle_mode not in ("original", "translated", "bilingual"):
        errors.append("Subtitle mode must be original, translated, or bilingual")
    if data.subtitle_mode in ("translated", "bilingual") and not data.subtitle_lang.strip():
        errors.append("subtitle_lang is required when subtitle_mode is translated or bilingual")
    if data.narration_preset.strip() and data.narration_preset.strip() not in _valid_presets():
        from movie_narrator.presets import list_presets
        available = ", ".join(sorted(list_presets().keys()))
        errors.append(f"Invalid preset: {data.narration_preset}. Must be one of: {available}")
    if data.scene_threshold is not None and not (0 <= data.scene_threshold <= 100):
        errors.append("Scene threshold must be 0-100")
    if data.match_min_score is not None and not (0 <= data.match_min_score <= 1):
        errors.append("Match min score must be 0-1")
    if data.translate_retries is not None and not (0 <= data.translate_retries <= 10):
        errors.append("Translate retries must be 0-10")
    return errors


def form_to_context_args(data: FormData) -> Dict[str, Any]:
    """Convert :class:`FormData` to ``build_context`` kwargs.

    Advanced params with ``None`` values are **not** injected into
    ``params`` — this lets Settings (``.env`` / ``MN_*``) defaults
    take effect. Only non-empty form values override Settings.
    """
    params: Dict[str, Any] = {}
    if data.scene_threshold is not None:
        params["scene_threshold"] = data.scene_threshold
    if data.match_min_score is not None:
        params["match_min_score"] = data.match_min_score
    if data.translate_provider.strip():
        params["translate_provider"] = data.translate_provider.strip()
    if data.translate_retries is not None:
        params["translate_retries"] = data.translate_retries

    return dict(
        movie=data.movie.strip(),
        style=data.style,
        duration=data.duration,
        voice=data.voice.strip() or None,
        format=data.format,
        video=data.video_path,
        library_dir=data.library_dir.strip() or None,
        research=data.research,
        bgm=data.bgm_path,
        no_bgm=data.no_bgm,
        no_clips=data.no_clips,
        strict=data.strict,
        workflow_steps=None,
        params=params or None,
        config_path=None,
        subtitle_lang=data.subtitle_lang.strip() or None,
        subtitle_mode=data.subtitle_mode,
        narration_preset=data.narration_preset.strip() or None,
    )
