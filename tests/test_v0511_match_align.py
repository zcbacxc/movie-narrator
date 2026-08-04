"""Tests for v0.5.11 Match & Alignment Precision features.

Covers:
- Word-level alignment: extraction, assignment, remapping
- Alignment confidence scoring: per-segment confidence, low-confidence flagging
- Alignment QA: validation, drift detection with tightened threshold
- Match quality: composite score, diversity penalty, aggregation
- Pipeline integration: align.py and match.py with new features
"""

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from movie_narrator.models import (
    Context,
    MatchedClip,
    Scene,
    Services,
    TimedSegment,
    WordSegment,
)
from movie_narrator.utils.alignment_qa import (
    AlignmentQualityMetrics,
    _DRIFT_THRESHOLD_V0511,
    _LOW_CONFIDENCE_THRESHOLD,
    extract_word_segments,
    assign_words_to_segments,
    compute_segment_confidence,
    flag_low_confidence_segments,
    word_level_remap,
    check_drift,
    validate_alignment,
)
from movie_narrator.utils.match_quality import (
    MatchQualitySummary,
    _DEFAULT_WEIGHTS,
    _DIVERSITY_PENALTY_PER_OVERUSE,
    compute_composite_score,
    compute_diversity_scores,
    score_clips,
    aggregate_match_quality,
)


# ── Helpers ────────────────────────────────────────────────


def _make_word_segments(n: int = 5, start: float = 0.0, gap: float = 0.5) -> list[dict]:
    """Create word-level segment dicts for testing."""
    words = []
    t = start
    for i in range(n):
        words.append(
            {
                "word": f"word{i}",
                "start": t,
                "end": t + gap * 0.8,
                "score": 0.8 + 0.03 * i,  # varying scores 0.80–0.92
            }
        )
        t += gap
    return words


def _make_matched_clips(
    n: int = 5,
    scene_indices: list[int] | None = None,
    sources: list[str] | None = None,
    scores: list[float] | None = None,
) -> list[MatchedClip]:
    """Create matched clips for testing."""
    if scene_indices is None:
        scene_indices = [i % 3 for i in range(n)]
    if sources is None:
        sources = ["embedding"] * n
    if scores is None:
        scores = [0.7 + 0.05 * i for i in range(n)]

    clips = []
    for i in range(n):
        clips.append(
            MatchedClip(
                segment_index=i,
                text=f"segment {i}",
                narr_start=float(i * 2.0),
                narr_end=float(i * 2.0 + 1.5),
                src_start=float(i * 3.0),
                src_end=float(i * 3.0 + 2.5),
                score=scores[i],
                scene_index=scene_indices[i],
                source=sources[i],
            )
        )
    return clips


# ── 1. Word segment extraction ────────────────────────────


def test_extract_word_segments_normal():
    result = {
        "word_segments": [
            {"word": "hello", "start": 0.0, "end": 0.5, "score": 0.9},
            {"word": "world", "start": 0.5, "end": 1.0, "score": 0.85},
        ]
    }
    words = extract_word_segments(result)
    assert len(words) == 2
    assert words[0]["word"] == "hello"
    assert words[0]["start"] == 0.0
    assert words[0]["end"] == 0.5
    assert words[0]["score"] == 0.9


def test_extract_word_segments_empty():
    assert extract_word_segments({}) == []
    assert extract_word_segments({"word_segments": []}) == []


def test_extract_word_segments_skips_empty_words():
    result = {
        "word_segments": [
            {"word": "", "start": 0.0, "end": 0.5, "score": 0.9},
            {"word": "  ", "start": 0.5, "end": 1.0, "score": 0.85},
            {"word": "real", "start": 1.0, "end": 1.5, "score": 0.8},
        ]
    }
    words = extract_word_segments(result)
    assert len(words) == 1
    assert words[0]["word"] == "real"


def test_extract_word_segments_missing_fields():
    result = {
        "word_segments": [
            {"word": "test"},  # missing start/end/score
        ]
    }
    words = extract_word_segments(result)
    assert len(words) == 1
    assert words[0]["start"] == 0.0
    assert words[0]["end"] == 0.0
    assert words[0]["score"] == 0.0


# ── 2. Word-to-segment assignment ─────────────────────────


def test_assign_words_to_segments_normal():
    segs = [
        TimedSegment(text="hello world", start=0.0, end=1.0),
        TimedSegment(text="foo bar", start=1.0, end=2.0),
    ]
    words = [
        {"word": "hello", "start": 0.1, "end": 0.5, "score": 0.9},
        {"word": "world", "start": 0.5, "end": 0.9, "score": 0.85},
        {"word": "foo", "start": 1.1, "end": 1.5, "score": 0.8},
        {"word": "bar", "start": 1.5, "end": 1.9, "score": 0.75},
    ]
    count = assign_words_to_segments(segs, words, [])
    assert count == 2
    assert len(segs[0].words) == 2
    assert segs[0].words[0].word == "hello"
    assert segs[0].words[1].word == "world"
    assert len(segs[1].words) == 2
    assert segs[1].words[0].word == "foo"
    assert segs[0].confidence > 0
    assert segs[1].confidence > 0


def test_assign_words_empty():
    segs = [TimedSegment(text="test", start=0.0, end=1.0)]
    count = assign_words_to_segments(segs, [], [])
    assert count == 0
    assert segs[0].words == []
    assert segs[0].confidence == 0.0


def test_assign_words_partial_overlap():
    segs = [
        TimedSegment(text="hello", start=0.5, end=1.5),
    ]
    words = [
        {"word": "before", "start": 0.0, "end": 0.4, "score": 0.9},
        {"word": "hello", "start": 0.6, "end": 1.2, "score": 0.85},
        {"word": "after", "start": 1.6, "end": 2.0, "score": 0.8},
    ]
    count = assign_words_to_segments(segs, words, [])
    assert count == 1
    assert len(segs[0].words) == 1
    assert segs[0].words[0].word == "hello"


# ── 3. Confidence scoring ─────────────────────────────────


def test_compute_segment_confidence_normal():
    words = [
        WordSegment(word="a", start=0.0, end=0.5, score=0.9),
        WordSegment(word="b", start=0.5, end=1.0, score=0.7),
    ]
    assert compute_segment_confidence(words) == pytest.approx(0.8)


def test_compute_segment_confidence_empty():
    assert compute_segment_confidence([]) == 0.0


def test_compute_segment_confidence_zero_scores():
    words = [
        WordSegment(word="a", start=0.0, end=0.5, score=0.0),
        WordSegment(word="b", start=0.5, end=1.0, score=0.0),
    ]
    assert compute_segment_confidence(words) == 0.0


def test_compute_segment_confidence_mixed_scores():
    words = [
        WordSegment(word="a", start=0.0, end=0.5, score=0.0),  # filtered out
        WordSegment(word="b", start=0.5, end=1.0, score=0.8),
    ]
    assert compute_segment_confidence(words) == pytest.approx(0.8)


def test_flag_low_confidence_segments():
    segs = [
        TimedSegment(text="high", start=0.0, end=1.0, confidence=0.9),
        TimedSegment(text="low", start=1.0, end=2.0, confidence=0.3),
        TimedSegment(text="none", start=2.0, end=3.0, confidence=0.0),  # no word data
        TimedSegment(text="borderline", start=3.0, end=4.0, confidence=0.55),
    ]
    indices = flag_low_confidence_segments(segs)
    assert 1 in indices  # 0.3 < 0.6
    assert 3 in indices  # 0.55 < 0.6
    assert 0 not in indices  # 0.9 > 0.6
    assert 2 not in indices  # 0.0 = no word data, not flagged


def test_flag_low_confidence_custom_threshold():
    segs = [
        TimedSegment(text="a", start=0.0, end=1.0, confidence=0.7),
    ]
    assert flag_low_confidence_segments(segs, threshold=0.8) == [0]
    assert flag_low_confidence_segments(segs, threshold=0.5) == []


# ── 4. Word-level remapping ───────────────────────────────


def test_word_level_remap_tightens():
    segs = [
        TimedSegment(text="hello", start=0.0, end=2.0),
    ]
    words = [
        {"word": "hello", "start": 0.2, "end": 0.8, "score": 0.9},
    ]
    tightened = word_level_remap(segs, words)
    assert tightened == 1
    assert segs[0].start == 0.2
    assert segs[0].end == 0.8


def test_word_level_remap_no_words():
    segs = [TimedSegment(text="test", start=0.0, end=1.0)]
    assert word_level_remap(segs, []) == 0
    assert segs[0].start == 0.0
    assert segs[0].end == 1.0


def test_word_level_remap_monotonic():
    segs = [
        TimedSegment(text="a", start=0.0, end=1.0),
        TimedSegment(text="b", start=1.0, end=2.0),
    ]
    words = [
        {"word": "a", "start": 0.1, "end": 0.9, "score": 0.9},
        {"word": "b", "start": 0.8, "end": 1.5, "score": 0.8},  # overlaps with first
    ]
    tightened = word_level_remap(segs, words)
    # Second segment should be clamped to not overlap with first
    assert segs[1].start >= segs[0].end


def test_word_level_remap_no_widening():
    """Word-level remap should only narrow, never widen segment boundaries."""
    segs = [
        TimedSegment(text="hello", start=0.5, end=1.5),
    ]
    words = [
        {"word": "hello", "start": 0.0, "end": 2.0, "score": 0.9},  # wider than segment
    ]
    tightened = word_level_remap(segs, words)
    assert tightened == 0  # no tightening since word range is wider
    assert segs[0].start == 0.5
    assert segs[0].end == 1.5


# ── 5. Drift detection ────────────────────────────────────


def test_check_drift_within_threshold():
    segs = [TimedSegment(text="a", start=0.0, end=4.5)]
    wx = [{"start": 0.0, "end": 5.0, "text": "test"}]
    # drift = |5.0 - 4.5| / 4.5 = 0.111 < 0.3
    is_drift, ratio = check_drift(segs, wx)
    assert not is_drift
    assert ratio < 0.3


def test_check_drift_exceeds_threshold():
    segs = [TimedSegment(text="a", start=0.0, end=4.5)]
    wx = [{"start": 0.0, "end": 10.0, "text": "test"}]
    # drift = |10.0 - 4.5| / 4.5 = 1.22 > 0.3
    is_drift, ratio = check_drift(segs, wx)
    assert is_drift
    assert ratio > 0.3


def test_check_drift_boundary():
    """At exactly 0.3 drift ratio, should not trigger (uses >, not >=)."""
    segs = [TimedSegment(text="a", start=0.0, end=10.0)]
    wx = [{"start": 0.0, "end": 13.0, "text": "test"}]
    # drift = |13.0 - 10.0| / 10.0 = 0.3
    is_drift, ratio = check_drift(segs, wx)
    assert not is_drift  # exactly at threshold, not above


def test_check_drift_multiple_segments():
    """Drift check only applies when ASR returns exactly 1 segment."""
    segs = [TimedSegment(text="a", start=0.0, end=1.0)]
    wx = [
        {"start": 0.0, "end": 1.0, "text": "a"},
        {"start": 1.0, "end": 2.0, "text": "b"},
    ]
    is_drift, _ = check_drift(segs, wx)
    assert not is_drift


def test_drift_threshold_is_03():
    """v0.5.11: drift threshold tightened from 0.5 to 0.3."""
    assert _DRIFT_THRESHOLD_V0511 == 0.3


# ── 6. Alignment QA validation ────────────────────────────


def test_validate_alignment_no_words():
    segs = [
        TimedSegment(text="a", start=0.0, end=1.0),
        TimedSegment(text="b", start=1.0, end=2.0),
    ]
    metrics = validate_alignment(segs)
    assert metrics.total_segments == 2
    assert metrics.segments_with_words == 0
    assert not metrics.word_level_available
    assert metrics.low_confidence_count == 0


def test_validate_alignment_with_confidence():
    segs = [
        TimedSegment(text="high", start=0.0, end=1.0, confidence=0.9),
        TimedSegment(text="low", start=1.0, end=2.0, confidence=0.3),
        TimedSegment(text="none", start=2.0, end=3.0, confidence=0.0),
    ]
    metrics = validate_alignment(segs)
    assert metrics.total_segments == 3
    assert metrics.segments_with_words == 2
    assert metrics.word_level_available
    assert metrics.avg_confidence == pytest.approx(0.6)
    assert metrics.min_confidence == pytest.approx(0.3)
    assert metrics.max_confidence == pytest.approx(0.9)
    assert metrics.low_confidence_count == 1
    assert 1 in metrics.low_confidence_indices


def test_validate_alignment_to_dict():
    segs = [TimedSegment(text="a", start=0.0, end=1.0, confidence=0.8)]
    metrics = validate_alignment(segs)
    d = metrics.to_dict()
    assert d["total_segments"] == 1
    assert d["segments_with_words"] == 1
    assert d["word_level_available"] is True
    assert "avg_confidence" in d
    assert "low_confidence_indices" in d


# ── 7. Composite score computation ────────────────────────


def test_compute_composite_score_all_dimensions():
    score = compute_composite_score(0.8, 0.7, 1.0)
    expected = (0.8 * 0.60 + 0.7 * 0.25 + 1.0 * 0.15) / (0.60 + 0.25 + 0.15)
    assert score == pytest.approx(round(expected, 4))


def test_compute_composite_score_partial():
    # Only embedding available
    score = compute_composite_score(0.8, None, None)
    assert score == pytest.approx(0.8)  # 100% weight to embedding

    # Only diversity available
    score = compute_composite_score(None, None, 0.5)
    assert score == pytest.approx(0.5)


def test_compute_composite_score_all_none():
    assert compute_composite_score(None, None, None) is None


def test_compute_composite_score_clamped():
    # Values outside [0, 1] are clamped
    score = compute_composite_score(1.5, -0.5, 2.0)
    assert score is not None
    assert 0.0 <= score <= 1.0


def test_compute_composite_score_custom_weights():
    weights = {"embedding": 1.0, "rhythm": 0.0, "diversity": 0.0}
    score = compute_composite_score(0.7, 0.9, 0.5, weights=weights)
    assert score == pytest.approx(0.7)


# ── 8. Diversity scoring ──────────────────────────────────


def test_compute_diversity_scores_no_reuse():
    clips = _make_matched_clips(5, scene_indices=[0, 1, 2, 3, 4])
    scores = compute_diversity_scores(clips)
    assert all(s == 1.0 for s in scores)


def test_compute_diversity_scores_with_reuse():
    # Scene 0 appears 4 times in window of 3 → overuse
    clips = _make_matched_clips(5, scene_indices=[0, 0, 0, 0, 1])
    scores = compute_diversity_scores(clips, window=3, max_reuse=2)
    # Clip 0: first use, score=1.0
    assert scores[0] == 1.0
    # Clip 1: second use in window, score=1.0
    assert scores[1] == 1.0
    # Clip 2: third use in window (2 previous), reuse=2, max=2, no penalty yet
    assert scores[2] == 1.0
    # Clip 3: fourth use, 3 previous in window, overuse=1, score=0.85
    assert scores[3] == pytest.approx(0.85)


def test_compute_diversity_scores_none_scene():
    clips = _make_matched_clips(3, scene_indices=[None, None, None])
    scores = compute_diversity_scores(clips)
    assert all(s == 1.0 for s in scores)


def test_compute_diversity_scores_window():
    """Window=2 means only look at last 2 clips."""
    clips = _make_matched_clips(5, scene_indices=[0, 1, 0, 1, 0])
    scores = compute_diversity_scores(clips, window=2, max_reuse=1)
    # Clip 2: scene 0, previous 2 clips = [0,1], reuse=1, max=1, no penalty
    assert scores[2] == 1.0
    # Clip 4: scene 0, previous 2 clips = [0,1], reuse=1, max=1, no penalty
    assert scores[4] == 1.0


# ── 9. Score clips ────────────────────────────────────────


def test_score_clips_embedding():
    clips = _make_matched_clips(
        3,
        scene_indices=[0, 1, 2],
        sources=["embedding", "embedding_topk", "embedding_top1"],
        scores=[0.8, 0.7, 0.6],
    )
    count = score_clips(clips)
    assert count == 3
    assert clips[0].embedding_score == 0.8
    assert clips[1].embedding_score == 0.7
    assert clips[2].embedding_score == 0.6
    assert clips[0].composite_score is not None


def test_score_clips_heuristic():
    clips = _make_matched_clips(
        2,
        sources=["heuristic", "heuristic"],
        scores=[1.0, 1.0],
    )
    count = score_clips(clips)
    assert count == 2  # diversity score provides composite
    assert clips[0].embedding_score is None
    assert clips[0].rhythm_score is None
    assert clips[0].diversity_score is not None
    assert clips[0].composite_score is not None


def test_score_clips_with_rhythm():
    clips = _make_matched_clips(2, sources=["embedding", "embedding"])
    rhythm = [0.8, 0.5]
    count = score_clips(clips, rhythm_scores=rhythm)
    assert count == 2
    assert clips[0].rhythm_score == 0.8
    assert clips[1].rhythm_score == 0.5


# ── 10. Match quality aggregation ─────────────────────────


def test_aggregate_match_quality_normal():
    clips = _make_matched_clips(
        4,
        scene_indices=[0, 1, 2, 3],
        sources=["embedding"] * 4,
        scores=[0.8, 0.7, 0.6, 0.5],
    )
    score_clips(clips)
    summary = aggregate_match_quality(clips)
    assert summary.total_clips == 4
    assert summary.clips_with_composite == 4
    assert summary.avg_composite > 0
    assert summary.avg_embedding > 0
    assert summary.diversity_penalty_count == 0


def test_aggregate_match_quality_with_diversity_penalty():
    clips = _make_matched_clips(
        4,
        scene_indices=[0, 0, 0, 0],
        sources=["embedding"] * 4,
        scores=[0.8, 0.8, 0.8, 0.8],
    )
    score_clips(clips, diversity_window=3, diversity_max_reuse=2)
    summary = aggregate_match_quality(clips)
    assert summary.diversity_penalty_count > 0


def test_aggregate_match_quality_empty():
    summary = aggregate_match_quality([])
    assert summary.total_clips == 0
    assert summary.clips_with_composite == 0


def test_aggregate_match_quality_to_dict():
    clips = _make_matched_clips(2, sources=["embedding", "embedding"])
    score_clips(clips)
    summary = aggregate_match_quality(clips)
    d = summary.to_dict()
    assert "total_clips" in d
    assert "avg_composite" in d
    assert "low_quality_indices" in d


# ── 11. Model fields ──────────────────────────────────────


def test_word_segment_model():
    ws = WordSegment(word="hello", start=0.0, end=0.5, score=0.9)
    assert ws.word == "hello"
    assert ws.start == 0.0
    assert ws.end == 0.5
    assert ws.score == 0.9


def test_timed_segment_default_fields():
    ts = TimedSegment(text="test", start=0.0, end=1.0)
    assert ts.words == []
    assert ts.confidence == 0.0


def test_timed_segment_with_words():
    ws = WordSegment(word="hello", start=0.0, end=0.5, score=0.9)
    ts = TimedSegment(text="hello", start=0.0, end=0.5, words=[ws], confidence=0.9)
    assert len(ts.words) == 1
    assert ts.confidence == 0.9


def test_matched_clip_default_fields():
    mc = MatchedClip(
        segment_index=0,
        text="test",
        narr_start=0.0,
        narr_end=1.0,
        src_start=0.0,
        src_end=1.0,
        score=0.8,
    )
    assert mc.embedding_score is None
    assert mc.rhythm_score is None
    assert mc.diversity_score is None
    assert mc.composite_score is None


def test_matched_clip_model_dump_includes_new_fields():
    mc = MatchedClip(
        segment_index=0,
        text="test",
        narr_start=0.0,
        narr_end=1.0,
        src_start=0.0,
        src_end=1.0,
        score=0.8,
        embedding_score=0.7,
        composite_score=0.65,
    )
    d = mc.model_dump()
    assert "embedding_score" in d
    assert "rhythm_score" in d
    assert "diversity_score" in d
    assert "composite_score" in d
    assert d["embedding_score"] == 0.7
    assert d["composite_score"] == 0.65
