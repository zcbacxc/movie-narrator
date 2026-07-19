import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Context
from movie_narrator.pipeline.scenes import detect_scenes


def test_detect_scenes_disabled_without_dep(tmp_path):
    ctx = Context(movie_name="m", output_dir=str(tmp_path))
    with patch(
        "movie_narrator.pipeline.scenes.probe", return_value=(False, "scenedetect not installed")
    ):
        detect_scenes(ctx)
    assert ctx.status.scene == "disabled"


def test_detect_scenes_skipped_no_source(tmp_path):
    ctx = Context(movie_name="m", output_dir=str(tmp_path))
    with patch(
        "movie_narrator.pipeline.scenes.probe", return_value=(True, "")
    ):
        detect_scenes(ctx)
    assert ctx.status.scene == "skipped"


def _run_detect_with_fake_scenedetect(ctx, threshold_expected):
    """Inject fake scenedetect modules so tests run without [media] extras."""
    mock_video = MagicMock()
    mock_video.duration.frame_num = 1000
    mock_manager = MagicMock()
    mock_manager.get_scene_list.return_value = []
    mock_detector_cls = MagicMock()

    fake_scenedetect = MagicMock()
    fake_scenedetect.open_video = MagicMock(return_value=mock_video)
    fake_scenedetect.SceneManager = MagicMock(return_value=mock_manager)

    fake_detectors = MagicMock()
    fake_detectors.ContentDetector = mock_detector_cls

    with (
        patch("movie_narrator.pipeline.scenes.probe", return_value=(True, "")),
        patch.dict(
            sys.modules,
            {
                "scenedetect": fake_scenedetect,
                "scenedetect.detectors": fake_detectors,
            },
        ),
    ):
        detect_scenes(ctx)

    mock_detector_cls.assert_called_once_with(threshold=threshold_expected)
    assert ctx.status.scene == "success"


def test_detect_scenes_uses_default_threshold(tmp_path):
    """ContentDetector uses inline default (27.0) when no metadata override."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "src.mp4"),
    )
    (tmp_path / "src.mp4").write_bytes(b"00")
    _run_detect_with_fake_scenedetect(ctx, threshold_expected=27.0)


def test_detect_scenes_metadata_threshold_override(tmp_path):
    """mn scenes --threshold injects metadata.scene_threshold override."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "src.mp4"),
    )
    ctx.metadata["scene_threshold"] = 15.0
    (tmp_path / "src.mp4").write_bytes(b"00")
    _run_detect_with_fake_scenedetect(ctx, threshold_expected=15.0)


# ── MS-01: 0-scene fallback regression test ──


def test_detect_scenes_0_scenes_synthesizes_full_length_scene(tmp_path):
    """MS-01: ContentDetector returns 0 scenes → synthesize 1 full-length Scene.

    Without this fallback, scenes=[] + status=success silently turns
    downstream into a text-only video (pure 字卡, no footage).
    The fix synthesizes one Scene covering the full video duration and
    sets scene_detection_degraded=True in metadata.
    """
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "src.mp4"),
    )
    (tmp_path / "src.mp4").write_bytes(b"00")

    mock_video = MagicMock()
    mock_video.duration = 120.0  # 2-minute video
    mock_manager = MagicMock()
    mock_manager.get_scene_list.return_value = []  # 0 scenes detected
    mock_detector_cls = MagicMock()

    fake_scenedetect = MagicMock()
    fake_scenedetect.open_video = MagicMock(return_value=mock_video)
    fake_scenedetect.SceneManager = MagicMock(return_value=mock_manager)

    fake_detectors = MagicMock()
    fake_detectors.ContentDetector = mock_detector_cls

    with (
        patch("movie_narrator.pipeline.scenes.probe", return_value=(True, "")),
        patch.dict(
            sys.modules,
            {"scenedetect": fake_scenedetect, "scenedetect.detectors": fake_detectors},
        ),
    ):
        detect_scenes(ctx)

    # Should NOT be scenes=[] — should have 1 synthesized scene
    assert len(ctx.scenes) == 1
    assert ctx.scenes[0].start == 0.0
    assert ctx.scenes[0].end == 120.0  # full video duration
    assert ctx.status.scene == "success"
    # MS-01: degradation flag set so downstream knows
    assert ctx.metadata.get("scene_detection_degraded") is True


def test_detect_scenes_normal_scenes_no_degraded_flag(tmp_path):
    """MS-01: normal scene detection (≥1 scene) → no degraded flag."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "src.mp4"),
    )
    (tmp_path / "src.mp4").write_bytes(b"00")

    # Fake FrameTimecode-like objects with get_seconds()
    def fake_tc(seconds):
        tc = MagicMock()
        tc.get_seconds.return_value = seconds
        return tc

    mock_video = MagicMock()
    mock_video.duration = 60.0
    mock_manager = MagicMock()
    mock_manager.get_scene_list.return_value = [
        (fake_tc(0.0), fake_tc(30.0)),
        (fake_tc(30.0), fake_tc(60.0)),
    ]
    mock_detector_cls = MagicMock()

    fake_scenedetect = MagicMock()
    fake_scenedetect.open_video = MagicMock(return_value=mock_video)
    fake_scenedetect.SceneManager = MagicMock(return_value=mock_manager)

    fake_detectors = MagicMock()
    fake_detectors.ContentDetector = mock_detector_cls

    with (
        patch("movie_narrator.pipeline.scenes.probe", return_value=(True, "")),
        patch.dict(
            sys.modules,
            {"scenedetect": fake_scenedetect, "scenedetect.detectors": fake_detectors},
        ),
    ):
        detect_scenes(ctx)

    assert len(ctx.scenes) == 2
    assert ctx.status.scene == "success"
    # No degraded flag when scenes were detected normally
    assert ctx.metadata.get("scene_detection_degraded") is None
