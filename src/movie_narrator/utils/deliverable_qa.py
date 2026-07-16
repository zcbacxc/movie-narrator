"""Deliverable media QA — probe a rendered video and flag publishability issues.

Probes container/streams/volume via ffprobe when available, falling back to
the imageio-ffmpeg-bundled ``ffmpeg`` binary (which is NOT ffprobe) by
parsing ``ffmpeg -i`` stderr. Volume is measured with ``volumedetect``.
All checks are advisory except when wired as a hard pipeline step.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class QAIssue:
    """A single failed QA check."""

    code: str
    message: str


@dataclass
class QAReport:
    """Aggregated QA result for a deliverable."""

    ok: bool
    issues: list[QAIssue] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)


def _ffmpeg_bin() -> str:
    """Return a usable ffmpeg binary path.

    Prefers a system ``ffprobe``-adjacent ``ffmpeg``; falls back to the
    imageio-ffmpeg bundled binary so probing works even without a system
    ffmpeg install.
    """
    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"  # last resort — let subprocess raise


def _probe_with_ffprobe(path: str) -> Optional[dict]:
    """Probe via ffprobe JSON output. Returns None if ffprobe unavailable."""
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
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
    except Exception:
        return None

    streams = data.get("streams", [])
    fmt = data.get("format", {})
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = 0.0
    try:
        duration = float(fmt.get("duration") or v_stream.get("duration") or 0.0)
    except (TypeError, ValueError):
        duration = 0.0

    width = int(v_stream.get("width", 0)) if v_stream else 0
    height = int(v_stream.get("height", 0)) if v_stream else 0

    return {
        "duration": duration,
        "has_video": v_stream is not None,
        "has_audio": a_stream is not None,
        "width": width,
        "height": height,
        "size_bytes": _file_size(path),
        "mean_volume": _detect_volume(path),
    }


def _probe_with_ffmpeg(path: str) -> dict:
    """Fallback probe using ``ffmpeg -i`` stderr parsing."""
    bin_path = _ffmpeg_bin()
    try:
        proc = subprocess.run(
            [bin_path, "-i", path, "-hide_banner"],
            capture_output=True, text=True, timeout=30,
        )
        # ffmpeg -i exits non-zero when there's no output, but stderr still
        # contains the stream info we need.
        stderr = proc.stderr or ""
    except Exception:
        stderr = ""

    has_video = "Video:" in stderr
    has_audio = "Audio:" in stderr

    duration = 0.0
    m = re.search(r"Duration:\s*([\d:.]+)", stderr)
    if m:
        parts = m.group(1).split(":")
        try:
            duration = sum(float(p) * (60 ** i) for i, p in enumerate(reversed(parts)))
        except ValueError:
            duration = 0.0

    width, height = 0, 0
    m = re.search(r"(\d{2,5})x(\d{2,5})", stderr)
    if m:
        width, height = int(m.group(1)), int(m.group(2))

    return {
        "duration": duration,
        "has_video": has_video,
        "has_audio": has_audio,
        "width": width,
        "height": height,
        "size_bytes": _file_size(path),
        "mean_volume": _detect_volume(path),
    }


def _file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def _detect_volume(path: str) -> Optional[float]:
    """Run ``ffmpeg -af volumedetect`` and parse mean_volume (dBFS).

    Returns None if the file has no audio or ffmpeg fails.
    """
    bin_path = _ffmpeg_bin()
    try:
        proc = subprocess.run(
            [bin_path, "-i", path, "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True, text=True, timeout=60,
        )
        stderr = proc.stderr or ""
    except Exception:
        return None

    m = re.search(r"mean_volume:\s*(-?[\d.]+)\s*dB", stderr)
    if m:
        return float(m.group(1))
    return None


def probe_media(path: str) -> dict:
    """Return ffprobe-like metrics for ``path``.

    Keys: ``duration``, ``has_video``, ``has_audio``, ``mean_volume``,
    ``width``, ``height``, ``size_bytes``. ``mean_volume`` is None when
    the file has no audio or volumedetect is unavailable.
    """
    result = _probe_with_ffprobe(path)
    if result is None:
        result = _probe_with_ffmpeg(path)
    return result


def evaluate_deliverable(
    video_path: str,
    *,
    expected_duration: float,
    max_silence_db: float = -50.0,
    min_duration_ratio: float = 0.85,
    max_duration_ratio: float = 1.15,
    min_size_bytes: int = 10_000,
) -> QAReport:
    """Run all QA checks and return a structured report.

    Checks:
    1. file exists and ``size >= min_size_bytes``
    2. has video stream and audio stream
    3. duration within ``[min_duration_ratio, max_duration_ratio]`` of expected
    4. ``mean_volume > max_silence_db`` (audio is not near-silent)
    5. width/height > 0
    """
    issues: list[QAIssue] = []
    metrics: dict = {}

    if not Path(video_path).exists():
        return QAReport(
            ok=False,
            issues=[QAIssue("missing_file", f"file not found: {video_path}")],
            metrics={},
        )

    metrics = probe_media(video_path)
    size = metrics.get("size_bytes", 0)
    if size < min_size_bytes:
        issues.append(QAIssue("tiny_file", f"file size {size}B < {min_size_bytes}B"))

    if not metrics.get("has_video"):
        issues.append(QAIssue("no_video_stream", "file has no video stream"))
    if not metrics.get("has_audio"):
        issues.append(QAIssue("no_audio_stream", "file has no audio stream"))

    duration = metrics.get("duration", 0.0) or 0.0
    if expected_duration > 0 and duration > 0:
        ratio = duration / expected_duration
        if ratio < min_duration_ratio:
            issues.append(QAIssue(
                "too_short",
                f"duration {duration:.2f}s is {ratio:.2%} of expected {expected_duration:.2f}s "
                f"(min {min_duration_ratio:.0%})",
            ))
        elif ratio > max_duration_ratio:
            issues.append(QAIssue(
                "too_long",
                f"duration {duration:.2f}s is {ratio:.2%} of expected {expected_duration:.2f}s "
                f"(max {max_duration_ratio:.0%})",
            ))

    mean_vol = metrics.get("mean_volume")
    if mean_vol is not None and mean_vol <= max_silence_db:
        issues.append(QAIssue(
            "silent_audio",
            f"mean volume {mean_vol:.1f}dB <= silence floor {max_silence_db:.1f}dB",
        ))

    if metrics.get("width", 0) <= 0 or metrics.get("height", 0) <= 0:
        issues.append(QAIssue(
            "bad_resolution",
            f"invalid dimensions {metrics.get('width')}x{metrics.get('height')}",
        ))

    return QAReport(ok=len(issues) == 0, issues=issues, metrics=metrics)
