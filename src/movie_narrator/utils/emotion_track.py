# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""EmotionTrack — unified time-axis × emotion × intensity value object.

G5: The narration's emotion was previously consumed in three independent
places (``pipeline/tts.py`` prosody, ``pipeline/bgm.py`` selection and zone
transitions), each reading ``beats_meta`` and carrying its own intensity
table. :class:`EmotionTrack` aggregates the beat emotions into one queryable
axis and owns the canonical intensity tables so every consumer reads from a
single source. It is a pure value object — no ``Context`` field semantics or
default behaviour change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional

# Canonical emotion vocabulary (v1.0.0). Produced by the script step and
# consumed by prosody / BGM / transitions. Kept here as documentation of the
# shared axis; unknown labels are tolerated (treated as intensity-neutrals).
EMOTIONS = frozenset({"suspense", "laughter", "intense", "calm", "twist"})

# Per-emotion intensity tables. Each is a different numeric profile of the
# same emotion axis — kept together so the axis is modelled in one place.
# Values are preserved verbatim from the historical per-consumer tables.
EMOTION_ENERGY: Dict[str, float] = {
    "intense": 0.9,
    "suspense": 0.7,
    "twist": 0.6,
    "laughter": 0.5,
    "calm": 0.2,
}

EMOTION_SPEED: Dict[str, float] = {
    "intense": 1.12,
    "suspense": 0.88,
    "calm": 0.94,
    "twist": 1.06,
    "laughter": 1.08,
}

EMOTION_BGM_GAIN_DB: Dict[str, float] = {
    "intense": 2.0,
    "suspense": -1.0,
    "calm": -3.0,
    "twist": 1.0,
    "laughter": 1.5,
}


@dataclass(frozen=True)
class EmotionTrack:
    """A queryable emotion timeline derived from ``beats_meta``.

    Attributes:
        emotions: One emotion label per beat (``None`` when absent / not a
            string). Order matches the beats order in ``beats_meta``.
    """

    emotions: List[Optional[str]] = field(default_factory=list)

    @classmethod
    def from_beats(cls, beats_meta: Optional[list]) -> "EmotionTrack":
        """Build a track from a ``beats_meta`` list.

        Non-dict entries and non-string emotions are mapped to ``None``
        (same leniency as the historical consumers — the script step
        already validated labels against :data:`EMOTIONS`).
        """
        emotions: List[Optional[str]] = []
        for bm in beats_meta or []:
            if isinstance(bm, dict):
                emo = bm.get("emotion")
                emotions.append(emo if isinstance(emo, str) else None)
            else:
                emotions.append(None)
        return cls(emotions=emotions)

    @classmethod
    def from_metadata(cls, metadata: Optional[Mapping[str, Any]]) -> "EmotionTrack":
        """Build a track from a ``Context.metadata`` mapping."""
        return cls.from_beats(metadata.get("beats_meta") if metadata else None)

    @property
    def empty(self) -> bool:
        """True when no beat carries a usable emotion."""
        return not any(self.emotions)

    def emotion(self, index: int) -> Optional[str]:
        """The raw emotion at a beat index (``None`` when out of range)."""
        if 0 <= index < len(self.emotions):
            return self.emotions[index]
        return None

    def segment_emotions(self, n_segments: int) -> List[Optional[str]]:
        """Distribute beat emotions across ``n_segments`` proportionally.

        Semantics are identical to the historical ``map_segment_emotions``:
        empty tracks yield all ``None``; ``None`` labels are forward-filled
        with the previous non-``None`` value; segment *i* gets the emotion
        of beat ``floor(i * n_beats / n_segments)``.
        """
        if not self.emotions or n_segments <= 0:
            return [None] * max(0, n_segments)

        if not any(self.emotions):
            return [None] * n_segments

        last_valid: Optional[str] = None
        filled: List[Optional[str]] = []
        for emo in self.emotions:
            if emo is not None:
                last_valid = emo
            filled.append(last_valid)

        n_beats = len(filled)
        result: List[Optional[str]] = []
        for i in range(n_segments):
            beat_idx = min(n_beats - 1, int(i * n_beats / n_segments))
            result.append(filled[beat_idx])
        return result

    def distribution(self) -> Optional[Dict[str, float]]:
        """Normalised emotion fraction distribution, or ``None`` when empty.

        Considers all emotions (not just a dominant one) so BGM matching can
        account for a multi-emotion arc.
        """
        counts: Dict[str, int] = {}
        for emo in self.emotions:
            if emo is None:
                continue
            counts[emo] = counts.get(emo, 0) + 1
        if not counts:
            return None
        total = sum(counts.values())
        return {e: c / total for e, c in counts.items()}

    def weighted_energy(self) -> float:
        """Weighted-average perceived energy across the emotion distribution."""
        dist = self.distribution()
        if not dist:
            return 0.0
        return sum(EMOTION_ENERGY.get(e, 0.5) * f for e, f in dist.items())

    @staticmethod
    def energy(emotion: Optional[str]) -> float:
        """Perception energy (0.0-1.0) for a single emotion."""
        return EMOTION_ENERGY.get(emotion, 0.5) if emotion else 0.5

    @staticmethod
    def speed(emotion: Optional[str]) -> float:
        """TTS speed multiplier for a single emotion (1.0 = no change)."""
        return EMOTION_SPEED.get(emotion, 1.0) if emotion else 1.0

    @staticmethod
    def bgm_gain_db(emotion: Optional[str]) -> float:
        """BGM zone gain (dB) for a single emotion."""
        return EMOTION_BGM_GAIN_DB.get(emotion, 0.0) if emotion else 0.0
