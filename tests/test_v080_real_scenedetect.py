# SPDX-License-Identifier: AGPL-3.0-or-later
"""Real PySceneDetect end-to-end test (gap-A coverage).

``tests/test_scenes.py`` only injects a *fake* scenedetect module, so it can
run on the standard CI runner (which lacks the ``[media]`` extra). That leaves
a real-library gap: regressions in the actual detection path — threshold
wiring, scene-list parsing, ``scenes.json`` persistence, the MS-01 0-scene
fallback — are never exercised against the genuine PySceneDetect / OpenCV
backend.

This module fills that gap. It is skipped automatically when PySceneDetect or
OpenCV is unavailable (i.e. on CI); run it locally after
``pip install "movie-narrator[media]"``.
"""
from pathlib import Path

import pytest

from movie_narrator.models import Context
from movie_narrator.pipeline.scenes import detect_scenes
from movie_narrator.utils.optional_deps import probe

# ── Availability gate ─────────────────────────────────────────────
_SCENEDET_OK, _ = probe("scenedetect")
try:
    import cv2  # noqa: F401  (PySceneDetect video backend)

    _CV2_OK = True
except Exception:  # noqa: BLE001
    _CV2_OK = False

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not (_SCENEDET_OK and _CV2_OK),
        reason="requires [media] extra (PySceneDetect + OpenCV); skipped in CI",
    ),
]


def _write_synthetic_video(path: Path, seconds: float = 2.0, fps: int = 30) -> None:
    """Write a tiny mp4 with one clear mid-clip cut (black -> white)."""
    import numpy as np

    width, height = 320, 240
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (width, height))
    n = int(round(seconds * fps))
    for i in range(n):
        # First half black, second half white -> one obvious cut near halfway.
        color = 0 if i < n // 2 else 255
        frame = np.full((height, width, 3), color, dtype=np.uint8)
        writer.write(frame)
    writer.release()


def test_detect_scenes_real_end_to_end(tmp_path):
    """Real PySceneDetect finds the synthetic cut and returns >=1 scene."""
    video_path = tmp_path / "src.mp4"
    _write_synthetic_video(video_path)

    ctx = Context(
        movie_name="real-scene-test",
        output_dir=str(tmp_path),
        source_video_path=str(video_path),
    )
    detect_scenes(ctx)

    assert ctx.status.scene == "success"
    # A clear black->white cut should yield at least 2 scenes.
    assert len(ctx.scenes) >= 2
    # scenes.json persisted for debugging.
    assert (tmp_path / "scenes.json").exists()
    # Not degraded — a real cut was detected.
    assert ctx.metadata.get("scene_detection_degraded") is None


def test_detect_scenes_real_low_contrast_fallback(tmp_path):
    """Real detection on a flat (single-color) clip hits the MS-01 fallback."""
    video_path = tmp_path / "flat.mp4"
    import numpy as np

    width, height = 320, 240
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(video_path), fourcc, 30, (width, height))
    for _ in range(60):
        writer.write(np.full((height, width, 3), 128, dtype=np.uint8))
    writer.release()

    ctx = Context(
        movie_name="real-flat-test",
        output_dir=str(tmp_path),
        source_video_path=str(video_path),
    )
    detect_scenes(ctx)

    assert ctx.status.scene == "success"
    # MS-01: 0 cuts -> synthesized single full-length scene.
    assert len(ctx.scenes) == 1
    assert ctx.metadata.get("scene_detection_degraded") is True
