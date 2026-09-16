# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Semantic-visual relevance — brightness/color proxy from luma + hist_rgb.

Frozen constraints:
* **luma + hist_rgb only** (no motion features)
* always ``status="proxy"`` when measured (not a true semantic judge)
* reads ``match_visual_features_samples`` produced by the match step's
  opt-in visual-feature pipeline (``utils/visual_features.py``)

Scoring (brightness/color cue):
* brightness fitness — mid-luma preferred; too dark / too blown clips score low
* color richness — normalized Shannon entropy of the RGB histogram
* score = mean(brightness_fitness, color_richness) averaged across samples
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

from .schema import DimensionResult

# Preferred mean-luma band (0–255). Outside the band the score decays.
_LUMA_LO = 40.0
_LUMA_HI = 200.0
# Ideal peak (used for triangular fitness).
_LUMA_IDEAL = 120.0


def _brightness_fitness(luma: float) -> float:
    """Map mean luma onto [0, 1]; peak at _LUMA_IDEAL."""
    if luma < 0:
        return 0.0
    if luma < _LUMA_LO:
        return max(0.0, luma / _LUMA_LO) * 0.5
    if luma > _LUMA_HI:
        # Decay from 1 at HI toward 0 at 255.
        span = max(255.0 - _LUMA_HI, 1.0)
        return max(0.0, 1.0 - (luma - _LUMA_HI) / span)
    # Inside band: triangular peak at ideal.
    if luma <= _LUMA_IDEAL:
        return 0.5 + 0.5 * (luma - _LUMA_LO) / max(_LUMA_IDEAL - _LUMA_LO, 1e-9)
    return 0.5 + 0.5 * (_LUMA_HI - luma) / max(_LUMA_HI - _LUMA_IDEAL, 1e-9)


def _hist_richness(hist: Sequence[float]) -> float:
    """Normalized Shannon entropy of a non-negative histogram, in [0, 1]."""
    values = [max(0.0, float(x)) for x in hist]
    total = sum(values)
    if total <= 0 or len(values) < 2:
        return 0.0
    h = 0.0
    for v in values:
        if v > 0:
            p = v / total
            h -= p * math.log(p)
    h_max = math.log(len(values))
    if h_max <= 0:
        return 0.0
    return h / h_max


def evaluate_semantic_visual_relevance(
    visual_samples: Optional[Sequence[Dict[str, Any]]],
) -> DimensionResult:
    """Proxy score from luma + hist_rgb samples.

    Args:
        visual_samples: List of dicts with ``luma`` (float) and
            ``hist_rgb`` (list[float]) — typically
            ``metadata["match_visual_features_samples"]``.

    Returns:
        DimensionResult with status proxy | unavailable. Never measured:
        this is a brightness/color cue, not a semantic judge.
    """
    samples = [s for s in (visual_samples or []) if isinstance(s, dict)]
    if not samples:
        return DimensionResult(
            name="semantic_visual_relevance",
            score=None,
            status="unavailable",
            details={"reason": "no_visual_features"},
        )

    brightness_scores: List[float] = []
    richness_scores: List[float] = []
    used = 0
    for s in samples:
        luma = s.get("luma")
        hist = s.get("hist_rgb")
        if luma is None or hist is None:
            continue
        try:
            luma_f = float(luma)
            hist_f = [float(x) for x in hist]
        except (TypeError, ValueError):
            continue
        brightness_scores.append(_brightness_fitness(luma_f))
        richness_scores.append(_hist_richness(hist_f))
        used += 1

    if used == 0:
        return DimensionResult(
            name="semantic_visual_relevance",
            score=None,
            status="unavailable",
            details={"reason": "samples_missing_luma_or_hist", "n_samples": len(samples)},
        )

    brightness = sum(brightness_scores) / used
    richness = sum(richness_scores) / used
    score = 0.5 * brightness + 0.5 * richness

    return DimensionResult(
        name="semantic_visual_relevance",
        score=round(score, 4),
        status="proxy",
        details={
            "n_samples": used,
            "brightness_fitness": round(brightness, 4),
            "color_richness": round(richness, 4),
            "luma_band": [_LUMA_LO, _LUMA_HI],
            "luma_ideal": _LUMA_IDEAL,
            "note": "luma+hist_rgb brightness/color cue only; no motion",
        },
    )
