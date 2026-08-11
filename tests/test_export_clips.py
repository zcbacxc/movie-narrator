# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the clip-export pipeline step."""

from types import SimpleNamespace
from unittest.mock import patch

from movie_narrator.models import Context, Scene, StepResult
from movie_narrator.pipeline.export_clips import export_clips


def _ctx(tmp_path, scenes=None):
    return Context(
        movie_name="Test",
        output_dir=str(tmp_path),
        source_video_path="/v.mp4",
        scenes=scenes or [],
    )


def _scene(index=0, start=0.0, end=5.0):
    return Scene(index=index, start=start, end=end)


# ── disabled / skipped guard paths ──────────────────────────


def test_disabled_by_metadata_flag():
    ctx = _ctx("/tmp")
    ctx.metadata["export_clips"] = False
    export_clips(ctx)
    assert ctx.status.export == "skipped"
    assert ctx.step_state.result == StepResult.SKIPPED
    assert ctx.step_state.message == "disabled by flag"


def test_disabled_when_scenedetect_missing(tmp_path):
    ctx = _ctx(tmp_path)
    with patch("movie_narrator.pipeline.export_clips.probe", return_value=(False, "need scenedetect")):
        export_clips(ctx)
    assert ctx.status.export == "disabled"
    assert ctx.step_state.result == StepResult.SKIPPED
    assert ctx.step_state.message == "need scenedetect"


def test_skipped_when_nothing_to_export(tmp_path):
    ctx = _ctx(tmp_path)
    with patch("movie_narrator.pipeline.export_clips.probe", return_value=(True, "")):
        export_clips(ctx)
    assert ctx.status.export == "skipped"
    assert ctx.step_state.message == "nothing to export"


def test_skipped_when_no_source_video(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.scenes = [_scene()]
    ctx.source_video_path = None
    with patch("movie_narrator.pipeline.export_clips.probe", return_value=(True, "")):
        export_clips(ctx)
    assert ctx.status.export == "skipped"
    assert ctx.step_state.message == "no source video"


def test_disabled_when_ffmpeg_missing(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.scenes = [_scene()]
    with (
        patch("movie_narrator.pipeline.export_clips.probe", return_value=(True, "")),
        patch("movie_narrator.pipeline.export_clips.ffmpeg_bin", return_value="ffmpeg"),
    ):
        export_clips(ctx)
    assert ctx.status.export == "disabled"
    assert ctx.step_state.message == "ffmpeg not found on PATH"


# ── success / failure paths ─────────────────────────────────


def _fake_proc(returncode=0, stderr=b""):
    return SimpleNamespace(returncode=returncode, stderr=stderr)


def test_success_exports_all_scenes(tmp_path):
    ctx = _ctx(tmp_path, scenes=[_scene(0, 0, 5), _scene(1, 5, 10)])
    with (
        patch("movie_narrator.pipeline.export_clips.probe", return_value=(True, "")),
        patch("movie_narrator.pipeline.export_clips.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch("movie_narrator.pipeline.export_clips.subprocess.run", return_value=_fake_proc()) as run,
    ):
        export_clips(ctx)
    assert ctx.status.export == "success"
    assert ctx.step_state.result == StepResult.SUCCESS
    assert ctx.clips_dir is not None
    assert run.call_count == 2
    assert ctx.scenes[0].clip_path is not None
    assert ctx.scenes[1].clip_path is not None
    # scene_0000.mp4 / scene_0001.mp4 naming
    assert ctx.scenes[0].clip_path.endswith("scene_0000.mp4")


def test_partial_when_one_scene_fails(tmp_path):
    ctx = _ctx(tmp_path, scenes=[_scene(0, 0, 5), _scene(1, 5, 10)])
    results = [_fake_proc(returncode=1, stderr=b"boom"), _fake_proc()]
    with (
        patch("movie_narrator.pipeline.export_clips.probe", return_value=(True, "")),
        patch("movie_narrator.pipeline.export_clips.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch("movie_narrator.pipeline.export_clips.subprocess.run", side_effect=results),
        patch("movie_narrator.pipeline.export_clips.append_warning") as warn,
    ):
        export_clips(ctx)
    assert ctx.status.export == "partial"
    warn.assert_called_once()
    assert "1 clip(s) failed" in warn.call_args[0][1]
    # Only the successful scene got a clip_path.
    assert ctx.scenes[0].clip_path is None
    assert ctx.scenes[1].clip_path is not None


def test_partial_when_subprocess_raises(tmp_path):
    ctx = _ctx(tmp_path, scenes=[_scene(0, 0, 5)])
    with (
        patch("movie_narrator.pipeline.export_clips.probe", return_value=(True, "")),
        patch("movie_narrator.pipeline.export_clips.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch(
            "movie_narrator.pipeline.export_clips.subprocess.run",
            side_effect=TimeoutError("timeout"),
        ),
        patch("movie_narrator.pipeline.export_clips.append_warning"),
    ):
        export_clips(ctx)
    assert ctx.status.export == "partial"
    assert ctx.scenes[0].clip_path is None


def test_uses_metadata_codecs_and_timeout(tmp_path):
    ctx = _ctx(tmp_path, scenes=[_scene(0, 0, 5)])
    ctx.metadata["render_video_codec"] = "libx265"
    ctx.metadata["render_audio_codec"] = "opus"
    ctx.metadata["render_ffmpeg_timeout"] = 123
    with (
        patch("movie_narrator.pipeline.export_clips.probe", return_value=(True, "")),
        patch("movie_narrator.pipeline.export_clips.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch("movie_narrator.pipeline.export_clips.subprocess.run", return_value=_fake_proc()) as run,
    ):
        export_clips(ctx)
    _, kwargs = run.call_args
    assert kwargs["timeout"] == 123
    cmd = run.call_args[0][0]
    assert "-c:v" in cmd and "libx265" in cmd
    assert "-c:a" in cmd and "opus" in cmd
    assert "+faststart" in cmd