# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Emotion-to-prosody mapping for emotion-aware TTS.

v0.5.9: Maps beat emotion labels (intense/suspense/calm/twist/laughter)
to speed adjustment factors applied as post-processing on TTS output.

The speed factor is applied via pydub's frame-rate trick, which changes
both speed and pitch — this is intentional for emotion expression:
- intense  → faster + higher pitch (energetic)
- suspense → slower (deliberate, tense)
- calm     → slightly slower (relaxed)
- twist    → slightly faster (surprise)
- laughter → faster (upbeat)

For segments without an emotion label, no adjustment is applied (1.0x).
"""

from __future__ import annotations

from pydub import AudioSegment


# Emotion → speed multiplier. 1.0 = no change.
# Range: 0.85 (15% slower) to 1.12 (12% faster).
_EMOTION_SPEED: dict[str, float] = {
    "intense": 1.12,
    "suspense": 0.88,
    "calm": 0.94,
    "twist": 1.06,
    "laughter": 1.08,
}

# Maximum absolute speed deviation from 1.0 (safety clamp).
_MAX_SPEED_DEVIATION = 0.15


def emotion_to_speed(emotion: str | None) -> float:
    """Map an emotion label to a speed multiplier.

    Returns 1.0 for unknown or missing emotions. Output is clamped to
    ``[1.0 - _MAX_SPEED_DEVIATION, 1.0 + _MAX_SPEED_DEVIATION]``.
    """
    if not emotion or emotion not in _EMOTION_SPEED:
        return 1.0
    speed = _EMOTION_SPEED[emotion]
    return max(1.0 - _MAX_SPEED_DEVIATION, min(1.0 + _MAX_SPEED_DEVIATION, speed))


def apply_speed(audio: AudioSegment, speed: float) -> AudioSegment:
    """Apply speed change to an AudioSegment.

    Uses the frame-rate override trick: resample at a higher/lower rate,
    then set the frame rate back to the original. This changes both
    speed and pitch, which is desirable for emotion expression.

    For ``speed == 1.0`` the audio is returned unchanged.
    """
    if speed == 1.0 or audio.frame_rate == 0:
        return audio

    new_rate = int(audio.frame_rate * speed)
    if new_rate <= 0:
        return audio

    shifted = audio._spawn(audio.raw_data, overrides={"frame_rate": new_rate})
    return shifted.set_frame_rate(audio.frame_rate)


def map_segment_emotions(
    n_segments: int,
    beats_meta: list | None,
) -> list[str | None]:
    """Distribute beat-level emotions across segments proportionally.

    When the number of beats differs from the number of segments (common
    after Phase 2 expansion), emotions are distributed by proportional
    mapping: segment *i* gets the emotion of beat
    ``floor(i * n_beats / n_segments)``.

    Returns a list of ``n_segments`` emotion strings (or ``None`` when
    no beats_meta is available).
    """
    if not beats_meta or n_segments <= 0:
        return [None] * max(0, n_segments)

    emotions: list[str | None] = []
    for bm in beats_meta:
        if isinstance(bm, dict):
            emo = bm.get("emotion")
            emotions.append(emo if isinstance(emo, str) else None)
        else:
            emotions.append(None)

    # Filter out None emotions — if none have values, return all None
    if not any(emotions):
        return [None] * n_segments

    # Fill None emotions with the previous non-None value (forward fill)
    last_valid: str | None = None
    filled: list[str | None] = []
    for emo in emotions:
        if emo is not None:
            last_valid = emo
        filled.append(last_valid)

    n_beats = len(filled)
    result: list[str | None] = []
    for i in range(n_segments):
        beat_idx = min(n_beats - 1, int(i * n_beats / n_segments))
        result.append(filled[beat_idx])

    return result
