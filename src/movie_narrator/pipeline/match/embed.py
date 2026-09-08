# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Embedding: sentence-vector encoding and cosine similarity helpers."""

import functools
import logging
from typing import List

from ...models import Context
from ._shared import _EMBEDDING_MODEL_NAME

logger = logging.getLogger(__name__)


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

    Returns:
        ``None`` when sentence-transformers is unavailable or fails at
        runtime, so the caller can fall back to the heuristic shape.
    """
    model = _load_embedding_model(model_name)
    vectors = model.encode(texts)
    import numpy as np

    arr = np.asarray(vectors, dtype=float)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return arr / norms


def _collect_visual_features(ctx: Context, scene_vecs, scenes) -> None:
    """G9 stage-1 skeleton: extract visual features and record availability.

    Pure-FFmpeg low-level features (luma + RGB histogram) are extracted per
    scene and recorded in ``ctx.metadata`` for the match_summary. Selection
    is deliberately unchanged — stage-1 features carry no semantics. Any
    failure degrades gracefully and is recorded, never breaking the chain.
    """
    from ...utils.visual_features import (
        extract_scene_visual_features,
        visual_feature_vector,
    )

    if not ctx.source_video_path:
        ctx.metadata["match_visual_features_available"] = False
        return
    try:
        features = extract_scene_visual_features(ctx.source_video_path, scenes)
    except Exception:  # noqa: BLE001
        logger.debug("visual feature extraction failed", exc_info=True)
        features = None
    if not features:
        ctx.metadata["match_visual_features_available"] = False
        return

    vectors = [visual_feature_vector(f) for f in features]
    ctx.metadata["match_visual_features_available"] = all(v is not None for v in vectors)
    ctx.metadata["match_visual_features_samples"] = [f.to_dict() for f in features][:3]


def _cosine_top1(target_vec, candidate_matrix) -> int:
    """
    Returns:
        Index of the candidate with the highest cosine similarity.

        ``target_vec`` and row vectors in ``candidate_matrix`` are assumed L2-normalized,
        so cosine reduces to dot product. Returns -1 if the matrix is empty.
    """
    if candidate_matrix.size == 0:
        return -1
    sims = candidate_matrix @ target_vec
    return int(sims.argmax())


def _cosine_topk(target_vec, candidate_matrix, k: int = 5) -> list[tuple[int, float]]:
    """
    Returns:
        Top-K candidates as ``(local_index, score)`` sorted by score descending.

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
