# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""v1.2 Wave 1B — GPU detection ffmpeg-resolution unification tests.

``detect_gpu_encoder`` now probes the binary returned by the shared
``ffmpeg_bin`` policy instead of ``shutil.which("ffmpeg")``, so detection
matches the binary the render pipeline actually invokes.
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
def _isolate_gpu_detect(monkeypatch, tmp_path):
    """Reset the probe cache, clear CI env, and redirect the on-disk
    capability cache to a temp file so tests never touch the real user dir."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(f"{_GPU_DETECT_MOD}._cache_path", lambda: tmp_path / "gpu_cache.json")
    detect_gpu_encoder.cache_clear()
    yield
    detect_gpu_encoder.cache_clear()


def _fake_proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)


def test_bare_ffmpeg_fallback_maps_to_none():
    """ffmpeg_bin returning the bare 'ffmpeg' string means no concrete binary -> None."""
    with patch(f"{_GPU_DETECT_MOD}.ffmpeg_bin", return_value="ffmpeg"):
        assert detect_gpu_encoder() is None


def test_subprocess_invoked_with_resolved_ffmpeg_path():
    """The resolved path (not a bare name) is passed to subprocess.run."""
    with (
        patch(f"{_GPU_DETECT_MOD}.ffmpeg_bin", return_value="/opt/ffmpeg/ffmpeg"),
        patch(
            f"{_GPU_DETECT_MOD}.subprocess.run",
            return_value=_fake_proc(stdout=""),
        ) as run_mock,
    ):
        assert detect_gpu_encoder() is None
    assert run_mock.call_args[0][0][0] == "/opt/ffmpeg/ffmpeg"


def test_detect_returns_vaapi():
    fake_stdout = " V..... h264_vaapi             H.264/AVC (VAAPI) (codec h264)\n"
    with (
        patch(f"{_GPU_DETECT_MOD}.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch(f"{_GPU_DETECT_MOD}.platform.system", return_value="Linux"),
        patch(f"{_GPU_DETECT_MOD}.subprocess.run", return_value=_fake_proc(stdout=fake_stdout)),
    ):
        assert detect_gpu_encoder() == "h264_vaapi"


def test_detect_returns_videotoolbox():
    fake_stdout = (
        " V..... h264_videotoolbox      VideoToolbox H.264 encoder (codec h264)\n"
        " V..... h264_nvenc            NVIDIA NVENC H.264 encoder (codec h264)\n"
    )
    with (
        patch(f"{_GPU_DETECT_MOD}.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch(f"{_GPU_DETECT_MOD}.platform.system", return_value="Darwin"),
        patch(f"{_GPU_DETECT_MOD}.subprocess.run", return_value=_fake_proc(stdout=fake_stdout)),
    ):
        assert detect_gpu_encoder() == "h264_videotoolbox"


def test_detect_returns_none_when_no_gpu_encoder():
    """ffmpeg present but no GPU encoder listed -> None (fall back to CPU)."""
    fake_stdout = " V....D libx264               libx264 H.264 / AVC (codec h264)\n"
    with (
        patch(f"{_GPU_DETECT_MOD}.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch(f"{_GPU_DETECT_MOD}.subprocess.run", return_value=_fake_proc(stdout=fake_stdout)),
    ):
        assert detect_gpu_encoder() is None


def test_detect_returns_none_on_nonzero_exit():
    with (
        patch(f"{_GPU_DETECT_MOD}.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch(
            f"{_GPU_DETECT_MOD}.subprocess.run",
            return_value=_fake_proc(returncode=1, stderr="error"),
        ),
    ):
        assert detect_gpu_encoder() is None


def test_resolve_encoder_hints_unchanged():
    """Explicit hints and cpu/auto fallbacks keep their pre-existing behavior."""
    assert resolve_encoder("cpu") == ("libx264", [])
    assert resolve_encoder("nvenc") == (
        "h264_nvenc",
        ["-preset", "p4", "-rc", "vbr", "-cq", "20"],
    )
    assert resolve_encoder("vaapi") == ("h264_vaapi", ["-preset", "fast"])
    assert resolve_encoder("videotoolbox") == ("h264_videotoolbox", ["-q:v", "65"])
    assert resolve_encoder("unknown") == ("libx264", [])
    with patch(f"{_GPU_DETECT_MOD}.detect_gpu_encoder", return_value=None):
        assert resolve_encoder("auto") == ("libx264", [])
        assert resolve_encoder(None) == ("libx264", [])


def test_get_encoder_info_reports_no_gpu():
    with (
        patch(f"{_GPU_DETECT_MOD}.detect_gpu_encoder", return_value=None),
        patch(
            f"{_GPU_DETECT_MOD}._infer_auto_fallback_reason",
            return_value="not_detected",
        ),
    ):
        info = get_encoder_info()
    assert info == {
        "requested": "auto",
        "detected": None,
        "active": "libx264",
        "gpu_available": False,
        "fallback_reason": "not_detected",
    }
