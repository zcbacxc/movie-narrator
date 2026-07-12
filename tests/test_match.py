from pathlib import Path

import pytest

from movie_narrator.models import Context, MatchedClip, Scene, TimedSegment
from movie_narrator.pipeline.match import match_clips


def _make_ctx(tmp_path, source_video="video.mp4"):
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / source_video),
        timed_segments=[
            TimedSegment(text="A", start=0.0, end=2.0),
            TimedSegment(text="B", start=2.5, end=5.0),
        ],
        scenes=[
            Scene(index=0, start=0.0, end=10.0),
        ],
    )
    ctx.status.scene = "success"
    return ctx


def test_match_clips_skipped_no_source(tmp_path):
    ctx = Context(movie_name="m", output_dir=str(tmp_path))
    match_clips(ctx)
    assert ctx.status.match == "skipped"


def test_match_clips_disabled_when_scene_disabled(tmp_path):
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "v.mp4"),
    )
    ctx.status.scene = "disabled"
    match_clips(ctx)
    assert ctx.status.match == "disabled"


def test_match_clips_skipped_no_scenes(tmp_path):
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "v.mp4"),
        timed_segments=[TimedSegment(text="A", start=0.0, end=2.0)],
    )
    ctx.status.scene = "success"
    match_clips(ctx)
    assert ctx.status.match == "skipped"


def test_match_clips_success(tmp_path):
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    match_clips(ctx)
    assert ctx.status.match == "success"
    assert len(ctx.matched_clips) == 2
    assert (tmp_path / "matches.json").exists()
    assert all(m.source == "heuristic" for m in ctx.matched_clips)
    assert all(m.score == 1.0 for m in ctx.matched_clips)
