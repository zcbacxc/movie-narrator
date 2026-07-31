# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""TTS output quality validation — clipping, SNR, silence checks.

v0.5.9: Per-segment audio quality metrics for the narration pipeline.
All functions accept a ``pydub.AudioSegment`` and return structured
metrics dicts.  They are advisory (soft gates) — issues are logged
and stored in ``ctx.metadata["audio_quality"]`` for diagnostics, but
never block the pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np
from pydub import AudioSegment


@dataclass
class SegmentAudioMetrics:
    """Quality metrics for a single TTS segment."""

    index: int
    duration_s: float
    peak_dbfs: float
    rms_dbfs: float
    clipping_ratio: float
    snr_db: Optional[float]
    silence_ratio: float
    issues: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "duration_s": round(self.duration_s, 3),
            "peak_dbfs": round(self.peak_dbfs, 1),
            "rms_dbfs": round(self.rms_dbfs, 1),
            "clipping_ratio": round(self.clipping_ratio, 4),
            "snr_db": round(self.snr_db, 1) if self.snr_db is not None else None,
            "silence_ratio": round(self.silence_ratio, 4),
            "issues": self.issues,
        }


def detect_clipping(audio: AudioSegment, threshold_db: float = -0.5) -> float:
    """Detect clipping by counting samples at or near maximum amplitude.

    Returns the ratio of clipped samples to total samples (0.0 – 1.0).
    A ratio above 0.001 (0.1%) is generally audible distortion.
    """
    samples = np.array(audio.get_array_of_samples(), dtype=np.float64)
    if len(samples) == 0:
        return 0.0

    max_val = float(np.iinfo(np.int16).max)
    # Samples within threshold_db of full scale are considered clipped
    clip_level = max_val * (10 ** (threshold_db / 20.0))
    clipped: Any = np.sum(np.abs(samples) >= clip_level)
    return float(clipped) / float(len(samples))


def estimate_snr(audio: AudioSegment, silence_threshold_dbfs: float = -50.0) -> Optional[float]:
    """Estimate signal-to-noise ratio in dB.

    Partitions the audio into 50ms windows. Windows with RMS below
    ``silence_threshold_dbfs`` are classified as "noise"; the rest are
    "signal".  SNR = 20 * log10(signal_rms / noise_rms).

    Returns ``None`` when there are no noise windows (all signal) or
    no signal windows (all noise), making SNR undefined.  When noise
    is pure silence (RMS = 0), a small floor is used so the SNR
    reports as a very high value rather than undefined.
    """
    samples = np.array(audio.get_array_of_samples(), dtype=np.float64)
    if len(samples) == 0:
        return None

    sample_rate = audio.frame_rate
    window_samples = max(1, int(0.05 * sample_rate))  # 50ms windows
    n_windows = len(samples) // window_samples
    if n_windows < 2:
        return None

    # Calculate RMS per window
    rms_values = []
    for i in range(n_windows):
        chunk = samples[i * window_samples : (i + 1) * window_samples]
        rms = np.sqrt(np.mean(chunk ** 2))
        rms_values.append(rms)

    rms_arr = np.array(rms_values)
    # Convert to dBFS (relative to int16 max)
    max_val = float(np.iinfo(np.int16).max)
    rms_db = 20 * np.log10(np.maximum(rms_arr / max_val, 1e-10))

    signal_mask = rms_db > silence_threshold_dbfs
    noise_mask = ~signal_mask

    signal_rms = np.sqrt(np.mean(rms_arr[signal_mask] ** 2)) if np.any(signal_mask) else 0.0
    noise_rms = np.sqrt(np.mean(rms_arr[noise_mask] ** 2)) if np.any(noise_mask) else 0.0

    if signal_rms <= 0:
        return None

    # When noise is pure silence (RMS = 0), use a tiny floor so SNR
    # reports as a very high value instead of None.
    noise_rms = max(noise_rms, 1e-10)
    snr = 20 * np.log10(signal_rms / noise_rms)
    return float(snr)


def check_silence(audio: AudioSegment, silence_threshold_dbfs: float = -50.0) -> float:
    """Check the ratio of silence in the audio.

    Returns the fraction of 50ms windows that are below the silence
    threshold.  A ratio above 0.5 means more than half the segment
    is silent, which may indicate a TTS failure.
    """
    samples = np.array(audio.get_array_of_samples(), dtype=np.float64)
    if len(samples) == 0:
        return 1.0

    sample_rate = audio.frame_rate
    window_samples = max(1, int(0.05 * sample_rate))
    n_windows = max(1, len(samples) // window_samples)

    silent_count = 0
    max_val = float(np.iinfo(np.int16).max)
    for i in range(n_windows):
        chunk = samples[i * window_samples : (i + 1) * window_samples]
        rms = np.sqrt(np.mean(chunk ** 2))
        rms_db = 20 * np.log10(max(rms / max_val, 1e-10))
        if rms_db <= silence_threshold_dbfs:
            silent_count += 1

    return float(silent_count) / float(n_windows)


def analyze_segment(
    audio: AudioSegment,
    index: int,
    *,
    clipping_threshold_db: float = -0.5,
    silence_threshold_dbfs: float = -50.0,
    max_clipping_ratio: float = 0.001,
    max_silence_ratio: float = 0.5,
    min_snr_db: float = 15.0,
) -> SegmentAudioMetrics:
    """Run all quality checks on a single audio segment.

    Returns a :class:`SegmentAudioMetrics` with all measurements and
    a list of issue descriptions (empty if all checks pass).
    """
    duration_s = len(audio) / 1000.0
    peak_dbfs = audio.max_dBFS
    rms_dbfs = audio.dBFS if audio.dBFS is not None else -100.0

    clipping_ratio = detect_clipping(audio, clipping_threshold_db)
    snr_db = estimate_snr(audio, silence_threshold_dbfs)
    silence_ratio = check_silence(audio, silence_threshold_dbfs)

    issues: list[str] = []

    if clipping_ratio > max_clipping_ratio:
        issues.append(
            f"clipping detected: {clipping_ratio:.2%} samples near max amplitude"
        )

    if silence_ratio > max_silence_ratio:
        issues.append(
            f"high silence ratio: {silence_ratio:.1%} of segment is silent"
        )

    if snr_db is not None and snr_db < min_snr_db:
        issues.append(f"low SNR: {snr_db:.1f}dB < {min_snr_db}dB threshold")

    if duration_s < 0.1:
        issues.append(f"segment too short: {duration_s:.3f}s")

    return SegmentAudioMetrics(
        index=index,
        duration_s=duration_s,
        peak_dbfs=peak_dbfs,
        rms_dbfs=rms_dbfs,
        clipping_ratio=clipping_ratio,
        snr_db=snr_db,
        silence_ratio=silence_ratio,
        issues=issues,
    )


def aggregate_metrics(metrics: list[SegmentAudioMetrics]) -> dict:
    """Aggregate per-segment metrics into a summary dict for metadata.json."""
    if not metrics:
        return {"segment_count": 0, "issues": []}

    total_issues = sum(len(m.issues) for m in metrics)
    segments_with_issues = sum(1 for m in metrics if m.issues)
    avg_duration = sum(m.duration_s for m in metrics) / len(metrics)
    avg_snr = (
        sum(m.snr_db for m in metrics if m.snr_db is not None)
        / max(1, sum(1 for m in metrics if m.snr_db is not None))
    )
    max_clipping = max(m.clipping_ratio for m in metrics)
    avg_silence = sum(m.silence_ratio for m in metrics) / len(metrics)

    return {
        "segment_count": len(metrics),
        "total_duration_s": round(sum(m.duration_s for m in metrics), 2),
        "avg_segment_duration_s": round(avg_duration, 3),
        "avg_snr_db": round(avg_snr, 1) if avg_snr else None,
        "max_clipping_ratio": round(max_clipping, 4),
        "avg_silence_ratio": round(avg_silence, 4),
        "segments_with_issues": segments_with_issues,
        "total_issues": total_issues,
        "all_issues": [issue for m in metrics for issue in m.issues],
    }
