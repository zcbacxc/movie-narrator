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
    mock_settings = MagicMock()
    mock_settings.scene_threshold = 42.5

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
        patch("movie_narrator.pipeline.scenes.get_settings", return_value=mock_settings),
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


def test_detect_scenes_uses_settings_threshold(tmp_path):
    """Spec §3/§4: ContentDetector must honor Settings.scene_threshold."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "src.mp4"),
    )
    (tmp_path / "src.mp4").write_bytes(b"00")
    _run_detect_with_fake_scenedetect(ctx, threshold_expected=42.5)


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
