# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Additional coverage for pipeline.align.

Focuses on branches not exercised by test_align.py: the funasr backend,
BackendUnavailable fallback to whisperx, generic error handling, empty-text
ASR segments, word-level alignment + low-confidence QA warnings, drift/remap
edge cases, and faster-whisper empty/drift paths. All external ASR backends
are fully mocked — no network / real model loading.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Context, Services, TimedSegment
from movie_narrator.pipeline._align_backend import BackendUnavailable
from movie_narrator.pipeline.align import (
    _align_with_funasr,
    _detect_drift,
    _remap_segments,
    align_audio,
)


def _make_ctx(tmp_path, audio=True):
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
        timed_segments=[
            TimedSegment(text="A", start=0.0, end=2.0),
            TimedSegment(text="B", start=2.5, end=5.0),
        ],
    )
    if audio:
        ctx.audio_path = str(tmp_path / "narration.mp3")
        (tmp_path / "narration.mp3").write_bytes(b"ID3")
    return ctx


def _wx_segments(segments):
    return [{"start": s[0], "end": s[1], "text": s[2]} for s in segments]


def _fake_whisperx(result, load_error=False):
    fake = types.ModuleType("whisperx")
    if load_error:
        fake.load_audio = MagicMock(side_effect=RuntimeError("load fail"))
        return fake
    fake.load_audio = MagicMock(return_value="audio")
    model = MagicMock()
    model.transcribe = MagicMock(return_value=result)
    fake.load_model = MagicMock(return_value=model)
    fake.load_align_model = MagicMock(return_value=(MagicMock(), {}))
    fake.align = MagicMock(return_value=result)
    return fake


def _whisperx_env(fake):
    from contextlib import ExitStack

    stack = ExitStack()
    stack.enter_context(
        patch("movie_narrator.pipeline._align_backend.probe",
              lambda name: (True, "") if name == "whisperx" else (False, ""))
    )
    stack.enter_context(
        patch("movie_narrator.pipeline.align.probe",
              lambda name: (True, "") if name == "whisperx" else (False, ""))
    )
    stack.enter_context(patch.dict(sys.modules, {"whisperx": fake}))
    return stack


# ── Drift / remap edge cases ───────────────────────────────


def test_detect_drift_zero_total_duration(tmp_path):
    """When narration total duration is 0, drift detection returns False."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        timed_segments=[TimedSegment(text="x", start=0.0, end=0.0)],
    )
    assert _detect_drift(ctx, [{"start": 0.0, "end": 1.0, "text": "x"}], "t") is False


def test_remap_segments_min_duration(tmp_path):
    """Zero-duration ASR segment is padded to the minimum duration."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        timed_segments=[TimedSegment(text="x", start=0.5, end=1.5)],
    )
    wx = [{"start": 1.0, "end": 1.0, "text": "x"}]
    _remap_segments(ctx, wx)
    assert ctx.timed_segments[0].start == 1.0
    assert ctx.timed_segments[0].end == pytest.approx(1.1)


# ── funasr backend ─────────────────────────────────────────


def test_align_with_funasr_success(tmp_path):
    ctx = _make_ctx(tmp_path)
    segments = _wx_segments([(0.1, 1.9, "A"), (2.4, 4.8, "B")])
    with patch("movie_narrator.pipeline.align.run_funasr", return_value=segments):
        with patch("movie_narrator.pipeline.align.validate_alignment") as va:
            va.return_value.low_confidence_count = 0
            va.return_value.to_dict.return_value = {}
            result = _align_with_funasr(ctx)
    assert result.status.align == "success"
    assert ctx.metadata["align_fallback"] is True
    assert ctx.metadata["align_segments"] == 2
    assert ctx.metadata["align_backward_skipped"] == 0


def test_align_with_funasr_empty(tmp_path):
    ctx = _make_ctx(tmp_path)
    with patch("movie_narrator.pipeline.align.run_funasr", return_value=[]):
        result = _align_with_funasr(ctx)
    assert result.status.align == "skipped"
    assert ctx.metadata["align_degraded"] is True


def test_align_with_funasr_drift(tmp_path):
    ctx = _make_ctx(tmp_path)
    segments = _wx_segments([(0.0, 20.0, "blob")])
    with patch("movie_narrator.pipeline.align.run_funasr", return_value=segments):
        result = _align_with_funasr(ctx)
    assert result.status.align == "skipped"
    assert ctx.metadata["align_degraded"] is True


def test_align_audio_funasr_dispatch(tmp_path):
    ctx = _make_ctx(tmp_path)
    segments = _wx_segments([(0.1, 1.9, "A"), (2.4, 4.8, "B")])
    with patch("movie_narrator.pipeline.align.select_align_backend",
               return_value=("funasr", "reason")):
        with patch("movie_narrator.pipeline.align.run_funasr", return_value=segments):
            with patch("movie_narrator.pipeline.align.validate_alignment") as va:
                va.return_value.low_confidence_count = 0
                va.return_value.to_dict.return_value = {}
                result = align_audio(ctx)
    assert result.status.align == "success"
    assert ctx.metadata["align_backend_used"] == "funasr"


# ── BackendUnavailable fallback + generic errors ───────────


def test_align_backendunavailable_fallbacks_to_whisperx(tmp_path):
    ctx = _make_ctx(tmp_path)
    result = {"segments": [{"start": 0.1, "end": 1.9, "text": "A"},
                           {"start": 2.4, "end": 4.8, "text": "B"}]}
    fake = _fake_whisperx(result)
    with patch("movie_narrator.pipeline.align.select_align_backend",
               return_value=("faster_whisper", "reason")):
        with patch("movie_narrator.pipeline.align.run_faster_whisper",
                   side_effect=BackendUnavailable("no fw")):
            with patch("movie_narrator.pipeline.align._detect_drift", return_value=False):
                with patch("movie_narrator.pipeline.align.validate_alignment") as va:
                    va.return_value.low_confidence_count = 0
                    va.return_value.to_dict.return_value = {}
                    with patch.dict(sys.modules, {"whisperx": fake}):
                        ctx2 = align_audio(ctx)
    assert ctx2.status.align == "success"
    assert len(ctx2.metadata["align_backend_attempted"]) == 1


def test_align_backendunavailable_fallback_fails(tmp_path):
    """If the whisperx fallback also fails, align is marked failed."""
    ctx = _make_ctx(tmp_path)
    with patch("movie_narrator.pipeline.align.select_align_backend",
               return_value=("faster_whisper", "reason")):
        with patch("movie_narrator.pipeline.align.run_faster_whisper",
                   side_effect=BackendUnavailable("no fw")):
            with patch("movie_narrator.pipeline.align._align_with_whisperx",
                       side_effect=RuntimeError("wx fail")):
                ctx2 = align_audio(ctx)
    assert ctx2.status.align == "failed"
    assert ctx2.step_state.message == "wx fail"


def test_align_audio_generic_error_sets_failed(tmp_path):
    ctx = _make_ctx(tmp_path)
    with patch("movie_narrator.pipeline.align.select_align_backend",
               return_value=("whisperx", "reason")):
        with patch("movie_narrator.pipeline.align._align_with_whisperx",
                   side_effect=RuntimeError("boom")):
            ctx2 = align_audio(ctx)
    assert ctx2.status.align == "failed"
    assert ctx2.step_state.message == "boom"


# ── whisperx paths ─────────────────────────────────────────


def test_align_whisperx_empty_text_segments(tmp_path):
    """Only whitespace/empty-text ASR segments → skipped."""
    ctx = _make_ctx(tmp_path)
    result = {"segments": [{"start": 0.0, "end": 1.0, "text": "  "},
                           {"start": 1.0, "end": 2.0, "text": ""}]}
    fake = _fake_whisperx(result)
    with _whisperx_env(fake):
        ctx2 = align_audio(ctx)
    assert ctx2.status.align == "skipped"
    assert ctx2.metadata["align_degraded"] is True


def test_align_whisperx_word_level_and_low_conf(tmp_path):
    """Word-level alignment runs and low-confidence QA warns."""
    ctx = _make_ctx(tmp_path)
    result = {
        "segments": [{"start": 0.1, "end": 1.9, "text": "A"},
                     {"start": 2.4, "end": 4.8, "text": "B"}],
        "word_segments": [{"word": "你", "start": 0.1, "end": 0.3, "score": 0.3}],
    }
    fake = _fake_whisperx(result)
    with _whisperx_env(fake):
        with patch("movie_narrator.pipeline.align.validate_alignment") as va:
            va.return_value.low_confidence_count = 1
            va.return_value.to_dict.return_value = {"x": 1}
            ctx2 = align_audio(ctx)
    assert ctx2.status.align == "success"
    assert ctx2.metadata["alignment_qa"] == {"x": 1}
    assert ctx2.metadata["align_word_segments"] == 1
    ctx.services.console.inline_warn.assert_called()


def test_align_whisperx_outer_error(tmp_path):
    """whisperx.load_audio raising → outer except marks align failed."""
    ctx = _make_ctx(tmp_path)
    fake = _fake_whisperx({}, load_error=True)
    with _whisperx_env(fake):
        ctx2 = align_audio(ctx)
    assert ctx2.status.align == "failed"


# ── faster-whisper paths ───────────────────────────────────


def test_align_faster_whisper_empty(tmp_path):
    ctx = _make_ctx(tmp_path)
    with patch("movie_narrator.pipeline.align.select_align_backend",
               return_value=("faster_whisper", "reason")):
        with patch("movie_narrator.pipeline.align.run_faster_whisper", return_value=[]):
            ctx2 = align_audio(ctx)
    assert ctx2.status.align == "skipped"
    assert ctx2.metadata["align_degraded"] is True


def test_align_faster_whisper_drift(tmp_path):
    ctx = _make_ctx(tmp_path)
    segments = _wx_segments([(0.0, 20.0, "blob")])
    with patch("movie_narrator.pipeline.align.select_align_backend",
               return_value=("faster_whisper", "reason")):
        with patch("movie_narrator.pipeline.align.run_faster_whisper", return_value=segments):
            ctx2 = align_audio(ctx)
    assert ctx2.status.align == "skipped"
    assert ctx2.metadata["align_degraded"] is True


def test_align_faster_whisper_success(tmp_path):
    ctx = _make_ctx(tmp_path)
    segments = _wx_segments([(0.1, 1.9, "A"), (2.4, 4.8, "B")])
    with patch("movie_narrator.pipeline.align.select_align_backend",
               return_value=("faster_whisper", "reason")):
        with patch("movie_narrator.pipeline.align.run_faster_whisper", return_value=segments):
            with patch("movie_narrator.pipeline.align.validate_alignment") as va:
                va.return_value.low_confidence_count = 0
                va.return_value.to_dict.return_value = {}
                ctx2 = align_audio(ctx)
    assert ctx2.status.align == "success"
    assert ctx2.metadata["align_fallback"] is True
    assert ctx2.metadata["align_segments"] == 2
    assert ctx2.metadata["align_backward_skipped"] == 0