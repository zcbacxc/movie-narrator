# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Resource-aware admission checks for heavy pipeline steps (v1.3.2).

Pure-stdlib heuristics that decide whether a step should start at all
given the machine's available resources. The render preflight
(``pipeline/render.py``) uses :func:`check_render_admission` to abort a
render *before* any heavy work when the temp volume does not look like
it can hold the render intermediates.

Heuristic (documented, deliberately simple — not a guarantee):

    temp_space_need ≈ 2 GB                    # ~1080p (1920×1080), 60 s
                      × (width × height) / (1920 × 1080)   # area ratio
                      × (duration_s / 60)                  # duration ratio
                      × 1.25                               # 10-bit factor (v1.5.0)
    clamped to [0.5 GB, 50 GB]

Rationale: a 1080p / 60 s render writes a video-only intermediate plus
the muxed output (CRF-18 x264 ≈ 100–300 MB each) and, depending on the
path, per-clip/temp files; 2 GB gives comfortable headroom without
being wasteful. The estimate scales linearly with frame area and
duration, floored at 0.5 GB (very short/low-res renders still need
working room) and capped at 50 GB (long 4K renders should not produce
absurd gates — the heuristic's purpose is catching obviously-full
disks, not predicting exact usage).

v1.5.0: 10-bit renders (``render_bit_depth: 10`` / hdr10) carry ~25%
more data per frame (yuv420p10le) and a slower encode keeps temp
intermediates alive longer, so the estimate gains a ×1.25 factor when
``bit_depth=10``. The factor is deliberately coarse — same clamp range.

The CPU count is advisory only: a low core count is surfaced as a
non-fatal ``advisory:`` reason, never a hard failure.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

# ── Heuristic constants ───────────────────────────────────

#: Estimated temp-space need for a 1080p / 60 s render (bytes).
_BASE_1080P_60S_BYTES = 2.0 * 1024 * 1024 * 1024
#: Reference frame area the base estimate is calibrated for.
_BASE_AREA_PX = 1920 * 1080
#: Reference duration (seconds) the base estimate is calibrated for.
_BASE_DURATION_S = 60.0
#: Clamp range for the estimate.
_MIN_NEED_BYTES = 0.5 * 1024 * 1024 * 1024
_MAX_NEED_BYTES = 50.0 * 1024 * 1024 * 1024
# v1.5.0: temp-space multiplier for 10-bit renders (yuv420p10le carries
# ~25% more data per frame; the slower encode also keeps intermediates
# alive longer). Deliberately coarse — the heuristic is not a guarantee.
_BIT_DEPTH_10_FACTOR = 1.25

_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class AdmissionCheck:
    """Outcome of a resource admission check.

    Attributes:
        ok: ``True`` when the step may start. ``False`` only for hard
            failures (insufficient disk space, unusable path).
        reasons: Human-readable failure descriptions. May also contain
            non-fatal entries prefixed with ``"advisory:"`` (e.g. low
            CPU count) which never affect ``ok``.
    """

    ok: bool
    reasons: tuple[str, ...]


def env_flag_enabled(name: str) -> bool:
    """True iff the given environment variable is set to a truthy value."""
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def _format_bytes(n: float) -> str:
    """Human-readable byte size for error messages (GB with 1 decimal)."""
    gb = n / (1024 * 1024 * 1024)
    if gb >= 1:
        return f"{gb:.1f} GB"
    mb = n / (1024 * 1024)
    return f"{mb:.0f} MB"


def estimate_temp_space_bytes(
    resolution: tuple[int, int],
    duration_estimate_s: float,
    *,
    bit_depth: int = 8,
) -> int:
    """Estimate the temp-space need (bytes) for a render.

    Implements the documented heuristic: a 2 GB base for 1080p / 60 s
    scaled linearly by frame area and duration (×1.25 for 10-bit
    renders, v1.5.0), clamped to ``[0.5 GB, 50 GB]``. Non-positive
    durations fall back to the floor (any render needs *some* working
    room).

    Args:
        resolution: ``(width, height)`` output resolution in pixels.
        duration_estimate_s: Expected output duration in seconds.
        bit_depth: Render bit depth (8 default; 10 applies the
            documented ×1.25 factor). Other values behave like 8.

    Returns:
        The estimated need in bytes (integer).
    """
    width, height = resolution
    area_ratio = max(float(width) * float(height), 0.0) / _BASE_AREA_PX
    duration_ratio = max(float(duration_estimate_s), 0.0) / _BASE_DURATION_S
    depth_factor = _BIT_DEPTH_10_FACTOR if int(bit_depth) == 10 else 1.0
    need = _BASE_1080P_60S_BYTES * area_ratio * duration_ratio * depth_factor
    return int(max(_MIN_NEED_BYTES, min(need, _MAX_NEED_BYTES)))


def check_render_admission(
    *,
    resolution: tuple[int, int],
    duration_estimate_s: float,
    temp_dir: Path,
    min_free_disk_bytes: int = 0,
    cpu_count: int | None = None,
    bit_depth: int = 8,
) -> AdmissionCheck:
    """Decide whether a render may start given available resources.

    Hard failure (``ok=False``) when:

    - the temp volume's free space cannot be determined, or
    - free space is below ``max(heuristic_need, min_free_disk_bytes)``.

    ``min_free_disk_bytes`` acts as a user-configurable floor on top of
    the heuristic (``0`` = heuristic only). The CPU count is advisory:
    a single-core machine adds an informational ``advisory:`` reason
    but never fails the check.

    Args:
        resolution: ``(width, height)`` output resolution in pixels.
        duration_estimate_s: Expected output duration in seconds.
        temp_dir: Directory the step will write intermediates into
            (its filesystem is probed).
        min_free_disk_bytes: User-configured minimum free-space floor.
        cpu_count: CPU core count (informational only).
        bit_depth: Render bit depth (v1.5.0; 8 default, 10 applies the
            documented ×1.25 temp-space factor). Keyword-only and
            defaulted so existing callers stay behaviour-identical.

    Returns:
        An :class:`AdmissionCheck` with ``ok`` and diagnostic reasons.
    """
    temp_dir = Path(temp_dir)
    hard_failures: list[str] = []
    advisory: list[str] = []

    try:
        free_bytes = shutil.disk_usage(temp_dir).free
    except OSError as exc:
        return AdmissionCheck(
            ok=False,
            reasons=(f"cannot determine free disk space for {temp_dir}: {exc}",),
        )

    need_bytes = estimate_temp_space_bytes(resolution, duration_estimate_s, bit_depth=bit_depth)
    required_bytes = max(need_bytes, max(int(min_free_disk_bytes), 0))
    if free_bytes < required_bytes:
        depth_note = " ×1.25 10-bit factor" if int(bit_depth) == 10 else ""
        hard_failures.append(
            f"insufficient disk space for render: need ~{_format_bytes(required_bytes)} "
            f"(heuristic for {resolution[0]}x{resolution[1]} @ "
            f"{float(duration_estimate_s):.0f}s{depth_note}), "
            f"only {_format_bytes(free_bytes)} free in {temp_dir}"
        )

    if cpu_count is not None and int(cpu_count) < 2:
        advisory.append(
            f"advisory: only {cpu_count} CPU detected — render will be slow (non-fatal)"
        )

    return AdmissionCheck(ok=not hard_failures, reasons=tuple(hard_failures + advisory))
