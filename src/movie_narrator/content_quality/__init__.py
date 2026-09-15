# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Content quality (M3) — independent post-pipeline analysis surface.

This package is **not** part of the production STEPS registry and does
**not** replace or merge with the 8-dimension engineering dashboard in
``utils/quality_dashboard.py`` (that system stays zero-change).

Public API::

    from movie_narrator.content_quality import (
        evaluate_content_quality,
        apply_content_quality,
        DimensionResult,
        CONTENT_QUALITY_SCHEMA_VERSION,
    )

Frozen metadata shape::

    metadata["content_quality"] = {
        "schema_version", "dimensions", "dimension_status",
        "overall", "provenance",
    }

Default OFF: enable via job.yaml ``params.content_quality_enabled: true``
(or the matching JobParams field). Race winner selection does **not**
consume content_quality unless an explicit flag is set.
"""

from __future__ import annotations

from .coherence import (
    CQ_EMBEDDING_IDENTITY,
    CQ_PROXY_EMBEDDING_IDENTITY,
    evaluate_coherence,
    lexical_hash_embed,
)
from .evaluate import (
    apply_content_quality,
    content_quality_enabled,
    evaluate_content_quality,
)
from .hook import evaluate_hook
from .pacing import evaluate_pacing
from .schema import (
    AVAILABLE_STATUSES,
    CONTENT_QUALITY_SCHEMA_VERSION,
    DIMENSION_NAMES,
    DimensionResult,
    arithmetic_mean_available,
    build_content_quality_dict,
)
from .visual import evaluate_semantic_visual_relevance

__all__ = [
    "AVAILABLE_STATUSES",
    "CQ_EMBEDDING_IDENTITY",
    "CQ_PROXY_EMBEDDING_IDENTITY",
    "CONTENT_QUALITY_SCHEMA_VERSION",
    "DIMENSION_NAMES",
    "DimensionResult",
    "apply_content_quality",
    "arithmetic_mean_available",
    "build_content_quality_dict",
    "content_quality_enabled",
    "evaluate_content_quality",
    "evaluate_coherence",
    "evaluate_hook",
    "evaluate_pacing",
    "evaluate_semantic_visual_relevance",
    "lexical_hash_embed",
]
