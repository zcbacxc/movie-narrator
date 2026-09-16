# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Coherence dimension — windowed cosine similarity of narration segments.

Frozen embedding identity for content_quality is **dedicated** and does
**not** read the match-step model override from ``ctx.metadata``.

Method (frozen):
* window = 3 segments → mean consecutive cosine within the window
* aggregate windows via **median**
* fewer than 2 segments → unavailable

Provenance always records ``effective_model_id`` / ``effective_revision``
plus preprocess / normalize so fixtures can pin identity.
"""

from __future__ import annotations

import hashlib
import logging
import math
import unicodedata
from typing import Callable, Dict, List, Optional, Sequence

from .schema import DimensionResult

logger = logging.getLogger(__name__)

WINDOW_SIZE = 3

# Frozen content-quality embedding identity (independent of match override).
CQ_EMBEDDING_IDENTITY: Dict[str, str] = {
    "model_id": "content-quality/minilm-multilingual-v1",
    "revision": "cq-pin-1",
    "preprocess": "nfc-strip-lower",
    "normalize": "l2",
}

# Lexical proxy used when sentence-transformers is unavailable (CI / no [ml]).
CQ_PROXY_EMBEDDING_IDENTITY: Dict[str, str] = {
    "model_id": "content-quality/lexical-hash-v1",
    "revision": "cq-proxy-1",
    "preprocess": "nfc-strip-lower",
    "normalize": "l2",
}

_PROXY_DIM = 64


def _preprocess(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").strip().lower()


def _l2_normalize(vec: Sequence[float]) -> List[float]:
    norm = math.sqrt(sum(x * x for x in vec))
    if norm <= 0:
        return [0.0] * len(vec)
    return [x / norm for x in vec]


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Dot product of two vectors (assume L2-normalized)."""
    n = min(len(a), len(b))
    if n == 0:
        return 0.0
    return sum(a[i] * b[i] for i in range(n))


def _stable_bucket(s: str) -> int:
    digest = hashlib.md5(s.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


def lexical_hash_embed(texts: Sequence[str]) -> List[List[float]]:
    """Deterministic bigram-hash embedder (proxy; no third-party deps)."""
    out: List[List[float]] = []
    for text in texts:
        t = _preprocess(text)
        v = [0.0] * _PROXY_DIM
        if len(t) == 1:
            v[_stable_bucket(t) % _PROXY_DIM] = 1.0
        else:
            for i in range(len(t) - 1):
                v[_stable_bucket(t[i : i + 2]) % _PROXY_DIM] += 1.0
        out.append(_l2_normalize(v))
    return out


def try_sentence_transformers_embed(texts: Sequence[str]) -> Optional[List[List[float]]]:
    """Embed with the frozen CQ model when sentence-transformers is installed.

    Returns None when the optional [ml] extra is missing or encode fails —
    callers then fall back to the lexical proxy (status=proxy).
    """
    try:
        from sentence_transformers import SentenceTransformer  # type: ignore
    except Exception:
        return None
    try:
        model = SentenceTransformer(CQ_EMBEDDING_IDENTITY["model_id"])
        raw = model.encode([_preprocess(t) for t in texts])
        return [_l2_normalize(list(map(float, row))) for row in raw]
    except Exception as e:  # noqa: BLE001 — degrade to proxy
        logger.debug("content_quality coherence ST encode failed: %s", e)
        return None


def window_median_cosine(
    vectors: Sequence[Sequence[float]],
    window: int = WINDOW_SIZE,
) -> Optional[float]:
    """Median of windowed mean consecutive cosines.

    For each sliding window of ``window`` consecutive segments, compute the
    mean of the (window-1) adjacent cosines; return the median across
    windows. When fewer than ``window`` vectors exist, fall back to the
    median of available adjacent cosines.
    """
    n = len(vectors)
    if n < 2:
        return None
    adjacent = [cosine(vectors[i], vectors[i + 1]) for i in range(n - 1)]
    # Clamp to [0, 1]: negative cosine means unrelated topics, score 0.
    adjacent = [max(0.0, min(1.0, c)) for c in adjacent]

    width = max(1, window - 1)
    if len(adjacent) < width:
        means = adjacent
    else:
        means = []
        for i in range(len(adjacent) - width + 1):
            chunk = adjacent[i : i + width]
            means.append(sum(chunk) / len(chunk))
    if not means:
        return None
    means_sorted = sorted(means)
    mid = len(means_sorted) // 2
    if len(means_sorted) % 2 == 1:
        return means_sorted[mid]
    return (means_sorted[mid - 1] + means_sorted[mid]) / 2.0


def evaluate_coherence(
    texts: Sequence[str],
    *,
    embedder: Optional[Callable[[Sequence[str]], Sequence[Sequence[float]]]] = None,
    precomputed_vectors: Optional[Sequence[Sequence[float]]] = None,
    window: int = WINDOW_SIZE,
) -> DimensionResult:
    """Compute narration coherence from segment texts or vectors.

    Args:
        texts: Narration segment texts (used when no vectors/embedder).
        embedder: Optional injectable embedder for unit tests.
        precomputed_vectors: Optional pre-aligned vectors (CI fixtures).
        window: Sliding window size (frozen default 3).

    Returns:
        DimensionResult with status measured | proxy | unavailable.
    """
    segments = list(texts or [])
    n = len(segments)
    if n < 2 and not precomputed_vectors:
        return DimensionResult(
            name="coherence",
            score=None,
            status="unavailable",
            details={"reason": "n_segments<2", "n_segments": n},
        )

    identity: Dict[str, str]
    status: str
    if precomputed_vectors is not None:
        vectors = [list(map(float, v)) for v in precomputed_vectors]
        identity = dict(CQ_EMBEDDING_IDENTITY)
        identity["model_id"] = identity["model_id"] + "#precomputed"
        status = "measured"
        source = "precomputed"
    elif embedder is not None:
        vectors = [list(map(float, v)) for v in embedder(segments)]
        identity = dict(CQ_EMBEDDING_IDENTITY)
        identity["model_id"] = identity["model_id"] + "#injected"
        status = "measured"
        source = "injected_embedder"
    else:
        st_vecs = try_sentence_transformers_embed(segments)
        if st_vecs is not None:
            vectors = st_vecs
            identity = dict(CQ_EMBEDDING_IDENTITY)
            status = "measured"
            source = "sentence_transformers"
        else:
            vectors = lexical_hash_embed(segments)
            identity = dict(CQ_PROXY_EMBEDDING_IDENTITY)
            status = "proxy"
            source = "lexical_hash"

    if len(vectors) < 2:
        return DimensionResult(
            name="coherence",
            score=None,
            status="unavailable",
            details={
                "reason": "n_vectors<2",
                "n_vectors": len(vectors),
                "source": source,
            },
        )

    med = window_median_cosine(vectors, window=window)
    if med is None:
        return DimensionResult(
            name="coherence",
            score=None,
            status="unavailable",
            details={"reason": "no_cosine", "source": source},
        )

    return DimensionResult(
        name="coherence",
        score=round(float(med), 4),
        status=status,  # type: ignore[arg-type]
        details={
            "window": window,
            "n_segments": len(vectors),
            "source": source,
            "effective_model_id": identity["model_id"],
            "effective_revision": identity["revision"],
            "preprocess": identity["preprocess"],
            "normalize": identity["normalize"],
        },
    )
