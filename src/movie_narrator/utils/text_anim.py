# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Text animation effects for v0.7.1 visual enhancement.

Provides fade, slide, and typewriter-style entrance animations for
subtitle text overlays. Each function returns a modified ImageClip
with the appropriate MoviePy effects applied.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Maximum fraction of a subtitle clip's duration that an entrance
# animation may consume. Subtitle segments are short, so we cap the
# animation at a quarter of the clip — this keeps the text readable for
# most of its on-screen time while still providing a polished entrance.
_MAX_ANIMATION_RATIO = 1.0 / 4.0

# Valid animation types (shared source of truth for pipeline + tests).
VALID_ANIMATION_TYPES = frozenset({"none", "fade", "slide_up", "slide_left"})


def get_animation_duration(clip_duration: float, requested: float = 0.3) -> float:
    """Clamp a text-animation duration so it never exceeds 1/4 of the clip.

    A subtitle entrance animation that runs longer than the subtitle is
    on screen would mean the text never settles, hurting readability.
    We therefore cap the effective duration at a quarter of the clip's
    runtime. Negative or zero durations are normalised to 0.

    Args:
        clip_duration: Duration of the subtitle clip (seconds).
        requested: Desired animation duration in seconds (default 0.3).

    Returns:
        The clamped animation duration in seconds (never negative).
    """
    if clip_duration is None or clip_duration <= 0:
        return 0.0
    if requested is None or requested <= 0:
        return 0.0
    max_allowed = clip_duration * _MAX_ANIMATION_RATIO
    return min(float(requested), max_allowed)


def _import_fade_effect():
    """Import MoviePy's FadeIn effect defensively.

    The ``moviepy.video.fx`` module layout has shifted across minor
    releases; importing inside a helper lets us catch ImportError /
    AttributeError uniformly and fall back to a no-op animation.
    """
    try:
        from moviepy.video.fx import FadeIn
        return FadeIn
    except Exception as e:  # noqa: BLE001 — broad on purpose, see docstring
        logger.debug("Failed to import FadeIn effect", exc_info=True)
        return None


def _safe_clip_duration(clip: Any) -> float:
    """Return the clip's duration as a float, or 0.0 when unknown."""
    try:
        d = getattr(clip, "duration", None)
        if d is None:
            return 0.0
        return float(d)
    except Exception as e:  # noqa: BLE001
        logger.debug("Failed to read clip duration", exc_info=True)
        return 0.0


def _read_base_position(clip: Any) -> tuple[float, float]:
    """Return the clip's resting (x, y) position as a numeric tuple.

    MoviePy 2.x stores the position set via ``with_position`` on
    ``clip.pos``. When that attribute is a static 2-tuple of numbers we
    use it as the animation base so the text settles exactly where it
    was meant to be. Falls back to ``(0, 0)`` for clips without a stored
    position or test mocks (subtitle overlays default to (0, 0)).
    """
    pos = getattr(clip, "pos", None)
    if isinstance(pos, (tuple, list)) and len(pos) == 2:
        try:
            return (float(pos[0]), float(pos[1]))
        except (TypeError, ValueError):
            return (0.0, 0.0)
    return (0.0, 0.0)


def apply_text_animation(
    clip: Any,
    animation_type: str,
    duration: float = 0.3,
) -> Any:
    """Apply an entrance animation to a subtitle text ImageClip.

    Args:
        clip: A MoviePy ``ImageClip`` for a subtitle overlay.
        animation_type: One of ``"none"``, ``"fade"``, ``"slide_up"``,
            ``"slide_left"``. ``"none"`` returns the clip unchanged.
        duration: Animation duration in seconds. Clamped to at most a
            quarter of the clip's own duration via
            :func:`get_animation_duration`.

    Returns:
        The (possibly modified) clip. When ``animation_type`` is
        ``"none"`` or MoviePy fx is unavailable, the original clip is
        returned unchanged so the render pipeline degrades gracefully.

    Notes:
        * ``fade`` uses MoviePy's ``FadeIn`` effect for an opacity ramp.
        * ``slide_up`` / ``slide_left`` interpolate the clip's position
          from a small offset back to its resting location. When the clip
          does not expose ``with_position`` (e.g. test mocks) we fall
          back to a ``FadeIn`` so the animation is still applied.
    """
    # "none" (or any unknown type) → no-op. Be lenient: an unrecognised
    # type must never crash a render, it simply degrades to no animation.
    if not animation_type or animation_type == "none":
        return clip

    # Resolve a safe duration. If the clip reports no duration we cannot
    # apply a meaningful animation, so return unchanged.
    clip_duration = _safe_clip_duration(clip)
    anim_dur = get_animation_duration(clip_duration, duration)
    if anim_dur <= 0:
        return clip

    FadeIn = _import_fade_effect()

    try:
        if animation_type == "fade":
            if FadeIn is not None:
                return clip.with_effects([FadeIn(min(anim_dur, clip_duration))])
            return clip

        if animation_type in ("slide_up", "slide_left"):
            slid = _apply_slide_entrance(clip, animation_type, anim_dur,
                                         clip_duration, FadeIn)
            if slid is not None:
                return slid
            # Fallback: fade.
            if FadeIn is not None:
                return clip.with_effects([FadeIn(min(anim_dur, clip_duration))])
    except Exception as e:  # noqa: BLE001 — graceful degradation
        # Any MoviePy fx failure must not abort the render.
        logger.debug("Text animation failed; returning original clip", exc_info=True)
        return clip

    return clip


def _apply_slide_entrance(
    clip: Any,
    animation_type: str,
    anim_dur: float,
    clip_duration: float,
    FadeIn: Optional[Any],
) -> Optional[Any]:
    """Apply a slide-up or slide-left entrance animation.

    Interpolates the clip's position from a small offset to its resting
    location (0, 0 for an overlay that is already positioned, or the
    clip's current position when available). The slide distance is a
    fixed fraction of the clip dimensions so it is visible but gentle.

    Returns the modified clip, or ``None`` when the clip does not expose
    the hooks needed for a positional animation (caller falls back to a
    fade in that case).
    """
    has_with_position = callable(getattr(clip, "with_position", None))
    if not has_with_position:
        return None

    # Slide distance: 6% of the relevant clip dimension.
    w = getattr(clip, "w", None) or 0
    h = getattr(clip, "h", None) or 0
    if animation_type == "slide_up":
        offset = float(h) * 0.06 if h else 0.0
    else:  # slide_left
        offset = float(w) * 0.06 if w else 0.0

    # When offset cannot be derived (e.g. mock without size), fall back
    # to a fixed pixel offset so the slide is still visible.
    if offset <= 0:
        offset = 20.0

    in_dur = min(anim_dur, clip_duration)

    # Subtitle overlays rest at their default position (top-left of the
    # full-canvas image, i.e. (0, 0)). When a clip already has a static
    # position stored (``clip.pos``) we animate relative to it so the
    # text settles exactly where it was meant to be.
    base = _read_base_position(clip)

    def _pos_fn(t):
        # ``t`` is relative to the clip's own start in MoviePy 2.x.
        progress = max(0.0, min(1.0, t / in_dur)) if in_dur > 0 else 1.0
        eased = progress
        if animation_type == "slide_up":
            return (base[0], base[1] + offset * (1.0 - eased))
        # slide_left
        return (base[0] + offset * (1.0 - eased), base[1])

    try:
        slid = clip.with_position(_pos_fn)
        # Preserve the original start time so timeline ordering is intact.
        start_t = getattr(clip, "start", None)
        if start_t is not None:
            slid = slid.with_start(start_t)
        # Combine with a fade for opacity polish when available.
        if FadeIn is not None and callable(getattr(slid, "with_effects", None)):
            slid = slid.with_effects([FadeIn(in_dur)])
        return slid
    except Exception as e:  # noqa: BLE001 — graceful degradation
        logger.debug("Slide entrance animation failed", exc_info=True)
        return None
