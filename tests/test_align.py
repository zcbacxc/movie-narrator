import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from movie_narrator.models import Context, TimedSegment
from movie_narrator.pipeline.align import align_audio


def _make_ctx(tmp_path, audio=True):
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        timed_segments=[
            TimedSegment(text="A", start=0.0, end=2.0),
            TimedSegment(text="B", start=2.5, end=5.0),
        ],
    )
    if audio:
        ctx.audio_path = str(tmp_path / "narration.mp3")
        (tmp_path / "narration.mp3").write_bytes(b"ID3")
    return ctx


def test_align_disabled_without_whisperx(tmp_path):
    ctx = _make_ctx(tmp_path)
    align_audio(ctx)
    assert ctx.status.align == "disabled"


def test_align_skipped_no_audio(tmp_path):
    ctx = _make_ctx(tmp_path, audio=False)
    with pytest.MonkeyPatch.context() as m:
        m.setattr("movie_narrator.pipeline.align.probe", lambda name: (True, ""))
        align_audio(ctx)
    assert ctx.status.align == "skipped"


def test_align_success_with_mocked_whisperx(tmp_path):
    ctx = _make_ctx(tmp_path)
    mock_result = {
        "segments": [
            {"start": 0.1, "end": 1.9, "text": "A"},
            {"start": 2.4, "end": 4.8, "text": "B"},
        ]
    }

    # Build a fake whisperx module so import succeeds inside align_audio
    fake_wx = types.ModuleType("whisperx")
    fake_wx.load_audio = MagicMock(return_value="audio")
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=mock_result)
    fake_wx.load_model = MagicMock(return_value=fake_model)
    # AQ-01: align pipeline needs load_align_model + align mocks
    fake_wx.load_align_model = MagicMock(return_value=(MagicMock(), {}))
    fake_wx.align = MagicMock(return_value=mock_result)

    with pytest.MonkeyPatch.context() as m:
        m.setattr("movie_narrator.pipeline.align.probe", lambda name: (True, ""))
        m.setitem(sys.modules, "whisperx", fake_wx)
        align_audio(ctx)

    assert ctx.status.align == "success"
    assert ctx.timed_segments[0].start == 0.1
    assert ctx.timed_segments[0].end == 1.9
    assert ctx.timed_segments[1].start == 2.4
    assert ctx.timed_segments[1].end == 4.8


def test_align_midpoint_distance_when_no_containment(tmp_path):
    """When no wx segment contains the ts midpoint, picks closest by distance."""
    ctx = _make_ctx(tmp_path)
    # wx segments are far from ts midpoints → must use distance fallback
    mock_result = {
        "segments": [
            {"start": 10.0, "end": 12.0, "text": "far A"},
            {"start": 20.0, "end": 22.0, "text": "far B"},
        ]
    }

    fake_wx = types.ModuleType("whisperx")
    fake_wx.load_audio = MagicMock(return_value="audio")
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=mock_result)
    fake_wx.load_model = MagicMock(return_value=fake_model)
    # AQ-01: align pipeline needs load_align_model + align mocks
    fake_wx.load_align_model = MagicMock(return_value=(MagicMock(), {}))
    fake_wx.align = MagicMock(return_value=mock_result)

    with pytest.MonkeyPatch.context() as m:
        m.setattr("movie_narrator.pipeline.align.probe", lambda name: (True, ""))
        m.setitem(sys.modules, "whisperx", fake_wx)
        align_audio(ctx)

    # ts0 midpoint=1.0, closest wx is 10-12 (mid=11)
    # ts1 midpoint=3.75, closest wx is 10-12 (mid=11)
    assert ctx.status.align == "success"
    # Both should be assigned to the closest segment
    assert ctx.timed_segments[0].start == 10.0
    assert ctx.timed_segments[0].end == 12.0


def test_align_unequal_segment_counts(tmp_path):
    """More ts segments than wx segments — no index-out-of-range."""
    ctx = _make_ctx(tmp_path)
    ctx.timed_segments = [
        TimedSegment(text=f"seg{i}", start=float(i * 2), end=float(i * 2 + 1.5))
        for i in range(5)  # 5 narration segments
    ]
    # Only 2 wx segments — old index-based code would skip segments 2-4
    mock_result = {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "first block"},
            {"start": 3.0, "end": 10.0, "text": "second block"},
        ]
    }

    fake_wx = types.ModuleType("whisperx")
    fake_wx.load_audio = MagicMock(return_value="audio")
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=mock_result)
    fake_wx.load_model = MagicMock(return_value=fake_model)
    # AQ-01: align pipeline needs load_align_model + align mocks
    fake_wx.load_align_model = MagicMock(return_value=(MagicMock(), {}))
    fake_wx.align = MagicMock(return_value=mock_result)

    with pytest.MonkeyPatch.context() as m:
        m.setattr("movie_narrator.pipeline.align.probe", lambda name: (True, ""))
        m.setitem(sys.modules, "whisperx", fake_wx)
        align_audio(ctx)

    assert ctx.status.align == "success"
    # All 5 segments should be assigned (not just first 2)
    # AQ-01 fix: timestamps are now monotonically non-decreasing
    # and non-overlapping (multiple segments can't share the same
    # wx segment time range without being pushed forward).
    for prev, curr in zip(ctx.timed_segments, ctx.timed_segments[1:]):
        assert curr.start >= prev.end  # monotonic non-overlap
        assert curr.end > curr.start    # always positive duration


def test_align_empty_whisperx_segments_warns(tmp_path):
    """WhisperX returns empty segments → inline_warn, timestamps unchanged.

    AQ-01 fix: empty ASR now degrades to status='skipped' (not 'success'),
    so downstream knows alignment didn't happen.
    """
    ctx = _make_ctx(tmp_path)
    original_starts = [ts.start for ts in ctx.timed_segments]
    original_ends = [ts.end for ts in ctx.timed_segments]

    mock_result = {"segments": []}

    fake_wx = types.ModuleType("whisperx")
    fake_wx.load_audio = MagicMock(return_value="audio")
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=mock_result)
    fake_wx.load_model = MagicMock(return_value=fake_model)

    with pytest.MonkeyPatch.context() as m:
        m.setattr("movie_narrator.pipeline.align.probe", lambda name: (True, ""))
        m.setitem(sys.modules, "whisperx", fake_wx)
        align_audio(ctx)

    # AQ-01: empty ASR → skipped (not success)
    assert ctx.status.align == "skipped"
    assert ctx.metadata.get("align_degraded") is True
    # Timestamps should be unchanged (no alignment happened)
    for i, ts in enumerate(ctx.timed_segments):
        assert ts.start == original_starts[i]
        assert ts.end == original_ends[i]


# ── C1 fix: align_fallback status='failed' (regression test) ──


def test_align_fallback_sets_status_failed(tmp_path):
    """C1 fix: when whisperx.align() raises, status='failed' (not 'success').

    Previously the except block set align_fallback=True but fell through
    to status='success' at the end, hiding the degradation from users
    and runner's _degraded_steps accumulation.
    """
    ctx = _make_ctx(tmp_path)
    mock_result = {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "first"},
            {"start": 3.0, "end": 6.0, "text": "second"},
        ]
    }

    fake_wx = types.ModuleType("whisperx")
    fake_wx.load_audio = MagicMock(return_value="audio")
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=mock_result)
    fake_wx.load_model = MagicMock(return_value=fake_model)
    # load_align_model raises — simulates OOM / version mismatch
    fake_wx.load_align_model = MagicMock(side_effect=RuntimeError("OOM"))
    fake_wx.align = MagicMock()

    with pytest.MonkeyPatch.context() as m:
        m.setattr("movie_narrator.pipeline.align.probe", lambda name: (True, ""))
        m.setitem(sys.modules, "whisperx", fake_wx)
        align_audio(ctx)

    # C1 fix: status should be 'failed', not 'success'
    assert ctx.status.align == "failed"
    assert ctx.metadata.get("align_fallback") is True
    assert ctx.metadata.get("align_degraded") is True
    # Remapping still runs (segment-level timestamps > TTS estimates)
    # but the degradation is visible to users and runner
    assert ctx.metadata.get("align_segments") == 2


# ── AQ-01: single-segment drift > 50% → skipped ──


def test_align_single_segment_drift_skips(tmp_path):
    """AQ-01: WhisperX returns 1 segment with duration drift > 50% → skipped.

    When ASR detects only 1 segment for the entire audio but its duration
    differs greatly from total narration duration, the alignment is
    unreliable (likely all-silence or all-noise detected as one blob).
    """
    ctx = _make_ctx(tmp_path)
    # narration total = (2.0-0.0) + (5.0-2.5) = 4.5s
    # wx single segment = 0.0 to 20.0s → drift = |20-4.5|/4.5 = 344% > 50%
    mock_result = {
        "segments": [
            {"start": 0.0, "end": 20.0, "text": "one giant blob"},
        ]
    }

    fake_wx = types.ModuleType("whisperx")
    fake_wx.load_audio = MagicMock(return_value="audio")
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=mock_result)
    fake_wx.load_model = MagicMock(return_value=fake_model)
    fake_wx.load_align_model = MagicMock(return_value=(MagicMock(), {}))
    fake_wx.align = MagicMock(return_value=mock_result)

    original_starts = [ts.start for ts in ctx.timed_segments]
    original_ends = [ts.end for ts in ctx.timed_segments]

    with pytest.MonkeyPatch.context() as m:
        m.setattr("movie_narrator.pipeline.align.probe", lambda name: (True, ""))
        m.setitem(sys.modules, "whisperx", fake_wx)
        align_audio(ctx)

    assert ctx.status.align == "skipped"
    assert ctx.metadata.get("align_degraded") is True
    # Timestamps unchanged (drift too large, alignment unreliable)
    for i, ts in enumerate(ctx.timed_segments):
        assert ts.start == original_starts[i]
        assert ts.end == original_ends[i]


def test_align_single_segment_no_drift_succeeds(tmp_path):
    """AQ-01: single segment with reasonable duration → success (not skipped)."""
    ctx = _make_ctx(tmp_path)
    # narration total = 4.5s, wx single segment = 0.0 to 5.0s → drift = 11% < 50%
    mock_result = {
        "segments": [
            {"start": 0.0, "end": 5.0, "text": "reasonable single segment"},
        ]
    }

    fake_wx = types.ModuleType("whisperx")
    fake_wx.load_audio = MagicMock(return_value="audio")
    fake_model = MagicMock()
    fake_model.transcribe = MagicMock(return_value=mock_result)
    fake_wx.load_model = MagicMock(return_value=fake_model)
    fake_wx.load_align_model = MagicMock(return_value=(MagicMock(), {}))
    fake_wx.align = MagicMock(return_value=mock_result)

    with pytest.MonkeyPatch.context() as m:
        m.setattr("movie_narrator.pipeline.align.probe", lambda name: (True, ""))
        m.setitem(sys.modules, "whisperx", fake_wx)
        align_audio(ctx)

    assert ctx.status.align == "success"
