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

    with pytest.MonkeyPatch.context() as m:
        m.setattr("movie_narrator.pipeline.align.probe", lambda name: (True, ""))
        m.setitem(sys.modules, "whisperx", fake_wx)
        align_audio(ctx)

    assert ctx.status.align == "success"
    assert ctx.timed_segments[0].start == 0.1
    assert ctx.timed_segments[0].end == 1.9
    assert ctx.timed_segments[1].start == 2.4
    assert ctx.timed_segments[1].end == 4.8
