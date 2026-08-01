# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Cross-step quality score aggregation — holistic quality dashboard.

v0.5.12: Collects per-step QA metrics from ``ctx.metadata`` and aggregates
them into a unified quality dashboard with an overall score, per-dimension
breakdown, and regression baseline comparison.

The dashboard is stored in ``ctx.metadata["quality_dashboard"]`` and
exported as part of ``metadata.json`` for downstream analysis.

Quality dimensions tracked:
- Script quality (from script_qa)
- Audio quality (from audio_quality)
- Alignment quality (from alignment_qa)
- Match quality (from match_quality)
- Subtitle quality (from subtitle_qa)
- Translation quality (from translation_glossary)
- Deliverable quality (from qa_report)
- Video encoding quality (from video_qa)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

logger = logging.getLogger(__name__)


# ── Default weights for overall score ────────────────────
# Each dimension contributes proportionally to the overall quality score.
# Dimensions not present (step skipped/failed) are excluded and their
# weight is redistributed.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "script": 0.10,
    "audio": 0.15,
    "alignment": 0.10,
    "match": 0.15,
    "subtitle": 0.15,
    "translation": 0.10,
    "deliverable": 0.15,
    "video_encoding": 0.10,
}

# Score classification thresholds
_GOOD_THRESHOLD = 0.75
_ACCEPTABLE_THRESHOLD = 0.50
_POOR_THRESHOLD = 0.30


@dataclass
class QualityDimension:
    """A single quality dimension in the dashboard."""

    name: str
    score: float  # 0.0 – 1.0
    weight: float
    issues_count: int = 0
    details: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        if self.score >= _GOOD_THRESHOLD:
            return "good"
        elif self.score >= _ACCEPTABLE_THRESHOLD:
            return "acceptable"
        elif self.score >= _POOR_THRESHOLD:
            return "poor"
        return "critical"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "score": round(self.score, 4),
            "weight": self.weight,
            "label": self.label,
            "issues_count": self.issues_count,
            "details": self.details,
        }


@dataclass
class RegressionDelta:
    """Change in a dimension's score vs baseline."""

    name: str
    current: float
    baseline: float
    delta: float

    @property
    def direction(self) -> str:
        if abs(self.delta) < 0.01:
            return "stable"
        return "improved" if self.delta > 0 else "regressed"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "current": round(self.current, 4),
            "baseline": round(self.baseline, 4),
            "delta": round(self.delta, 4),
            "direction": self.direction,
        }


@dataclass
class QualityDashboard:
    """Aggregated quality metrics across all pipeline steps."""

    overall_score: float = 0.0
    dimensions: list[QualityDimension] = field(default_factory=list)
    total_issues: int = 0
    regression_deltas: list[RegressionDelta] = field(default_factory=list)
    regression_summary: str = "no_baseline"  # no_baseline | stable | improved | regressed

    @property
    def label(self) -> str:
        if self.overall_score >= _GOOD_THRESHOLD:
            return "good"
        elif self.overall_score >= _ACCEPTABLE_THRESHOLD:
            return "acceptable"
        elif self.overall_score >= _POOR_THRESHOLD:
            return "poor"
        return "critical"

    def to_dict(self) -> dict:
        return {
            "overall_score": round(self.overall_score, 4),
            "label": self.label,
            "dimensions": [d.to_dict() for d in self.dimensions],
            "total_issues": self.total_issues,
            "regression_deltas": [d.to_dict() for d in self.regression_deltas],
            "regression_summary": self.regression_summary,
        }


# ── Per-dimension score extractors ───────────────────────
# Each function extracts a 0.0–1.0 score from the corresponding
# ctx.metadata key. Returns None when the dimension is not present.


def _extract_script_score(meta: dict) -> Optional[tuple[float, int, dict]]:
    """Extract script quality score from script_qa metadata."""
    qa = meta.get("script_qa")
    if not qa:
        return None
    issues = qa.get("total_issues", 0)
    # Score: 1.0 when no issues, decreases by 0.1 per issue (min 0.0)
    score = max(0.0, 1.0 - issues * 0.1)
    return score, issues, {"total_issues": issues}


def _extract_audio_score(meta: dict) -> Optional[tuple[float, int, dict]]:
    """Extract audio quality score from audio_quality metadata."""
    aq = meta.get("audio_quality")
    if not aq:
        return None
    summary = aq.get("summary", {})
    segments = aq.get("segments", [])
    if not segments:
        return None
    # Count segments with issues
    issue_count = sum(1 for s in segments if s.get("issues"))
    total = len(segments)
    # Score: ratio of clean segments
    score = (total - issue_count) / total if total > 0 else 1.0
    details = {
        "total_segments": total,
        "segments_with_issues": issue_count,
        "avg_snr_db": summary.get("avg_snr_db"),
        "avg_clipping": summary.get("avg_clipping_ratio"),
    }
    return score, issue_count, details


def _extract_alignment_score(meta: dict) -> Optional[tuple[float, int, dict]]:
    """Extract alignment quality score from alignment_qa metadata."""
    aq = meta.get("alignment_qa")
    if not aq:
        return None
    total = aq.get("total_segments", 0)
    low_conf = aq.get("low_confidence_count", 0)
    if total == 0:
        return None
    # Score: ratio of non-low-confidence segments
    score = (total - low_conf) / total
    return score, low_conf, {
        "total_segments": total,
        "low_confidence_count": low_conf,
        "avg_confidence": aq.get("avg_confidence"),
    }


def _extract_match_score(meta: dict) -> Optional[tuple[float, int, dict]]:
    """Extract match quality score from match_quality metadata."""
    mq = meta.get("match_quality")
    if not mq:
        return None
    total = mq.get("total_clips", 0)
    if total == 0:
        return None
    low_q = mq.get("low_quality_count", 0)
    avg_composite = mq.get("avg_composite", 0.0)
    # Score: use avg_composite directly (already 0.0–1.0)
    score = avg_composite
    return score, low_q, {
        "total_clips": total,
        "low_quality_count": low_q,
        "avg_composite": round(avg_composite, 4),
        "diversity_penalty_count": mq.get("diversity_penalty_count", 0),
    }


def _extract_subtitle_score(meta: dict) -> Optional[tuple[float, int, dict]]:
    """Extract subtitle quality score from subtitle_qa metadata."""
    sq = meta.get("subtitle_qa")
    if not sq:
        return None
    # May have "original" and/or "translated" tracks
    total_issues = 0
    total_cues = 0
    for track_key in ("original", "translated"):
        track = sq.get(track_key)
        if not track:
            continue
        total_cues += track.get("total_cues", 0)
        total_issues += track.get("issues_count", 0)
    if total_cues == 0:
        return None
    # Score: ratio of cues without issues
    score = max(0.0, (total_cues - total_issues) / total_cues)
    return score, total_issues, {
        "total_cues": total_cues,
        "total_issues": total_issues,
        "display_fit_issues": len(sq.get("display_fit_issues", [])),
    }


def _extract_translation_score(meta: dict) -> Optional[tuple[float, int, dict]]:
    """Extract translation quality from glossary and untranslated metadata."""
    glossary = meta.get("translation_glossary")
    untranslated = meta.get("untranslated_indices", [])
    if not glossary and not untranslated:
        return None

    issues = 0
    details: dict = {}

    if glossary:
        inconsistent = glossary.get("inconsistent_count", 0)
        issues += inconsistent
        details["inconsistent_terms"] = inconsistent

    if untranslated:
        issues += len(untranslated)
        details["untranslated_lines"] = len(untranslated)

    # Score: 1.0 when no issues, decrease by 0.05 per issue (min 0.0)
    score = max(0.0, 1.0 - issues * 0.05)
    return score, issues, details


def _extract_deliverable_score(meta: dict) -> Optional[tuple[float, int, dict]]:
    """Extract deliverable quality from qa_report metadata."""
    qa = meta.get("qa_report")
    if not qa:
        return None
    ok = qa.get("ok", True)
    issues = qa.get("issues", [])
    issue_count = len(issues)
    # Score: 1.0 when ok, 0.0 when not ok
    score = 1.0 if ok else max(0.0, 1.0 - issue_count * 0.2)
    return score, issue_count, {"ok": ok, "issue_codes": [i.get("code", "") for i in issues]}


def _extract_video_encoding_score(meta: dict) -> Optional[tuple[float, int, dict]]:
    """Extract video encoding quality from video_qa metadata."""
    vq = meta.get("video_qa")
    if not vq:
        return None
    ok = vq.get("ok", True)
    issues = vq.get("issues", [])
    issue_count = len(issues)
    # Score: 1.0 when ok, 0.5 when issues exist
    score = 1.0 if ok else max(0.0, 1.0 - issue_count * 0.15)
    metrics = vq.get("metrics", {})
    return score, issue_count, {
        "ok": ok,
        "codec": metrics.get("codec"),
        "resolution": f"{metrics.get('width', 0)}x{metrics.get('height', 0)}",
        "bitrate_kbps": metrics.get("bitrate_kbps"),
    }


# Registry of dimension extractors
_DIMENSION_EXTRACTORS: list[tuple[str, Callable]] = [
    ("script", _extract_script_score),
    ("audio", _extract_audio_score),
    ("alignment", _extract_alignment_score),
    ("match", _extract_match_score),
    ("subtitle", _extract_subtitle_score),
    ("translation", _extract_translation_score),
    ("deliverable", _extract_deliverable_score),
    ("video_encoding", _extract_video_encoding_score),
]


# ── Dashboard construction ───────────────────────────────


def collect_quality_dimensions(
    metadata: dict,
    weights: Optional[dict[str, float]] = None,
) -> list[QualityDimension]:
    """Collect all available quality dimensions from pipeline metadata.

    Returns a list of :class:`QualityDimension` for each dimension that
    has data in ``metadata``. Dimensions without data are excluded.
    """
    if weights is None:
        weights = _DEFAULT_WEIGHTS

    dimensions: list[QualityDimension] = []
    for name, extractor in _DIMENSION_EXTRACTORS:
        result = extractor(metadata)
        if result is None:
            continue
        score, issues, details = result
        dimensions.append(QualityDimension(
            name=name,
            score=score,
            weight=weights.get(name, 0.0),
            issues_count=issues,
            details=details,
        ))
    return dimensions


def compute_overall_score(dimensions: list[QualityDimension]) -> float:
    """Compute weighted average of dimension scores.

    Missing dimensions are excluded and their weight is redistributed.
    Returns 0.0 when no dimensions are available.
    """
    if not dimensions:
        return 0.0
    total_weight = sum(d.weight for d in dimensions)
    if total_weight <= 0:
        return 0.0
    weighted_sum = sum(d.score * d.weight for d in dimensions)
    return weighted_sum / total_weight


def build_quality_dashboard(
    metadata: dict,
    *,
    baseline_path: Optional[str] = None,
    weights: Optional[dict[str, float]] = None,
) -> QualityDashboard:
    """Build a complete quality dashboard from pipeline metadata.

    Parameters
    ----------
    metadata
        The ``ctx.metadata`` dict containing per-step QA results.
    baseline_path
        Optional path to a previous ``metadata.json`` for regression
        comparison. When provided, computes per-dimension deltas.
    weights
        Custom dimension weights. Defaults to ``_DEFAULT_WEIGHTS``.
    """
    dimensions = collect_quality_dimensions(metadata, weights=weights)
    overall = compute_overall_score(dimensions)
    total_issues = sum(d.issues_count for d in dimensions)

    dashboard = QualityDashboard(
        overall_score=overall,
        dimensions=dimensions,
        total_issues=total_issues,
    )

    # Regression comparison
    if baseline_path and Path(baseline_path).exists():
        deltas = _compare_with_baseline(dimensions, baseline_path)
        dashboard.regression_deltas = deltas
        regressed = sum(1 for d in deltas if d.direction == "regressed")
        improved = sum(1 for d in deltas if d.direction == "improved")
        if regressed > 0:
            dashboard.regression_summary = "regressed"
        elif improved > 0:
            dashboard.regression_summary = "improved"
        else:
            dashboard.regression_summary = "stable"

    return dashboard


def _compare_with_baseline(
    current_dims: list[QualityDimension],
    baseline_path: str,
) -> list[RegressionDelta]:
    """Compare current dimensions against a baseline metadata.json.

    Returns deltas for dimensions present in both current and baseline.
    """
    try:
        baseline_file = Path(baseline_path)
        if not baseline_file.exists():
            return []
        baseline_data = json.loads(baseline_file.read_text(encoding="utf-8"))
        baseline_dashboard = baseline_data.get("quality_dashboard", {})
        baseline_dims = {d["name"]: d["score"] for d in baseline_dashboard.get("dimensions", [])}
    except (OSError, ValueError) as e:
        logger.warning("Failed to load baseline for regression comparison: %s", e)
        return []

    deltas: list[RegressionDelta] = []
    for dim in current_dims:
        if dim.name in baseline_dims:
            baseline_score = baseline_dims[dim.name]
            deltas.append(RegressionDelta(
                name=dim.name,
                current=dim.score,
                baseline=baseline_score,
                delta=dim.score - baseline_score,
            ))
    return deltas
