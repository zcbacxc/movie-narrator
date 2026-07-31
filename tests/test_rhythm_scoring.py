"""Tests for rhythm-zone influence on match scoring.

Covers:
- ``_compute_rhythm_adjustment`` unit tests (boundary, linearity, no-penalty)
- ``_greedy_topk_assign`` integration with rhythm zones
- ``match_clips`` end-to-end with beats_meta carrying rhythm_zone
- match_summary ``rhythm_scoring`` field
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock

import numpy as np
import pytest

from movie_narrator.models import Context, Scene, TimedSegment
from movie_narrator.pipeline import match as match_module
from movie_narrator.pipeline.match import (
    _RHYTHM_ADJUSTMENT_MAX,
    _RHYTHM_ZONE_TIMELINE_CENTER,
    _compute_rhythm_adjustment,
    _greedy_topk_assign,
    match_clips,
)


@pytest.fixture(autouse=True)
def _clear_embedding_cache():
    match_module._load_embedding_model.cache_clear()
    yield
    match_module._load_embedding_model.cache_clear()


# ── Unit: _compute_rhythm_adjustment ────────────────────────


class TestComputeRhythmAdjustment:
    """Unit tests for the rhythm-zone score bonus function."""

    def test_none_rhythm_zone_returns_zero(self):
        scene = Scene(index=0, start=0.0, end=10.0)
        assert _compute_rhythm_adjustment(None, scene, 0.0, 100.0) == 0.0

    def test_unknown_rhythm_zone_returns_zero(self):
        scene = Scene(index=0, start=0.0, end=10.0)
        assert _compute_rhythm_adjustment("unknown", scene, 0.0, 100.0) == 0.0

    def test_zero_scene_span_returns_zero(self):
        scene = Scene(index=0, start=0.0, end=10.0)
        assert _compute_rhythm_adjustment("hook", scene, 0.0, 0.0) == 0.0

    def test_hook_at_timeline_start_gets_max_bonus(self):
        """A 'hook' beat matched against a scene at position 0.15 should
        receive the maximum bonus."""
        scene_start = 0.0
        scene_span = 100.0
        # Scene midpoint at 15s → position 0.15 → matches hook center
        scene = Scene(index=0, start=10.0, end=20.0)
        adj = _compute_rhythm_adjustment("hook", scene, scene_start, scene_span)
        assert adj == pytest.approx(_RHYTHM_ADJUSTMENT_MAX, abs=0.01)

    def test_settle_at_timeline_end_gets_max_bonus(self):
        """A 'settle' beat matched against a scene at position 0.85 should
        receive the maximum bonus."""
        scene_start = 0.0
        scene_span = 100.0
        scene = Scene(index=0, start=80.0, end=90.0)  # mid=85 → pos=0.85
        adj = _compute_rhythm_adjustment("settle", scene, scene_start, scene_span)
        assert adj == pytest.approx(_RHYTHM_ADJUSTMENT_MAX, abs=0.01)

    def test_bonus_never_negative(self):
        """The adjustment must never be a penalty (no negative values)."""
        scene_start = 0.0
        scene_span = 100.0
        # hook prefers pos=0.15; a scene at pos=0.95 is far away
        scene = Scene(index=0, start=90.0, end=100.0)
        adj = _compute_rhythm_adjustment("hook", scene, scene_start, scene_span)
        assert adj >= 0.0

    def test_bonus_decreases_with_distance(self):
        """Closer scenes get higher bonuses than farther ones."""
        scene_start = 0.0
        scene_span = 100.0
        near = Scene(index=0, start=10.0, end=20.0)   # pos ≈ 0.15 (hook center)
        far = Scene(index=1, start=50.0, end=60.0)    # pos ≈ 0.55 (far from hook)
        near_adj = _compute_rhythm_adjustment("hook", near, scene_start, scene_span)
        far_adj = _compute_rhythm_adjustment("hook", far, scene_start, scene_span)
        assert near_adj > far_adj

    def test_all_zones_have_preferred_positions(self):
        """Every defined rhythm zone has a timeline center."""
        for zone in ("hook", "rising", "peak", "settle"):
            assert zone in _RHYTHM_ZONE_TIMELINE_CENTER

    def test_bonus_bounded_by_max(self):
        """No bonus exceeds _RHYTHM_ADJUSTMENT_MAX."""
        scene_start = 0.0
        scene_span = 100.0
        for zone in _RHYTHM_ZONE_TIMELINE_CENTER:
            center = _RHYTHM_ZONE_TIMELINE_CENTER[zone]
            mid = center * scene_span
            scene = Scene(index=0, start=mid - 5, end=mid + 5)
            adj = _compute_rhythm_adjustment(zone, scene, scene_start, scene_span)
            assert adj <= _RHYTHM_ADJUSTMENT_MAX + 1e-9


# ── Integration: _greedy_topk_assign with rhythm zones ─────


class TestGreedyTopkAssignRhythm:
    """Integration tests for rhythm zone influence on _greedy_topk_assign."""

    def test_rhythm_bonus_can_break_tie(self):
        """When two scenes have equal cosine similarity, the rhythm zone
        bonus should prefer the scene at the correct timeline position."""
        # Two scenes with identical embeddings (same direction)
        scene_vecs = np.array([[1.0, 0.0], [1.0, 0.0]])
        narration_vecs = np.array([[1.0, 0.0]])
        scenes = [
            Scene(index=0, start=0.0, end=10.0),    # pos=0.05 (early)
            Scene(index=1, start=80.0, end=90.0),   # pos=0.85 (late)
        ]
        # "hook" prefers early scenes → scene 0 should win
        beats_meta = [{"rhythm_zone": "hook"}]
        results = _greedy_topk_assign(
            narration_vecs=narration_vecs,
            scene_vecs=scene_vecs,
            scenes=scenes,
            topk=2,
            beats_meta=beats_meta,
            scene_start=0.0,
            scene_span=100.0,
        )
        assert results[0][0] == 0  # scene 0 (early) selected for hook

    def test_settle_prefers_late_scene_on_tie(self):
        """'settle' should prefer the late scene on a tie."""
        scene_vecs = np.array([[1.0, 0.0], [1.0, 0.0]])
        narration_vecs = np.array([[1.0, 0.0]])
        scenes = [
            Scene(index=0, start=0.0, end=10.0),    # pos=0.05 (early)
            Scene(index=1, start=80.0, end=90.0),   # pos=0.85 (late)
        ]
        beats_meta = [{"rhythm_zone": "settle"}]
        results = _greedy_topk_assign(
            narration_vecs=narration_vecs,
            scene_vecs=scene_vecs,
            scenes=scenes,
            topk=2,
            beats_meta=beats_meta,
            scene_start=0.0,
            scene_span=100.0,
        )
        assert results[0][0] == 1  # scene 1 (late) selected for settle

    def test_no_rhythm_zone_keeps_pure_semantic(self):
        """Without beats_meta, scoring is purely semantic (no rhythm bonus)."""
        scene_vecs = np.array([[1.0, 0.0], [0.0, 1.0]])
        narration_vecs = np.array([[1.0, 0.0]])
        scenes = [
            Scene(index=0, start=0.0, end=10.0),
            Scene(index=1, start=80.0, end=90.0),
        ]
        # No beats_meta → no rhythm adjustment → scene 0 wins on pure cosine
        results = _greedy_topk_assign(
            narration_vecs=narration_vecs,
            scene_vecs=scene_vecs,
            scenes=scenes,
            topk=2,
        )
        assert results[0][0] == 0

    def test_strong_semantic_overrides_rhythm(self):
        """A strong semantic match in the 'wrong' position should still win
        over a weak match in the 'right' position."""
        # Scene 0: weak semantic match but at hook's preferred position
        # Scene 1: strong semantic match but far from hook's position
        scene_vecs = np.array([[0.1, 0.0], [1.0, 0.0]])
        narration_vecs = np.array([[1.0, 0.0]])
        scenes = [
            Scene(index=0, start=10.0, end=20.0),   # pos=0.15 (hook center)
            Scene(index=1, start=80.0, end=90.0),   # pos=0.85 (far from hook)
        ]
        beats_meta = [{"rhythm_zone": "hook"}]
        results = _greedy_topk_assign(
            narration_vecs=narration_vecs,
            scene_vecs=scene_vecs,
            scenes=scenes,
            topk=2,
            beats_meta=beats_meta,
            scene_start=0.0,
            scene_span=100.0,
        )
        # Scene 1 raw=1.0, rhythm bonus≈0 → adjusted≈1.0
        # Scene 0 raw=0.1, rhythm bonus=0.15 → adjusted=0.25
        # Scene 1 should still win
        assert results[0][0] == 1


# ── End-to-end: match_clips with rhythm zones ──────────────


def _setup_embedding_mock(monkeypatch):
    """Common mock setup for sentence_transformers + transcript."""
    monkeypatch.setattr(match_module, "probe", lambda name: (True, ""))
    mock_transcript = [
        {"start": 0.0, "end": 25.0, "text": "alpha scene zero"},
        {"start": 25.0, "end": 50.0, "text": "beta scene one"},
        {"start": 50.0, "end": 75.0, "text": "gamma scene two"},
        {"start": 75.0, "end": 100.0, "text": "delta scene three"},
    ]
    monkeypatch.setattr(
        match_module, "_transcribe_video_audio", lambda *a, **k: mock_transcript
    )

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            arr = np.zeros((len(texts), 4), dtype=float)
            for i, t in enumerate(texts):
                tl = t.lower()
                if "alpha" in tl or "scene 0" in tl or "scene zero" in tl:
                    arr[i] = np.array([1.0, 0.0, 0.0, 0.0])
                elif "beta" in tl or "scene 1" in tl or "scene one" in tl:
                    arr[i] = np.array([0.0, 1.0, 0.0, 0.0])
                elif "gamma" in tl or "scene 2" in tl or "scene two" in tl:
                    arr[i] = np.array([0.0, 0.0, 1.0, 0.0])
                elif "delta" in tl or "scene 3" in tl or "scene three" in tl:
                    arr[i] = np.array([0.0, 0.0, 0.0, 1.0])
            return arr

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)


def test_match_clips_rhythm_scoring_in_summary(tmp_path, monkeypatch):
    """match_summary contains rhythm_scoring field when beats_meta has zones."""
    _setup_embedding_mock(monkeypatch)
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
    )
    (tmp_path / "video.mp4").write_bytes(b"00")
    ctx.status.scene = "success"
    ctx.scenes = [
        Scene(index=0, start=0.0, end=25.0),
        Scene(index=1, start=25.0, end=50.0),
        Scene(index=2, start=50.0, end=75.0),
        Scene(index=3, start=75.0, end=100.0),
    ]
    ctx.timed_segments = [
        TimedSegment(text="alpha alpha", start=0.0, end=3.0),
        TimedSegment(text="beta beta", start=3.5, end=6.0),
        TimedSegment(text="gamma gamma", start=6.5, end=9.0),
        TimedSegment(text="delta delta", start=9.5, end=12.0),
    ]
    ctx.metadata["beats_meta"] = [
        {"text": "alpha", "approx_ratio": 0.1, "rhythm_zone": "hook", "emotion": "suspense"},
        {"text": "beta", "approx_ratio": 0.35, "rhythm_zone": "rising", "emotion": "intense"},
        {"text": "gamma", "approx_ratio": 0.65, "rhythm_zone": "peak", "emotion": "intense"},
        {"text": "delta", "approx_ratio": 0.9, "rhythm_zone": "settle", "emotion": "calm"},
    ]

    match_clips(ctx)
    summary = ctx.metadata["match_summary"]

    assert "rhythm_scoring" in summary
    assert summary["rhythm_scoring"]["enabled"] is True
    assert summary["rhythm_scoring"]["adjustment_max"] == _RHYTHM_ADJUSTMENT_MAX
    zones = summary["rhythm_scoring"]["zones"]
    assert zones["hook"] == 1
    assert zones["rising"] == 1
    assert zones["peak"] == 1
    assert zones["settle"] == 1


def test_match_clips_rhythm_scoring_disabled_without_zones(tmp_path, monkeypatch):
    """rhythm_scoring.enabled is False when beats_meta has no rhythm_zone."""
    _setup_embedding_mock(monkeypatch)
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
    )
    (tmp_path / "video.mp4").write_bytes(b"00")
    ctx.status.scene = "success"
    ctx.scenes = [
        Scene(index=0, start=0.0, end=25.0),
        Scene(index=1, start=25.0, end=50.0),
    ]
    ctx.timed_segments = [
        TimedSegment(text="alpha alpha", start=0.0, end=3.0),
        TimedSegment(text="beta beta", start=3.5, end=6.0),
    ]
    ctx.metadata["beats_meta"] = [
        {"text": "alpha", "approx_ratio": 0.1, "rhythm_zone": None, "emotion": None},
        {"text": "beta", "approx_ratio": 0.5, "rhythm_zone": None, "emotion": None},
    ]

    match_clips(ctx)
    summary = ctx.metadata["match_summary"]

    assert "rhythm_scoring" in summary
    assert summary["rhythm_scoring"]["enabled"] is False


def test_match_clips_rhythm_scoring_no_beats_meta(tmp_path, monkeypatch):
    """rhythm_scoring.enabled is False when beats_meta is absent entirely."""
    _setup_embedding_mock(monkeypatch)
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
    )
    (tmp_path / "video.mp4").write_bytes(b"00")
    ctx.status.scene = "success"
    ctx.scenes = [
        Scene(index=0, start=0.0, end=25.0),
        Scene(index=1, start=25.0, end=50.0),
    ]
    ctx.timed_segments = [
        TimedSegment(text="alpha alpha", start=0.0, end=3.0),
        TimedSegment(text="beta beta", start=3.5, end=6.0),
    ]

    match_clips(ctx)
    summary = ctx.metadata["match_summary"]

    assert "rhythm_scoring" in summary
    assert summary["rhythm_scoring"]["enabled"] is False


def test_match_clips_rhythm_hook_prefers_early_scene(tmp_path, monkeypatch):
    """When two scenes have equal semantic similarity, the hook rhythm zone
    should nudge selection toward the early scene."""
    _setup_embedding_mock(monkeypatch)
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
    )
    (tmp_path / "video.mp4").write_bytes(b"00")
    ctx.status.scene = "success"
    ctx.scenes = [
        Scene(index=0, start=0.0, end=25.0),     # early
        Scene(index=1, start=75.0, end=100.0),   # late
    ]
    # Both segments use the same text → equal semantic similarity to both scenes
    # (since both scene captions contain "alpha")
    ctx.timed_segments = [
        TimedSegment(text="alpha alpha", start=0.0, end=3.0),
    ]
    # Override transcript so both scenes have the same caption
    monkeypatch.setattr(
        match_module,
        "_transcribe_video_audio",
        lambda *a, **k: [
            {"start": 0.0, "end": 25.0, "text": "alpha shared"},
            {"start": 75.0, "end": 100.0, "text": "alpha shared"},
        ],
    )
    ctx.metadata["beats_meta"] = [
        {"text": "alpha", "approx_ratio": 0.1, "rhythm_zone": "hook", "emotion": "suspense"},
    ]

    match_clips(ctx)
    # hook prefers early (pos=0.15); scene 0 is at pos≈0.125, scene 1 at pos≈0.875
    # With equal cosine, the rhythm bonus should tip toward scene 0
    assert ctx.matched_clips[0].scene_index == 0
