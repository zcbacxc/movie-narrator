# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scene merging, window clamping, and match diversity fallback helpers."""

from typing import List, Optional, Tuple

from ...models import Scene


def _merge_short_scenes(
    scenes: List[Scene],
    min_duration: float = 3.0,
) -> List[Scene]:
    """Merge consecutive scenes shorter than *min_duration* into their neighbour.

    Produces a new list of Scene objects with re-indexed sequential indices.
    Scenes that are already long enough are kept as-is.  Short scenes are
    merged into the *following* scene if it exists, otherwise into the
    *preceding* one.
    """
    if not scenes:
        return scenes

    merged: List[Scene] = []
    pending: Optional[Scene] = None

    for scene in scenes:
        duration = scene.end - scene.start
        if duration >= min_duration:
            if pending is not None:
                # Merge pending into this scene
                merged_scene = Scene(
                    index=0,  # re-indexed later
                    start=pending.start,
                    end=scene.end,
                )
                merged.append(merged_scene)
                pending = None
            else:
                merged.append(Scene(index=0, start=scene.start, end=scene.end))
        else:
            if pending is not None:
                # Extend pending
                pending = Scene(index=0, start=pending.start, end=scene.end)
            else:
                pending = Scene(index=0, start=scene.start, end=scene.end)

    # Flush any remaining pending scene
    if pending is not None:
        if merged:
            # Merge into the last scene
            last = merged[-1]
            merged[-1] = Scene(index=0, start=last.start, end=pending.end)
        else:
            merged.append(pending)

    # Re-index
    for i, s in enumerate(merged):
        s.index = i

    return merged


def _clamp_scene_window(
    scene_start: float,
    scene_end: float,
    narr_duration: float,
    video_start: float,
    video_end: float,
    clamp_min: float = 0.5,
    clamp_max: float = 3.0,
) -> Tuple[float, float]:
    """Adjust the source window so the speed factor stays within [clamp_min, clamp_max].

    Speed factor = src_duration / narr_duration.
    When the factor exceeds clamp_max (fast-forward), the window is shrunk.
    When it falls below clamp_min (slow-motion), the window is expanded.
    The window is centered on the original scene midpoint and clamped to
    [video_start, video_end].

    Returns:
        Adjusted (src_start, src_end).
    """
    src_duration = scene_end - scene_start
    if narr_duration <= 0:
        narr_duration = 0.1

    factor = src_duration / narr_duration

    if clamp_min <= factor <= clamp_max:
        return scene_start, scene_end

    # Target a duration that gives a factor at the clamp boundary.
    # factor = src_duration / narr_duration:
    #   factor > clamp_max → src too long (fast-forward) → shrink window
    #   factor < clamp_min → src too short (slow-mo)   → expand window
    if factor > clamp_max:
        target_src = narr_duration * clamp_max
    else:
        target_src = narr_duration * clamp_min

    # Center the new window on the original scene midpoint
    mid = (scene_start + scene_end) / 2.0
    half = target_src / 2.0
    new_start = mid - half
    new_end = mid + half

    # Clamp to video boundaries
    if new_start < video_start:
        new_start = video_start
        new_end = new_start + target_src
    if new_end > video_end:
        new_end = video_end
        new_start = new_end - target_src
        if new_start < video_start:
            new_start = video_start

    return new_start, new_end


def _apply_diversity(
    matched_clips: list, scenes: list, window: int = 3, max_reuse: int = 2
) -> tuple[int, list[dict]]:
    """Post-process matched clips to reduce consecutive scene reuse.

    If a scene index appears more than ``max_reuse`` times within a
    sliding window of ``window`` segments, swap the latest occurrence
    to the nearest unused scene.

    Returns:
        ``(swaps_count, swaps_log)`` where ``swaps_log`` is a list
        of ``{"segment_index": int, "old_scene": int, "new_scene": int}``
        dicts for auditability — downstream consumers can distinguish
        original embedding scores from post-swap scores.

        Only swaps ``scene_index`` / ``src_start`` / ``src_end`` — score
        and source remain unchanged (the match quality is not affected,
        just the footage selection).
    """
    if not matched_clips or len(scenes) <= 1:
        return 0, []

    swaps = 0
    swaps_log: list[dict] = []
    for i in range(len(matched_clips)):
        # Count scene reuse in the look-back window [i-window+1, i]
        win_start = max(0, i - window + 1)
        window_clips = matched_clips[win_start : i + 1]
        scene_counts: dict[int, int] = {}
        for mc in window_clips:
            scene_counts[mc.scene_index] = scene_counts.get(mc.scene_index, 0) + 1

        current_scene = matched_clips[i].scene_index
        if scene_counts.get(current_scene, 0) <= max_reuse:
            continue  # within limit

        # Find nearest unused scene (by index proximity)
        used_in_window = set(mc.scene_index for mc in window_clips)
        candidates = [s for s in scenes if s.index not in used_in_window]
        if not candidates:
            continue  # all scenes used in window, nothing to swap

        # Pick the nearest scene by index distance
        best_scene = min(candidates, key=lambda s: abs(s.index - current_scene))

        # Re-clamp the new scene's window to fit narration duration
        narr_duration = matched_clips[i].narr_end - matched_clips[i].narr_start
        clamped_start, clamped_end = _clamp_scene_window(
            best_scene.start,
            best_scene.end,
            narr_duration,
            video_start=0.0,
            video_end=max(s.end for s in scenes),
            clamp_min=0.85,
            clamp_max=1.25,
        )
        old_scene = matched_clips[i].scene_index
        matched_clips[i].scene_index = best_scene.index
        matched_clips[i].src_start = clamped_start
        matched_clips[i].src_end = clamped_end
        swaps += 1
        swaps_log.append(
            {
                "segment_index": matched_clips[i].segment_index,
                "old_scene": old_scene,
                "new_scene": best_scene.index,
            }
        )

    return swaps, swaps_log
