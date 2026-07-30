# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Video encoding quality checks — bitrate, codec, resolution, frame rate.

v0.5.12: Extends the existing ``deliverable_qa.py`` with encoding-specific
checks that validate the rendered video meets platform publishing standards.

All checks are advisory — issues are stored in ``ctx.metadata["video_qa"]``
for diagnostics and the QA report, but never block the pipeline unless
wired as a hard gate via ``--strict``.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


logger = logging.getLogger(__name__)


# ── Thresholds ───────────────────────────────────────────

# Minimum resolution for publishable video (720p).
_MIN_WIDTH = 1280
_MIN_HEIGHT = 720

# Acceptable video codecs (H.264 and H.265/HEVC).
_ACCEPTABLE_CODECS = {"h264", "hevc", "h265"}

# Acceptable audio codecs.
_ACCEPTABLE_AUDIO_CODECS = {"aac", "mp3", "opus"}

# Minimum bitrate for 720p video (kbps).
_MIN_BITRATE_KBPS = 1500

# Acceptable frame rate range.
_MIN_FPS = 23.0
_MAX_FPS = 31.0

# Standard aspect ratios (width / height) with tolerance.
_STANDARD_RATIOS = {
    "16:9": 16 / 9,
    "9:16": 9 / 16,
}
_ASPECT_TOLERANCE = 0.02


# ── Data structures ──────────────────────────────────────


@dataclass
class VideoEncodingMetrics:
    """Encoding details extracted from ffprobe."""

    codec: str = ""
    profile: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    bitrate_kbps: int = 0
    pixel_format: str = ""
    audio_codec: str = ""
    audio_bitrate_kbps: int = 0
    audio_channels: int = 0
    audio_sample_rate: int = 0

    def to_dict(self) -> dict:
        return {
            "codec": self.codec,
            "profile": self.profile,
            "width": self.width,
            "height": self.height,
            "fps": round(self.fps, 2),
            "bitrate_kbps": self.bitrate_kbps,
            "pixel_format": self.pixel_format,
            "audio_codec": self.audio_codec,
            "audio_bitrate_kbps": self.audio_bitrate_kbps,
            "audio_channels": self.audio_channels,
            "audio_sample_rate": self.audio_sample_rate,
        }


@dataclass
class VideoQAReport:
    """Aggregated video encoding quality report."""

    ok: bool = True
    metrics: VideoEncodingMetrics = field(default_factory=VideoEncodingMetrics)
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "metrics": self.metrics.to_dict(),
            "issues": self.issues,
            "recommendations": self.recommendations,
        }


# ── Probing ──────────────────────────────────────────────


def _run_ffprobe(path: str, timeout: int = 30) -> Optional[dict]:
    """Run ffprobe and return parsed JSON, or None if unavailable."""
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return None
    try:
        proc = subprocess.run(
            [
                ffprobe, "-v", "quiet",
                "-print_format", "json",
                "-show_format", "-show_streams",
                path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        if proc.returncode != 0 or not (proc.stdout or "").strip():
            return None
        return json.loads(proc.stdout)
    except Exception:
        logger.debug("ffprobe execution failed", exc_info=True)
        return None


def probe_video_encoding(path: str) -> VideoEncodingMetrics:
    """Extract video encoding metrics via ffprobe.

    Returns a :class:`VideoEncodingMetrics` with all fields zeroed/empty
    if ffprobe is unavailable or probing fails.
    """
    data = _run_ffprobe(path)
    if data is None:
        return VideoEncodingMetrics()

    streams = data.get("streams", [])
    fmt = data.get("format", {})
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    metrics = VideoEncodingMetrics()

    if v_stream:
        metrics.codec = v_stream.get("codec_name", "")
        metrics.profile = v_stream.get("profile", "")
        metrics.width = int(v_stream.get("width", 0))
        metrics.height = int(v_stream.get("height", 0))

        # Parse frame rate: ffprobe returns it as a string fraction like "30000/1001"
        fps_str = v_stream.get("r_frame_rate", "0/1")
        try:
            num, den = fps_str.split("/")
            den_f = float(den)
            metrics.fps = float(num) / den_f if den_f > 0 else 0.0
        except (ValueError, ZeroDivisionError):
            metrics.fps = 0.0

        metrics.pixel_format = v_stream.get("pix_fmt", "")

        # Bitrate: prefer stream-level, fall back to format-level
        br = v_stream.get("bit_rate")
        if br:
            metrics.bitrate_kbps = int(int(br) / 1000)
        elif fmt.get("bit_rate"):
            metrics.bitrate_kbps = int(int(fmt["bit_rate"]) / 1000)

    if a_stream:
        metrics.audio_codec = a_stream.get("codec_name", "")
        br = a_stream.get("bit_rate")
        if br:
            metrics.audio_bitrate_kbps = int(int(br) / 1000)
        metrics.audio_channels = int(a_stream.get("channels", 0))
        metrics.audio_sample_rate = int(a_stream.get("sample_rate", 0))

    return metrics


# ── Quality checks ───────────────────────────────────────


def check_encoding_quality(
    metrics: VideoEncodingMetrics,
    *,
    min_width: int = _MIN_WIDTH,
    min_height: int = _MIN_HEIGHT,
    min_bitrate_kbps: int = _MIN_BITRATE_KBPS,
    min_fps: float = _MIN_FPS,
    max_fps: float = _MAX_FPS,
) -> VideoQAReport:
    """Validate encoding metrics against publishing thresholds.

    Returns a :class:`VideoQAReport` with issues and recommendations.
    The report is advisory — callers decide whether to act on it.
    """
    report = VideoQAReport(metrics=metrics)
    issues: list[str] = []
    recommendations: list[str] = []

    # ── Codec check ──
    if metrics.codec and metrics.codec not in _ACCEPTABLE_CODECS:
        issues.append(
            f"video codec '{metrics.codec}' is not in accepted list "
            f"({', '.join(sorted(_ACCEPTABLE_CODECS))})"
        )
        recommendations.append(
            "Re-encode with H.264 (libx264) for maximum platform compatibility"
        )

    # ── Resolution check ──
    if metrics.width > 0 and metrics.height > 0:
        if metrics.width < min_width or metrics.height < min_height:
            issues.append(
                f"resolution {metrics.width}x{metrics.height} is below "
                f"minimum {min_width}x{min_height}"
            )
            recommendations.append(
                f"Re-render at {min_width}x{min_height} or higher"
            )

        # Aspect ratio check
        actual_ratio = metrics.width / metrics.height
        matched = False
        for label, expected_ratio in _STANDARD_RATIOS.items():
            if abs(actual_ratio - expected_ratio) <= _ASPECT_TOLERANCE:
                matched = True
                break
        if not matched:
            issues.append(
                f"aspect ratio {actual_ratio:.3f} is not standard "
                f"({', '.join(_STANDARD_RATIOS)})"
            )
            recommendations.append(
                "Use 16:9 (landscape) or 9:16 (portrait) for platform compatibility"
            )

    # ── Bitrate check ──
    if metrics.bitrate_kbps > 0 and metrics.bitrate_kbps < min_bitrate_kbps:
        issues.append(
            f"video bitrate {metrics.bitrate_kbps} kbps is below "
            f"minimum {min_bitrate_kbps} kbps"
        )
        recommendations.append(
            "Increase video bitrate or use a slower encoding preset for better quality"
        )

    # ── Frame rate check ──
    if metrics.fps > 0:
        if metrics.fps < min_fps:
            issues.append(
                f"frame rate {metrics.fps:.1f} fps is below minimum {min_fps:.1f} fps"
            )
            recommendations.append("Use 24, 25, or 30 fps")
        elif metrics.fps > max_fps:
            issues.append(
                f"frame rate {metrics.fps:.1f} fps is above maximum {max_fps:.1f} fps"
            )

    # ── Audio codec check ──
    if metrics.audio_codec and metrics.audio_codec not in _ACCEPTABLE_AUDIO_CODECS:
        issues.append(
            f"audio codec '{metrics.audio_codec}' is not in accepted list "
            f"({', '.join(sorted(_ACCEPTABLE_AUDIO_CODECS))})"
        )
        recommendations.append("Use AAC for maximum platform compatibility")

    # ── Pixel format check ──
    if metrics.pixel_format and metrics.pixel_format not in ("yuv420p", "yuv422p", "yuv444p"):
        issues.append(
            f"pixel format '{metrics.pixel_format}' may cause compatibility issues"
        )
        recommendations.append("Use yuv420p for maximum compatibility")

    report.issues = issues
    report.recommendations = recommendations
    report.ok = len(issues) == 0
    return report


def evaluate_video_quality(
    video_path: str,
    *,
    min_width: int = _MIN_WIDTH,
    min_height: int = _MIN_HEIGHT,
    min_bitrate_kbps: int = _MIN_BITRATE_KBPS,
) -> VideoQAReport:
    """Probe a video file and run all encoding quality checks.

    Convenience wrapper: probes the file then validates.
    Returns a :class:`VideoQAReport` with all fields empty if the
    file doesn't exist or ffprobe is unavailable.
    """
    if not Path(video_path).exists():
        report = VideoQAReport()
        report.ok = False
        report.issues.append(f"file not found: {video_path}")
        return report

    metrics = probe_video_encoding(video_path)
    return check_encoding_quality(
        metrics,
        min_width=min_width,
        min_height=min_height,
        min_bitrate_kbps=min_bitrate_kbps,
    )
