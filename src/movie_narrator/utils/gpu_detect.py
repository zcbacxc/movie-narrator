# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""GPU encoder auto-detection for v0.7.0 render acceleration.

Probes the local ffmpeg build for hardware-accelerated H.264 encoders
(NVENC / VAAPI / VideoToolbox) and resolves a ``(codec, ffmpeg_params)``
tuple the render pipeline can pass straight to MoviePy / ffmpeg.

All probes are cached via :func:`functools.lru_cache` so repeated calls
during a single render are cheap and deterministic.
"""

from __future__ import annotations

import json
import os
import platform
import re
import subprocess
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Optional

from .ffmpeg_bin import ffmpeg_bin

# Canonical ffmpeg encoder names for each GPU backend.
_NVENC = "h264_nvenc"
_VAAPI = "h264_vaapi"
_VIDEOTOOLBOX = "h264_videotoolbox"

# Base detection order (applies when the platform has no preference).
_BASE_ORDER: list[str] = [_NVENC, _VAAPI, _VIDEOTOOLBOX]

# Recommended ffmpeg params per backend.  These are conservative quality
# presets — NVENC ``p4`` + VBR/CQ 20 is the standard quality/speed
# sweet spot, VAAPI uses a fast preset, and VideoToolbox uses a single
# quality factor.
#
# NOTE: ``-vaapi_device`` is a *global* ffmpeg option that must appear
# before ``-i`` — MoviePy's ``ffmpeg_params`` are output-side, so we
# cannot pass it.  The VAAPI encoder will use the default device
# (``/dev/dri/renderD128`` on most Linux systems) or fail gracefully
# with a fallback to libx264 in the render pipeline.
_GPU_PARAMS: dict[str, list[str]] = {
    _NVENC: ["-preset", "p4", "-rc", "vbr", "-cq", "20"],
    _VAAPI: ["-preset", "fast"],
    _VIDEOTOOLBOX: ["-q:v", "65"],
}

# User-facing hint -> canonical codec name.
_HINT_TO_CODEC: dict[str, str] = {
    "nvenc": _NVENC,
    "vaapi": _VAAPI,
    "videotoolbox": _VIDEOTOOLBOX,
}

# Fallback reason enum (v1.2.1). A non-None value explains why the *active*
# codec ended up as libx264, for reporting in metadata.json / logs.
REASON_NO_FFMPEG = "no_ffmpeg"
REASON_CI_SKIPPED = "ci_skipped"
REASON_PROBE_FAILED = "probe_failed"
REASON_NOT_DETECTED = "not_detected"
REASON_UNKNOWN_HINT = "unknown_hint"
REASON_GPU_RUNTIME_FALLBACK = "gpu_runtime_fallback"


def _candidate_order() -> list[str]:
    """
    Returns:
        The platform-aware candidate detection order.

        Windows prefers NVENC, macOS prefers VideoToolbox, Linux prefers
        VAAPI.  The remaining encoders keep their base relative order so a
        secondary GPU is still picked up when the preferred one is absent.
    """
    system = platform.system()
    preferred = {
        "Windows": _NVENC,
        "Darwin": _VIDEOTOOLBOX,
        "Linux": _VAAPI,
    }.get(system)
    order = list(_BASE_ORDER)
    if preferred and order and order[0] != preferred:
        order.remove(preferred)
        order.insert(0, preferred)
    return order


# Matches an ffmpeg ``-encoders`` line, e.g.:
#   " V..... h264_nvenc            NVIDIA NVENC H.264 encoder (codec h264)"
# Group 1 captures the encoder name token (alphanumeric + underscore).
_ENCODER_LINE_RE = re.compile(r"^\s*[VAS]\S*\s+(\w+)")


def _parse_encoder_names(stdout: str) -> set[str]:
    """Extract the set of encoder names from ``ffmpeg -encoders`` stdout."""
    names: set[str] = set()
    for line in stdout.splitlines():
        match = _ENCODER_LINE_RE.match(line)
        if match:
            names.add(match.group(1))
    return names


def _resolve_ffmpeg() -> Optional[str]:
    """Return a concrete ffmpeg binary path, or ``None`` when unavailable.

    Delegates to the shared :func:`ffmpeg_bin` resolution so GPU detection
    probes the *same* binary the render pipeline actually invokes
    (``MN_FFMPEG_BIN`` override → imageio-ffmpeg bundled build → system
    ``PATH``).  The bare ``"ffmpeg"`` last-resort string is mapped to
    ``None`` — the same convention as ``audio_mix._ffmpeg_bin`` — because
    it means no concrete binary could be resolved.
    """
    resolved = ffmpeg_bin()
    if resolved and resolved != "ffmpeg":
        return resolved
    return None


# ── Persistent capability cache (v1.2.1) ────────────────────────────
# Detect results are persisted so repeated renders / worker restarts skip
# re-running ``ffmpeg -encoders``. Detection is keyed to a concrete ffmpeg
# binary identity so an upgraded/replaced ffmpeg invalidates the entry.
# The directory matches the ``config._USER_DIR`` convention
# (``~/.movie-narrator``) without importing config, keeping this low-level
# util free of the pydantic-settings dependency.
_CACHE_SCHEMA_VERSION = 1
_DEFAULT_CACHE_DIR = Path.home() / ".movie-narrator"
_DEFAULT_CACHE_FILE = _DEFAULT_CACHE_DIR / "gpu_cache.json"


def _cache_path() -> Path:
    """Return the on-disk capability-cache path (overridable by tests)."""
    return _DEFAULT_CACHE_FILE


def _cache_key(ffmpeg: str) -> str:
    """Build a stable cache key for a concrete ffmpeg binary + environment."""
    try:
        stat = Path(ffmpeg).stat()
        mtime = int(stat.st_mtime)
        size = stat.st_size
    except OSError:
        mtime = -1
        size = -1
    identity = {
        "ffmpeg": ffmpeg,
        "system": platform.system(),
        "machine": platform.machine(),
        "ci": bool(os.getenv("CI")),
        "mtime": mtime,
        "size": size,
        "schema": _CACHE_SCHEMA_VERSION,
    }
    return json.dumps(identity, sort_keys=True, separators=(",", ":"))


def _load_capability_cache() -> dict:
    """Load cached probe entries. Returns ``{}`` on any failure (never raises)."""
    path = _cache_path()
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):  # ValueError covers JSONDecodeError
        return {}
    if not isinstance(data, dict) or data.get("schema_version") != _CACHE_SCHEMA_VERSION:
        return {}
    entries = data.get("entries")
    return entries if isinstance(entries, dict) else {}


def _store_capability_cache(entries: dict) -> None:
    """Persist probe entries atomically. Best-effort — never raises."""
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"schema_version": _CACHE_SCHEMA_VERSION, "entries": entries}
        fd, tmp_path = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, sort_keys=True)
            os.replace(tmp_path, path)
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    except Exception:  # noqa: BLE001  # a cache-write failure must never abort a render
        pass


def clear_gpu_cache() -> None:
    """Reset the in-process probe cache and the on-disk capability cache."""
    detect_gpu_encoder.cache_clear()
    try:
        _cache_path().unlink(missing_ok=True)
    except OSError:
        pass


def _probe(ffmpeg: str) -> tuple[Optional[str], bool]:
    """Run ``ffmpeg -encoders`` and return ``(detected, probed_ok)``.

    ``probed_ok`` is ``True`` only when the probe ran to completion — if no
    GPU encoder is registered it is still ``True`` so the negative result can
    be cached. It is ``False`` when ffmpeg is missing or the probe errored.
    """
    try:
        proc = subprocess.run(  # nosec B607  # ffmpeg is a system binary we rely on PATH resolving
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None, False

    if proc.returncode != 0:
        return None, False

    available = _parse_encoder_names(proc.stdout)
    for candidate in _candidate_order():
        if candidate in available:
            return candidate, True
    return None, True


@lru_cache(maxsize=1)
def detect_gpu_encoder() -> Optional[str]:
    """Detect the best available GPU H.264 encoder.

    Runs ``<ffmpeg> -hide_banner -encoders`` once (resolving ffmpeg via
    the shared :func:`ffmpeg_bin` policy so detection matches the binary
    the render pipeline really uses) and returns the first available
    encoder in platform-aware priority order.  Returns ``None`` when
    ffmpeg is missing, the probe fails, or no GPU encoder is registered.

    Successful probe results (positive or negative) are persisted to the
    on-disk capability cache (see :func:`_store_capability_cache`) so a
    fresh process / worker restart doesn't re-probe the same ffmpeg.  The
    in-process :func:`lru_cache` remains the first layer.
    """
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        return None

    # CI environments (GitHub Actions, etc.) list GPU encoders in
    # ``ffmpeg -encoders`` but have no actual GPU hardware.  Using them
    # produces ``Unrecognized option`` or ``Broken pipe`` errors.  Skip
    # detection entirely (and never read/write the disk cache) so CI always
    # uses libx264.
    if os.getenv("CI"):
        return None

    key = _cache_key(ffmpeg)
    if key:
        cached = _load_capability_cache().get(key)
        if cached is not None and cached.get("probed_ok"):
            return cached.get("detected")

    detected, probed_ok = _probe(ffmpeg)

    # Only a clean probe is cached — a transient probe failure (nonzero exit,
    # missing binary) must not be hardened into "no GPU".  ``probed_ok`` is
    # True for both a found encoder and a clean no-GPU result, so the negative
    # is also reused next time.
    if key and probed_ok:
        entries = _load_capability_cache()
        entries[key] = {"detected": detected, "probed_ok": True}
        _store_capability_cache(entries)
    return detected


def resolve_encoder(requested: Optional[str]) -> tuple[str, list[str]]:
    """Resolve a requested encoder hint to ``(codec, ffmpeg_params_extra)``.

    ``requested`` accepts:

    * ``None`` / ``"auto"`` — auto-detect, fall back to ``libx264``.
    * ``"cpu"`` — force software ``libx264`` (no extra params).
    * ``"nvenc"`` / ``"vaapi"`` / ``"videotoolbox"`` — explicit backend
      with its recommended ffmpeg params.

    Unknown values fall back to ``libx264`` so a typo never aborts a
    render.

    This is a thin wrapper over :func:`_resolve_encoder_with_reason` that
    drops the fallback reason, preserving the public ``(codec, params)``
    contract.
    """
    codec, params, _ = _resolve_encoder_with_reason(requested)
    return (codec, params)


def _infer_auto_fallback_reason() -> str:
    """Reason why :func:`detect_gpu_encoder` yielded ``None`` on the auto path.

    Called only after detection returned ``None``. Distinguishes the cases
    without re-probing: a clean negative result is written to the disk cache
    (``probed_ok: true``), so its presence indicates "no GPU registered"
    rather than a probe error.
    """
    if os.getenv("CI"):
        return REASON_CI_SKIPPED
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        return REASON_NO_FFMPEG
    key = _cache_key(ffmpeg)
    if key:
        cached = _load_capability_cache().get(key)
        if cached is not None and cached.get("probed_ok"):
            return REASON_NOT_DETECTED
    return REASON_PROBE_FAILED


def _resolve_encoder_with_reason(
    requested: Optional[str],
) -> tuple[str, list[str], Optional[str]]:
    """Like :func:`resolve_encoder` but also returns the fallback reason.

    The third element is ``None`` when the resolved codec is not a fallback
    (a GPU encoder is active, or software encoding was explicitly requested).
    """
    if requested in (None, "auto"):
        gpu = detect_gpu_encoder()
        if gpu is not None:
            return (gpu, list(_GPU_PARAMS[gpu]), None)
        return ("libx264", [], _infer_auto_fallback_reason())

    if requested == "cpu":
        return ("libx264", [], None)

    codec = _HINT_TO_CODEC.get(requested)
    if codec is not None:
        return (codec, list(_GPU_PARAMS[codec]), None)

    return ("libx264", [], REASON_UNKNOWN_HINT)


def get_encoder_info(requested: Optional[str] = None) -> dict:
    """Build an encoder info dict for ``metadata.json``.

    The ``detected`` field reports what the GPU probe found (independent
    of ``requested``), while ``active`` reflects the codec that
    :func:`resolve_encoder` would actually select.  ``fallback_reason`` is
    ``None`` unless the active codec fell back to ``libx264``. ``requested``
    is normalised to ``"auto"`` when ``None`` for readable output.
    """
    detected = detect_gpu_encoder()
    active, _, reason = _resolve_encoder_with_reason(requested)
    return {
        "requested": requested if requested is not None else "auto",
        "detected": detected,
        "active": active,
        "gpu_available": detected is not None,
        "fallback_reason": reason,
    }
