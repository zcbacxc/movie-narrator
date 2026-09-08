# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Matching: act partitioning, rhythm scoring, and greedy top-K assignment."""

from typing import List, Optional

from ...models import Scene
from .embed import _cosine_top1, _cosine_topk

_DEFAULT_ACT_WEIGHTS = [0.15, 0.25, 0.40, 0.20]

# Maps each rhythm_zone to the preferred timeline position (normalized 0–1).
# "hook"   → early film (0.15)  — establishing shots, dynamic intros
# "rising" → mid-early (0.40)   — rising action builds
# "peak"   → mid-late  (0.65)   — climax / confrontation
# "settle" → late film (0.85)   — resolution, denouement
_RHYTHM_ZONE_TIMELINE_CENTER: dict[str, float] = {
    "hook": 0.15,
    "rising": 0.40,
    "peak": 0.65,
    "settle": 0.85,
}

# Maximum bonus applied to a candidate scene's score when it sits exactly
# at the rhythm zone's preferred timeline position.  Kept small (0.15) so
# the adjustment is a *soft hint* — semantic similarity still dominates.
_RHYTHM_ADJUSTMENT_MAX = 0.15


def _partition_scenes_by_act(
    scenes: List[Scene],
    n_acts: int = 4,
) -> List[List[Scene]]:
    """Partition scenes into *n_acts* equal-time buckets.

    Returns:
        A list of scene lists, one per act.  Acts with no scenes
        get an empty list — the caller is responsible for fallback.
    """
    if not scenes:
        return [[] for _ in range(n_acts)]

    scene_start = min(s.start for s in scenes)
    scene_end = max(s.end for s in scenes)
    span = scene_end - scene_start
    if span <= 0:
        return [list(scenes)] + [[] for _ in range(n_acts - 1)]

    bucket_size = span / n_acts
    buckets: List[List[Scene]] = [[] for _ in range(n_acts)]
    for s in scenes:
        idx = min(n_acts - 1, int((s.start - scene_start) / bucket_size))
        buckets[idx].append(s)
    return buckets


def _assign_segments_to_acts(
    n_segments: int,
    weights: List[float],
) -> List[int]:
    """Assign *n_segments* narration segments to acts by *weights*.

    Returns:
        A list of act indices (0-based), one per segment, in
        chronological order.  Segment counts per act are proportional to
        weights, adjusted to sum exactly to *n_segments*.
    """
    total = sum(weights)
    if total <= 0:
        weights = list(_DEFAULT_ACT_WEIGHTS)
        total = sum(weights)
    norm = [w / total for w in weights]

    # Raw counts (may not sum to n_segments due to rounding)
    counts = [max(1, round(n_segments * w)) for w in norm]

    # Adjust to sum exactly to n_segments
    while sum(counts) > n_segments:
        max_idx = counts.index(max(counts))
        counts[max_idx] -= 1
    while sum(counts) < n_segments:
        max_idx = counts.index(max(counts))
        counts[max_idx] += 1

    # Build chronological assignment list
    assignments: List[int] = []
    for act_idx, count in enumerate(counts):
        assignments.extend([act_idx] * count)
    return assignments


def _get_act_candidate_indices(
    act_idx: int,
    n_acts: int,
    act_scenes: List[List[Scene]],
    allow_overflow: bool = True,
) -> List[int]:
    """
    Returns:
        Global scene indices for act *act_idx* + optional adjacent overflow.

        When the target act has no scenes, expands search to all acts.
    """
    # Start with the act's own scenes
    indices = [s.index for s in act_scenes[act_idx]]

    if not indices and allow_overflow:
        # Act is empty — fall back to all scenes
        for bucket in act_scenes:
            indices.extend(s.index for s in bucket)
        return indices

    if allow_overflow and len(act_scenes) > 1:
        # Add adjacent acts (±1) for overflow candidates
        for delta in (-1, 1):
            neighbor = act_idx + delta
            if 0 <= neighbor < len(act_scenes):
                indices.extend(s.index for s in act_scenes[neighbor])

    return indices


def _apply_rhythm_density_hint(merge_min: float, beats_meta: list[dict]) -> float:
    """Nudge the scene-merge threshold based on rhythm_zone markings.

    This is a *soft hint*, not a hard override: the configured
    ``scene_merge_min_duration`` is blended with the per-beat rhythm
    signals so that "hook" beats (which benefit from rapid, dense cuts)
    lower the threshold, while "settle" beats (which benefit from
    longer, calmer scenes) raise it. The adjustment is bounded so the
    value never collapses to zero or grows unbounded.

    ``hook``  -> more scenes (denser)  -> lower merge threshold.
    ``settle``-> fewer scenes (sparser)-> higher merge threshold.

    Returns:
        The (possibly adjusted) merge_min. When beats_meta is empty,
        no beat carries a rhythm_zone, or merge_min is non-positive, the
        original value is returned unchanged.
    """
    if not beats_meta or merge_min <= 0:
        return merge_min

    hooks = sum(1 for bm in beats_meta if bm.get("rhythm_zone") == "hook")
    settles = sum(1 for bm in beats_meta if bm.get("rhythm_zone") == "settle")
    if hooks == 0 and settles == 0:
        return merge_min

    # Net signal in [-1, 1]: positive = hook-heavy (denser),
    # negative = settle-heavy (sparser).
    net = (hooks - settles) / len(beats_meta)
    # Bound the multiplicative nudge to ±40% of the configured threshold
    # so it stays a soft hint rather than a hard override.
    factor = 1.0 - 0.4 * net
    adjusted = merge_min * factor
    # Floor at 0.5s — a near-zero threshold would over-fragment scenes.
    return max(0.5, adjusted)


def _compute_rhythm_adjustment(
    rhythm_zone: Optional[str],
    scene: Scene,
    scene_start: float,
    scene_span: float,
) -> float:
    """Soft score bonus for scenes whose timeline position matches a rhythm zone.

    Returns:
        A value in ``[0, _RHYTHM_ADJUSTMENT_MAX]``.  Zero when
        *rhythm_zone* is ``None``, unknown, or *scene_span* is non-positive.

        Only applies a **positive bonus** (never a penalty) so that a strong
        semantic match in the "wrong" timeline position is still selected —
        the rhythm zone merely breaks ties or nudges near-equal candidates.
    """
    if rhythm_zone is None or scene_span <= 0:
        return 0.0

    center = _RHYTHM_ZONE_TIMELINE_CENTER.get(rhythm_zone)
    if center is None:
        return 0.0

    scene_mid = (scene.start + scene.end) / 2.0
    scene_pos = (scene_mid - scene_start) / scene_span
    scene_pos = max(0.0, min(1.0, scene_pos))

    # Linear falloff: full bonus at *center*, zero at distance ≥ 1.0
    distance = abs(scene_pos - center)
    return _RHYTHM_ADJUSTMENT_MAX * max(0.0, 1.0 - distance)


def _greedy_topk_assign(
    narration_vecs,
    scene_vecs,
    scenes: List[Scene],
    topk: int = 5,
    reuse_penalty: float = 0.15,
    reuse_window: int = 3,
    use_weighted_acts: bool = False,
    act_assignments: Optional[list[int]] = None,
    act_scenes: Optional[list[list[Scene]]] = None,
    act_weights: Optional[list[float]] = None,
    beats_meta: Optional[list[dict]] = None,
    scene_start: float = 0.0,
    scene_span: float = 0.0,
) -> list[tuple[int, float, str]]:
    """Greedy top-K assignment with order-backtrack reuse penalty.

    For each narration segment, computes top-K candidate scenes from the
    embedding similarity, then picks the candidate with the highest
    *adjusted* score — where scenes used in the last ``reuse_window``
    segments get a ``reuse_penalty`` deduction.

    When *beats_meta* is provided and a beat carries a ``rhythm_zone``,
    a soft timeline-position bonus is added to each
    candidate's raw score so that scenes at the rhythm zone's preferred
    position in the film get a small boost.  The bonus is bounded by
    ``_RHYTHM_ADJUSTMENT_MAX`` and never turns into a penalty.

    Returns:
        A list of ``(scene_index, score, source)`` per segment.
        ``source`` is ``"embedding_topk"`` when top-K ran, ``"embedding_top1"``
        when top-K is disabled (k <= 1).
    """
    import numpy as np

    n_seg = len(narration_vecs)
    n_scenes = len(scenes)
    results: list[tuple[int, float, str]] = []
    recent_usage: list[int] = []  # scene indices used recently (chronological)

    source = "embedding_topk" if topk > 1 else "embedding_top1"

    for i in range(n_seg):
        # Rhythm zone for this segment (None if not available)
        rhythm_zone = None
        if beats_meta and i < len(beats_meta):
            rhythm_zone = beats_meta[i].get("rhythm_zone")

        # Determine candidate pool
        if use_weighted_acts and act_assignments and act_scenes and act_weights:
            act_idx = act_assignments[i]
            cand_indices = _get_act_candidate_indices(act_idx, len(act_weights), act_scenes)
            cand_indices = [idx for idx in cand_indices if idx < n_scenes]
            if not cand_indices:
                cand_indices = list(range(n_scenes))
        else:
            cand_indices = list(range(n_scenes))

        cand_vecs = scene_vecs[np.array(cand_indices)]

        if topk > 1:
            top_candidates = _cosine_topk(narration_vecs[i], cand_vecs, k=topk)
        else:
            # Fallback to top-1
            best_local = _cosine_top1(narration_vecs[i], cand_vecs)
            if best_local < 0:
                results.append((0, 1.0, source))
                continue
            top_candidates = [(best_local, float(cand_vecs[best_local] @ narration_vecs[i]))]

        if not top_candidates:
            results.append((0, 1.0, source))
            continue

        # Adjust scores with reuse penalty + rhythm zone bonus
        recent_set = set(recent_usage[-reuse_window:]) if recent_usage else set()
        # Initialise from the first candidate's *adjusted* score (not raw)
        # so that the penalty on a recently-used top-1 candidate can actually
        # let a lower-ranked candidate win.
        first_global = cand_indices[top_candidates[0][0]]
        first_raw = top_candidates[0][1]
        first_rhythm = _compute_rhythm_adjustment(
            rhythm_zone, scenes[first_global], scene_start, scene_span
        )
        first_adjusted = first_raw + first_rhythm
        if first_global in recent_set:
            first_adjusted -= reuse_penalty
        best_global_idx = first_global
        best_adjusted = first_adjusted
        best_raw = first_raw

        for local_idx, raw_score in top_candidates:
            global_idx = cand_indices[local_idx]
            rhythm_bonus = _compute_rhythm_adjustment(
                rhythm_zone, scenes[global_idx], scene_start, scene_span
            )
            adjusted = raw_score + rhythm_bonus
            if global_idx in recent_set:
                adjusted -= reuse_penalty
            if adjusted > best_adjusted:
                best_adjusted = adjusted
                best_global_idx = global_idx
                best_raw = raw_score

        results.append((best_global_idx, best_raw, source))
        recent_usage.append(best_global_idx)

    return results
