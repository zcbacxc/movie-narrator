"""Audio loudness helpers — peak normalization and BGM ducking.

Uses pydub only (no scipy). Ducking is a simple windowed envelope:
when narration RMS in a window exceeds the speech threshold, BGM is
attenuated by ``duck_db`` for that window with linear attack/release.
"""

from __future__ import annotations

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
            window_envelope.append(duck_db)
        else:
            window_envelope.append(0.0)

    # Smooth the envelope with linear attack/release so the duck fades
    # in/out rather than clicking. Convert per-window dB → per-window
    # amplitude factor, then interpolate across windows.
    smoothed = _smooth_envelope(window_envelope, attack_ms, release_ms, window_ms)

    # Apply the envelope by slicing BGM into windows and gain-adjusting each.
    ducked_chunks: list[AudioSegment] = []
    for i in range(n_windows):
        start = i * window_ms
        end = min(start + window_ms, target_len)
        chunk = bgm_base[start:end]
        extra_db = smoothed[i]
        if extra_db < 0.0:
            chunk = chunk.apply_gain(extra_db)
        ducked_chunks.append(chunk)

    if not ducked_chunks:
        return narration

    ducked_bgm = ducked_chunks[0]
    for c in ducked_chunks[1:]:
        ducked_bgm = ducked_bgm + c

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
