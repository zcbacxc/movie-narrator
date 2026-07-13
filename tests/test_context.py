from pathlib import Path

import pytest

from movie_narrator.models import (
    Assets,
    Context,
    MatchedClip,
    PipelineStatus,
    ResearchInfo,
    Scene,
    ScriptSegment,
    TimedSegment,
)
from movie_narrator.pipeline.subtitle import _format_time


@pytest.fixture
def ctx(tmp_path):
    return Context(movie_name="Inception", output_dir=str(tmp_path))


def test_context_creation(ctx):
    assert ctx.movie_name == "Inception"
    assert ctx.output_dir
    assert ctx.segments == []
    assert ctx.timed_segments == []
    assert ctx.audio_path is None
    assert ctx.final_audio_path is None
    assert ctx.subtitle_path is None
    assert ctx.video_path is None
    assert ctx.source_video_path is None
    assert ctx.clips_dir is None
    assert ctx.status.research == "disabled"
    assert ctx.status.export == "disabled"
    assert ctx.assets.bgm is None
    assert ctx.research.summary == ""


def test_context_requires_output_dir():
    with pytest.raises(Exception):
        Context(movie_name="X")


def test_context_with_segments(tmp_path):
    ctx = Context(
        movie_name="Inception",
        output_dir=str(tmp_path),
        segments=[ScriptSegment(text="A"), ScriptSegment(text="B")],
        timed_segments=[TimedSegment(text="A", start=0.0, end=2.3)],
    )
    assert len(ctx.segments) == 2
    assert len(ctx.timed_segments) == 1


def test_pipeline_status_fields():
    s = PipelineStatus()
    assert s.model_dump() == {
        "research": "disabled",
        "align": "disabled",
        "scene": "disabled",
        "match": "disabled",
        "bgm": "disabled",
        "export": "disabled",
        "translate": "skipped",
    }


def test_matched_clip_source_default():
    m = MatchedClip(
        segment_index=0,
        text="t",
        narr_start=0.0,
        narr_end=1.0,
        src_start=0.0,
        src_end=1.0,
        score=1.0,
    )
    assert m.source == "fallback"


def test_format_time():
    assert _format_time(0.0) == "00:00:00,000"
    assert _format_time(1.999999999) == "00:00:02,000"
    assert _format_time(60.0) == "00:01:00,000"
    assert _format_time(3661.123) == "01:01:01,123"


def test_subtitle_file_generated(tmp_path):
    ctx = Context(
        movie_name="Test",
        output_dir=str(tmp_path),
        timed_segments=[
            TimedSegment(text="Hello", start=0.0, end=2.0),
            TimedSegment(text="World", start=2.5, end=5.0),
        ],
    )
    from movie_narrator.pipeline.subtitle import generate_subtitle

    generate_subtitle(ctx)

    srt_path = tmp_path / "subtitle.srt"
    assert srt_path.exists()
    content = srt_path.read_text(encoding="utf-8")
    assert "Hello" in content
    assert "World" in content
