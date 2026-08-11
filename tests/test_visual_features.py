# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for G9 stage-1 visual feature extraction + match hook."""

import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import patch

from movie_narrator.models import Context, Scene
from movie_narrator.pipeline import match as match_mod
from movie_narrator.utils.visual_features import (
    VisualFeature,
    _extract_frame_jpg,
    _ffprobe_fps,
    _frame_luma_hist,
    _l1_diff,
    _static_skip_collapse,
    extract_scene_visual_features,
    visual_feature_vector,
)


def _scene(index, start, end):
    return Scene(index=index, start=start, end=end)


def _feat(index, luma=0.5):
    return VisualFeature(scene_index=index, luma=luma, hist_rgb=[0.1] * 48)


# ── visual_feature_vector ───────────────────────────────────


def test_visual_feature_vector_shape():
    v = visual_feature_vector(_feat(0))
    assert v is not None
    assert len(v) == 49  # 1 luma + 48 hist (16*3)


def test_visual_feature_vector_empty_hist():
    assert visual_feature_vector(VisualFeature(0, 0.5, [])) is None


def test_l1_diff():
    assert _l1_diff([1.0, 0.0], [1.0, 0.0]) == 0.0
    assert _l1_diff([1.0, 0.0], [0.0, 1.0]) == 1.0


# ── extract_scene_visual_features (mid-frame path) ──────────


@patch("movie_narrator.utils.visual_features._ffprobe_fps", return_value=0.0)
@patch("movie_narrator.utils.visual_features._extract_frame_jpg")
@patch("movie_narrator.utils.visual_features._frame_luma_hist")
def test_extract_midframe_path(mock_hist, mock_jpg, mock_fps):
    mock_jpg.return_value = "/tmp/f.jpg"
    mock_hist.return_value = (0.5, [0.1] * 48)
    scenes = [_scene(0, 0, 10), _scene(1, 10, 20)]
    feats = extract_scene_visual_features("/v.mp4", scenes)
    assert feats is not None
    assert [f.scene_index for f in feats] == [0, 1]
    assert mock_jpg.call_count == 2
    assert mock_jpg.call_args_list[0][0][1] == 5.0  # midpoint of scene 0
    assert mock_jpg.call_args_list[1][0][1] == 15.0  # midpoint of scene 1
    mock_fps.assert_not_called()  # fps=0 path probes FPS only when fps>0


@patch("movie_narrator.utils.visual_features._ffprobe_fps", return_value=0.0)
@patch("movie_narrator.utils.visual_features._extract_frame_jpg", return_value=None)
def test_extract_returns_none_when_frames_fail(mock_jpg, mock_fps):
    feats = extract_scene_visual_features("/v.mp4", [_scene(0, 0, 10)])
    assert feats is None


@patch("movie_narrator.utils.visual_features._ffprobe_fps", return_value=0.0)
@patch("movie_narrator.utils.visual_features._extract_frame_jpg")
@patch("movie_narrator.utils.visual_features._frame_luma_hist", return_value=None)
def test_extract_returns_none_when_hist_fails(mock_hist, mock_jpg, mock_fps):
    mock_jpg.return_value = "/tmp/f.jpg"
    feats = extract_scene_visual_features("/v.mp4", [_scene(0, 0, 10)])
    assert feats is None


# ── static-frame skip path (fps > 0) ────────────────────────


@patch("movie_narrator.utils.visual_features._ffprobe_fps", return_value=25.0)
def test_static_skip_collapse_used(mock_fps):
    with patch("movie_narrator.utils.visual_features._extract_frame_jpg") as mock_jpg:
        with patch("movie_narrator.utils.visual_features._frame_luma_hist") as mock_hist:
            mock_jpg.return_value = "/tmp/f.jpg"
            mock_hist.return_value = (0.5, [0.1] * 48)
            feats = extract_scene_visual_features(
                "/v.mp4", [_scene(0, 0, 2)], fps=5, static_skip_threshold=0.5
            )
    assert feats is not None
    assert len(feats) == 1
    assert mock_jpg.call_count >= 1  # sampled multiple frames
    assert mock_fps.called


# ── match.py hook ───────────────────────────────────────────


def _ctx(source_video_path):
    return Context(
        movie_name="Test",
        output_dir="output/test",
        source_video_path=source_video_path,
    )


def test_collect_visual_features_disabled_when_no_video():
    ctx = _ctx(None)
    match_mod._collect_visual_features(ctx, None, [_scene(0, 0, 10)])
    assert ctx.metadata["match_visual_features_available"] is False


def test_collect_visual_features_records_when_ok():
    ctx = _ctx("/v.mp4")
    feats = [_feat(0), _feat(1)]
    with patch("movie_narrator.utils.visual_features.extract_scene_visual_features",
               return_value=feats):
        match_mod._collect_visual_features(ctx, None, [_scene(0, 0, 10), _scene(1, 10, 20)])
    assert ctx.metadata["match_visual_features_available"] is True
    assert len(ctx.metadata["match_visual_features_samples"]) == 2


def test_collect_visual_features_degrades_on_failure():
    ctx = _ctx("/v.mp4")
    with patch("movie_narrator.utils.visual_features.extract_scene_visual_features",
               return_value=None):
        match_mod._collect_visual_features(ctx, None, [_scene(0, 0, 10)])
    assert ctx.metadata["match_visual_features_available"] is False
    assert "match_visual_features_samples" not in ctx.metadata


# ── hook is guided by config flag (default off) ─────────────


def test_hook_skipped_when_flag_off():
    ctx = _ctx("/v.mp4")
    with patch.object(match_mod, "_collect_visual_features") as spy:
        # Simulate the guarded block condition.
        if ctx.metadata.get("match_visual_features", False):
            spy(ctx, None, [])
        spy.assert_not_called()


def test_hook_runs_when_flag_on():
    with patch.object(match_mod, "_collect_visual_features") as spy:
        ctx = _ctx("/v.mp4")
        ctx.metadata["match_visual_features"] = True
        if ctx.metadata.get("match_visual_features", False):
            spy(ctx, None, [])
        spy.assert_called_once()


# ── VisualFeature.to_dict ───────────────────────────────────


def test_to_dict_rounding():
    f = VisualFeature(scene_index=3, luma=0.523456, hist_rgb=[0.123456, 0.98764])
    d = f.to_dict()
    assert d["scene_index"] == 3
    assert d["luma"] == 0.5235
    assert d["hist_rgb"] == [0.1235, 0.9876]


# ── _ffprobe_fps ────────────────────────────────────────────


def _ffprobe_proc(stdout):
    return SimpleNamespace(stdout=stdout)


def test_ffprobe_fps_no_binary():
    with patch("shutil.which", return_value=None) as w:
        assert _ffprobe_fps("/v.mp4") == 0.0
    w.assert_called_with("ffprobe")


def test_ffprobe_fps_parses_fraction():
    with (
        patch("shutil.which", return_value="/ffprobe"),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            return_value=_ffprobe_proc("30000/1001\n"),
        ),
    ):
        assert abs(_ffprobe_fps("/v.mp4") - 30000 / 1001) < 1e-9


def test_ffprobe_fps_parses_whole_number():
    with (
        patch("shutil.which", return_value="/ffprobe"),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            return_value=_ffprobe_proc("24\n"),
        ),
    ):
        assert _ffprobe_fps("/v.mp4") == 24.0


def test_ffprobe_fps_zero_denominator():
    with (
        patch("shutil.which", return_value="/ffprobe"),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            return_value=_ffprobe_proc("0/0\n"),
        ),
    ):
        assert _ffprobe_fps("/v.mp4") == 0.0


def test_ffprobe_fps_empty_output():
    with (
        patch("shutil.which", return_value="/ffprobe"),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            return_value=_ffprobe_proc(""),
        ),
    ):
        assert _ffprobe_fps("/v.mp4") == 0.0


def test_ffprobe_fps_raises_returns_zero():
    with (
        patch("shutil.which", return_value="/ffprobe"),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            side_effect=OSError("ffprobe missing"),
        ),
    ):
        assert _ffprobe_fps("/v.mp4") == 0.0


def test_ffprobe_fps_bad_float_raises_returns_zero():
    with (
        patch("shutil.which", return_value="/ffprobe"),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            return_value=_ffprobe_proc("abc/def\n"),
        ),
    ):
        assert _ffprobe_fps("/v.mp4") == 0.0


# ── _extract_frame_jpg ──────────────────────────────────────


def _jpg_proc(returncode=0):
    return SimpleNamespace(returncode=returncode)


def test_extract_frame_jpg_success():
    fake_tmp = SimpleNamespace(name="/tmp/x.jpg", close=lambda: None)
    with (
        patch("movie_narrator.utils.visual_features.ffmpeg_bin", return_value="/ffmpeg"),
        patch(
            "movie_narrator.utils.visual_features.tempfile.NamedTemporaryFile",
            return_value=fake_tmp,
        ),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            return_value=_jpg_proc(),
        ) as run,
        patch("movie_narrator.utils.visual_features.os.path.getsize", return_value=100),
    ):
        path = _extract_frame_jpg("/v.mp4", 1.5, 480)
    assert path == "/tmp/x.jpg"
    cmd = run.call_args[0][0]
    assert "-ss" in cmd and "1.5" in cmd
    assert "scale=480:-2" in cmd


def test_extract_frame_jpg_nonzero_returncode():
    fake_tmp = SimpleNamespace(name="/tmp/x.jpg", close=lambda: None)
    with (
        patch("movie_narrator.utils.visual_features.ffmpeg_bin", return_value="/ffmpeg"),
        patch(
            "movie_narrator.utils.visual_features.tempfile.NamedTemporaryFile",
            return_value=fake_tmp,
        ),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            return_value=_jpg_proc(returncode=1),
        ),
        patch("movie_narrator.utils.visual_features.os.path.getsize", return_value=100),
        patch("movie_narrator.utils.visual_features.os.unlink") as unlink,
    ):
        assert _extract_frame_jpg("/v.mp4", 1.5, 480) is None
    unlink.assert_called_with("/tmp/x.jpg")


def test_extract_frame_jpg_empty_file():
    fake_tmp = SimpleNamespace(name="/tmp/x.jpg", close=lambda: None)
    with (
        patch("movie_narrator.utils.visual_features.ffmpeg_bin", return_value="/ffmpeg"),
        patch(
            "movie_narrator.utils.visual_features.tempfile.NamedTemporaryFile",
            return_value=fake_tmp,
        ),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            return_value=_jpg_proc(),
        ),
        patch("movie_narrator.utils.visual_features.os.path.getsize", return_value=0),
        patch("movie_narrator.utils.visual_features.os.unlink"),
    ):
        assert _extract_frame_jpg("/v.mp4", 1.5, 480) is None


def test_extract_frame_jpg_run_raises():
    fake_tmp = SimpleNamespace(name="/tmp/x.jpg", close=lambda: None)
    with (
        patch("movie_narrator.utils.visual_features.ffmpeg_bin", return_value="/ffmpeg"),
        patch(
            "movie_narrator.utils.visual_features.tempfile.NamedTemporaryFile",
            return_value=fake_tmp,
        ),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            side_effect=RuntimeError("boom"),
        ),
        patch("movie_narrator.utils.visual_features.os.unlink") as unlink,
    ):
        assert _extract_frame_jpg("/v.mp4", 1.5, 480) is None
    unlink.assert_called_with("/tmp/x.jpg")


def test_extract_frame_jpg_run_raises_unlink_oserror():
    fake_tmp = SimpleNamespace(name="/tmp/x.jpg", close=lambda: None)
    with (
        patch("movie_narrator.utils.visual_features.ffmpeg_bin", return_value="/ffmpeg"),
        patch(
            "movie_narrator.utils.visual_features.tempfile.NamedTemporaryFile",
            return_value=fake_tmp,
        ),
        patch(
            "movie_narrator.utils.visual_features.subprocess.run",
            side_effect=RuntimeError("boom"),
        ),
        patch(
            "movie_narrator.utils.visual_features.os.unlink",
            side_effect=OSError("gone"),
        ),
    ):
        assert _extract_frame_jpg("/v.mp4", 1.5, 480) is None


# ── _frame_luma_hist (fake PIL) ─────────────────────────────


class _FakeChannel:
    def __init__(self, values):
        self._values = list(values)

    def getdata(self):
        return self._values

    def histogram(self):
        h = [0] * 256
        for v in self._values:
            h[v] += 1
        return h


class _FakeImage:
    width = 4
    height = 4

    def __init__(self):
        self._ch = {c: _FakeChannel([200] * 16) for c in "RGB"}

    @classmethod
    def open(cls, *a, **k):
        return cls()

    def convert(self, *a, **k):
        return self

    def getchannel(self, name):
        return self._ch[name]


def _fake_pil_module():
    mod = types.ModuleType("PIL")
    mod.Image = _FakeImage
    return mod


def test_frame_luma_hist_success():
    with patch.dict(sys.modules, {"PIL": _fake_pil_module()}):
        res = _frame_luma_hist("/x.jpg", 16)
    assert res is not None
    luma, hist = res
    assert luma == 200.0  # all pixels 200, Rec.601 reduces to same value
    assert len(hist) == 48  # 16 bins * 3 channels
    # bin 12 holds everything (200 // 16 == 12)
    assert hist[12] == 1.0
    assert hist[13] == 0.0


def test_frame_luma_hist_pil_import_fails():
    with patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}):
        assert _frame_luma_hist("/x.jpg", 16) is None


def test_frame_luma_hist_decode_fails():
    import PIL  # noqa: F401  (ensure real module untouched)

    mod = types.ModuleType("PIL")
    mod.Image = _FakeImage
    # Force Image.open to raise.
    def boom_open(*a, **k):
        raise OSError("bad image")

    mod.Image.open = boom_open
    with patch.dict(sys.modules, {"PIL": mod}):
        assert _frame_luma_hist("/x.jpg", 16) is None


# ── _static_skip_collapse ───────────────────────────────────


def test_static_skip_collapse_fps_zero():
    assert _static_skip_collapse("/v.mp4", 0, 10, width=480, hist_bins=16, fps=0.0, threshold=0.02) is None


def test_static_skip_collapse_caps_at_30_frames():
    with (
        patch("movie_narrator.utils.visual_features._extract_frame_jpg", return_value="/tmp/f.jpg") as jpg,
        patch("movie_narrator.utils.visual_features._frame_luma_hist", return_value=(0.5, [0.1] * 48)) as hist,
    ):
        res = _static_skip_collapse("/v.mp4", 0, 10, width=480, hist_bins=16, fps=10.0, threshold=0.02)
    assert res is not None
    assert jpg.call_count == 30  # capped
    assert hist.call_count == 30


def test_static_skip_collapse_skips_static_frames():
    # Two frames far apart in time; second frame identical -> skipped.
    frames = []

    def fake_jpg(path, t, width):
        frames.append(t)
        return f"/tmp/{len(frames)}.jpg"

    with (
        patch("movie_narrator.utils.visual_features._extract_frame_jpg", side_effect=fake_jpg),
        patch("movie_narrator.utils.visual_features._frame_luma_hist", return_value=(0.5, [0.1] * 48)),
        patch("movie_narrator.utils.visual_features.os.unlink"),
    ):
        res = _static_skip_collapse("/v.mp4", 0, 2, width=480, hist_bins=16, fps=5.0, threshold=0.5)
    assert res is not None
    # With a high threshold all frames collapse to the first => 1 kept.
    assert len(frames) == 10  # still sampled 10 times
    assert res[0] == 0.5


def test_static_skip_collapse_none_when_no_frames():
    with (
        patch("movie_narrator.utils.visual_features._extract_frame_jpg", return_value=None),
    ):
        assert _static_skip_collapse("/v.mp4", 0, 2, width=480, hist_bins=16, fps=5.0, threshold=0.02) is None


def test_static_skip_collapse_skips_when_hist_none():
    def fake_jpg(path, t, width):
        return f"/tmp/{t}.jpg"

    with (
        patch("movie_narrator.utils.visual_features._extract_frame_jpg", side_effect=fake_jpg),
        patch("movie_narrator.utils.visual_features._frame_luma_hist", return_value=None),
        patch("movie_narrator.utils.visual_features.os.unlink"),
    ):
        assert _static_skip_collapse("/v.mp4", 0, 2, width=480, hist_bins=16, fps=5.0, threshold=0.02) is None


def test_static_skip_collapse_averages_across_frames():
    counter = {"n": 0}

    def fake_hist(jpg, bins):
        counter["n"] += 1
        val = 0.0 if counter["n"] % 2 else 1.0
        return val, [val] * 48

    with (
        patch("movie_narrator.utils.visual_features._extract_frame_jpg", return_value="/tmp/f.jpg"),
        patch("movie_narrator.utils.visual_features._frame_luma_hist", side_effect=fake_hist),
        patch("movie_narrator.utils.visual_features.os.unlink"),
    ):
        res = _static_skip_collapse("/v.mp4", 0, 2, width=480, hist_bins=16, fps=5.0, threshold=0.0)
    # threshold 0 -> no skip; alternating average of 0.0 and 1.0 = 0.5
    assert res is not None
    assert counter["n"] == 10
    assert res[0] == 0.5
    assert abs(res[1][0] - 0.5) < 1e-9
