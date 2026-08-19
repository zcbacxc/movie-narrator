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
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .ffmpeg_bin import ffmpeg_bin


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
        """Convert the QA result to a dictionary.

        Returns:
            Dictionary representation of the QA result.
        """
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
        """Convert the QA result to a dictionary.

        Returns:
            Dictionary representation of the QA result.
        """
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
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
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
    except Exception:  # noqa: BLE001
        logger.debug("ffprobe execution failed", exc_info=True)
        return None


def probe_video_encoding(path: str) -> VideoEncodingMetrics:
    """Extract video encoding metrics via ffprobe.

    Returns:
        A :class:`VideoEncodingMetrics` with all fields zeroed/empty
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

    Returns:
        A :class:`VideoQAReport` with issues and recommendations.
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
        recommendations.append("Re-encode with H.264 (libx264) for maximum platform compatibility")

    # ── Resolution check ──
    if metrics.width > 0 and metrics.height > 0:
        # Portrait (vertical) video transposes the landscape minimums:
        # 720p landscape requires ≥1280×720, so a 9:16 portrait requires
        # ≥720×1280 — the same pixel budget in the rotated orientation.
        # Without this, a compliant 1080×1920 vertical video would be
        # falsely flagged as "below minimum 1280×720".
        is_portrait = metrics.height >= metrics.width
        min_w = min_height if is_portrait else min_width
        min_h = min_width if is_portrait else min_height

        if metrics.width < min_w or metrics.height < min_h:
            issues.append(
                f"resolution {metrics.width}x{metrics.height} is below "
                f"minimum {min_w}x{min_h}"
            )
            recommendations.append(f"Re-render at {min_w}x{min_h} or higher")

        # Aspect ratio check
        actual_ratio = metrics.width / metrics.height
        matched = False
        for label, expected_ratio in _STANDARD_RATIOS.items():
            if abs(actual_ratio - expected_ratio) <= _ASPECT_TOLERANCE:
                matched = True
                break
        if not matched:
            issues.append(
                f"aspect ratio {actual_ratio:.3f} is not standard ({', '.join(_STANDARD_RATIOS)})"
            )
            recommendations.append(
                "Use 16:9 (landscape) or 9:16 (portrait) for platform compatibility"
            )

    # ── Bitrate check ──
    if metrics.bitrate_kbps > 0 and metrics.bitrate_kbps < min_bitrate_kbps:
        issues.append(
            f"video bitrate {metrics.bitrate_kbps} kbps is below minimum {min_bitrate_kbps} kbps"
        )
        recommendations.append(
            "Increase video bitrate or use a slower encoding preset for better quality"
        )

    # ── Frame rate check ──
    if metrics.fps > 0:
        if metrics.fps < min_fps:
            issues.append(f"frame rate {metrics.fps:.1f} fps is below minimum {min_fps:.1f} fps")
            recommendations.append("Use 24, 25, or 30 fps")
        elif metrics.fps > max_fps:
            issues.append(f"frame rate {metrics.fps:.1f} fps is above maximum {max_fps:.1f} fps")

    # ── Audio codec check ──
    if metrics.audio_codec and metrics.audio_codec not in _ACCEPTABLE_AUDIO_CODECS:
        issues.append(
            f"audio codec '{metrics.audio_codec}' is not in accepted list "
            f"({', '.join(sorted(_ACCEPTABLE_AUDIO_CODECS))})"
        )
        recommendations.append("Use AAC for maximum platform compatibility")

    # ── Pixel format check ──
    if metrics.pixel_format and metrics.pixel_format not in ("yuv420p", "yuv422p", "yuv444p"):
        issues.append(f"pixel format '{metrics.pixel_format}' may cause compatibility issues")
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


# ── Slideshow-risk & black-frame detection (G2) ────────────


@dataclass
class SlideshowRisk:
    """Slideshow (degraded to image carousel) risk analysis.

    ``risk`` is a 0–1 score where higher means more likely the output is a
    near-static image sequence rather than a real video. ``static_ratio`` is
    the fraction of sampled frame transitions with negligible motion.
    ``black_ratio`` is the fraction of sampled frames that are near-black.
    """

    risk: float = 0.0
    static_ratio: float = 0.0
    avg_motion: float = 0.0
    black_ratio: float = 0.0
    samples: int = 0
    probed: bool = False

    def to_dict(self) -> dict:
        """Return a JSON-serializable representation."""
        return {
            "risk": round(self.risk, 3),
            "static_ratio": round(self.static_ratio, 3),
            "avg_motion": round(self.avg_motion, 3),
            "black_ratio": round(self.black_ratio, 3),
            "samples": self.samples,
            "probed": self.probed,
        }


def _extract_luma_frames(video_path: str, sample_sec: float, max_frames: int) -> list[float]:
    """Sample frames across the video and return their mean luma (0–255).

    Uses ffmpeg to extract one frame every ``sample_sec`` seconds and PIL to
    compute each frame's mean luminance (ITU-R 601-2 luma). Returns an empty
    list when ffmpeg or PIL is unavailable or extraction fails, so callers can
    degrade gracefully (``probed=False``).
    """
    ffmpeg = ffmpeg_bin()
    if not ffmpeg:
        return []
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        return []

    with tempfile.TemporaryDirectory() as tmp:
        pattern = os.path.join(tmp, "frame_%04d.png")
        try:
            proc = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-i",
                    video_path,
                    "-vf",
                    f"fps=1/{sample_sec}",
                    "-frames:v",
                    str(max_frames),
                    pattern,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
            )
        except Exception:  # noqa: BLE001
            logger.debug("frame sampling failed", exc_info=True)
            return []
        if proc.returncode != 0:
            logger.debug("ffmpeg frame sampling exit=%s", proc.returncode)
            return []

        frames = sorted(Path(tmp).glob("frame_*.png"))
        lumas: list[float] = []
        for f in frames:
            try:
                img = Image.open(f).convert("L")
                if img.width > 64:
                    img = img.resize((64, 64))
                pixels = list(img.getdata())
                if pixels:
                    lumas.append(sum(pixels) / len(pixels))
            except Exception:  # noqa: BLE001  # nosec B112 — skip corrupted frames
                continue
        return lumas


def check_slideshow_risk(
    video_path: str,
    *,
    sample_sec: float = 1.0,
    max_frames: int = 60,
    motion_static_threshold: float = 1.5,
    black_luma_threshold: float = 16.0,
) -> SlideshowRisk:
    """Analyze a video for slideshow-degradation and near-black segments.

    Estimated by sampling frames and measuring:
    - **motion**: mean absolute change in luma between consecutive samples
      (a near-static video has ~0 motion → looks like an image carousel);
    - **static_ratio**: fraction of consecutive transitions below
      ``motion_static_threshold``;
    - **black_ratio**: fraction of sampled frames below ``black_luma_threshold``.

    Risk is a composition of the two failure signals:

        risk = clamp(1 - avg_motion / motion_static_threshold)
             + black_ratio * 0.5

    Returns a :class:`SlideshowRisk` with ``probed=False`` when the probe
    cannot run (no ffmpeg/PIL, extraction failure, or too few frames).
    """
    lumas = _extract_luma_frames(video_path, sample_sec, max_frames)
    if len(lumas) < 2:
        return SlideshowRisk()

    # Frame-to-frame motion = luma delta between consecutive samples.
    deltas = [abs(lumas[i] - lumas[i - 1]) for i in range(1, len(lumas))]
    avg_motion = sum(deltas) / len(deltas)
    static = sum(1 for d in deltas if d < motion_static_threshold) / len(deltas)
    black = sum(1 for lum in lumas if lum < black_luma_threshold) / len(lumas)

    motion_risk = max(0.0, 1.0 - avg_motion / motion_static_threshold)
    risk = min(1.0, motion_risk + black * 0.5)

    return SlideshowRisk(
        risk=risk,
        static_ratio=static,
        avg_motion=avg_motion,
        black_ratio=black,
        samples=len(lumas),
        probed=True,
    )
