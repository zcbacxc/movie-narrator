# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Post-pipeline content-quality orchestrator.

**Not** a registered pipeline step. Call after ``run_pipeline`` (or race
candidate completion) when ``content_quality_enabled`` is set. Never
mutates ``utils/quality_dashboard.py`` behaviour.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Sequence

from .coherence import (
    CQ_EMBEDDING_IDENTITY,
    CQ_PROXY_EMBEDDING_IDENTITY,
    evaluate_coherence,
)
from .hook import evaluate_hook
from .pacing import (
    DEFAULT_IDEAL_PAUSE_HI,
    DEFAULT_IDEAL_PAUSE_LO,
    DEFAULT_PACING_BINS,
    DEFAULT_PACING_HI,
    DEFAULT_PACING_LO,
    evaluate_pacing,
)
from .schema import (
    CONTENT_QUALITY_SCHEMA_VERSION,
    DimensionResult,
    build_content_quality_dict,
)
from .visual import evaluate_semantic_visual_relevance

logger = logging.getLogger(__name__)

# Avoid importing Context at module level (keeps import graph light).
try:
    from ..models import Context
except Exception:  # pragma: no cover — defensive
    Context = None


def _facts_from_ctx(ctx: Any) -> Dict[str, Any]:
    """Extract the facts content_quality needs from a Context (or dict)."""
    if ctx is None:
        return {}
    if isinstance(ctx, dict):
        meta = ctx.get("metadata") if "metadata" in ctx else ctx
        timed = ctx.get("timed_segments", [])
        texts = ctx.get("texts")
        return {
            "metadata": meta or {},
            "timed_segments": timed or [],
            "texts": texts,
        }

    meta = getattr(ctx, "metadata", None) or {}
    timed = getattr(ctx, "timed_segments", None) or []
    segments = getattr(ctx, "segments", None) or []
    seg_texts: Optional[List[str]] = None
    if segments:
        seg_texts = [getattr(s, "text", "") or "" for s in segments]
    return {
        "metadata": dict(meta),
        "timed_segments": list(timed),
        "texts": seg_texts,
    }


def _pacing_kwargs(meta: Dict[str, Any]) -> Dict[str, Any]:
    kwargs: Dict[str, Any] = {}
    if meta.get("content_quality_pacing_bins") is not None:
        kwargs["bins"] = int(meta["content_quality_pacing_bins"])
    if meta.get("content_quality_pacing_lo") is not None:
        kwargs["lo"] = float(meta["content_quality_pacing_lo"])
    if meta.get("content_quality_pacing_hi") is not None:
        kwargs["hi"] = float(meta["content_quality_pacing_hi"])
    return kwargs


def evaluate_content_quality(
    ctx: Any = None,
    *,
    facts: Optional[Dict[str, Any]] = None,
    embedder: Optional[Callable[[Sequence[str]], Sequence[Sequence[float]]]] = None,
    precomputed_vectors: Optional[Sequence[Sequence[float]]] = None,
    independent_hook_judge: Optional[Callable[[Dict[str, Any]], Optional[float]]] = None,
) -> Dict[str, Any]:
    """Evaluate all content-quality dimensions and return the frozen dict.

    Args:
        ctx: Pipeline Context (preferred) or None when ``facts`` is given.
        facts: Explicit facts dict with keys ``metadata`` /
            ``timed_segments`` / ``texts`` (optional).
        embedder: Injectable coherence embedder (unit tests).
        precomputed_vectors: Precomputed coherence vectors (CI fixtures).
        independent_hook_judge: Optional hook fallback (only used when
            script_qa/script_judge lack hook_strength).

    Returns:
        The frozen ``metadata["content_quality"]`` mapping.
    """
    f = facts if facts is not None else _facts_from_ctx(ctx)
    meta: Dict[str, Any] = f.get("metadata") or {}
    timed = f.get("timed_segments") or []
    texts = f.get("texts")

    # ── pacing ──
    narr_sec = None
    dm = meta.get("duration_metrics") or {}
    if isinstance(dm, dict):
        narr_sec = dm.get("narration_sec")
    pacing = evaluate_pacing(timed, narr_sec, **_pacing_kwargs(meta))

    # ── coherence ──
    if texts is None and timed:
        texts = [getattr(s, "text", "") or "" for s in timed]
    if not texts and ctx is not None and not isinstance(ctx, dict):
        segments = getattr(ctx, "segments", None) or []
        if segments:
            texts = [getattr(s, "text", "") or "" for s in segments]
    coherence = evaluate_coherence(
        texts or [],
        embedder=embedder,
        precomputed_vectors=precomputed_vectors,
    )

    # ── hook ──
    hook = evaluate_hook(meta, independent_judge=independent_hook_judge)

    # ── semantic_visual_relevance ──
    visual_samples = meta.get("match_visual_features_samples")
    visual = evaluate_semantic_visual_relevance(visual_samples)

    results: List[DimensionResult] = [pacing, coherence, hook, visual]

    provenance: Dict[str, Any] = {
        "module": "content_quality",
        "schema_version": CONTENT_QUALITY_SCHEMA_VERSION,
        "execution": "post_pipeline",
        "quality_dashboard_mutated": False,
        "coherence_identity": dict(CQ_EMBEDDING_IDENTITY),
        "coherence_proxy_identity": dict(CQ_PROXY_EMBEDDING_IDENTITY),
        "coherence_effective_model_id": coherence.details.get("effective_model_id"),
        "coherence_effective_revision": coherence.details.get("effective_revision"),
        "coherence_source": coherence.details.get("source"),
        "hook_source": hook.details.get("source"),
        "pacing_bins": pacing.details.get("bins", DEFAULT_PACING_BINS),
        "pacing_lo": pacing.details.get("lo", DEFAULT_PACING_LO),
        "pacing_hi": pacing.details.get("hi", DEFAULT_PACING_HI),
        "ideal_pause_band": [
            pacing.details.get("ideal_pause_lo", DEFAULT_IDEAL_PAUSE_LO),
            pacing.details.get("ideal_pause_hi", DEFAULT_IDEAL_PAUSE_HI),
        ],
        "dimension_details": {r.name: r.details for r in results},
        "dimension_judges": {r.name: r.judge for r in results},
    }

    return build_content_quality_dict(results, provenance)


def content_quality_enabled(metadata: Dict[str, Any]) -> bool:
    """Return True when the job opted into content-quality analysis."""
    return bool((metadata or {}).get("content_quality_enabled"))


def apply_content_quality(
    ctx: Any,
    *,
    embedder: Optional[Callable[[Sequence[str]], Sequence[Sequence[float]]]] = None,
    precomputed_vectors: Optional[Sequence[Sequence[float]]] = None,
    independent_hook_judge: Optional[Callable[[Dict[str, Any]], Optional[float]]] = None,
    force: bool = False,
) -> Dict[str, Any]:
    """Post-pipeline hook: evaluate and write ``metadata["content_quality"]``.

    Default-off: does nothing (returns ``{}``) unless
    ``metadata["content_quality_enabled"]`` is truthy or ``force=True``.

    Args:
        ctx: Pipeline Context (must have ``.metadata``).
        embedder: Optional injectable coherence embedder.
        precomputed_vectors: Optional precomputed vectors.
        independent_hook_judge: Optional hook fallback judge.
        force: Evaluate even when the enable flag is unset.

    Returns:
        The content_quality dict (empty when skipped).
    """
    meta = getattr(ctx, "metadata", None)
    if not isinstance(meta, dict):
        return {}
    if not force and not content_quality_enabled(meta):
        return {}

    try:
        result = evaluate_content_quality(
            ctx,
            embedder=embedder,
            precomputed_vectors=precomputed_vectors,
            independent_hook_judge=independent_hook_judge,
        )
    except Exception as e:  # noqa: BLE001 — analysis must not break the run
        logger.warning("content_quality evaluation failed: %s", e)
        return {}

    meta["content_quality"] = result
    return result
