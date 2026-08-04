# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scene transition effects for v0.7.1 visual enhancement.

Provides crossfade, dissolve, and slide transitions between video
clips in the composite timeline. Each transition returns a list of
MoviePy effects/clips that the render pipeline applies.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from moviepy import VideoClip

logger = logging.getLogger(__name__)

# Maximum fraction of a clip's duration that a transition may consume.
# Keeping transitions short relative to the clip prevents a clip from
# being entirely "in transition" (which would make it never reach full
# opacity / correct position and look broken).
_MAX_TRANSITION_RATIO = 1.0 / 3.0

# Valid transition types and positions (kept here so the render pipeline
# and tests share a single source of truth).
VALID_TRANSITION_TYPES = frozenset({"none", "fade", "dissolve", "slide"})
VALID_TRANSITION_POSITIONS = frozenset({"in", "out", "both"})


def get_transition_duration(clip_duration: float, requested: float = 0.5) -> float:
    """Clamp a transition duration so it never exceeds 1/3 of the clip.

    A transition that is longer than the clip itself produces visual
    glitches (the clip would never reach full opacity), so we cap the
    effective duration at a third of the clip's runtime. Negative or
    zero durations are normalised to a safe default.

    Args:
        clip_duration: Duration of the clip the transition is applied to.
        requested: Desired transition duration in seconds (default 0.5).

    Returns:
        The clamped transition duration in seconds (never negative).
    """
    if clip_duration is None or clip_duration <= 0:
        return 0.0
    if requested is None or requested <= 0:
        return 0.0
    # Cap at 1/3 of the clip duration.
    max_allowed = clip_duration * _MAX_TRANSITION_RATIO
    return min(float(requested), max_allowed)


def _import_fade_effects():
    """Import MoviePy's FadeIn / FadeOut effects defensively.

    MoviePy's ``video.fx`` submodule layout has shifted between minor
    releases; importing inside a helper lets us catch ImportError /
    AttributeError uniformly and fall back to a no-op transition.
    """
    try:
        from moviepy.video.fx import FadeIn, FadeOut
        return FadeIn, FadeOut
    except Exception:  # noqa: BLE001 — broad on purpose, see docstring
        logger.debug("Failed to import FadeIn/FadeOut effects", exc_info=True)
        return None, None


def apply_transition(
    clip: Any,
    transition_type: str,
    duration: float = 0.5,
    position: str = "both",
) -> Any:
    """Apply a scene transition effect to a video clip.

    Args:
        clip: A MoviePy clip (e.g. the fitted source subclip).
        transition_type: One of ``"none"``, ``"fade"``, ``"dissolve"``,
            ``"slide"``. ``"none"`` returns the clip unchanged.
        duration: Transition duration in seconds. Clamped to at most a
            third of the clip's own duration via
            :func:`get_transition_duration`.
        position: Which end of the clip to animate — ``"in"``, ``"out"``
            or ``"both"`` (default).

    Returns:
        The (possibly modified) clip. When ``transition_type`` is
        ``"none"`` or MoviePy fx is unavailable, the original clip is
        returned unchanged so the render pipeline degrades gracefully.

    Notes:
        * ``fade`` and ``dissolve`` both use MoviePy's ``FadeIn`` /
          ``FadeOut`` effects (a dissolve is effectively a fade against
          a held frame of the surrounding composite).
        * ``slide`` animates the clip's x-position. When the clip does
          not expose ``with_position`` (e.g. test mocks) we fall back to
          a fade so the transition is still applied gracefully.
    """
    # "none" (or any unknown type) → no-op. Be lenient: an unrecognised
    # type must never crash a render, it simply degrades to no transition.
    if not transition_type or transition_type == "none":
        return clip

    if position not in VALID_TRANSITION_POSITIONS:
        position = "both"

    # Resolve a safe duration. If the clip reports no duration we cannot
    # apply a meaningful transition, so return unchanged.
    clip_duration = _safe_clip_duration(clip)
    trans_dur = get_transition_duration(clip_duration, duration)
    if trans_dur <= 0:
        return clip

    FadeIn, FadeOut = _import_fade_effects()

    try:
        if transition_type in ("fade", "dissolve"):
            effects = []
            if position in ("in", "both") and FadeIn is not None:
                effects.append(FadeIn(min(trans_dur, clip_duration)))
            if position in ("out", "both") and FadeOut is not None:
                effects.append(FadeOut(min(trans_dur, clip_duration)))
            if effects:
                return clip.with_effects(effects)

        if transition_type == "slide":
            # Attempt a simple x-offset slide. If the clip does not
            # support with_position / with_effects (e.g. test mocks),
            # fall back to a fade so the transition is still applied.
            slid = _apply_slide(clip, trans_dur, position, clip_duration,
                                FadeIn, FadeOut)
            if slid is not None:
                return slid
            # Fallback: fade.
            effects = []
            if position in ("in", "both") and FadeIn is not None:
                effects.append(FadeIn(min(trans_dur, clip_duration)))
            if position in ("out", "both") and FadeOut is not None:
                effects.append(FadeOut(min(trans_dur, clip_duration)))
            if effects:
                return clip.with_effects(effects)
    except Exception:  # noqa: BLE001 — graceful degradation
        # Any MoviePy fx failure must not abort the render.
        logger.debug("Transition failed; returning original clip", exc_info=True)
        return clip

    return clip


def _safe_clip_duration(clip: VideoClip) -> float:
    """
    Returns:
        The clip's duration as a float, or 0.0 when unknown.
    """
    try:
        d = getattr(clip, "duration", None)
        if d is None:
            return 0.0
        return float(d)
    except Exception:  # noqa: BLE001
        logger.debug("Failed to read clip duration", exc_info=True)
        return 0.0


def _read_base_position(clip: VideoClip) -> tuple[float, float]:
    """
    Returns:
        The clip's resting (x, y) position as a numeric tuple.

        MoviePy 2.x stores the position set via ``with_position`` on
        ``clip.pos``. When that attribute is a static 2-tuple of numbers we
        use it as the slide base so a centered (contain-mode) clip keeps its
        vertical centering during the animation. Falls back to ``(0, 0)``
        for clips without a stored position or test mocks.
    """
    pos = getattr(clip, "pos", None)
    if isinstance(pos, (tuple, list)) and len(pos) == 2:
        try:
            return (float(pos[0]), float(pos[1]))
        except (TypeError, ValueError):
            return (0.0, 0.0)
    return (0.0, 0.0)


def _apply_slide(
    clip: VideoClip,
    trans_dur: float,
    position: str,
    clip_duration: float,
    FadeIn: Optional[type],
    FadeOut: Optional[type],
) -> Optional[VideoClip]:
    """Apply a horizontal slide entrance/exit.

    Uses MoviePy's ``with_position`` with a callable that interpolates
    the x-offset from a small delta back to 0 (entrance) or from 0 to a
    delta (exit). The slide distance is a fixed fraction of the clip
    width so it is visible but not jarring.

    Returns:
        The modified clip, or ``None`` when the clip does not expose
        the hooks needed for a positional animation (caller falls back to a
        fade in that case).
    """
    width = getattr(clip, "w", None) or getattr(clip, "size", None)
    if isinstance(width, (tuple, list)):
        width = width[0]
    if not width or width <= 0:
        return None

    # Slide offset: 8% of the clip width (a noticeable but gentle slide).
    offset = float(width) * 0.08
    start_t = getattr(clip, "start", None) or 0.0
    in_dur = min(trans_dur, clip_duration) if position in ("in", "both") else 0.0
    out_dur = min(trans_dur, clip_duration) if position in ("out", "both") else 0.0

    has_with_position = callable(getattr(clip, "with_position", None))
    if not has_with_position:
        return None

    # Respect an existing static position (e.g. contain-mode centering) so
    # the slide animates relative to the clip's resting location instead of
    # snapping it to the top-left corner. MoviePy 2.x stores the position on
    # ``clip.pos`` after ``with_position``; falls back to (0, 0) otherwise.
    base = _read_base_position(clip)

    def _pos_fn(t):
        # ``t`` is relative to the clip's own start in MoviePy 2.x.
        x = base[0]
        if in_dur > 0 and t < in_dur:
            # Entrance: ease from +offset to base.
            progress = max(0.0, min(1.0, t / in_dur))
            x = base[0] + offset * (1.0 - progress)
        elif out_dur > 0 and t > (clip_duration - out_dur):
            # Exit: ease from base to -offset.
            progress = max(0.0, min(1.0,
                                     (t - (clip_duration - out_dur)) / out_dur))
            x = base[0] - offset * progress
        return (x, base[1])

    try:
        slid = clip.with_position(_pos_fn)
        # Preserve the original start time so timeline ordering is intact.
        if start_t:
            slid = slid.with_start(start_t)
        # Combine with a fade for opacity polish when available.
        effects = []
        if position in ("in", "both") and FadeIn is not None:
            effects.append(FadeIn(min(trans_dur, clip_duration)))
        if position in ("out", "both") and FadeOut is not None:
            effects.append(FadeOut(min(trans_dur, clip_duration)))
        if effects and callable(getattr(slid, "with_effects", None)):
            slid = slid.with_effects(effects)
        return slid
    except Exception:  # noqa: BLE001 — graceful degradation
        logger.debug("Slide transition failed", exc_info=True)
        return None
