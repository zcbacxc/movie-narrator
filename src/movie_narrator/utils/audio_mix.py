# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Audio loudness helpers — peak normalization and BGM ducking.

Uses pydub for I/O and numpy for envelope application (replaces
O(n²) pydub chunk concatenation with O(n) numpy array multiplication).
Ducking is a simple windowed envelope: when narration RMS in a window
exceeds the speech threshold, BGM is attenuated by ``duck_db`` for that
window with linear attack/release.
"""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Optional

import numpy as np
from pydub import AudioSegment
from pydub.utils import db_to_float

from ..utils.ffmpeg_bin import ffmpeg_bin as _resolve_ffmpeg
from ..utils.process import run_ffmpeg_subprocess

logger = logging.getLogger(__name__)

#: Deadline for the FFmpeg ``sidechaincompress`` mix (seconds). The default
#: 5 min is generous for a <1 min narration track; on timeout the ffmpeg tree
#: is terminated and the caller falls back to the envelope backend.
_SIDECHAIN_TIMEOUT = 300.0


class DuckingBackend(str, Enum):
    """Selects the BGM ducking implementation.

    G7: the historical envelope-based ducking stays as the default
    (``ENVELOPE``). ``SIDECHAIN`` routes through FFmpeg's
    ``sidechaincompress`` for a more natural, proportional duck that
    follows the narration's real-time level ("voice up, BGM down; pause,
    BGM recovers"). ``ENVELOPE`` has no external dependency; ``SIDECHAIN``
    requires FFmpeg on ``PATH`` (fallback to ``ENVELOPE`` when absent).
    """

    ENVELOPE = "envelope"
    SIDECHAIN = "sidechaincompress"

    @classmethod
    def from_value(cls, value: Optional[str]) -> "DuckingBackend":
        """Parse a config value, defaulting to :attr:`ENVELOPE` on any
        unrecognized / missing input (lenient, never raises)."""
        if not value:
            return cls.ENVELOPE
        try:
            return cls(value)
        except ValueError:
            return cls.ENVELOPE


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
    """RMS-based loudness normalization.

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


def duck_bgm_envelope(
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

    This is the historical envelope backend. See :func:`duck_bgm` for the
    backend-dispatching entry point.

    Returns:
        A mix the same length as ``narration``.
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
            # Proportional duck curve — scale duck amount by narration
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

    # Apply the envelope via numpy instead of pydub chunk slicing.
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
    per_sample: Any = np.ones(n_samples, dtype=np.float64)
    for i, factor in enumerate(amp_factors):
        start_sample = i * samples_per_window
        end_sample = min(start_sample + samples_per_window, n_samples)
        if start_sample >= n_samples:
            break
        per_sample[start_sample:end_sample] = factor

    # Apply gain to raw samples
    raw = np.array(bgm_base.get_array_of_samples(), dtype=np.float64)
    raw *= per_sample[: len(raw)]
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
    backend: DuckingBackend | str | None = DuckingBackend.ENVELOPE,
) -> AudioSegment:
    """Duck ``bgm`` under ``narration`` and overlay the two tracks.

    Backend-dispatching entry point (G7). Selects the ducking
    implementation via ``backend``:

    - :attr:`DuckingBackend.ENVELOPE` (default) — the historical windowed
      RMS envelope (no external dependency).
    - :attr:`DuckingBackend.SIDECHAIN` — FFmpeg ``sidechaincompress`` for a
      proportional, more natural duck. Falls back to the envelope backend
      when FFmpeg is unavailable or the sidechain mix fails.

    All other parameters match the envelope backend's contract.

    Returns:
        A mix the same length as ``narration``.
    """
    selected = DuckingBackend.from_value(backend)
    if selected is DuckingBackend.SIDECHAIN:
        mixed = duck_bgm_sidechain(
            narration,
            bgm,
            bgm_gain_db=bgm_gain_db,
            duck_db=duck_db,
            attack_ms=attack_ms,
            release_ms=release_ms,
        )
        if mixed is not None:
            return mixed
        logger.debug("sidechaincompress ducking unavailable; falling back to envelope")
    return duck_bgm_envelope(
        narration,
        bgm,
        bgm_gain_db=bgm_gain_db,
        duck_db=duck_db,
        attack_ms=attack_ms,
        release_ms=release_ms,
        window_ms=window_ms,
        speech_threshold_dbfs=speech_threshold_dbfs,
    )


def crossfade_segments(
    segments: list[tuple[AudioSegment, float]],
    crossfade_ms: int = 500,
) -> AudioSegment:
    """Concatenate audio segments with crossfade transitions.

    v0.5.9: BGM dynamic transition — crossfade between BGM sections
    at emotion zone boundaries to avoid abrupt mood changes.

    Args:
        segments: list of ``(audio, start_offset_s)`` tuples. The
            ``start_offset_s`` is the position in the final track where
            this segment should begin (used only for metadata; the
            actual concatenation is sequential).
        crossfade_ms: crossfade duration in milliseconds.

    Returns:
        A single AudioSegment with all segments crossfaded together.
    """
    if not segments:
        return AudioSegment.empty()
    if len(segments) == 1:
        return segments[0][0]

    result = segments[0][0]
    for i in range(1, len(segments)):
        seg = segments[i][0]
        # Clamp crossfade to the shorter of the two adjacent segments
        cf = min(crossfade_ms, len(result), len(seg))
        if cf <= 0:
            result = result + seg
        else:
            result = result.append(seg, crossfade=cf)
    return result


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


# ── G7: sidechaincompress ducking backend ────────────────


def _ffmpeg_bin() -> Optional[str]:
    """Locate an FFmpeg binary, or ``None`` when unavailable.

    Delegates to the shared :func:`ffmpeg_bin` resolution so the whole project
    uses one policy: ``MN_FFMPEG_BIN`` override → imageio-ffmpeg bundled build
    (full-featured, ships ``sidechaincompress`` / ``asplit`` / ``amix``) →
    system ``ffmpeg`` on ``PATH``. The bare ``"ffmpeg"`` fallback is mapped to
    ``None`` so callers can fall back to the envelope backend.
    """
    resolved = _resolve_ffmpeg()
    if resolved and resolved != "ffmpeg":
        return resolved
    return None


def duck_bgm_sidechain(
    narration: AudioSegment,
    bgm: AudioSegment,
    *,
    bgm_gain_db: float = -18.0,
    duck_db: float = -10.0,
    attack_ms: int = 50,
    release_ms: int = 200,
) -> Optional[AudioSegment]:
    """Duck ``bgm`` under ``narration`` using FFmpeg ``sidechaincompress``.

    Implements the same contract as :func:`duck_bgm` but through FFmpeg's
    native sidechain compressor, which proportionally lowers the BGM while
    narration is present and lets it recover during pauses — a more natural
    mix than the fixed envelope.

    Args:
        narration: Voice track (sidechain key).
        bgm: Background music track.
        bgm_gain_db: Baseline attenuation applied to BGM before ducking.
        duck_db: Maximum duck amount in dB (maps to the compressor's
            ``threshold``/``ratio``).
        attack_ms / release_ms: Compressor envelope timings.

    Returns:
        The ducked+overlaid mix, or ``None`` if FFmpeg is unavailable or
        the sidechain filter cannot be built (callers fall back to the
        envelope backend).
    """
    ffmpeg = _ffmpeg_bin()
    if not ffmpeg:
        return None

    # Normalize both tracks to a common format for ffmpeg's multi-input.
    common = {"frame_rate": narration.frame_rate, "channels": 1, "sample_width": 2}
    narr = narration.set_frame_rate(common["frame_rate"]).set_channels(1)
    bgm_st = bgm.set_frame_rate(common["frame_rate"]).set_channels(1)

    target_len = len(narration)
    if len(bgm_st) < target_len:
        times = target_len // max(len(bgm_st), 1) + 1
        bgm_st = bgm_st * times
    bgm_st = bgm_st[:target_len]

    # Baseline BGM gain before ducking.
    bgm_st = bgm_st.apply_gain(bgm_gain_db)

    # sidechaincompress: key = narration, compressed = bgm.
    # The amix node is the final output — ffmpeg maps it automatically.
    # ``threshold`` is a 0-1 linear level (voice above this triggers the
    # duck); ``ratio`` scales how much BGM drops relative to voice excess.
    threshold = round(db_to_float(-40.0), 5)  # ≈0.01 — speech detection floor
    release = max(10, release_ms)
    attack = max(1, attack_ms)
    ratio = max(2.0, abs(duck_db) / 6.0)
    filter_graph = (
        "[0:a]asplit[n1][n2];"
        f"[1:a][n1]sidechaincompress=threshold={threshold}:"
        f"ratio={ratio}:attack={attack}:release={release}[ducked];"
        f"[n2][ducked]amix=inputs=2:duration=first:dropout_transition=0"
    )

    # Export narration & bgm to temp WAVs.
    import tempfile

    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmpdir:
        narr_path = str(Path(tmpdir) / "narr.wav")
        bgm_path = str(Path(tmpdir) / "bgm.wav")
        out_path = str(Path(tmpdir) / "out.wav")
        narr.export(narr_path, format="wav")
        bgm_st.export(bgm_path, format="wav")

        cmd = [
            ffmpeg,
            "-y",
            "-i",
            narr_path,
            "-i",
            bgm_path,
            "-filter_complex",
            filter_graph,
            out_path,
        ]

        try:
            # Deadline + process-tree kill: on timeout the child is terminated
            # so a hung ffmpeg cannot leak CPU or leave a zombie process.
            proc = run_ffmpeg_subprocess(
                cmd,
                timeout=_SIDECHAIN_TIMEOUT,
            )
        except Exception:  # noqa: BLE001
            return None
        if proc.returncode != 0:
            logger.debug("sidechaincompress failed: %s", proc.stderr)
            return None

        mixed = AudioSegment.from_file(out_path)

    # Trim/pad to exact narration length.
    if len(mixed) > target_len:
        mixed = mixed[:target_len]
    elif len(mixed) < target_len:
        mixed = mixed + AudioSegment.silent(duration=target_len - len(mixed))
    return mixed
