"""Audio loudness helpers — peak normalization and BGM ducking.

Uses pydub for I/O and numpy for envelope application (AQ-07: replaces
O(n²) pydub chunk concatenation with O(n) numpy array multiplication).
Ducking is a simple windowed envelope: when narration RMS in a window
exceeds the speech threshold, BGM is attenuated by ``duck_db`` for that
window with linear attack/release.
"""

from __future__ import annotations

import numpy as np
from pydub import AudioSegment
from pydub.utils import db_to_float


def normalize_peak(seg: AudioSegment, target_dbfs: float = -14.0) -> AudioSegment:
    """Normalize ``seg`` so its peak reaches approximately ``target_dbfs``.

    ``target_dbfs`` is interpreted as a peak (max) target, consistent with
    pydub's ``max_dBFS``. A silent segment (max == 0) is returned unchanged
    to avoid a divide-by-zero explosion.
    """
    if seg.max == 0:
        return seg
    gain = target_dbfs - seg.max_dBFS
    return seg.apply_gain(gain)


def normalize_loudnorm(seg: AudioSegment, target_dbfs: float = -16.0) -> AudioSegment:
    """RMS-based loudness normalization (EP6).

    Approximates EBU R128 loudness normalization using RMS measurement.
    More consistent than peak normalization across different content types
    because it accounts for the overall energy, not just the loudest sample.

    Gain is clamped to ±12 dB to prevent extreme amplification of near-silent
    segments or excessive attenuation of loud ones.
    """
    if seg.rms == 0:
        return seg
    current_rms_db = seg.dBFS
    if current_rms_db is None or current_rms_db <= -100:
        return seg
    gain = target_dbfs - current_rms_db
    gain = max(-12.0, min(12.0, gain))
    return seg.apply_gain(gain)


def duck_bgm(
    narration: AudioSegment,
    bgm: AudioSegment,
    *,
    bgm_gain_db: float = -18.0,
    duck_db: float = -10.0,
    attack_ms: int = 50,
    release_ms: int = 200,
    window_ms: int = 50,
    speech_threshold_dbfs: float = -40.0,
) -> AudioSegment:
    """Duck ``bgm`` under ``narration`` and overlay the two tracks.

    1. Apply ``bgm_gain_db`` baseline attenuation to BGM.
    2. Loop/trim BGM to narration length.
    3. For each ``window_ms`` window, if narration RMS > ``speech_threshold_dbfs``,
       apply an extra ``duck_db`` attenuation to that BGM window. Linear
       attack (``attack_ms``) ramps the duck in; release (``release_ms``)
       ramps it out, avoiding abrupt volume jumps.
    4. Overlay ducked BGM under narration.

    Returns a mix the same length as ``narration``.
    """
    # Baseline BGM gain + loop/trim to narration length.
    bgm_base = bgm.apply_gain(bgm_gain_db)
    target_len = len(narration)
    if len(bgm_base) < target_len:
        times = target_len // max(len(bgm_base), 1) + 1
        bgm_base = bgm_base * times
    bgm_base = bgm_base[:target_len]

    if target_len == 0:
        return narration

    # Build the per-window gain envelope (in dB, 0 = no extra attenuation).
    n_windows = max(1, target_len // window_ms)
    window_envelope: list[float] = []  # extra attenuation dB per window
    for i in range(n_windows):
        start = i * window_ms
        end = min(start + window_ms, target_len)
        chunk = narration[start:end]
        if len(chunk) == 0:
            window_envelope.append(0.0)
            continue
        rms_db = chunk.dBFS
        if rms_db is None or rms_db <= -100:
            window_envelope.append(0.0)
        elif rms_db > speech_threshold_dbfs:
            # EP6: Proportional duck curve — scale duck amount by narration
            # energy above the threshold, producing a smoother, more natural
            # ducking effect. Full duck_db is reached at +10dB above threshold.
            excess = rms_db - speech_threshold_dbfs
            proportional = min(1.0, excess / 10.0)
            window_envelope.append(duck_db * proportional)
        else:
            window_envelope.append(0.0)

    # Smooth the envelope with linear attack/release so the duck fades
    # in/out rather than clicking. Convert per-window dB → per-window
    # amplitude factor, then interpolate across windows.
    smoothed = _smooth_envelope(window_envelope, attack_ms, release_ms, window_ms)

    # AQ-07: Apply the envelope via numpy instead of pydub chunk slicing.
    # The old approach sliced BGM into n_windows chunks, applied gain per
    # chunk, then concatenated with `+` — O(n²) due to pydub copying all
    # previous data on each concatenation.
    #
    # New approach: build a per-sample amplitude envelope as a numpy array,
    # multiply the BGM's raw samples in one operation, then reconstruct
    # a single AudioSegment. This is O(n) in total samples.
    if not smoothed or n_windows == 0:
        return narration

    # Convert dB envelope → linear amplitude factors
    amp_factors = np.array(
        [db_to_float(db) if db < 0.0 else 1.0 for db in smoothed],
        dtype=np.float64,
    )

    # Expand per-window factors to per-sample (linear interpolation at
    # window boundaries for smooth transitions).
    n_samples = len(bgm_base.get_array_of_samples())
    sample_rate = bgm_base.frame_rate
    samples_per_window = max(1, window_ms * sample_rate // 1000)
    per_sample = np.ones(n_samples, dtype=np.float64)
    for i, factor in enumerate(amp_factors):
        start_sample = i * samples_per_window
        end_sample = min(start_sample + samples_per_window, n_samples)
        if start_sample >= n_samples:
            break
        per_sample[start_sample:end_sample] = factor

    # Apply gain to raw samples
    raw = np.array(bgm_base.get_array_of_samples(), dtype=np.float64)
    raw *= per_sample[:len(raw)]
    raw = np.clip(raw, np.iinfo(np.int16).min, np.iinfo(np.int16).max)
    raw = raw.astype(np.int16)

    # Reconstruct AudioSegment from modified samples
    ducked_bgm = AudioSegment(
        raw.tobytes(),
        frame_rate=bgm_base.frame_rate,
        sample_width=bgm_base.sample_width,
        channels=bgm_base.channels,
    )

    # Ensure exact length (window rounding may add/drop a few ms).
    if len(ducked_bgm) != target_len:
        ducked_bgm = ducked_bgm[:target_len]

    return narration.overlay(ducked_bgm)


def _smooth_envelope(
    envelope: list[float],
    attack_ms: int,
    release_ms: int,
    window_ms: int,
) -> list[float]:
    """Linear-interpolate attack/release across window indices.

    ``attack_ms``/``release_ms`` are converted to window counts. When the
    envelope transitions from 0 → duck, it ramps linearly over the attack
    windows; duck → 0 ramps over the release windows.
    """
    n = len(envelope)
    if n == 0:
        return []

    attack_w = max(1, attack_ms // max(window_ms, 1))
    release_w = max(1, release_ms // max(window_ms, 1))

    smoothed = list(envelope)
    for i in range(1, n):
        prev = smoothed[i - 1]
        cur = envelope[i]
        if cur < prev:
            # Ramping into duck (attack): limit step to prev - duck/attack_w.
            max_step = abs(cur) / attack_w
            smoothed[i] = max(cur, prev - max_step)
        elif cur > prev:
            # Ramping out of duck (release): limit step to duck/release_w.
            max_step = abs(prev) / release_w
            smoothed[i] = min(cur, prev + max_step)
    return smoothed
