# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scene captioning / labelling from transcripts."""

from typing import List, Optional, Tuple

from ...models import Scene


def _build_scene_captions(
    scenes: List[Scene],
    transcript: Optional[List[dict]],
) -> List[Tuple[str, bool]]:
    """Build semantic scene labels from WhisperX transcript.

    Each scene gets the concatenated text of all transcript segments
    that overlap with the scene's time range. Falls back to a
    deterministic placeholder when no transcript is available or a
    scene has no overlapping speech.

    Returns
    -------
    List[Tuple[str, bool]]
        Each tuple is ``(label, is_fake)`` where ``is_fake=True`` marks
        a placeholder label (no real transcript). Callers use the flag
        instead of string-pattern matching to detect fake captions
        (eliminates fragile startswith("scene ") heuristic).
    """
    if not transcript:
        return [(_build_scene_label(s.index, s.start, s.end), True) for s in scenes]

    labels: List[Tuple[str, bool]] = []
    for scene in scenes:
        # Collect transcript segments overlapping this scene
        overlapping_texts = []
        for seg in transcript:
            # Overlap test: seg.start < scene.end AND seg.end > scene.start
            if seg["start"] < scene.end and seg["end"] > scene.start:
                overlapping_texts.append(seg["text"])

        if overlapping_texts:
            # Join with space, truncate to keep embedding quality high
            caption = " ".join(overlapping_texts)[:200]
            labels.append((caption, False))
        else:
            # No speech in this scene — use placeholder
            labels.append((_build_scene_label(scene.index, scene.start, scene.end), True))

    return labels


def _build_scene_label(scene_index: int, start: float, end: float) -> str:
    """Best-effort scene caption used as the embedding target text.

    Until a real ML caption pipeline ships, this produces a deterministic label
    from the scene index and time span so the embedding re-rank path is
    exercisable without external services.
    """
    return f"scene {scene_index} from {start:.1f}s to {end:.1f}s"
