# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Match quality scoring — composite score aggregation across embedding, rhythm, diversity.

v0.5.11: Provides per-clip composite quality scores by combining embedding
cosine similarity, rhythm zone alignment, and scene diversity penalties.

All scores are advisory — stored in ``MatchedClip`` fields and aggregated
in ``ctx.metadata["match_quality"]`` for diagnostics.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from ..models import MatchedClip


# ── Default weights for composite score ───────────────────
# Embedding similarity is the primary signal (60%).
# Rhythm alignment is a secondary bonus (25%).
# Diversity is a penalty dimension (15%) — high reuse lowers the score.
_DEFAULT_WEIGHTS = {
    "embedding": 0.60,
    "rhythm": 0.25,
    "diversity": 0.15,
}

# Default diversity penalty parameters
_DEFAULT_DIVERSITY_WINDOW = 3
_DEFAULT_MAX_REUSE = 2
_DIVERSITY_PENALTY_PER_OVERUSE = 0.15  # 15% penalty per overuse within window


# ── Data structures ───────────────────────────────────────


@dataclass
class MatchQualitySummary:
    """Aggregated match quality metrics for the entire pipeline."""

    total_clips: int = 0
    clips_with_composite: int = 0
    avg_composite: float = 0.0
    min_composite: float = 0.0
    max_composite: float = 0.0
    avg_embedding: float = 0.0
    avg_rhythm: float = 0.0
    avg_diversity: float = 0.0
    low_quality_count: int = 0  # composite < 0.4
    low_quality_indices: list[int] = field(default_factory=list)
    diversity_penalty_count: int = 0  # clips with diversity_score < 1.0

    def to_dict(self) -> dict:
        return {
            "total_clips": self.total_clips,
            "clips_with_composite": self.clips_with_composite,
            "avg_composite": round(self.avg_composite, 4),
            "min_composite": round(self.min_composite, 4),
            "max_composite": round(self.max_composite, 4),
            "avg_embedding": round(self.avg_embedding, 4),
            "avg_rhythm": round(self.avg_rhythm, 4),
            "avg_diversity": round(self.avg_diversity, 4),
            "low_quality_count": self.low_quality_count,
            "low_quality_indices": self.low_quality_indices,
            "diversity_penalty_count": self.diversity_penalty_count,
        }


# ── Composite score computation ───────────────────────────


def compute_composite_score(
    embedding_score: Optional[float],
    rhythm_score: Optional[float],
    diversity_score: Optional[float],
    *,
    weights: dict[str, float] | None = None,
) -> Optional[float]:
    """Compute a composite quality score from individual dimensions.

    Each dimension is in [0.0, 1.0].  The composite is a weighted average
    of available dimensions.  Missing dimensions (None) are excluded and
    their weight is redistributed proportionally.

    Returns None if all dimensions are None (e.g. heuristic clips).
    """
    if weights is None:
        weights = _DEFAULT_WEIGHTS

    available: dict[str, float] = {}
    for dim, val in [
        ("embedding", embedding_score),
        ("rhythm", rhythm_score),
        ("diversity", diversity_score),
    ]:
        if val is not None:
            available[dim] = max(0.0, min(1.0, val))

    if not available:
        return None

    total_weight = sum(weights[dim] for dim in available)
    if total_weight <= 0:
        return None

    weighted_sum = sum(available[dim] * weights[dim] for dim in available)
    return round(weighted_sum / total_weight, 4)


# ── Diversity scoring ─────────────────────────────────────


def compute_diversity_scores(
    matched_clips: list[MatchedClip],
    *,
    window: int = _DEFAULT_DIVERSITY_WINDOW,
    max_reuse: int = _DEFAULT_MAX_REUSE,
) -> list[float]:
    """Compute per-clip diversity scores based on scene reuse.

    For each clip, checks how many times the same ``scene_index`` appears
    in the previous ``window`` clips.  If reuse exceeds ``max_reuse``,
    a penalty is applied:

        diversity_score = 1.0 - (overuse_count * _DIVERSITY_PENALTY_PER_OVERUSE)

    Clipped to [0.0, 1.0].

    Returns a list of diversity scores (one per clip).  Heuristic clips
    (no scene_index) receive 1.0 (no penalty).
    """
    scores: list[float] = []
    scene_history: list[int] = []

    for mc in matched_clips:
        if mc.scene_index is None:
            scores.append(1.0)
            continue

        # Count how many times this scene appeared in the last `window` clips
        recent = scene_history[-window:] if window > 0 else []
        reuse_count = recent.count(mc.scene_index)

        if reuse_count > max_reuse:
            overuse = reuse_count - max_reuse
            score = max(0.0, 1.0 - overuse * _DIVERSITY_PENALTY_PER_OVERUSE)
        else:
            score = 1.0

        scores.append(score)
        scene_history.append(mc.scene_index)

    return scores


# ── Per-clip scoring ──────────────────────────────────────


def score_clips(
    matched_clips: list[MatchedClip],
    *,
    rhythm_scores: list[Optional[float]] | None = None,
    diversity_window: int = _DEFAULT_DIVERSITY_WINDOW,
    diversity_max_reuse: int = _DEFAULT_MAX_REUSE,
    weights: dict[str, float] | None = None,
) -> int:
    """Compute and assign per-dimension scores to matched clips.

    Assigns ``embedding_score``, ``rhythm_score``, ``diversity_score``,
    and ``composite_score`` to each clip.

    - ``embedding_score``: the clip's existing ``score`` if from embedding
      path, else None.
    - ``rhythm_score``: from the ``rhythm_scores`` list if provided, else None.
    - ``diversity_score``: computed from scene reuse patterns.
    - ``composite_score``: weighted average of available dimensions.

    Modifies clips in-place.  Returns count of clips with composite scores.
    """
    # Compute diversity scores for all clips
    diversity_scores = compute_diversity_scores(
        matched_clips,
        window=diversity_window,
        max_reuse=diversity_max_reuse,
    )

    count = 0
    for i, mc in enumerate(matched_clips):
        # Embedding score: use the existing score if from embedding path
        if mc.source in ("embedding", "embedding_topk", "embedding_top1"):
            mc.embedding_score = round(max(0.0, min(1.0, mc.score)), 4)
        else:
            mc.embedding_score = None

        # Rhythm score: from provided list
        if rhythm_scores and i < len(rhythm_scores):
            mc.rhythm_score = rhythm_scores[i]
        else:
            mc.rhythm_score = None

        # Diversity score
        mc.diversity_score = round(diversity_scores[i], 4)

        # Composite score
        mc.composite_score = compute_composite_score(
            mc.embedding_score,
            mc.rhythm_score,
            mc.diversity_score,
            weights=weights,
        )

        if mc.composite_score is not None:
            count += 1

    return count


# ── Aggregation ───────────────────────────────────────────


def aggregate_match_quality(
    matched_clips: list[MatchedClip],
) -> MatchQualitySummary:
    """Aggregate per-clip quality scores into a summary.

    Returns a :class:`MatchQualitySummary` suitable for
    ``ctx.metadata["match_quality"]``.
    """
    summary = MatchQualitySummary(total_clips=len(matched_clips))

    composites = [
        mc.composite_score for mc in matched_clips
        if mc.composite_score is not None
    ]
    embeddings = [
        mc.embedding_score for mc in matched_clips
        if mc.embedding_score is not None
    ]
    rhythms = [
        mc.rhythm_score for mc in matched_clips
        if mc.rhythm_score is not None
    ]
    diversities = [
        mc.diversity_score for mc in matched_clips
        if mc.diversity_score is not None
    ]

    summary.clips_with_composite = len(composites)

    if composites:
        summary.avg_composite = sum(composites) / len(composites)
        summary.min_composite = min(composites)
        summary.max_composite = max(composites)

    if embeddings:
        summary.avg_embedding = sum(embeddings) / len(embeddings)

    if rhythms:
        summary.avg_rhythm = sum(rhythms) / len(rhythms)

    if diversities:
        summary.avg_diversity = sum(diversities) / len(diversities)

    # Flag low-quality clips (composite < 0.4)
    for i, mc in enumerate(matched_clips):
        if mc.composite_score is not None and mc.composite_score < 0.4:
            summary.low_quality_count += 1
            summary.low_quality_indices.append(i)
        if mc.diversity_score is not None and mc.diversity_score < 1.0:
            summary.diversity_penalty_count += 1

    return summary
