import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Context, MatchedClip, TimedSegment
from movie_narrator.pipeline.render import render_video


SAMPLE_RATE = 44100
_AUDIO_SECONDS = 6.0
_N_CHANNELS = 2
_SAMPLE_WIDTH = 2  # 16-bit


def _write_silent_wav(path: Path, duration: float) -> None:
    n_frames = int(duration * SAMPLE_RATE)
    data = b"\x00" * (n_frames * _N_CHANNELS * _SAMPLE_WIDTH)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(_N_CHANNELS)
        wf.setsampwidth(_SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(data)


def _make_ctx(tmp_path, matched=False, include_fallback=False):
    audio_path = tmp_path / "narration.wav"
    _write_silent_wav(audio_path, _AUDIO_SECONDS)
    matched_clips = []
    if matched:
        matched_clips.append(
            MatchedClip(
                segment_index=0,
                text="Hello",
                narr_start=0.0,
                narr_end=2.0,
                src_start=0.0,
                src_end=2.0,
                score=1.0,
                source="heuristic",
                scene_index=0,
            )
        )
    if include_fallback:
        # Construction-default "fallback" rows must be ignored by render (Spec §2).
        matched_clips.append(
            MatchedClip(
                segment_index=1,
                text="World",
                narr_start=2.5,
                narr_end=5.0,
                src_start=2.0,
                src_end=4.0,
                score=0.0,
                source="fallback",
                scene_index=1,
            )
        )
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        style="s",
        duration=10,
        timed_segments=[
            TimedSegment(text="Hello", start=0.0, end=2.0),
            TimedSegment(text="World", start=2.5, end=5.0),
        ],
        source_video_path=str(tmp_path / "source.mp4"),
        matched_clips=matched_clips,
    )
    ctx.audio_path = str(audio_path)
    return ctx


def _chainable_clip(end: float = 2.0):
    """MoviePy-style chainable clip mock used only for write path."""
    clip = MagicMock(name="clip")
    clip.end = end
    clip.duration = end
    clip.w = 1920
    clip.h = 1080
    clip.size = (1920, 1080)
    clip.audio = None
    clip.mask = None
    clip.with_speed_scaled.return_value = clip
    clip.with_start.return_value = clip
    clip.with_duration.return_value = clip
    clip.with_position.return_value = clip
    clip.with_audio.return_value = clip
    clip.resized.return_value = clip
    clip.write_videofile = MagicMock()
    clip.close = MagicMock()
    return clip


def test_render_without_matched_clips(tmp_path):
    """Without matched clips, renders text overlays on solid background.

    Mocks `CompositeVideoClip` and `write_videofile` to avoid real MoviePy
    encoding (Windows file-lock flake on temp audio files; F-012).
    """
    ctx = _make_ctx(tmp_path, matched=False)

    bg = _chainable_clip(end=_AUDIO_SECONDS)
    img = _chainable_clip(end=2.5)
    audio = MagicMock(name="audio")
    audio.duration = _AUDIO_SECONDS
    audio.close = MagicMock()

    final = _chainable_clip(end=_AUDIO_SECONDS)
    final.with_audio = MagicMock(return_value=final)

    def _fake_write(path, **kwargs):
        Path(path).write_bytes(b"")

    final.write_videofile = MagicMock(side_effect=_fake_write)

    with (
        patch("movie_narrator.pipeline.render.CompositeVideoClip", return_value=final),
        patch("movie_narrator.pipeline.render.ColorClip", return_value=bg),
        patch("movie_narrator.pipeline.render.ImageClip", return_value=img),
        patch("movie_narrator.pipeline.render.AudioFileClip", return_value=audio),
        patch("movie_narrator.pipeline.render._create_text_image", return_value=MagicMock()),
    ):
        render_video(ctx)

    final.write_videofile.assert_called_once()
    assert ctx.video_path == str(tmp_path / "final.mp4")


def test_render_with_matched_clips(tmp_path):
    """With matched clips, uses real-footage branch; ignores fallback rows.

    Patches VideoFileClip + CompositeVideoClip so the branch filter is
    exercised without requiring an ffmpeg-decodable fixture.
    """
    (tmp_path / "source.mp4").write_bytes(b"00")
    ctx = _make_ctx(tmp_path, matched=True, include_fallback=True)

    fake_source = MagicMock(name="source")
    fake_source.subclipped = MagicMock(side_effect=lambda s, e: _chainable_clip(end=e - s))
    fake_source.close = MagicMock()

    final = _chainable_clip(end=_AUDIO_SECONDS)
    bg = _chainable_clip(end=_AUDIO_SECONDS)
    img = _chainable_clip(end=2.5)
    audio = MagicMock(name="audio")
    audio.duration = _AUDIO_SECONDS
    audio.close = MagicMock()

    with (
        patch("movie_narrator.pipeline.render.VideoFileClip", return_value=fake_source) as mock_vfc,
        patch("movie_narrator.pipeline.render.CompositeVideoClip", return_value=final) as mock_cvc,
        patch("movie_narrator.pipeline.render.ColorClip", return_value=bg),
        patch("movie_narrator.pipeline.render.ImageClip", return_value=img),
        patch("movie_narrator.pipeline.render.AudioFileClip", return_value=audio),
        patch("movie_narrator.pipeline.render._create_text_image", return_value=MagicMock()),
    ):
        render_video(ctx)

    mock_vfc.assert_called_once_with(str(tmp_path / "source.mp4"))
    # Only the heuristic row is used; the fallback row must be skipped.
    assert fake_source.subclipped.call_count == 1
    fake_source.subclipped.assert_called_with(0.0, 2.0)
    fake_source.close.assert_called_once()
    mock_cvc.assert_called_once()
    final.write_videofile.assert_called_once()
    assert ctx.video_path == str(tmp_path / "final.mp4")
    assert (tmp_path / "metadata.json").exists()
