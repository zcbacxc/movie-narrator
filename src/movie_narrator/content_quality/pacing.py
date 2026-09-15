# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Pacing dimension — pause density + duration entropy.

Measured from timed segments (post-TTS / align). Two signals:

* **pause_density** = sum(neighbor gaps) / narration_duration
* **duration entropy** H_norm = H / log(K) over K equal-width duration bins

Unavailable rules (frozen):
* single segment (n < 2) → unavailable
* CV sample size < 2 (same as n < 2 for this statistic) → unavailable
* missing / non-positive narration duration → unavailable

Bin parameters (K, lo, hi) are fixture-pinned defaults and can be
overridden via ``content_quality_pacing_*`` job params.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from .schema import DimensionResult

# Fixture-pinned defaults for duration binning and ideal pause band.
DEFAULT_PACING_BINS = 5
DEFAULT_PACING_LO = 0.5  # seconds — durations ≤ lo collapse into bin 0
DEFAULT_PACING_HI = 8.0  # seconds — durations ≥ hi collapse into last bin
DEFAULT_IDEAL_PAUSE_LO = 0.02
DEFAULT_IDEAL_PAUSE_HI = 0.20


def _pause_score(density: float, ideal_lo: float, ideal_hi: float) -> float:
    """Map pause density onto [0, 1]; 1.0 inside the ideal band."""
    if density < 0:
        return 0.0
    if density < ideal_lo:
        return density / ideal_lo if ideal_lo > 0 else 0.0
    if density <= ideal_hi:
        return 1.0
    # Above ideal: linear decay to 0 at 2× ideal_hi.
    excess = (density - ideal_hi) / ideal_hi if ideal_hi > 0 else 1.0
    return max(0.0, 1.0 - excess)


def _duration_entropy_h_norm(durations: Sequence[float], bins: int, lo: float, hi: float) -> float:
    """Normalized Shannon entropy of duration histogram, in [0, 1]."""
    if bins < 2 or not durations:
        return 0.0
    counts = [0] * bins
    span = max(hi - lo, 1e-9)
    for d in durations:
        if d <= lo:
            counts[0] += 1
        elif d >= hi:
            counts[-1] += 1
        else:
            idx = int((d - lo) / span * bins)
            idx = min(max(idx, 0), bins - 1)
            counts[idx] += 1
    total = sum(counts)
    if total <= 0:
        return 0.0
    h = 0.0
    for c in counts:
        if c:
            p = c / total
            h -= p * math.log(p)
    h_max = math.log(bins)
    if h_max <= 0:
        return 0.0
    return h / h_max


def evaluate_pacing(
    timed_segments: Sequence[Any],
    narration_duration: Optional[float] = None,
    *,
    bins: int = DEFAULT_PACING_BINS,
    lo: float = DEFAULT_PACING_LO,
    hi: float = DEFAULT_PACING_HI,
    ideal_pause_lo: float = DEFAULT_IDEAL_PAUSE_LO,
    ideal_pause_hi: float = DEFAULT_IDEAL_PAUSE_HI,
) -> DimensionResult:
    """Compute the pacing dimension from timed segments.

    Args:
        timed_segments: Objects with ``.start`` / ``.end`` (seconds).
        narration_duration: Total narration span. When None, derived as
            ``max(end) - min(start)`` over segments.
        bins: K duration histogram bins.
        lo: Lower bin boundary (seconds).
        hi: Upper bin boundary (seconds).
        ideal_pause_lo: Ideal pause-density band lower bound.
        ideal_pause_hi: Ideal pause-density band upper bound.

    Returns:
        DimensionResult with status measured | unavailable.
    """
    segs = list(timed_segments or [])
    n = len(segs)
    if n < 2:
        return DimensionResult(
            name="pacing",
            score=None,
            status="unavailable",
            details={"reason": "n_segments<2", "n_segments": n},
        )

    starts = [float(s.start) for s in segs]
    ends = [float(s.end) for s in segs]
    if narration_duration is None:
        narration_duration = max(ends) - min(starts)
    narration_duration = float(narration_duration)
    if narration_duration <= 0:
        return DimensionResult(
            name="pacing",
            score=None,
            status="unavailable",
            details={"reason": "narration_duration<=0", "n_segments": n},
        )

    gaps: List[float] = []
    for i in range(n - 1):
        gap = starts[i + 1] - ends[i]
        gaps.append(max(0.0, gap))

    pause_density = sum(gaps) / narration_duration
    durations = [max(0.0, e - s) for s, e in zip(starts, ends)]
    h_norm = _duration_entropy_h_norm(durations, bins, lo, hi)

    p_score = _pause_score(pause_density, ideal_pause_lo, ideal_pause_hi)
    score = 0.5 * p_score + 0.5 * h_norm

    return DimensionResult(
        name="pacing",
        score=round(score, 4),
        status="measured",
        details={
            "pause_density": round(pause_density, 4),
            "duration_entropy_h_norm": round(h_norm, 4),
            "pause_score": round(p_score, 4),
            "n_segments": n,
            "narration_duration": round(narration_duration, 4),
            "bins": bins,
            "lo": lo,
            "hi": hi,
            "ideal_pause_lo": ideal_pause_lo,
            "ideal_pause_hi": ideal_pause_hi,
            "gaps": [round(g, 4) for g in gaps],
        },
    )
