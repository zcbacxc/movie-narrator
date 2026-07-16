import json
import logging
import hashlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..models import Context, MatchedClip, Scene, StepResult, TimedSegment
from ..utils.optional_deps import probe

_EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"  # default, overridden by ctx.metadata

logger = logging.getLogger(__name__)


# ── Scene captioning via WhisperX ──────────────────────────


def _video_audio_hash(video_path: str) -> str:
    """Stable hash of the video file for cache key."""
    h = hashlib.sha256()
    with open(video_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()[:16]


def _transcribe_video_audio(
    video_path: str,
    output_dir: Path,
    device: str = "cpu",
    model_name: str = "medium",
    language: str = "zh",
) -> Optional[List[dict]]:
    """Transcribe the video's audio track with WhisperX.

    Returns a list of ``{"start", "end", "text"}`` dicts, or ``None``
    when WhisperX is unavailable or transcription fails.
    Results are cached as ``video_transcript.json`` keyed by file hash.
    """
    cache_key = _video_audio_hash(video_path)
    cache_path = output_dir / f"transcript_{cache_key}.json"

    # Cache hit
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            pass  # corrupt cache, re-transcribe

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
    except Exception as e:
        logger.warning("WhisperX video transcription failed: %s", e)
        return None


def _build_scene_captions(
    scenes: List[Scene],
    transcript: Optional[List[dict]],
) -> List[str]:
    """Build semantic scene labels from WhisperX transcript.

    Each scene gets the concatenated text of all transcript segments
    that overlap with the scene's time range. Falls back to a
    deterministic placeholder when no transcript is available or a
    scene has no overlapping speech.
    """
    if not transcript:
        return [_build_scene_label(s.index, s.start, s.end) for s in scenes]

    labels: List[str] = []
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
            labels.append(caption)
        else:
            # No speech in this scene — use placeholder
            labels.append(_build_scene_label(scene.index, scene.start, scene.end))

    return labels


def _build_scene_label(scene_index: int, start: float, end: float) -> str:
    """Best-effort scene caption used as the embedding target text.

    Until a real ML caption pipeline ships, this produces a deterministic label
    from the scene index and time span so the embedding re-rank path is
    exercisable without external services.
    """
    return f"scene {scene_index} from {start:.1f}s to {end:.1f}s"


def _embed_texts(texts: List[str], model_name: str = _EMBEDDING_MODEL_NAME):
    """Encode a list of strings to L2-normalized vectors.

    Returns ``None`` when sentence-transformers is unavailable or fails at
    runtime, so the caller can fall back to the heuristic shape.
    """
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(model_name)
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
    clamp_min = ctx.metadata.get("match_speed_clamp_min", 0.5)
    clamp_max = ctx.metadata.get("match_speed_clamp_max", 3.0)
    merge_min = ctx.metadata.get("scene_merge_min_duration", 0.0)
    output_dir = Path(ctx.output_dir)

    try:
        return _match_clips_impl(
            ctx, min_score, clamp_min, clamp_max, merge_min, output_dir
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
    output_dir: Path,
) -> Context:
    # Optionally merge short scenes to reduce extreme speed factors
    scenes = ctx.scenes
    if merge_min > 0:
        scenes = _merge_short_scenes(scenes, min_duration=merge_min)
        ctx.services.console.debug(
            f"  scene merge: {len(ctx.scenes)} -> {len(scenes)} scenes (min={merge_min}s)"
        )

    # Compute total scene span
    scene_start = min(s.start for s in scenes)
    scene_end = max(s.end for s in scenes)
    scene_span = scene_end - scene_start

    first_start = ctx.timed_segments[0].start
    last_end = ctx.timed_segments[-1].end
    narr_span = last_end - first_start

    # --- Heuristic baseline -------------------------------------------------
    # Map each narration midpoint proportionally onto the scene span, pick the
    # containing scene window. Produces a stable candidate per segment with
    # score=1.0 (plan T14 normative rule).
    heuristic = []
    for i, seg in enumerate(ctx.timed_segments):
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
    final = []
    st_ok, st_hint = probe("sentence_transformers")
    if st_ok and len(scenes) > 1:
        try:
            # Try WhisperX scene captioning first
            transcript = None
            wx_ok, _ = probe("whisperx")
            if wx_ok and ctx.source_video_path:
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
                    ctx.services.console.debug("  WhisperX returned no transcript; using fallback labels")

            scene_labels = _build_scene_captions(scenes, transcript)
            emb_model = ctx.metadata.get("embedding_model_name", _EMBEDDING_MODEL_NAME)
            scene_vecs = _embed_texts(scene_labels, emb_model)
            narration_vecs = _embed_texts([seg.text for seg in ctx.timed_segments], emb_model)
            for i, seg in enumerate(ctx.timed_segments):
                best_idx = _cosine_top1(narration_vecs[i], scene_vecs)
                best_scene = scenes[best_idx]
                score = float(scene_vecs[best_idx] @ narration_vecs[i])
                final.append((heuristic[i], score, best_scene, "embedding"))
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

    # Log speed factor stats
    if matched_clips:
        factors = []
        for mc in matched_clips:
            narr_dur = mc.narr_end - mc.narr_start
            if narr_dur > 0:
                factors.append((mc.src_end - mc.src_start) / narr_dur)
        if factors:
            ctx.services.console.debug(
                f"  speed factors: min={min(factors):.2f}x max={max(factors):.2f}x "
                f"avg={sum(factors)/len(factors):.2f}x (clamp={clamp_min}~{clamp_max}x)"
            )

    matches_path = output_dir / "matches.json"
    matches_path.write_text(
        json.dumps(
            [m.model_dump() for m in matched_clips], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    ctx.status.match = "success"
    return ctx


# Backwards-compatible alias for in-process callers that imported this
# from the module top-level before the refactor.
match_clips_original = _match_clips_impl
