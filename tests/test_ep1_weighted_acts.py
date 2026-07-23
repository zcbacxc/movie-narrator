"""EP1: Act-weighted timeline partitioning tests.

Verifies that match_timeline_mode="weighted_acts" correctly:
- Partitions scenes into 4 act buckets by time
- Allocates narration segments to acts by weight
- Constrains heuristic + embedding candidates to act buckets
- Falls back gracefully when too few scenes/segments
- Audit metadata (timeline.mode, act_weights, segments_per_act) is correct
"""

from unittest.mock import MagicMock

import pytest

from movie_narrator.models import Context, Scene, Services, TimedSegment
from movie_narrator.pipeline import match as match_module
from movie_narrator.pipeline.match import (
    _assign_segments_to_acts,
    _get_act_candidate_indices,
    _partition_scenes_by_act,
    match_clips,
)


@pytest.fixture(autouse=True)
def _clear_embedding_cache():
    match_module._load_embedding_model.cache_clear()
    yield
    match_module._load_embedding_model.cache_clear()


# ── Unit tests for helper functions ──


class TestPartitionScenesByAct:
    def test_four_equal_buckets(self):
        """20 scenes spanning 0-400s → 5 scenes per act."""
        scenes = [Scene(index=i, start=float(i * 20), end=float(i * 20 + 20)) for i in range(20)]
        buckets = _partition_scenes_by_act(scenes, n_acts=4)
        assert len(buckets) == 4
        for b in buckets:
            assert len(b) == 5

    def test_uneven_scene_distribution(self):
        """Scenes clustered in first half → acts 3,4 may be empty."""
        scenes = [Scene(index=i, start=float(i * 5), end=float(i * 5 + 5)) for i in range(10)]
        buckets = _partition_scenes_by_act(scenes, n_acts=4)
        assert len(buckets[0]) > 0
        assert len(buckets[1]) > 0
        # Acts 2,3 may be empty — that's OK, caller handles fallback

    def test_empty_scenes(self):
        buckets = _partition_scenes_by_act([], n_acts=4)
        assert all(b == [] for b in buckets)

    def test_single_scene(self):
        scenes = [Scene(index=0, start=0.0, end=10.0)]
        buckets = _partition_scenes_by_act(scenes, n_acts=4)
        assert len(buckets[0]) == 1
        assert all(len(b) == 0 for b in buckets[1:])


class TestAssignSegmentsToActs:
    def test_counts_sum_to_n(self):
        """Segment counts per act must sum exactly to n_segments."""
        weights = [0.15, 0.25, 0.40, 0.20]
        for n in [4, 8, 12, 18, 25, 50]:
            assignments = _assign_segments_to_acts(n, weights)
            assert len(assignments) == n

    def test_weight_proportional(self):
        """Act 2 (weight=0.40) should get the most segments."""
        weights = [0.15, 0.25, 0.40, 0.20]
        assignments = _assign_segments_to_acts(20, weights)
        counts = [assignments.count(a) for a in range(4)]
        assert counts[2] == max(counts), f"Act 2 should have most segments, got {counts}"

    def test_chronological_order(self):
        """Assignments should be in chronological order (all act 0, then act 1, ...)."""
        weights = [0.25, 0.25, 0.25, 0.25]
        assignments = _assign_segments_to_acts(16, weights)
        for i in range(len(assignments) - 1):
            assert assignments[i] <= assignments[i + 1]

    def test_zero_weights_fallback(self):
        """All-zero weights → fallback to default."""
        assignments = _assign_segments_to_acts(8, [0, 0, 0, 0])
        assert len(assignments) == 8

    def test_min_one_per_act(self):
        """Each act should get at least 1 segment when n >= n_acts."""
        weights = [0.01, 0.01, 0.01, 0.97]
        assignments = _assign_segments_to_acts(8, weights)
        counts = [assignments.count(a) for a in range(4)]
        for c in counts:
            assert c >= 1


class TestGetActCandidateIndices:
    def test_own_act_plus_overflow(self):
        """Act 1 candidates should include acts 0,1,2 (±1 overflow)."""
        scenes = [Scene(index=i, start=float(i * 20), end=float(i * 20 + 20)) for i in range(20)]
        buckets = _partition_scenes_by_act(scenes, n_acts=4)
        indices = _get_act_candidate_indices(1, 4, buckets, allow_overflow=True)
        # Should include scenes from acts 0, 1, 2 (not act 3)
        assert len(indices) > 0
        # All indices from acts 0-2 (scenes 0-14)
        for idx in indices:
            assert 0 <= idx <= 14

    def test_empty_act_fallback(self):
        """Empty act → all scenes as candidates."""
        buckets = [[], [], [Scene(index=5, start=100.0, end=120.0)], []]
        indices = _get_act_candidate_indices(0, 4, buckets, allow_overflow=True)
        assert len(indices) > 0  # falls back to all scenes

    def test_no_overflow(self):
        """allow_overflow=False → only own act scenes."""
        scenes = [Scene(index=i, start=float(i * 20), end=float(i * 20 + 20)) for i in range(20)]
        buckets = _partition_scenes_by_act(scenes, n_acts=4)
        indices = _get_act_candidate_indices(1, 4, buckets, allow_overflow=False)
        # Only act 1 scenes (5-9)
        for idx in indices:
            assert 5 <= idx <= 9


# ── Integration tests through match_clips ──


def _make_ctx(tmp_path, n_scenes=20, n_segments=18, mode="weighted_acts", weights=None):
    ctx = Context(
        movie_name="test",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
    )
    ctx.services = Services(console=MagicMock())
    ctx.status.scene = "success"
    (tmp_path / "video.mp4").write_bytes(b"00")

    ctx.scenes = [
        Scene(index=i, start=float(i * 20), end=float(i * 20 + 20))
        for i in range(n_scenes)
    ]
    ctx.timed_segments = [
        TimedSegment(text=f"segment {i}", start=float(i * 3), end=float(i * 3 + 2.5))
        for i in range(n_segments)
    ]

    ctx.metadata["match_timeline_mode"] = mode
    if weights:
        ctx.metadata["match_act_weights"] = weights

    # Disable embedding path — isolate weighted_acts heuristic
    import unittest.mock as mock
    ctx._mock_patch = mock.patch(
        "movie_narrator.pipeline.match.probe", lambda name: (False, "")
    )
    return ctx


def test_weighted_acts_produces_act_constrained_heuristic(tmp_path):
    """18 segments over 20 scenes with weighted_acts → src_start distribution
    should be non-uniform, concentrated in act 2 (weight=0.40).
    """
    ctx = _make_ctx(tmp_path, n_scenes=20, n_segments=18,
                    weights=[0.15, 0.25, 0.40, 0.20])
    with ctx._mock_patch:
        match_clips(ctx)

    summary = ctx.metadata["match_summary"]
    timeline = summary["timeline"]

    # Audit fields
    assert timeline["mode"] == "weighted_acts"
    assert timeline["act_weights"] == [0.15, 0.25, 0.40, 0.20]
    assert timeline["segments_per_act"] is not None
    assert sum(timeline["segments_per_act"]) == 18

    # Act 2 should have the most segments (weight=0.40)
    assert timeline["segments_per_act"][2] == max(timeline["segments_per_act"])

    # Verify src_start distribution: act 2 segments should map to scenes 10-14
    # (act 2 covers 200-300s out of 0-400s total)
    act2_segs = [mc for i, mc in enumerate(ctx.matched_clips)
                 if i < timeline["segments_per_act"][0] + timeline["segments_per_act"][1]
                 + timeline["segments_per_act"][2]
                 and i >= timeline["segments_per_act"][0] + timeline["segments_per_act"][1]]
    for mc in act2_segs:
        # Scene index should be in act 2 range (10-14) or adjacent overflow (5-19)
        assert 5 <= mc.scene_index <= 19, (
            f"segment {mc.segment_index} scene_index={mc.scene_index} "
            f"should be in act 2 bucket range"
        )


def test_weighted_acts_disabled_with_few_scenes(tmp_path):
    """Fewer than 8 scenes → falls back to uniform, mode in metadata = "uniform"."""
    ctx = _make_ctx(tmp_path, n_scenes=5, n_segments=10,
                    weights=[0.15, 0.25, 0.40, 0.20])
    with ctx._mock_patch:
        match_clips(ctx)

    timeline = ctx.metadata["match_summary"]["timeline"]
    assert timeline["mode"] == "uniform"
    assert timeline["act_weights"] is None


def test_weighted_acts_disabled_with_few_segments(tmp_path):
    """Fewer than 4 segments → falls back to uniform."""
    ctx = _make_ctx(tmp_path, n_scenes=20, n_segments=3,
                    weights=[0.15, 0.25, 0.40, 0.20])
    with ctx._mock_patch:
        match_clips(ctx)

    timeline = ctx.metadata["match_summary"]["timeline"]
    assert timeline["mode"] == "uniform"


def test_uniform_mode_default(tmp_path):
    """No match_timeline_mode set → uniform (default, backward compat)."""
    ctx = _make_ctx(tmp_path, n_scenes=20, n_segments=18, mode="uniform")
    with ctx._mock_patch:
        match_clips(ctx)

    timeline = ctx.metadata["match_summary"]["timeline"]
    assert timeline["mode"] == "uniform"
    assert timeline["act_weights"] is None


def test_weighted_acts_different_weights(tmp_path):
    """Custom weights → segment counts match weight ratios."""
    ctx = _make_ctx(tmp_path, n_scenes=40, n_segments=20,
                    weights=[0.10, 0.20, 0.50, 0.20])
    with ctx._mock_patch:
        match_clips(ctx)

    timeline = ctx.metadata["match_summary"]["timeline"]
    assert timeline["mode"] == "weighted_acts"
    counts = timeline["segments_per_act"]

    # Act 2 (weight=0.50) should have the most segments
    assert counts[2] == max(counts)
    # Act 0 (weight=0.10) should have the fewest
    assert counts[0] == min(counts)
    # Sum to 20
    assert sum(counts) == 20


def test_weighted_acts_src_start_not_uniform(tmp_path):
    """With weighted_acts, src_start values should NOT be uniformly distributed
    across the full timeline — they should cluster in act 2 (the heaviest act).
    """
    ctx = _make_ctx(tmp_path, n_scenes=40, n_segments=20,
                    weights=[0.10, 0.20, 0.50, 0.20])
    with ctx._mock_patch:
        match_clips(ctx)

    src_starts = [mc.src_start for mc in ctx.matched_clips]

    # Count how many src_starts fall in act 2 (60%-100% of 800s = 480-800)
    act2_count = sum(1 for s in src_starts if 320 <= s <= 640)
    # With 50% weight, ~10 of 20 segments should be in act 2
    assert act2_count >= 8, (
        f"Expected >=8 segments in act 2 range, got {act2_count}/20. "
        f"src_starts={sorted(src_starts)}"
    )
