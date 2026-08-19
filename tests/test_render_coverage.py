# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""High-coverage unit tests for ``pipeline/render.py``.

Covers the branch paths that the existing render tests do not exercise:
preview mode, footage production branches (cover/contain/fallback),
scene transitions, text animation, title/end/watermark/disclaimer cards,
GPU encode fallback, resource-cleanup edge cases, ``_export_cover_image``,
``_overlay_text``, ``_create_watermark_image`` and the progress logger.

All heavyweight dependencies (MoviePy clips, PIL, ffmpeg, subprocess) are
mocked so no real ffmpeg / video file is required.
"""

from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pytest

from movie_narrator.models import Context, MatchedClip, Services, TimedSegment
from movie_narrator.pipeline import render as render_mod
from movie_narrator.pipeline.render import (
    _export_cover_image,
    _overlay_text,
    _substitute_movie,
    _create_watermark_image,
    render_video,
)


# ── Helpers ─────────────────────────────────────────────────


def _chainable_clip(end: float = 6.0) -> MagicMock:
    """MoviePy-style chainable clip mock."""
    clip = MagicMock(name="clip")
    clip.duration = end
    clip.w = 1920
    clip.h = 1080
    clip.size = (1920, 1080)
    clip.subclipped.return_value = clip
    clip.with_speed_scaled.return_value = clip
    clip.cropped.return_value = clip
    clip.resized.return_value = clip
    clip.with_position.return_value = clip
    clip.with_start.return_value = clip
    clip.with_duration.return_value = clip
    clip.with_audio.return_value = clip
    clip.with_effects.return_value = clip
    clip.write_videofile = MagicMock()
    clip.close = MagicMock()
    return clip


def _fake_run_writing_partial(fake_proc):
    """``subprocess.run`` side-effect that stages the atomic-publish ``.part``.

    Mirrors what a real ffmpeg mux does: write the output file that the
    command's last argument names, then return the completed process.
    """

    def _run(cmd, **kw):
        if str(cmd[-1]).endswith(".part"):
            Path(cmd[-1]).write_bytes(b"final-moov-fake-bytes")
        return fake_proc

    return _run


def _make_ctx(tmp_path, *, with_source=True, matched=True, n_segments=1):
    """Build a minimal Context for render_video tests."""
    ctx = Context(
        movie_name="飞驰人生",
        output_dir=str(tmp_path),
        timed_segments=[
            TimedSegment(text=f"片段{i}", start=i * 2.0, end=i * 2.0 + 2.0)
            for i in range(n_segments)
        ],
        source_video_path=str(tmp_path / "source.mp4") if with_source else None,
    )
    ctx.audio_path = str(tmp_path / "narration.wav")
    if matched:
        ctx.matched_clips = [
            MatchedClip(
                segment_index=i,
                text=f"片段{i}",
                narr_start=i * 2.0,
                narr_end=i * 2.0 + 2.0,
                src_start=i * 2.0,
                src_end=i * 2.0 + 2.0,
                score=0.9,
                source="heuristic",
                scene_index=i,
            )
            for i in range(n_segments)
        ]
    return ctx


_UNSET = object()


def _run_render(
    ctx,
    monkeypatch,
    *,
    final_clips=_UNSET,
    fail_applied=False,
    apply_transition=None,
    apply_text_animation=None,
    export_cover=None,
    **overrides,
):
    """Run render_video with all heavy deps mocked.

    Returns the ``final`` CompositeVideoClip mock so callers can assert.
    """
    final = _chainable_clip()
    final.with_audio = MagicMock(return_value=final)
    if final_clips is not _UNSET:
        final.clips = final_clips

    def _fake_write(path, **kw):
        Path(path).write_bytes(b"moov-fake-bytes")

    final.write_videofile = MagicMock(side_effect=_fake_write)

    audio = MagicMock(name="audio")
    audio.duration = overrides.pop("audio_duration", 6.0)
    audio.close = MagicMock()

    source = MagicMock(name="source")
    source.subclipped = MagicMock(
        side_effect=lambda s, e: (_ for _ in ()).throw(ValueError("boom"))
        if fail_applied
        else _chainable_clip(end=e - s)
    )
    source.close = MagicMock()

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stderr = ""

    monkeypatch.setattr(render_mod, "ensure_final_audio", MagicMock())
    monkeypatch.setattr(render_mod, "CompositeVideoClip", MagicMock(return_value=final))
    monkeypatch.setattr(render_mod, "ColorClip", MagicMock(return_value=_chainable_clip()))
    monkeypatch.setattr(render_mod, "ImageClip", MagicMock(return_value=_chainable_clip()))
    monkeypatch.setattr(render_mod, "AudioFileClip", MagicMock(return_value=audio))
    monkeypatch.setattr(render_mod, "VideoFileClip", MagicMock(return_value=source))
    monkeypatch.setattr(render_mod, "_create_text_image", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(render_mod, "_create_watermark_image", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(render_mod, "build_metadata_json", MagicMock(return_value={}))
    monkeypatch.setattr(render_mod, "resolve_encoder", MagicMock(return_value=("libx264", [])))
    monkeypatch.setattr(render_mod, "get_encoder_info", MagicMock(return_value={}))
    monkeypatch.setattr(
        render_mod, "_export_cover_image",
        export_cover if export_cover is not None else MagicMock(),
    )
    monkeypatch.setattr(render_mod, "compute_fit_box", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(
        render_mod, "apply_transition",
        apply_transition if apply_transition is not None else (lambda c, t, d: c),
    )
    monkeypatch.setattr(render_mod, "get_transition_duration", MagicMock(return_value=0.3))
    monkeypatch.setattr(
        render_mod, "apply_text_animation",
        apply_text_animation if apply_text_animation is not None else (lambda c, t, d: c),
    )
    monkeypatch.setattr(render_mod, "get_animation_duration", MagicMock(return_value=0.2))
    monkeypatch.setattr("subprocess.run", MagicMock(side_effect=_fake_run_writing_partial(fake_proc)))
    monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))

    render_video(ctx)
    return final


# ── 1. _overlay_text ────────────────────────────────────────


class TestOverlayText:
    def _ctx(self, mode, translated):
        ctx = MagicMock()
        ctx.metadata.get = MagicMock(side_effect=lambda k, d=None: {
            "subtitle_mode": mode,
        }.get(k, d))
        ctx.translated_texts = list(translated)
        return ctx

    def test_original_mode_returns_seg_text(self):
        ctx = self._ctx("original", ["译1"])
        seg = TimedSegment(text="本1", start=0.0, end=1.0)
        assert _overlay_text(ctx, 0, seg) == "本1"

    def test_translated_mode_returns_translation(self):
        ctx = self._ctx("translated", ["译1"])
        seg = TimedSegment(text="本1", start=0.0, end=1.0)
        assert _overlay_text(ctx, 0, seg) == "译1"

    def test_bilingual_mode_joins(self):
        ctx = self._ctx("bilingual", ["译1"])
        seg = TimedSegment(text="本1", start=0.0, end=1.0)
        assert _overlay_text(ctx, 0, seg) == "本1\n译1"

    def test_missing_translation_falls_back(self):
        ctx = self._ctx("translated", [])
        seg = TimedSegment(text="本1", start=0.0, end=1.0)
        assert _overlay_text(ctx, 0, seg) == "本1"


# ── 2. _substitute_movie ────────────────────────────────────


class TestSubstituteMovieEmpty:
    def test_empty_text_returns_unchanged(self):
        assert _substitute_movie("", "飞驰人生") == ""


# ── 3. _create_watermark_image ──────────────────────────────


class TestCreateWatermarkImage:
    def test_returns_rgba_ndarray(self, monkeypatch):
        fake_draw = MagicMock()
        fake_draw.textbbox.return_value = (0, 0, 100, 30)
        fake_draw.text = MagicMock()

        class FakeImage:
            @staticmethod
            def new(mode, size, color=None):
                return MagicMock()

        monkeypatch.setattr(render_mod, "Image", FakeImage)
        monkeypatch.setattr(render_mod, "ImageDraw", MagicMock(Draw=lambda *a, **k: fake_draw))
        monkeypatch.setattr("movie_narrator.utils.font.get_font", MagicMock())

        arr = _create_watermark_image("水印", (1080, 1920), fontsize=36)
        assert isinstance(arr, np.ndarray)


# ── 4. _RenderProgressLogger.bars_callback ──────────────────


class TestRenderProgressLogger:
    def test_renames_bar_title(self, monkeypatch):
        logger = render_mod._RenderProgressLogger()
        logger.state["bars"] = {"t": {"title": "t"}}
        monkeypatch.setattr(
            render_mod.TqdmProgressBarLogger,
            "bars_callback",
            lambda self, bar, attr, value, old_value=None: None,
        )
        logger.bars_callback("t", "status", "res", "res")
        assert logger.state["bars"]["t"]["title"] == "Rendering"


# ── 5. _export_cover_image ──────────────────────────────────


class TestExportCoverImage:
    def _ctx(self, tmp_path, clips=None):
        ctx = Context(
            movie_name="飞驰人生",
            output_dir=str(tmp_path),
            source_video_path=str(tmp_path / "source.mp4"),
            services=Services(console=MagicMock()),
        )
        ctx.matched_clips = clips or []
        return ctx

    def _clip(self, score=0.9):
        return MatchedClip(
            segment_index=0,
            text="t",
            narr_start=0.0,
            narr_end=2.0,
            src_start=0.0,
            src_end=2.0,
            score=score,
            source="heuristic",
        )

    def test_no_usable_clips_skips(self, tmp_path, monkeypatch):
        ctx = self._ctx(tmp_path, clips=[])
        monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))
        _export_cover_image(ctx, [], tmp_path)
        ctx.services.console.debug.assert_called()
        assert not (tmp_path / "cover.jpg").exists()

    def test_no_scored_clips_skips(self, tmp_path, monkeypatch):
        ctx = self._ctx(tmp_path, clips=[self._clip(score=0.0)])
        _export_cover_image(ctx, [self._clip(score=0.0)], tmp_path)
        ctx.services.console.debug.assert_called()

    def test_ffmpeg_not_found_skips(self, tmp_path, monkeypatch):
        ctx = self._ctx(tmp_path, clips=[self._clip()])
        monkeypatch.setattr(
            "movie_narrator.pipeline.render.ffmpeg_bin", MagicMock(return_value="ffmpeg")
        )
        _export_cover_image(ctx, [self._clip()], tmp_path)
        ctx.services.console.debug.assert_called()

    def test_extract_failure_skips(self, tmp_path, monkeypatch):
        ctx = self._ctx(tmp_path, clips=[self._clip()])
        monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))
        proc = MagicMock()
        proc.returncode = 1
        proc.stderr = "boom"
        monkeypatch.setattr("subprocess.run", MagicMock(return_value=proc))
        _export_cover_image(ctx, [self._clip()], tmp_path)
        ctx.services.console.debug.assert_called()

    def test_extract_subprocess_error_skips(self, tmp_path, monkeypatch):
        ctx = self._ctx(tmp_path, clips=[self._clip()])
        monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))
        from subprocess import SubprocessError

        def _raise(*a, **k):
            raise OSError("no")

        monkeypatch.setattr("subprocess.run", MagicMock(side_effect=_raise))
        _export_cover_image(ctx, [self._clip()], tmp_path)
        ctx.services.console.debug.assert_called()

    def test_success_exports_cover(self, tmp_path, monkeypatch):
        ctx = self._ctx(tmp_path, clips=[self._clip()])
        (tmp_path / "_cover_raw.jpg").write_bytes(b"x")
        fake_img = MagicMock()
        fake_img.size = (1000, 500)
        fake_img.resize.return_value = fake_img
        fake_img.paste = MagicMock()
        fake_img.save = MagicMock()
        fake_draw = MagicMock()
        fake_draw.textbbox.return_value = (0, 0, 100, 20)
        fake_draw.line = MagicMock()
        fake_draw.text = MagicMock()

        class FakeImage:
            @staticmethod
            def open(path):
                fake_img.convert.return_value = fake_img
                return fake_img

            @staticmethod
            def new(mode, size, color=None):
                return MagicMock()

        monkeypatch.setattr(render_mod, "Image", FakeImage)
        monkeypatch.setattr(render_mod, "ImageDraw", MagicMock(Draw=lambda *a, **k: fake_draw))
        monkeypatch.setattr("movie_narrator.utils.font.get_font", MagicMock())
        monkeypatch.setattr(
            "movie_narrator.utils.text_image._wrap_line",
            lambda text, draw, font, maxw: [text],
        )
        monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        monkeypatch.setattr("subprocess.run", MagicMock(return_value=proc))
        _export_cover_image(ctx, [self._clip()], tmp_path)
        fake_img.save.assert_called()
        # raw frame cleaned up in finally
        assert not (tmp_path / "_cover_raw.jpg").exists()

    def test_success_resizes_wide_cover(self, tmp_path, monkeypatch):
        """Wide frames (w > 1280) are resized to a standard cover width."""
        ctx = self._ctx(tmp_path, clips=[self._clip()])
        (tmp_path / "_cover_raw.jpg").write_bytes(b"x")
        fake_img = MagicMock()
        fake_img.size = (2000, 1000)
        fake_img.resize.return_value = fake_img
        fake_img.paste = MagicMock()
        fake_img.save = MagicMock()
        fake_draw = MagicMock()
        fake_draw.textbbox.return_value = (0, 0, 100, 20)
        fake_draw.line = MagicMock()
        fake_draw.text = MagicMock()

        class FakeImage:
            @staticmethod
            def open(path):
                fake_img.convert.return_value = fake_img
                return fake_img

            @staticmethod
            def new(mode, size, color=None):
                return MagicMock()

        monkeypatch.setattr(render_mod, "Image", FakeImage)
        monkeypatch.setattr(render_mod, "ImageDraw", MagicMock(Draw=lambda *a, **k: fake_draw))
        monkeypatch.setattr("movie_narrator.utils.font.get_font", MagicMock())
        monkeypatch.setattr(
            "movie_narrator.utils.text_image._wrap_line",
            lambda text, draw, font, maxw: [text],
        )
        monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""
        monkeypatch.setattr("subprocess.run", MagicMock(return_value=proc))
        _export_cover_image(ctx, [self._clip()], tmp_path)
        fake_img.resize.assert_called_with((1280, 640))
        fake_img.save.assert_called()

    def test_overlay_error_skips(self, tmp_path, monkeypatch):
        ctx = self._ctx(tmp_path, clips=[self._clip()])
        (tmp_path / "_cover_raw.jpg").write_bytes(b"x")
        monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))
        proc = MagicMock()
        proc.returncode = 0
        proc.stderr = ""

        class FakeImage:
            @staticmethod
            def open(path):
                raise ValueError("bad image")

        monkeypatch.setattr(render_mod, "Image", FakeImage)
        monkeypatch.setattr("subprocess.run", MagicMock(return_value=proc))
        _export_cover_image(ctx, [self._clip()], tmp_path)
        ctx.services.console.debug.assert_called()


# ── 6. render_video — baseline happy path ────────────────────


class TestRenderVideo:
    def test_baseline_with_matched_clips_cover(self, tmp_path, monkeypatch):
        """Cover-mode footage branch + libx264 write path."""
        ctx = _make_ctx(tmp_path, n_segments=2)
        final = _run_render(ctx, monkeypatch)
        final.write_videofile.assert_called_once()
        assert ctx.video_path == str(tmp_path / "final.mp4")
        assert ctx.metadata["footage_coverage"]["ratio"] == 1.0

    def test_baseline_no_footage(self, tmp_path, monkeypatch):
        """No matched clips → text-only render, zero coverage."""
        ctx = _make_ctx(tmp_path, with_source=False, matched=False, n_segments=1)
        _run_render(ctx, monkeypatch)
        assert ctx.metadata["footage_coverage"]["ratio"] == 0.0

    def test_preview_mode(self, tmp_path, monkeypatch):
        """Preview mode truncates audio + segments and names output preview.mp4."""
        ctx = _make_ctx(tmp_path, n_segments=1)
        ctx.metadata["render_preview_mode"] = True
        ctx.metadata["render_preview_sec"] = 5.0
        monkeypatch.setattr(
            "movie_narrator.utils.preview.get_preview_duration",
            MagicMock(return_value=5.0),
        )
        truncate = MagicMock(return_value=[TimedSegment(text="x", start=0.0, end=5.0)])
        monkeypatch.setattr(
            "movie_narrator.utils.preview.truncate_segments_for_preview", truncate
        )
        _run_render(ctx, monkeypatch, audio_duration=10.0)
        assert ctx.video_path == str(tmp_path / "preview.mp4")
        truncate.assert_called_once()

    def test_matched_contain_transition_animation(self, tmp_path, monkeypatch):
        """contain fit-mode + scene transition + text animation branches."""
        ctx = _make_ctx(tmp_path, n_segments=1)
        ctx.metadata["render_fit_mode"] = "contain"
        ctx.metadata["render_transition"] = "fade"
        ctx.metadata["render_text_animation"] = "fade"
        apply_transition = MagicMock(side_effect=lambda c, t, d: c)
        apply_text_anim = MagicMock(side_effect=lambda c, t, d: c)
        _run_render(
            ctx,
            monkeypatch,
            apply_transition=apply_transition,
            apply_text_animation=apply_text_anim,
        )
        apply_transition.assert_called()
        apply_text_anim.assert_called()

    def test_matched_fallback_to_text(self, tmp_path, monkeypatch):
        """ValueError in footage branch → text-image fallback clip."""
        ctx = _make_ctx(tmp_path, n_segments=1)
        _run_render(ctx, monkeypatch, fail_applied=True)
        assert ctx.video_path == str(tmp_path / "final.mp4")

    def test_title_and_end_and_watermark_and_disclaimer(self, tmp_path, monkeypatch):
        """All four template overlay branches."""
        ctx = _make_ctx(tmp_path, n_segments=1)
        ctx.metadata["render_title_card_sec"] = 2
        ctx.metadata["render_template"] = {
            "title_card_text": "{movie}片头",
            "end_card_text": "{movie}片尾",
            "watermark_text": "{movie}出品",
            "disclaimer_text": "免责声明",
        }
        _run_render(ctx, monkeypatch)
        assert ctx.video_path == str(tmp_path / "final.mp4")

    def test_title_card_fade_import_error(self, tmp_path, monkeypatch):
        """FadeIn/FadeOut effect failure degrades gracefully."""
        import moviepy.video.fx as vfx

        class _BadFx:
            def __init__(self, *a, **k):
                raise ValueError("no fx")

        monkeypatch.setattr(vfx, "FadeIn", _BadFx)
        monkeypatch.setattr(vfx, "FadeOut", _BadFx)
        ctx = _make_ctx(tmp_path, n_segments=1)
        ctx.metadata["render_title_card_sec"] = 2
        ctx.metadata["render_template"] = {"title_card_text": "{movie}"}
        _run_render(ctx, monkeypatch)
        assert ctx.video_path == str(tmp_path / "final.mp4")

    def test_libx264_write_failure_re_raises(self, tmp_path, monkeypatch):
        """libx264 write_videofile failure re-raises (no GPU fallback)."""
        ctx = _make_ctx(tmp_path, n_segments=1)
        final = _chainable_clip()
        final.with_audio = MagicMock(return_value=final)
        final.write_videofile = MagicMock(side_effect=OSError("cpu fail"))
        audio = MagicMock(name="audio")
        audio.duration = 6.0
        audio.close = MagicMock()
        source = MagicMock(name="source")
        source.subclipped = MagicMock(return_value=_chainable_clip())
        source.close = MagicMock()
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stderr = ""

        monkeypatch.setattr(render_mod, "ensure_final_audio", MagicMock())
        monkeypatch.setattr(render_mod, "CompositeVideoClip", MagicMock(return_value=final))
        monkeypatch.setattr(render_mod, "ColorClip", MagicMock(return_value=_chainable_clip()))
        monkeypatch.setattr(render_mod, "ImageClip", MagicMock(return_value=_chainable_clip()))
        monkeypatch.setattr(render_mod, "AudioFileClip", MagicMock(return_value=audio))
        monkeypatch.setattr(render_mod, "VideoFileClip", MagicMock(return_value=source))
        monkeypatch.setattr(render_mod, "_create_text_image", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(render_mod, "build_metadata_json", MagicMock(return_value={}))
        monkeypatch.setattr("subprocess.run", MagicMock(return_value=fake_proc))
        monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))

        with pytest.raises(OSError):
            render_video(ctx)

    def test_end_card_fade_import_error(self, tmp_path, monkeypatch):
        """End-card FadeIn/FadeOut effect failure degrades gracefully."""
        import moviepy.video.fx as vfx

        class _BadFx:
            def __init__(self, *a, **k):
                raise ValueError("no fx")

        monkeypatch.setattr(vfx, "FadeIn", _BadFx)
        monkeypatch.setattr(vfx, "FadeOut", _BadFx)
        ctx = _make_ctx(tmp_path, n_segments=1)
        ctx.metadata["render_title_card_sec"] = 0
        ctx.metadata["render_template"] = {"end_card_text": "{movie}完了"}
        _run_render(ctx, monkeypatch)
        assert ctx.video_path == str(tmp_path / "final.mp4")

    def test_gpu_encoder_and_failure_retry(self, tmp_path, monkeypatch):
        """GPU encoder params + graceful fallback to libx264 on failure."""
        ctx = _make_ctx(tmp_path, n_segments=1)
        ctx.metadata["render_encoder"] = "nvenc"
        monkeypatch.setattr(
            render_mod, "resolve_encoder", MagicMock(return_value=("h264_nvenc", ["-preset", "p4"]))
        )
        final = _chainable_clip()
        final.with_audio = MagicMock(return_value=final)
        state = {"n": 0}

        def _write(path, **kw):
            if state["n"] == 0:
                state["n"] += 1
                raise OSError("gpu fail")
            state["n"] += 1
            Path(path).write_bytes(b"moov-fake-bytes")

        final.write_videofile = MagicMock(side_effect=_write)
        audio = MagicMock(name="audio")
        audio.duration = 6.0
        audio.close = MagicMock()
        source = MagicMock(name="source")
        source.subclipped = MagicMock(return_value=_chainable_clip())
        source.close = MagicMock()
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stderr = ""

        monkeypatch.setattr(render_mod, "ensure_final_audio", MagicMock())
        monkeypatch.setattr(render_mod, "CompositeVideoClip", MagicMock(return_value=final))
        monkeypatch.setattr(render_mod, "ColorClip", MagicMock(return_value=_chainable_clip()))
        monkeypatch.setattr(render_mod, "ImageClip", MagicMock(return_value=_chainable_clip()))
        monkeypatch.setattr(render_mod, "AudioFileClip", MagicMock(return_value=audio))
        monkeypatch.setattr(render_mod, "VideoFileClip", MagicMock(return_value=source))
        monkeypatch.setattr(render_mod, "_create_text_image", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(render_mod, "build_metadata_json", MagicMock(return_value={}))
        monkeypatch.setattr(render_mod, "get_encoder_info", MagicMock(return_value={}))
        monkeypatch.setattr("subprocess.run", MagicMock(side_effect=_fake_run_writing_partial(fake_proc)))
        monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))

        render_video(ctx)
        assert state["n"] == 2
        assert ctx.video_path == str(tmp_path / "final.mp4")

    def test_child_clips_cleanup_error(self, tmp_path, monkeypatch):
        """final_video.clips raises → child_clips falls back to empty."""
        ctx = _make_ctx(tmp_path, n_segments=1)
        _run_render(ctx, monkeypatch, final_clips=None)

    def test_resource_close_error(self, tmp_path, monkeypatch):
        """A clip's close() raising must not abort cleanup."""
        ctx = _make_ctx(tmp_path, n_segments=1)
        final = _chainable_clip()
        final.with_audio = MagicMock(return_value=final)
        final.write_videofile = MagicMock(
            side_effect=lambda path, **kw: Path(path).write_bytes(b"x")
        )
        audio = MagicMock(name="audio")
        audio.duration = 6.0
        audio.close = MagicMock(side_effect=RuntimeError("close fail"))
        source = MagicMock(name="source")
        source.subclipped = MagicMock(return_value=_chainable_clip())
        source.close = MagicMock()
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.stderr = ""

        monkeypatch.setattr(render_mod, "ensure_final_audio", MagicMock())
        monkeypatch.setattr(render_mod, "CompositeVideoClip", MagicMock(return_value=final))
        monkeypatch.setattr(render_mod, "ColorClip", MagicMock(return_value=_chainable_clip()))
        monkeypatch.setattr(render_mod, "ImageClip", MagicMock(return_value=_chainable_clip()))
        monkeypatch.setattr(render_mod, "AudioFileClip", MagicMock(return_value=audio))
        monkeypatch.setattr(render_mod, "VideoFileClip", MagicMock(return_value=source))
        monkeypatch.setattr(render_mod, "_create_text_image", MagicMock(return_value=MagicMock()))
        monkeypatch.setattr(render_mod, "build_metadata_json", MagicMock(return_value={}))
        monkeypatch.setattr("subprocess.run", MagicMock(side_effect=_fake_run_writing_partial(fake_proc)))
        monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))

        render_video(ctx)
        assert ctx.video_path == str(tmp_path / "final.mp4")

    def test_rmtree_error_and_cache_cleanup(self, tmp_path, monkeypatch):
        """rmtree raising OSError is swallowed; cache dir is removed."""
        import shutil as _shutil

        ctx = _make_ctx(tmp_path, n_segments=1)
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        real_rmtree = _shutil.rmtree

        def _fail_or_ok(path, **kw):
            if ".tmp" in str(path):
                raise OSError("rmtree fail")
            return real_rmtree(path, **kw)

        monkeypatch.setattr("shutil.rmtree", MagicMock(side_effect=_fail_or_ok))
        _run_render(ctx, monkeypatch)
        # cache dir removed via real rmtree (which swallowed the .tmp failure)
        assert not cache_dir.exists()

    def test_footage_gate_and_duration_metrics(self, tmp_path, monkeypatch):
        """Low-coverage gate + duration metrics metadata."""
        ctx = _make_ctx(tmp_path, with_source=False, matched=False, n_segments=2)
        ctx.metadata["render_require_footage"] = True
        ctx.metadata["render_min_footage_coverage"] = 0.5
        ctx.metadata["duration"] = 10
        _run_render(ctx, monkeypatch, audio_duration=10.0)
        assert "render_video" in ctx.metadata["_degraded_steps"]
        assert ctx.metadata["duration_metrics"]["target_sec"] == 10
        assert ctx.metadata["duration_metrics"]["ratio"] >= 0.0

    def test_cover_export_called(self, tmp_path, monkeypatch):
        """render_cover_export=True invokes the cover exporter."""
        ctx = _make_ctx(tmp_path, n_segments=1)
        ctx.metadata["render_cover_export"] = True
        spy = MagicMock()
        _run_render(ctx, monkeypatch, export_cover=spy)
        spy.assert_called_once()

    def test_vertical_safe_area_applied(self, tmp_path, monkeypatch):
        """9:16 vertical safe-area narrows the subtitle band."""
        ctx = _make_ctx(tmp_path, n_segments=1)
        ctx.metadata["video_format"] = "9:16"
        ctx.metadata["render_vertical_safe_area"] = True
        ctx.metadata["render_template"] = {
            "aspect_safe_area": {"max_width_ratio": 0.75, "bottom_margin_ratio": 0.20}
        }
        _run_render(ctx, monkeypatch)
        assert ctx.video_path == str(tmp_path / "final.mp4")