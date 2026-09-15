# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Content-quality schema types and overall aggregation.

Independent of the 8-dimension engineering dashboard in
``utils/quality_dashboard.py`` — that system stays zero-change.
Content quality is a **post-pipeline analysis** surface stored under
``metadata["content_quality"]``.

Frozen schema::

    metadata["content_quality"] = {
        schema_version,
        dimensions,        # {name: score | None}
        dimension_status,  # {name: "measured"|"proxy"|"unavailable"|"error"}
        overall,           # arithmetic mean of available (measured ∪ proxy), else None
        provenance,        # how the scores were produced
    }

Overall rule (frozen): arithmetic mean over **available** dimensions
(status in {measured, proxy}). Unavailable / error dimensions are not in
the denominator. All unavailable → ``overall is None``. No weights this
phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# Schema version of metadata["content_quality"], independent of package SemVer.
CONTENT_QUALITY_SCHEMA_VERSION = "1"

DimensionStatus = Literal["measured", "proxy", "unavailable", "error"]

# Statuses that count toward the overall mean.
AVAILABLE_STATUSES = frozenset({"measured", "proxy"})

# Frozen dimension names for this phase.
DIMENSION_NAMES: tuple[str, ...] = (
    "pacing",
    "coherence",
    "hook",
    "semantic_visual_relevance",
)


@dataclass
class DimensionResult:
    """Outcome of one content-quality dimension.

    Attributes:
        name: Dimension identifier (matches DIMENSION_NAMES).
        score: 0.0–1.0 score, or None when unavailable/error.
        status: measured | proxy | unavailable | error.
        details: Diagnostic payload (not part of the frozen top-level keys).
        judge: Optional free-form judge note (str | None).
    """

    name: str
    score: Optional[float]
    status: DimensionStatus
    details: Dict[str, Any] = field(default_factory=dict)
    judge: Optional[str] = None

    def is_available(self) -> bool:
        """Return True when this dimension contributes to the overall mean."""
        return self.status in AVAILABLE_STATUSES and self.score is not None


def arithmetic_mean_available(scores: List[float]) -> Optional[float]:
    """Arithmetic mean over available scores; None when the list is empty."""
    if not scores:
        return None
    return sum(scores) / len(scores)


def build_content_quality_dict(
    results: List[DimensionResult],
    provenance: Dict[str, Any],
    *,
    schema_version: str = CONTENT_QUALITY_SCHEMA_VERSION,
) -> Dict[str, Any]:
    """Assemble the frozen ``metadata["content_quality"]`` mapping.

    Args:
        results: Per-dimension results (order preserved in dimensions).
        provenance: How scores were produced (model ids, sources, flags).
        schema_version: Schema stamp; defaults to CONTENT_QUALITY_SCHEMA_VERSION.

    Returns:
        Dict with keys schema_version / dimensions / dimension_status /
        overall / provenance.
    """
    dimensions: Dict[str, Optional[float]] = {}
    dimension_status: Dict[str, str] = {}
    available: List[float] = []
    for r in results:
        dimensions[r.name] = (
            round(r.score, 4) if r.score is not None else None
        )
        dimension_status[r.name] = r.status
        if r.is_available() and r.score is not None:
            available.append(r.score)

    overall = arithmetic_mean_available(available)
    return {
        "schema_version": schema_version,
        "dimensions": dimensions,
        "dimension_status": dimension_status,
        "overall": round(overall, 4) if overall is not None else None,
        "provenance": provenance,
    }
