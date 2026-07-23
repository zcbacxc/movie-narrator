import json
import logging
import hashlib
import os
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..models import Context, MatchedClip, Scene, StepResult, TimedSegment
from ..utils.optional_deps import probe

_EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"  # default, overridden by ctx.metadata

logger = logging.getLogger(__name__)


# ── Scene captioning via WhisperX ──────────────────────────


def _video_audio_hash(video_path: str) -> str:
    """Lightweight cache key from file stat — avoids reading the full
    video file so cache hits incur zero I/O overhead.

    Uses ``mtime`` + ``size``: collisions are practically impossible
    for this use-case (same values = file wasn't re-encoded).
    """
    s = os.stat(video_path)
    raw = f"{s.st_mtime}_{s.st_size}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _cache_key(video_path: str, model_name: str, language: str) -> str:
    """Build a cache key that includes model and language.

    Without model/language in the key, switching from small/zh to
    medium/en would silently reuse the wrong transcript.
    """
    file_hash = _video_audio_hash(video_path)
    return f"transcript_{file_hash}_{model_name}_{language}.json"


def _transcribe_video_audio(
    video_path: str,
    output_dir: Path,
    device: str = "cpu",
    model_name: str = "medium",
    language: str = "zh",
) -> Optional[List[dict]]:
    """Transcribe the video's audio track for scene-level captions.

    Tries WhisperX first (word-level alignment). If WhisperX is not
    importable or fails (common on Windows CPU due to k2-fsa missing),
    falls back to faster-whisper (CTranslate2, no pyannote/k2-fsa deps).

    Returns a list of ``{"start", "end", "text"}`` dicts, or ``None``
    when both backends are unavailable or transcription fails.
    Results are cached per video file hash + model + language.
    """
    cache_path = output_dir / _cache_key(video_path, model_name, language)

    # Cache hit
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass  # corrupt cache, re-transcribe

    # Try WhisperX first (preserves forced alignment if available)
    try:
        import whisperx

        audio = whisperx.load_audio(video_path)
        model = whisperx.load_model(model_name, device=device)
        result = model.transcribe(audio, language=language)

        segments = []
        if result and "segments" in result:
            for wseg in result["segments"]:
                start = wseg.get("start", 0.0)
                end = wseg.get("end", 0.0)
                text = wseg.get("text", "").strip()
                if text:
                    segments.append({"start": start, "end": end, "text": text})

        if segments:
            cache_path.write_text(
                json.dumps(segments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return segments if segments else None
    except Exception as wx_err:
        logger.warning("WhisperX video transcription failed: %s", wx_err)
        # Fall through to faster-whisper

    # Fallback: faster-whisper (works on Windows CPU where k2-fsa missing)
    try:
        from ._align_backend import transcribe_with_faster_whisper
        segments = transcribe_with_faster_whisper(
            audio_path=video_path,
            device=device,
            language=language,
            # Use "small" for faster-whisper fallback (int8 on CPU);
            # WhisperX's "medium" would be too slow without GPU.
            model_size="small" if model_name == "medium" else model_name,
        )
        if segments:
            cache_path.write_text(
                json.dumps(segments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return segments if segments else None
    except Exception as fw_err:
        logger.warning("faster-whisper video transcription failed: %s", fw_err)
        return None


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
        (F2 fix — eliminates fragile startswith("scene ") heuristic).
    """
    if not transcript:
        return [
            (_build_scene_label(s.index, s.start, s.end), True)
            for s in scenes
        ]

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
            labels.append(
                (_build_scene_label(scene.index, scene.start, scene.end), True)
            )

    return labels


def _build_scene_label(scene_index: int, start: float, end: float) -> str:
    """Best-effort scene caption used as the embedding target text.

    Until a real ML caption pipeline ships, this produces a deterministic label
    from the scene index and time span so the embedding re-rank path is
    exercisable without external services.
    """
    return f"scene {scene_index} from {start:.1f}s to {end:.1f}s"


import functools


@functools.lru_cache(maxsize=2)
def _load_embedding_model(model_name: str):
    """Load and cache a SentenceTransformer model.

    Loading takes 1-3 seconds and ~200MB; caching avoids re-loading
    when _embed_texts is called twice per match_clips run (scene labels
    + narration texts).
    """
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model_name)


def _embed_texts(texts: List[str], model_name: str = _EMBEDDING_MODEL_NAME):
    """Encode a list of strings to L2-normalized vectors.

    Returns ``None`` when sentence-transformers is unavailable or fails at
    runtime, so the caller can fall back to the heuristic shape.
    """
    model = _load_embedding_model(model_name)
    vectors = model.encode(texts)
    import numpy as np

    arr = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return arr / norms


def _cosine_top1(target_vec, candidate_matrix) -> int:
    """Return index of the candidate with the highest cosine similarity.

    ``target_vec`` and row vectors in ``candidate_matrix`` are assumed L2-normalized,
    so cosine reduces to dot product. Returns -1 if the matrix is empty.
    """
    if candidate_matrix.size == 0:
        return -1
    sims = candidate_matrix @ target_vec
    return int(sims.argmax())


def _cosine_topk(
    target_vec, candidate_matrix, k: int = 5
) -> list[tuple[int, float]]:
    """Return top-K candidates as ``(local_index, score)`` sorted by score descending.

    ``local_index`` is the row index within ``candidate_matrix``.
    Returns an empty list if the matrix is empty or k <= 0.
    """
    if candidate_matrix.size == 0 or k <= 0:
        return []
    import numpy as np

    sims = candidate_matrix @ target_vec
    k = min(k, len(sims))
    # argpartition for O(n) top-K, then sort the K winners
    top_indices = np.argpartition(sims, -k)[-k:]
    top_indices = top_indices[np.argsort(sims[top_indices])[::-1]]
    return [(int(i), float(sims[i])) for i in top_indices]


def _greedy_topk_assign(
    narration_vecs,
    scene_vecs,
    scenes: List[Scene],
    topk: int = 5,
    reuse_penalty: float = 0.15,
    reuse_window: int = 3,
    use_weighted_acts: bool = False,
    act_assignments: Optional[list[int]] = None,
    act_scenes: Optional[list[list[Scene]]] = None,
    act_weights: Optional[list[float]] = None,
) -> list[tuple[int, float, str]]:
    """Greedy top-K assignment with order-backtrack reuse penalty (EP3).

    For each narration segment, computes top-K candidate scenes from the
    embedding similarity, then picks the candidate with the highest
    *adjusted* score — where scenes used in the last ``reuse_window``
    segments get a ``reuse_penalty`` deduction.

    Returns a list of ``(scene_index, score, source)`` per segment.
    ``source`` is ``"embedding_topk"`` when top-K ran, ``"embedding_top1"``
    when top-K is disabled (k <= 1).
    """
    import numpy as np

    n_seg = len(narration_vecs)
    n_scenes = len(scenes)
    results: list[tuple[int, float, str]] = []
    recent_usage: list[int] = []  # scene indices used recently (chronological)

    source = "embedding_topk" if topk > 1 else "embedding_top1"

    for i in range(n_seg):
        # Determine candidate pool
        if use_weighted_acts and act_assignments and act_scenes:
            act_idx = act_assignments[i]
            cand_indices = _get_act_candidate_indices(
                act_idx, len(act_weights), act_scenes
            )
            cand_indices = [idx for idx in cand_indices if idx < n_scenes]
            if not cand_indices:
                cand_indices = list(range(n_scenes))
        else:
            cand_indices = list(range(n_scenes))

        cand_vecs = scene_vecs[np.array(cand_indices)]

        if topk > 1:
            top_candidates = _cosine_topk(narration_vecs[i], cand_vecs, k=topk)
        else:
            # Fallback to top-1
            best_local = _cosine_top1(narration_vecs[i], cand_vecs)
            if best_local < 0:
                results.append((0, 1.0, source))
                continue
            top_candidates = [(best_local, float(cand_vecs[best_local] @ narration_vecs[i]))]

        if not top_candidates:
            results.append((0, 1.0, source))
            continue

        # Adjust scores with reuse penalty
        recent_set = set(recent_usage[-reuse_window:]) if recent_usage else set()
        # Initialise from the first candidate's *adjusted* score (not raw)
        # so that the penalty on a recently-used top-1 candidate can actually
        # let a lower-ranked candidate win.
        first_global = cand_indices[top_candidates[0][0]]
        first_raw = top_candidates[0][1]
        best_global_idx = first_global
        best_adjusted = first_raw - reuse_penalty if first_global in recent_set else first_raw
        best_raw = first_raw

        for local_idx, raw_score in top_candidates:
            global_idx = cand_indices[local_idx]
            adjusted = raw_score
            if global_idx in recent_set:
                adjusted -= reuse_penalty
            if adjusted > best_adjusted:
                best_adjusted = adjusted
                best_global_idx = global_idx
                best_raw = raw_score

        results.append((best_global_idx, best_raw, source))
        recent_usage.append(best_global_idx)

    return results


# ── Scene merging ───────────────────────────────────────────


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


# ── Speed clamp ─────────────────────────────────────────────


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

    Returns adjusted (src_start, src_end).
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


# ── Main match logic ────────────────────────────────────────


def _apply_diversity(
    matched_clips: list, scenes: list, window: int = 3, max_reuse: int = 2
) -> tuple[int, list[dict]]:
    """Post-process matched clips to reduce consecutive scene reuse (WP3).

    If a scene index appears more than ``max_reuse`` times within a
    sliding window of ``window`` segments, swap the latest occurrence
    to the nearest unused scene.

    Returns ``(swaps_count, swaps_log)`` where ``swaps_log`` is a list
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
        swaps_log.append({
            "segment_index": matched_clips[i].segment_index,
            "old_scene": old_scene,
            "new_scene": best_scene.index,
        })

    return swaps, swaps_log


# ── EP1: Act-weighted timeline partitioning ────────────────


_DEFAULT_ACT_WEIGHTS = [0.15, 0.25, 0.40, 0.20]


def _partition_scenes_by_act(
    scenes: List[Scene],
    n_acts: int = 4,
) -> List[List[Scene]]:
    """Partition scenes into *n_acts* equal-time buckets.

    Returns a list of scene lists, one per act.  Acts with no scenes
    get an empty list — the caller is responsible for fallback.
    """
    if not scenes:
        return [[] for _ in range(n_acts)]

    scene_start = min(s.start for s in scenes)
    scene_end = max(s.end for s in scenes)
    span = scene_end - scene_start
    if span <= 0:
        return [list(scenes)] + [[] for _ in range(n_acts - 1)]

    bucket_size = span / n_acts
    buckets: List[List[Scene]] = [[] for _ in range(n_acts)]
    for s in scenes:
        idx = min(n_acts - 1, int((s.start - scene_start) / bucket_size))
        buckets[idx].append(s)
    return buckets


def _assign_segments_to_acts(
    n_segments: int,
    weights: List[float],
) -> List[int]:
    """Assign *n_segments* narration segments to acts by *weights*.

    Returns a list of act indices (0-based), one per segment, in
    chronological order.  Segment counts per act are proportional to
    weights, adjusted to sum exactly to *n_segments*.
    """
    n_acts = len(weights)
    total = sum(weights)
    if total <= 0:
        weights = list(_DEFAULT_ACT_WEIGHTS)
        total = sum(weights)
    norm = [w / total for w in weights]

    # Raw counts (may not sum to n_segments due to rounding)
    counts = [max(1, round(n_segments * w)) for w in norm]

    # Adjust to sum exactly to n_segments
    while sum(counts) > n_segments:
        max_idx = counts.index(max(counts))
        counts[max_idx] -= 1
    while sum(counts) < n_segments:
        max_idx = counts.index(max(counts))
        counts[max_idx] += 1

    # Build chronological assignment list
    assignments: List[int] = []
    for act_idx, count in enumerate(counts):
        assignments.extend([act_idx] * count)
    return assignments


def _get_act_candidate_indices(
    act_idx: int,
    n_acts: int,
    act_scenes: List[List[Scene]],
    allow_overflow: bool = True,
) -> List[int]:
    """Return global scene indices for act *act_idx* + optional adjacent overflow.

    When the target act has no scenes, expands search to all acts.
    """
    # Start with the act's own scenes
    indices = [s.index for s in act_scenes[act_idx]]

    if not indices and allow_overflow:
        # Act is empty — fall back to all scenes
        for bucket in act_scenes:
            indices.extend(s.index for s in bucket)
        return indices

    if allow_overflow and len(act_scenes) > 1:
        # Add adjacent acts (±1) for overflow candidates
        for delta in (-1, 1):
            neighbor = act_idx + delta
            if 0 <= neighbor < len(act_scenes):
                indices.extend(s.index for s in act_scenes[neighbor])

    return indices


def match_clips(ctx: Context) -> Context:
    if not ctx.source_video_path:
        ctx.status.match = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no source video"
        return ctx
    if ctx.status.scene == "disabled":
        ctx.status.match = "disabled"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "scene disabled"
        return ctx
    if not ctx.scenes:
        ctx.status.match = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no scenes"
        return ctx
    if not ctx.timed_segments:
        ctx.status.match = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no timed segments"
        return ctx

    min_score = ctx.metadata.get("match_min_score", 0.25)
    clamp_min = ctx.metadata.get("match_speed_clamp_min", 0.85)
    clamp_max = ctx.metadata.get("match_speed_clamp_max", 1.25)
    merge_min = ctx.metadata.get("scene_merge_min_duration", 2.0)
    drop_min = ctx.metadata.get("match_drop_scene_min_duration", 0.4)
    output_dir = Path(ctx.output_dir)

    try:
        return _match_clips_impl(
            ctx, min_score, clamp_min, clamp_max, merge_min, drop_min, output_dir
        )
    except Exception as e:
        ctx.status.match = "failed"
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        return ctx


def _match_clips_impl(
    ctx: Context,
    min_score: float,
    clamp_min: float,
    clamp_max: float,
    merge_min: float,
    drop_min: float,
    output_dir: Path,
) -> Context:
    # Optionally merge short scenes to reduce extreme speed factors
    scenes = ctx.scenes
    scenes_in = len(ctx.scenes)  # F1: original scene count
    if merge_min > 0:
        scenes = _merge_short_scenes(scenes, min_duration=merge_min)
        ctx.services.console.debug(
            f"  scene merge: {len(ctx.scenes)} -> {len(scenes)} scenes (min={merge_min}s)"
        )

    # Drop tiny scenes (e.g. <0.4s) that produce jarring sub-frame cuts.
    # If filtering would remove *all* scenes, keep the merged list as a
    # last-resort so matching still produces output.
    scenes_after_merge = len(scenes)  # F1: count after merge, before drop
    if drop_min > 0 and scenes:
        filtered = [s for s in scenes if (s.end - s.start) >= drop_min]
        if filtered:
            scenes = filtered
            # Re-index after drop so scene indices are sequential.
            for i, s in enumerate(scenes):
                s.index = i

    # Compute total scene span
    scene_start = min(s.start for s in scenes)
    scene_end = max(s.end for s in scenes)
    scene_span = scene_end - scene_start

    first_start = ctx.timed_segments[0].start
    last_end = ctx.timed_segments[-1].end
    narr_span = last_end - first_start

    # ── EP1: Act-weighted timeline partitioning ─────────────
    # When match_timeline_mode="weighted_acts", partition scenes into 4
    # equal-time buckets and assign narration segments to acts by weight.
    # Each segment's heuristic midpoint is mapped within its assigned
    # bucket (not the full timeline), and embedding candidates are
    # restricted to the bucket (+ adjacent overflow).
    timeline_mode = ctx.metadata.get("match_timeline_mode", "uniform")
    act_weights = ctx.metadata.get("match_act_weights", list(_DEFAULT_ACT_WEIGHTS))
    use_weighted_acts = (
        timeline_mode == "weighted_acts"
        and len(scenes) >= 8
        and len(ctx.timed_segments) >= 4
    )
    # EP3: top-K rerank params
    topk = ctx.metadata.get("match_topk", 5)
    reuse_penalty = ctx.metadata.get("match_topk_reuse_penalty", 0.15)
    if use_weighted_acts:
        act_scenes = _partition_scenes_by_act(scenes, n_acts=len(act_weights))
        act_assignments = _assign_segments_to_acts(
            len(ctx.timed_segments), act_weights
        )
        ctx.services.console.debug(
            f"  EP1 weighted_acts: {len(act_weights)} acts, "
            f"segments per act: {[act_assignments.count(a) for a in range(len(act_weights))]}"
        )
        # Pre-compute act -> segment indices map (O(n) once, not O(n²) per segment)
        act_seg_map: dict[int, list[int]] = {}
        for seg_idx, act_i in enumerate(act_assignments):
            act_seg_map.setdefault(act_i, []).append(seg_idx)
    else:
        act_scenes = None
        act_assignments = None

    # --- Heuristic baseline -------------------------------------------------
    # Map each narration midpoint proportionally onto the scene span, pick the
    # containing scene window. Produces a stable candidate per segment with
    # score=1.0 (plan T14 normative rule).
    heuristic = []
    for i, seg in enumerate(ctx.timed_segments):
        if use_weighted_acts:
            # EP1: map within assigned act bucket
            act_idx = act_assignments[i]
            bucket = act_scenes[act_idx]
            if not bucket:
                # Empty act — fall back to all scenes
                bucket = scenes
            b_start = min(s.start for s in bucket)
            b_end = max(s.end for s in bucket)
            b_span = b_end - b_start

            # Position within this segment's slot in the bucket
            act_segs = act_seg_map[act_idx]
            pos_in_act = act_segs.index(i)
            n_in_act = len(act_segs)
            if n_in_act > 1:
                local_ratio = (pos_in_act + 0.5) / n_in_act
            else:
                local_ratio = 0.5

            src_mid = b_start + local_ratio * b_span if b_span > 0 else b_start

            # Find containing scene within bucket
            containing = None
            for scene in bucket:
                if scene.start <= src_mid <= scene.end:
                    containing = scene
                    break
            if containing is None:
                containing = bucket[0]
        else:
            # Original uniform mapping
            narr_mid = (seg.start + seg.end) / 2.0
            if narr_span > 0:
                ratio = (narr_mid - first_start) / narr_span
                src_mid = scene_start + ratio * scene_span
            else:
                src_mid = scene_start

            containing = None
            for scene in scenes:
                if scene.start <= src_mid <= scene.end:
                    containing = scene
                    break
            if containing is None:
                containing = scenes[0]

        heuristic.append(
            {
                "segment_index": i,
                "text": seg.text,
                "narr_start": seg.start,
                "narr_end": seg.end,
                "scene_index": containing.index,
                "src_start": containing.start,
                "src_end": containing.end,
            }
        )

    # --- Optional embedding re-rank ----------------------------------------
    # F1: initialize tracking variables for match_summary (defined in all
    # branches below, but referenced at function end after the try/except).
    final = []
    scene_captions: List[Tuple[str, bool]] = []
    usable_label_ratio: float = 0.0
    raw_scores: List[float] = []
    low_score_fallback_count = 0
    transcript: Optional[List[dict]] = None
    st_ok, st_hint = probe("sentence_transformers")
    if st_ok and len(scenes) > 1:
        try:
            # Try WhisperX scene captioning first
            transcript = None
            wx_ok, wx_hint = probe("whisperx")
            if not wx_ok:
                ctx.services.console.inline_warn(
                    f"WhisperX not available ({wx_hint}); using fallback scene labels. "
                    f"Install with: pip install 'movie-narrator[ml]'"
                )
            elif ctx.source_video_path:
                wx_device = ctx.metadata.get("whisperx_device", "cpu")
                wx_model = ctx.metadata.get("whisperx_model", "medium")
                wx_lang = ctx.metadata.get("whisperx_language", "zh")
                ctx.services.console.debug(
                    f"  WhisperX scene captioning: device={wx_device} model={wx_model} lang={wx_lang}"
                )
                transcript = _transcribe_video_audio(
                    ctx.source_video_path,
                    output_dir,
                    device=wx_device,
                    model_name=wx_model,
                    language=wx_lang,
                )
                if transcript:
                    ctx.services.console.debug(
                        f"  scene captions: {len(transcript)} transcript segments "
                        f"-> {len(scenes)} scenes"
                    )
                else:
                    ctx.services.console.inline_warn(
                        "WhisperX transcription returned no results; using fallback scene labels"
                    )

            scene_captions = _build_scene_captions(scenes, transcript)

            # ── MS-02: Truth in match (Q-M1) ──────────────
            # Detect fake captions (placeholder labels without real transcript).
            # If too many scenes have fake captions, embedding re-rank is
            # meaningless — it's matching narration against "scene 0 from
            # 0.0s to 15.0s" strings that carry no semantic information.
            # Threshold: if >70% of labels are fake, force heuristic.
            #
            # F2 fix: use the is_fake flag from _build_scene_captions
            # instead of fragile string-pattern matching. This eliminates
            # the implicit dependency on the "scene {i} from {s1}s to {s2}s"
            # label template — if the template changes, the flag still works.
            fake_count = sum(1 for _, is_fake in scene_captions if is_fake)
            fake_ratio = fake_count / len(scene_captions) if scene_captions else 1.0
            usable_label_ratio = 1.0 - fake_ratio
            if fake_ratio > 0.7:
                ctx.services.console.inline_warn(
                    f"Scene captions are {fake_ratio:.0%} placeholder labels "
                    f"({fake_count}/{len(scene_captions)} scenes have no real transcript). "
                    f"Embedding re-rank would be misleading — forcing heuristic match. "
                    f"Install WhisperX with: pip install 'movie-narrator[ml]'"
                )
                ctx.metadata["match_captions_fake"] = True
                final = [(h, 1.0, None, "heuristic") for h in heuristic]
            else:
                ctx.metadata["match_captions_fake"] = False
                emb_model = ctx.metadata.get("embedding_model_name", _EMBEDDING_MODEL_NAME)
                # F2: extract labels from (label, is_fake) tuples
                scene_labels = [label for label, _ in scene_captions]
                scene_vecs = _embed_texts(scene_labels, emb_model)
                narration_vecs = _embed_texts([seg.text for seg in ctx.timed_segments], emb_model)

                # EP3: greedy top-K assignment with reuse penalty
                topk_results = _greedy_topk_assign(
                    narration_vecs=narration_vecs,
                    scene_vecs=scene_vecs,
                    scenes=scenes,
                    topk=topk,
                    reuse_penalty=reuse_penalty,
                    reuse_window=ctx.metadata.get("match_diversity_window", 3),
                    use_weighted_acts=use_weighted_acts,
                    act_assignments=act_assignments if use_weighted_acts else None,
                    act_scenes=act_scenes if use_weighted_acts else None,
                    act_weights=act_weights if use_weighted_acts else None,
                )

                for i, (scene_idx, score, source) in enumerate(topk_results):
                    best_scene = scenes[scene_idx]
                    final.append((heuristic[i], score, best_scene, source))
                    # F1: collect raw embedding score (before low-score
                    # fallback overrides it to 1.0). Lets match_summary
                    # distinguish "matched well" from "matched poorly".
                    raw_scores.append(score)
        except Exception as e:
            ctx.services.console.inline_warn(
                f"embedding re-rank unavailable ({e}); using heuristic"
            )
            final = [(h, 1.0, None, "heuristic") for h in heuristic]
    else:
        final = [(h, 1.0, None, "heuristic") for h in heuristic]

    # --- Build matched clips with speed clamp -------------------------------
    matched_clips = []
    video_total_duration = scene_end  # total video duration for boundary clamping

    for h, score, best_scene, source in final:
        scene_obj = best_scene if best_scene is not None else next(
            s for s in scenes if s.index == h["scene_index"]
        )
        if score < min_score:
            # Embedding score too low — fall back to heuristic for this segment
            # instead of dropping it entirely. Dropping causes missing video
            # footage for that narration segment.
            ctx.services.console.debug(
                f"  segment {h['segment_index']}: embedding score {score:.3f} < "
                f"min_score {min_score:.3f}; falling back to heuristic"
            )
            scene_obj = next(s for s in scenes if s.index == h["scene_index"])
            source = "heuristic"
            score = 1.0
            low_score_fallback_count += 1  # F1: count low-score fallbacks

        narr_duration = h["narr_end"] - h["narr_start"]
        # Apply speed clamp: adjust src_start/src_end so factor stays in [clamp_min, clamp_max]
        clamped_start, clamped_end = _clamp_scene_window(
            scene_obj.start,
            scene_obj.end,
            narr_duration,
            video_start=0.0,
            video_end=video_total_duration,
            clamp_min=clamp_min,
            clamp_max=clamp_max,
        )

        matched_clips.append(
            MatchedClip(
                segment_index=h["segment_index"],
                text=h["text"],
                narr_start=h["narr_start"],
                narr_end=h["narr_end"],
                src_start=clamped_start,
                src_end=clamped_end,
                score=score,
                scene_index=scene_obj.index,
                source=source,
            )
        )

    ctx.matched_clips = matched_clips

    # ── WP3: Diversity post-processing ──────────────────────
    # Prevent consecutive scene reuse: if the same scene index appears
    # more than match_max_scene_reuse times within match_diversity_window
    # segments, swap later occurrences to the nearest unused scene.
    diversity_swaps, diversity_swaps_log = _apply_diversity(
        matched_clips, scenes,
        window=ctx.metadata.get("match_diversity_window", 3),
        max_reuse=ctx.metadata.get("match_max_scene_reuse", 2),
    )

    # Log speed factor stats + collect for match_summary (F1)
    speed_factors: List[float] = []
    if matched_clips:
        for mc in matched_clips:
            narr_dur = mc.narr_end - mc.narr_start
            if narr_dur > 0:
                speed_factors.append((mc.src_end - mc.src_start) / narr_dur)
        if speed_factors:
            ctx.services.console.debug(
                f"  speed factors: min={min(speed_factors):.2f}x max={max(speed_factors):.2f}x "
                f"avg={sum(speed_factors)/len(speed_factors):.2f}x (clamp={clamp_min}~{clamp_max}x)"
            )

    matches_path = output_dir / "matches.json"
    matches_path.write_text(
        json.dumps(
            [m.model_dump() for m in matched_clips], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )

    # ── F1: match_summary for metadata.json (full schema) ──────
    # Records the match quality breakdown so L2 hand-test can verify
    # the main path isn't "全 heuristic 糊弄" (O9/O10 in checklist).
    # Schema per CORE_ENGINE_TREATMENT_PLAN §5.2.3.
    # EP3: sources can be "embedding_topk", "embedding_top1", or "heuristic"
    embedding_count = sum(
        1 for mc in matched_clips
        if mc.source in ("embedding", "embedding_topk", "embedding_top1")
    )
    heuristic_count = sum(1 for mc in matched_clips if mc.source == "heuristic")
    topk_count = sum(1 for mc in matched_clips if mc.source == "embedding_topk")
    top1_count = sum(1 for mc in matched_clips if mc.source == "embedding_top1")
    total = len(matched_clips)

    # score stats: only for source==embedding clips that were adopted
    # (i.e. did NOT fall back to heuristic due to low score)
    adopted_embedding_scores = [
        mc.score for mc in matched_clips
        if mc.source in ("embedding", "embedding_topk", "embedding_top1")
    ]

    def _stats(values: List[float]) -> Optional[dict]:
        if not values:
            return None
        return {
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "avg": round(sum(values) / len(values), 4),
        }

    def _stats_with_n(values: List[float]) -> Optional[dict]:
        if not values:
            return None
        return {
            "min": round(min(values), 4),
            "max": round(max(values), 4),
            "avg": round(sum(values) / len(values), 4),
            "n": len(values),
        }

    # Determine degraded_reason
    degraded_reason: Optional[str] = None
    if ctx.metadata.get("match_captions_fake"):
        degraded_reason = "fake_captions"
    elif heuristic_count == total and total > 0:
        degraded_reason = "all_heuristic"

    ctx.metadata["match_summary"] = {
        "version": 1,
        "status": "success",
        "segments": total,
        "scenes_in": scenes_in,
        "scenes_after_merge": scenes_after_merge,
        "scenes_after_drop": len(scenes),
        "merge_min_duration": merge_min,
        "drop_min_duration": drop_min,
        "min_score": min_score,
        "speed_clamp": [clamp_min, clamp_max],
        "source_counts": {
            "embedding": embedding_count,
            "embedding_topk": topk_count,
            "embedding_top1": top1_count,
            "heuristic": heuristic_count,
        },
        "heuristic_ratio": round(heuristic_count / total, 4) if total else 1.0,
        "embedding_ratio": round(embedding_count / total, 4) if total else 0.0,
        "score": _stats(adopted_embedding_scores),
        "raw_score": _stats_with_n(raw_scores),
        "speed_factor": _stats(speed_factors),
        "low_score_fallback_count": low_score_fallback_count,
        "captioning": {
            "used": transcript is not None,
            "usable_label_ratio": round(usable_label_ratio, 4) if scene_captions else 0.0,
            "cached": ctx.metadata.get("match_transcript_cached", False),
            "language": ctx.metadata.get("whisperx_language", "zh"),
            "model": ctx.metadata.get("whisperx_model", "medium"),
        },
        "embedding_model": ctx.metadata.get("embedding_model_name", _EMBEDDING_MODEL_NAME),
        "degraded_reason": degraded_reason,
        "diversity": {
            "swaps": diversity_swaps,
            "swaps_log": diversity_swaps_log,
            "window": ctx.metadata.get("match_diversity_window", 3),
            "max_reuse": ctx.metadata.get("match_max_scene_reuse", 2),
        },
        "timeline": {
            "mode": "weighted_acts" if use_weighted_acts else "uniform",
            "act_weights": act_weights if use_weighted_acts else None,
            "segments_per_act": (
                [act_assignments.count(a) for a in range(len(act_weights))]
                if use_weighted_acts else None
            ),
        },
        "topk": {
            "k": topk,
            "reuse_penalty": reuse_penalty,
            "topk_count": topk_count,
            "top1_count": top1_count,
        },
        # Back-compat fields (kept for existing consumers)
        "total": total,
        "embedding": embedding_count,
        "heuristic": heuristic_count,
        "captions_fake": ctx.metadata.get("match_captions_fake", False),
    }

    ctx.status.match = "success"
    return ctx


# Backwards-compatible alias for in-process callers that imported this
# from the module top-level before the refactor.
match_clips_original = _match_clips_impl
