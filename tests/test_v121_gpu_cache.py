# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""v1.2.1 — persistent GPU capability cache + fallback-reason reporting.

Covers the two lightweight improvements:

* **Persistent capability cache** — successful probe results are persisted to
  ``~/.movie-narrator/gpu_cache.json`` so fresh processes / worker restarts
  don't re-run ``ffmpeg -encoders``. Negative (no-GPU) results are cached
  too, but probe *failures* never are.
* **Encoder fallback reason** — ``fallback_reason`` explains why the active
  codec is ``libx264`` for observability / ``metadata.json``.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.utils import gpu_detect as gd
from movie_narrator.utils.gpu_detect import detect_gpu_encoder

_GPU_DETECT_MOD = "movie_narrator.utils.gpu_detect"


@pytest.fixture(autouse=True)
def _isolate_gpu_detect(monkeypatch, tmp_path):
    """Redirect the capability cache to a temp file and reset state per test."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(gd, "_cache_path", lambda: tmp_path / "gpu_cache.json")
    detect_gpu_encoder.cache_clear()
    yield
    detect_gpu_encoder.cache_clear()


def _fake_proc(stdout: str = "", returncode: int = 0, stderr: str = "") -> MagicMock:
    return MagicMock(stdout=stdout, stderr=stderr, returncode=returncode)


def _write_entry(cache_file, key, detected, probed_ok=True):
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": gd._CACHE_SCHEMA_VERSION,
        "entries": {key: {"detected": detected, "probed_ok": probed_ok}},
    }
    cache_file.write_text(json.dumps(payload), encoding="utf-8")


# ── A: persistent capability cache ───────────────────────────────────


def test_cache_hit_avoids_probe(tmp_path, monkeypatch):
    """A cached entry short-circuits the probe (subprocess.run not called)."""
    key = gd._cache_key("/usr/bin/ffmpeg")
    _write_entry(tmp_path / "gpu_cache.json", key, "h264_nvenc")
    monkeypatch.setattr(gd, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    with patch(f"{_GPU_DETECT_MOD}.subprocess.run") as run_mock:
        assert detect_gpu_encoder() == "h264_nvenc"
    run_mock.assert_not_called()


def test_cache_miss_probes_and_writes(tmp_path, monkeypatch):
    """A miss runs the probe and persists a positive result to disk."""
    monkeypatch.setattr(gd, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    with patch(
        f"{_GPU_DETECT_MOD}.subprocess.run",
        return_value=_fake_proc(
            stdout=" V..... h264_nvenc   NVIDIA NVENC H.264 encoder (codec h264)\n"
        ),
    ):
        assert detect_gpu_encoder() == "h264_nvenc"
    payload = json.loads((tmp_path / "gpu_cache.json").read_text(encoding="utf-8"))
    key = gd._cache_key("/usr/bin/ffmpeg")
    assert payload["entries"][key] == {"detected": "h264_nvenc", "probed_ok": True}


def test_negative_result_is_cached_and_reused(tmp_path, monkeypatch):
    """A clean 'no GPU encoder' result is cached, so a fresh call reuses it."""
    monkeypatch.setattr(gd, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    run_mock = MagicMock(return_value=_fake_proc(stdout=" V....D libx264 ...\n"))
    with patch(f"{_GPU_DETECT_MOD}.subprocess.run", run_mock):
        assert detect_gpu_encoder() is None
    detect_gpu_encoder.cache_clear()  # simulate a fresh process
    with patch(f"{_GPU_DETECT_MOD}.subprocess.run", run_mock):
        assert detect_gpu_encoder() is None
    assert run_mock.call_count == 1  # only the first call probed


def test_probe_failure_is_not_cached(tmp_path, monkeypatch):
    """A probe failure (nonzero exit) returns None but writes no cache entry."""
    monkeypatch.setattr(gd, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    with patch(
        f"{_GPU_DETECT_MOD}.subprocess.run",
        return_value=_fake_proc(returncode=1, stderr="boom"),
    ):
        assert detect_gpu_encoder() is None
    assert not (tmp_path / "gpu_cache.json").exists()


def test_corrupt_cache_falls_back_to_probe(tmp_path, monkeypatch):
    """Invalid JSON on disk is treated as a miss and recovered via probing."""
    cache_file = tmp_path / "gpu_cache.json"
    cache_file.write_text("{ not valid json !!!", encoding="utf-8")
    monkeypatch.setattr(gd, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    with patch(
        f"{_GPU_DETECT_MOD}.subprocess.run",
        return_value=_fake_proc(stdout=" V..... h264_vaapi   H.264/AVC (VAAPI) (codec h264)\n"),
    ) as run_mock:
        assert detect_gpu_encoder() == "h264_vaapi"
    assert run_mock.call_count == 1


def test_key_invalidates_when_ffmpeg_binary_changes(tmp_path, monkeypatch):
    """A different ffmpeg path yields a different key, forcing a re-probe."""
    _write_entry(tmp_path / "gpu_cache.json", gd._cache_key("/usr/bin/ffmpeg"), "h264_nvenc")
    monkeypatch.setattr(gd, "ffmpeg_bin", lambda: "/opt/ffmpeg/ffmpeg")
    with patch(
        f"{_GPU_DETECT_MOD}.subprocess.run",
        return_value=_fake_proc(
            stdout=" V..... h264_videotoolbox   VideoToolbox H.264 encoder (codec h264)\n"
        ),
    ) as run_mock:
        assert detect_gpu_encoder() == "h264_videotoolbox"
    assert run_mock.call_count == 1


def test_ci_never_reads_or_writes_cache(tmp_path, monkeypatch):
    """CI skips detection entirely and leaves the cache untouched."""
    monkeypatch.setenv("CI", "1")
    monkeypatch.setattr(gd, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    cache_file = tmp_path / "gpu_cache.json"
    cache_file.write_text("sentinel", encoding="utf-8")
    with patch(f"{_GPU_DETECT_MOD}.subprocess.run") as run_mock:
        assert detect_gpu_encoder() is None
    run_mock.assert_not_called()
    assert cache_file.read_text(encoding="utf-8") == "sentinel"


def test_clear_gpu_cache_removes_file(tmp_path, monkeypatch):
    """clear_gpu_cache() deletes the on-disk file and resets the in-process cache."""
    monkeypatch.setattr(gd, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    with patch(
        f"{_GPU_DETECT_MOD}.subprocess.run",
        return_value=_fake_proc(
            stdout=" V..... h264_nvenc   NVIDIA NVENC H.264 encoder (codec h264)\n"
        ),
    ):
        detect_gpu_encoder()
    cache_file = tmp_path / "gpu_cache.json"
    assert cache_file.exists()
    gd.clear_gpu_cache()
    assert not cache_file.exists()


# ── B: fallback-reason reporting ─────────────────────────────────────


def test_resolve_unknown_hint_reports_reason():
    """Unknown hint -> libx264 with reason 'unknown_hint'."""
    codec, params, reason = gd._resolve_encoder_with_reason("bogus")
    assert (codec, params) == ("libx264", [])
    assert reason == "unknown_hint"


def test_resolve_cpu_has_no_fallback_reason():
    """Explicit 'cpu' is intentional software encoding, not a fallback."""
    _, _, reason = gd._resolve_encoder_with_reason("cpu")
    assert reason is None


def test_resolve_explicit_gpu_has_no_fallback_reason():
    """Explicit GPU hint keeps its codec and reports no fallback."""
    codec, _, reason = gd._resolve_encoder_with_reason("nvenc")
    assert codec == "h264_nvenc"
    assert reason is None


def test_get_encoder_info_gpu_active_no_fallback():
    """GPU active -> fallback_reason is null."""
    with patch(f"{_GPU_DETECT_MOD}.detect_gpu_encoder", return_value="h264_nvenc"):
        info = gd.get_encoder_info()
    assert info["active"] == "h264_nvenc"
    assert info["fallback_reason"] is None


def test_get_encoder_info_auto_fallback_reason_propagates():
    """auto with no GPU -> libx264 with the inferred fallback reason."""
    with (
        patch(f"{_GPU_DETECT_MOD}.detect_gpu_encoder", return_value=None),
        patch(
            f"{_GPU_DETECT_MOD}._infer_auto_fallback_reason",
            return_value="not_detected",
        ),
    ):
        info = gd.get_encoder_info()
    assert info["active"] == "libx264"
    assert info["fallback_reason"] == "not_detected"


def test_infer_reason_no_ffmpeg(monkeypatch):
    """No resolvable binary -> 'no_ffmpeg'."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(gd, "_resolve_ffmpeg", lambda: None)
    assert gd._infer_auto_fallback_reason() == "no_ffmpeg"


def test_infer_reason_ci_skipped(monkeypatch):
    """CI env set -> 'ci_skipped'."""
    monkeypatch.setenv("CI", "1")
    assert gd._infer_auto_fallback_reason() == "ci_skipped"


def test_infer_reason_not_detected_when_clean_negative_cached(tmp_path, monkeypatch):
    """A cached clean no-GPU entry -> 'not_detected' (not a probe failure)."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(gd, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    _write_entry(
        tmp_path / "gpu_cache.json", gd._cache_key("/usr/bin/ffmpeg"), None, probed_ok=True
    )
    assert gd._infer_auto_fallback_reason() == "not_detected"


def test_infer_reason_probe_failed_when_no_cache(tmp_path, monkeypatch):
    """No cached negative entry -> 'probe_failed'."""
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(gd, "ffmpeg_bin", lambda: "/usr/bin/ffmpeg")
    assert gd._infer_auto_fallback_reason() == "probe_failed"
