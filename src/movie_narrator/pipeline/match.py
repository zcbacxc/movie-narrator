import json
import math
from pathlib import Path
from typing import Optional

from ..config import get_settings
from ..models import Context, MatchedClip, StepResult
from ..utils.optional_deps import probe

_EMBEDDING_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"


def _build_scene_label(scene_index: int, start: float, end: float) -> str:
    """Best-effort scene caption used as the embedding target text.

    Until a real ML caption pipeline ships, this produces a deterministic label
    from the scene index and time span so the embedding re-rank path is
    exercisable without external services.
    """
    return f"scene {scene_index} from {start:.1f}s to {end:.1f}s"


def _embed_texts(texts):
    """Encode a list of strings to L2-normalized vectors.

    Returns ``None`` when sentence-transformers is unavailable or fails at
    runtime, so the caller can fall back to the heuristic shape.
    """
    from sentence_transformers import SentenceTransformer

    model = SentenceTransformer(_EMBEDDING_MODEL_NAME)
    vectors = model.encode(texts)
    import numpy as np
    arr = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return arr / norms


def _cosine_top1(target_vec, candidate_matrix):
    """Return index of the candidate with the highest cosine similarity.

    ``target_vec`` and row vectors in ``candidate_matrix`` are assumed L2-normalized,
    so cosine reduces to dot product. Returns -1 if the matrix is empty.
    """
    if candidate_matrix.size == 0:
        return -1
    sims = candidate_matrix @ target_vec
    return int(sims.argmax())


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

    settings = get_settings()
    min_score = ctx.metadata.get("match_min_score", settings.match_min_score)
    output_dir = Path(ctx.output_dir)

    try:
        return _match_clips_impl(ctx, min_score, output_dir)
    except Exception as e:
        ctx.status.match = "failed"
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        return ctx


def _match_clips_impl(ctx: Context, min_score: float, output_dir: Path) -> Context:
    # Compute total scene span
    scene_start = min(s.start for s in ctx.scenes)
    scene_end = max(s.end for s in ctx.scenes)
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
        for scene in ctx.scenes:
            if scene.start <= src_mid <= scene.end:
                containing = scene
                break
        if containing is None:
            containing = ctx.scenes[0]

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
    if st_ok and len(ctx.scenes) > 1:
        try:
            scene_labels = [_build_scene_label(s.index, s.start, s.end) for s in ctx.scenes]
            scene_vecs = _embed_texts(scene_labels)
            narration_vecs = _embed_texts([seg.text for seg in ctx.timed_segments])
            for i, seg in enumerate(ctx.timed_segments):
                best_idx = _cosine_top1(narration_vecs[i], scene_vecs)
                best_scene = ctx.scenes[best_idx]
                score = float(scene_vecs[best_idx] @ narration_vecs[i])
                final.append((heuristic[i], score, best_scene, "embedding"))
        except Exception as e:
            ctx.services.console.inline_warn(f"embedding re-rank unavailable ({e}); using heuristic")
            final = [(h, 1.0, None, "heuristic") for h in heuristic]
    else:
        final = [(h, 1.0, None, "heuristic") for h in heuristic]

    matched_clips = []
    for h, score, best_scene, source in final:
        scene_obj = best_scene if best_scene is not None else next(
            s for s in ctx.scenes if s.index == h["scene_index"]
        )
        if score < min_score:
            continue
        matched_clips.append(
            MatchedClip(
                segment_index=h["segment_index"],
                text=h["text"],
                narr_start=h["narr_start"],
                narr_end=h["narr_end"],
                src_start=scene_obj.start,
                src_end=scene_obj.end,
                score=score,
                scene_index=scene_obj.index,
                source=source,
            )
        )

    ctx.matched_clips = matched_clips

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
