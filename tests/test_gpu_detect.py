# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the v0.7.0 GPU encoder auto-detection utility.

All external calls (``shutil.which``, ``subprocess.run``) are mocked so
the suite passes in a ffmpeg-less CI environment.
"""

from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.utils.gpu_detect import (
    detect_gpu_encoder,
    get_encoder_info,
    resolve_encoder,
)

_GPU_DETECT_MOD = "movie_narrator.utils.gpu_detect"


@pytest.fixture(autouse=True)
def _clear_lru_cache(monkeypatch):
    """Reset the ``detect_gpu_encoder`` cache and clear CI env between tests."""
    monkeypatch.delenv("CI", raising=False)
    detect_gpu_encoder.cache_clear()
    yield
    detect_gpu_encoder.cache_clear()


def _fake_proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)


def test_detect_returns_none_when_ffmpeg_missing():
    """No ffmpeg on PATH -> detection short-circuits to None."""
    with patch(f"{_GPU_DETECT_MOD}.shutil.which", return_value=None):
        assert detect_gpu_encoder() is None


def test_detect_returns_none_in_ci_environment(monkeypatch):
    """CI env var set -> detection skips even when ffmpeg has GPU encoders."""
    monkeypatch.setenv("CI", "1")
    fake_stdout = (
        " V..... h264_nvenc            NVIDIA NVENC H.264 encoder (codec h264)\n"
    )
    with patch(f"{_GPU_DETECT_MOD}.shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch(
             f"{_GPU_DETECT_MOD}.subprocess.run",
             return_value=_fake_proc(stdout=fake_stdout),
         ):
        assert detect_gpu_encoder() is None


def test_detect_returns_nvenc_when_available():
    """ffmpeg -encoders listing h264_nvenc -> returns h264_nvenc."""
    fake_stdout = (
        " Encoders:\n"
        " ------\n"
        " V..... h264_nvenc            NVIDIA NVENC H.264 encoder (codec h264)\n"
        " V....D libx264               libx264 H.264 / AVC (codec h264)\n"
    )
    with patch(f"{_GPU_DETECT_MOD}.shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch(
             f"{_GPU_DETECT_MOD}.subprocess.run",
             return_value=_fake_proc(stdout=fake_stdout),
         ):
        assert detect_gpu_encoder() == "h264_nvenc"


def test_resolve_cpu_returns_libx264():
    """Explicit 'cpu' hint forces software libx264 with no extra params."""
    assert resolve_encoder("cpu") == ("libx264", [])


def test_resolve_nvenc_returns_recommended_params():
    """'nvenc' hint maps to h264_nvenc with the NVENC quality preset."""
    codec, params = resolve_encoder("nvenc")
    assert codec == "h264_nvenc"
    assert params == ["-preset", "p4", "-rc", "vbr", "-cq", "20"]


def test_resolve_auto_falls_back_to_libx264_when_no_gpu():
    """'auto' with no detectable GPU falls back to libx264."""
    with patch(f"{_GPU_DETECT_MOD}.detect_gpu_encoder", return_value=None):
        assert resolve_encoder("auto") == ("libx264", [])


def test_resolve_unknown_falls_back_to_libx264():
    """Unknown hint values degrade gracefully to libx264."""
    assert resolve_encoder("unknown") == ("libx264", [])


def test_get_encoder_info_structure():
    """get_encoder_info() returns the documented metadata dict shape."""
    with patch(f"{_GPU_DETECT_MOD}.detect_gpu_encoder", return_value="h264_nvenc"):
        info = get_encoder_info()
    assert info == {
        "requested": "auto",
        "detected": "h264_nvenc",
        "active": "h264_nvenc",
        "gpu_available": True,
    }
