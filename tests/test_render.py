"""Unit tests for render_video — VideoFileClip failure path.

The full render integration test is in test_render_real.py.
This file tests the inline_warn fallback when VideoFileClip fails.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Context, MatchedClip, Services, TimedSegment


def _make_ctx_with_clips(tmp_path):
    console = MagicMock()
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
        timed_segments=[
            TimedSegment(text="A", start=0.0, end=2.0),
        ],
        services=Services(console=console),
    )
    ctx.matched_clips = [
        MatchedClip(
            segment_index=0,
            text="A",
            narr_start=0.0,
            narr_end=2.0,
            src_start=0.0,
            src_end=2.0,
            score=0.9,
            scene_index=0,
            source="heuristic",
        ),
    ]
    ctx.audio_path = str(tmp_path / "narration.mp3")
    (tmp_path / "narration.mp3").write_bytes(b"ID3")
    return ctx


def test_render_videofileclip_failure_warns(tmp_path, monkeypatch):
    """VideoFileClip raises → inline_warn called with fallback message."""
    ctx = _make_ctx_with_clips(tmp_path)

    # Mock VideoFileClip to raise
    def mock_videofileclip(*a, **kw):
        raise OSError("cannot open video")

    # Patch at the moviepy module level
    import movie_narrator.pipeline.render as render_mod
    monkeypatch.setattr(render_mod, "VideoFileClip", mock_videofileclip)

    # Mock CompositeVideoClip to avoid actual rendering
    mock_composite = MagicMock()
    monkeypatch.setattr(render_mod, "CompositeVideoClip", mock_composite)
    monkeypatch.setattr(render_mod, "ColorClip", MagicMock())
    monkeypatch.setattr(render_mod, "AudioFileClip", MagicMock())

    # Mock write_videofile to avoid ffmpeg
    mock_composite.return_value.write_videofile = MagicMock()

    # Mock text image creation
    monkeypatch.setattr(render_mod, "_create_text_image", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(render_mod, "ImageClip", MagicMock())

    # Mock metadata export
    monkeypatch.setattr(render_mod, "build_metadata_json", MagicMock(return_value={}))

    try:
        render_mod.render_video(ctx)
    except Exception:
        pass  # May fail later in render, but inline_warn should have been called

    # Check inline_warn was called with video failure message
    warn_calls = ctx.services.console.inline_warn.call_args_list
    assert any("text-only" in str(c) or "Cannot open" in str(c) for c in warn_calls)
