"""Unit tests for render_video — VideoFileClip failure path.

The full render integration test is in test_render_real.py.
This file tests the inline_warn fallback when VideoFileClip fails.
"""

from pathlib import Path
from unittest.mock import MagicMock

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


def test_render_runtime_gpu_fallback_reports_reason(tmp_path, monkeypatch):
    """GPU encode failure -> falls back to libx264 and marks encoder_info.

    v1.2.1: when the hardware encode throws mid-render, the pipeline retries
    with libx264 and ``encoder_info`` in metadata must be truthful about both
    the active codec and the fallback reason (``gpu_runtime_fallback``).
    """
    ctx = _make_ctx_with_clips(tmp_path)
    import movie_narrator.pipeline.render as render_mod

    def mock_videofileclip(*a, **kw):
        raise OSError("cannot open video")

    monkeypatch.setattr(render_mod, "VideoFileClip", mock_videofileclip)
    mock_composite = MagicMock()
    monkeypatch.setattr(render_mod, "CompositeVideoClip", mock_composite)
    monkeypatch.setattr(render_mod, "ColorClip", MagicMock())

    audio_clip = MagicMock()
    audio_clip.duration = 2.0
    monkeypatch.setattr(render_mod, "AudioFileClip", lambda _p: audio_clip)

    monkeypatch.setattr(render_mod, "ensure_final_audio", lambda ctx_: None)
    monkeypatch.setattr(render_mod, "_create_text_image", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(render_mod, "ImageClip", MagicMock())
    monkeypatch.setattr(
        render_mod,
        "_get_video_sizes",
        lambda ctx_: {"16:9": (1920, 1080)},
    )

    # Force the ENCODE to advertise a GPU encoder...
    monkeypatch.setattr(
        render_mod,
        "resolve_encoder",
        lambda hint: ("h264_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "20"]),
    )
    # ...then make the first hardware encode throw so the runtime fallback fires.
    write_calls = {"n": 0}

    def fake_write(*a, **kw):
        write_calls["n"] += 1
        if write_calls["n"] == 1:
            raise OSError("nvidia driver failed")
        return None

    monkeypatch.setattr(render_mod, "_write_videofile_with_deadline", fake_write)

    # Deterministic ffmpeg + mux subprocess that materialises the .part target.
    monkeypatch.setattr(render_mod, "ffmpeg_bin", lambda: "/fake/ffmpeg")

    def fake_run(cmd, *a, **kw):
        # mux_cmd's last element is the staging .part path.
        Path(str(cmd[-1])).write_bytes(b"0")
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        render_mod,
        "get_encoder_info",
        lambda hint: {
            "requested": hint or "auto",
            "detected": "h264_nvenc",
            "active": "h264_nvenc",
            "gpu_available": True,
            "fallback_reason": None,
        },
    )
    monkeypatch.setattr(render_mod, "build_metadata_json", lambda ctx_: {})

    render_mod.render_video(ctx)

    assert write_calls["n"] == 2  # GPU try + libx264 retry
    info = ctx.metadata["encoder_info"]
    assert info["active"] == "libx264"
    assert info["fallback_reason"] == "gpu_runtime_fallback"
