# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Preview mode utilities for v0.7.2.

Provides functions to truncate pipeline data (timed segments, matched
clips, audio) to a preview duration, enabling fast iteration without
a full render.
"""

from __future__ import annotations

from typing import List

# v0.7.2: Steps that are non-essential for preview rendering.  These are
# all SOFT steps whose output is not required to validate the first N
# seconds of a render.  Hard steps (generate_script, generate_voice,
# render_video, etc.) must always run.
_PREVIEW_SKIPPABLE_STEPS = frozenset({
    "research_plot",
    "translate_subtitles",
    "run_qa_gate",
    "export_clips",
})

# Clamping bounds for the preview duration, in seconds.  The lower bound
# avoids uselessly short previews; the upper bound caps cost so a typo
# (e.g. 3600s) cannot accidentally trigger a near-full render.
_PREVIEW_MIN_SEC = 3.0
_PREVIEW_MAX_SEC = 60.0


def truncate_segments_for_preview(segments: List, preview_sec: float) -> List:
    """
    Returns:
        Segments that start before ``preview_sec``, truncated to end at it.

        Each returned segment is a shallow copy with ``end`` clamped to
        ``preview_sec`` so the preview never extends past the requested
        window.  Segments that start at or after ``preview_sec`` are dropped
        entirely — they would not be visible in the truncated output.

        The input list is not mutated; segments that already fit inside the
        window are reused by reference (no copy needed).
    """
    result: List = []
    for seg in segments:
        # Drop segments that begin at or beyond the preview boundary.
        if seg.start >= preview_sec:
            continue
        # Reuse the original reference when no truncation is needed.
        if seg.end <= preview_sec:
            result.append(seg)
        else:
            result.append(seg.model_copy(update={"end": min(seg.end, preview_sec)}))
    return result


def truncate_clips_for_preview(clips: List, preview_sec: float) -> List:
    """
    Returns:
        Clips whose ``narr_start < preview_sec``, truncated.

        Each returned clip is a shallow copy with ``narr_end`` clamped to
        ``preview_sec``.  Clips that start at or after ``preview_sec`` are
        dropped — they fall outside the preview window.

        The input list is not mutated; clips that already fit inside the
        window are reused by reference (no copy needed).
    """
    result: List = []
    for clip in clips:
        # Drop clips that begin at or beyond the preview boundary.
        if clip.narr_start >= preview_sec:
            continue
        # Reuse the original reference when no truncation is needed.
        if clip.narr_end <= preview_sec:
            result.append(clip)
        else:
            result.append(clip.model_copy(update={"narr_end": min(clip.narr_end, preview_sec)}))
    return result


def get_preview_duration(requested: float, total_duration: float) -> float:
    """Return the effective preview duration in seconds.

    Computes ``min(requested, total_duration)`` then clamps the result
    to the range ``[3, 60]`` seconds.  The clamp prevents absurdly short
    or long previews regardless of what the caller requests.

    Note: when ``total_duration`` is itself below the lower clamp (e.g.
    a 2-second source), the returned value may exceed the real duration.
    Callers that need a hard ceiling should apply ``min(result, total)``
    themselves (the render pipeline does exactly this).
    """
    effective = min(requested, total_duration)
    return max(_PREVIEW_MIN_SEC, min(_PREVIEW_MAX_SEC, effective))


def should_skip_step_for_preview(step_name: str, preview_mode: bool) -> bool:
    """
    Returns:
        True when *step_name* may be skipped during preview rendering.

        Only SOFT steps that are non-essential for validating the first N
        seconds (``research_plot``, ``translate_subtitles``, ``run_qa_gate``,
        ``export_clips``) are skippable.  Hard steps (``generate_script``,
        ``generate_voice``, ``render_video``, etc.) always run so the preview
        is a faithful representation of the final output.

        When ``preview_mode`` is False the function always returns False,
        preserving full-pipeline behaviour and keeping preview mode OFF by
        default (backward compatible).
    """
    if not preview_mode:
        return False
    return step_name in _PREVIEW_SKIPPABLE_STEPS
