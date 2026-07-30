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

import platform
import re
import shutil
import subprocess
from functools import lru_cache
from typing import Optional

# Canonical ffmpeg encoder names for each GPU backend.
_NVENC = "h264_nvenc"
_VAAPI = "h264_vaapi"
_VIDEOTOOLBOX = "h264_videotoolbox"

# Base detection order (applies when the platform has no preference).
_BASE_ORDER: list[str] = [_NVENC, _VAAPI, _VIDEOTOOLBOX]

# Recommended ffmpeg params per backend.  These are conservative quality
# presets — NVENC ``p4`` + VBR/CQ 20 is the standard quality/speed
# sweet spot, VAAPI ``fast`` targets the renderD128 node, and
# VideoToolbox uses a single quality factor.
_GPU_PARAMS: dict[str, list[str]] = {
    _NVENC: ["-preset", "p4", "-rc", "vbr", "-cq", "20"],
    _VAAPI: ["-preset", "fast", "-vaapi_device", "/dev/dri/renderD128"],
    _VIDEOTOOLBOX: ["-q:v", "65"],
}

# User-facing hint -> canonical codec name.
_HINT_TO_CODEC: dict[str, str] = {
    "nvenc": _NVENC,
    "vaapi": _VAAPI,
    "videotoolbox": _VIDEOTOOLBOX,
}


def _candidate_order() -> list[str]:
    """Return the platform-aware candidate detection order.

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


@lru_cache(maxsize=1)
def detect_gpu_encoder() -> Optional[str]:
    """Detect the best available GPU H.264 encoder.

    Runs ``ffmpeg -hide_banner -encoders`` once and returns the first
    available encoder in platform-aware priority order.  Returns
    ``None`` when ffmpeg is missing, the probe fails, or no GPU encoder
    is registered.
    """
    if shutil.which("ffmpeg") is None:
        return None

    try:
        proc = subprocess.run(
            ["ffmpeg", "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if proc.returncode != 0:
        return None

    available = _parse_encoder_names(proc.stdout)
    for candidate in _candidate_order():
        if candidate in available:
            return candidate
    return None


def resolve_encoder(requested: Optional[str]) -> tuple[str, list[str]]:
    """Resolve a requested encoder hint to ``(codec, ffmpeg_params_extra)``.

    ``requested`` accepts:

    * ``None`` / ``"auto"`` — auto-detect, fall back to ``libx264``.
    * ``"cpu"`` — force software ``libx264`` (no extra params).
    * ``"nvenc"`` / ``"vaapi"`` / ``"videotoolbox"`` — explicit backend
      with its recommended ffmpeg params.

    Unknown values fall back to ``libx264`` so a typo never aborts a
    render.
    """
    if requested in (None, "auto"):
        gpu = detect_gpu_encoder()
        if gpu is not None:
            return (gpu, list(_GPU_PARAMS[gpu]))
        return ("libx264", [])

    if requested == "cpu":
        return ("libx264", [])

    codec = _HINT_TO_CODEC.get(requested)
    if codec is not None:
        return (codec, list(_GPU_PARAMS[codec]))

    return ("libx264", [])


def get_encoder_info(requested: Optional[str] = None) -> dict:
    """Build an encoder info dict for ``metadata.json``.

    The ``detected`` field reports what the GPU probe found (independent
    of ``requested``), while ``active`` reflects the codec that
    :func:`resolve_encoder` would actually select.  ``requested`` is
    normalised to ``"auto"`` when ``None`` for readable output.
    """
    detected = detect_gpu_encoder()
    active, _ = resolve_encoder(requested)
    return {
        "requested": requested if requested is not None else "auto",
        "detected": detected,
        "active": active,
        "gpu_available": detected is not None,
    }
