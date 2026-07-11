from pathlib import Path

import pytest

from movie_narrator.models import Context, ScriptSegment, TimedSegment
from movie_narrator.pipeline.subtitle import _format_time


@pytest.fixture
def ctx():
    return Context(movie_name="Inception")


def test_context_creation(ctx):
    assert ctx.movie_name == "Inception"
    assert ctx.segments == []
    assert ctx.timed_segments == []
    assert ctx.audio_path is None
    assert ctx.subtitle_path is None
    assert ctx.video_path is None


def test_context_with_segments():
    ctx = Context(
        movie_name="Inception",
        segments=[ScriptSegment(text="A"), ScriptSegment(text="B")],
        timed_segments=[TimedSegment(text="A", start=0.0, end=2.3)],
    )
    assert len(ctx.segments) == 2
    assert len(ctx.timed_segments) == 1
    assert ctx.timed_segments[0].text == "A"


def test_format_time():
    assert _format_time(0.0) == "00:00:00,000"
    assert _format_time(1.999999999) == "00:00:02,000"
    assert _format_time(60.0) == "00:01:00,000"
    assert _format_time(3661.123) == "01:01:01,123"


def test_subtitle_file_generated(tmp_path):
    ctx = Context(
        movie_name="Test",
        timed_segments=[
            TimedSegment(text="Hello", start=0.0, end=2.0),
            TimedSegment(text="World", start=2.5, end=5.0),
        ],
    )
    ctx.metadata["output_dir"] = str(tmp_path)

    from movie_narrator.pipeline.subtitle import generate_subtitle
    generate_subtitle(ctx)

    srt_path = tmp_path / "subtitle.srt"
    assert srt_path.exists()
    content = srt_path.read_text(encoding="utf-8")
    assert "Hello" in content
    assert "World" in content
    assert "00:00:00,000" in content
