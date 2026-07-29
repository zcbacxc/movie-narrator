"""Structured QA report export — human-readable + JSON quality report.

v0.5.12: Generates a comprehensive QA report from all pipeline quality
metrics and exports it alongside deliverables (``qa_report.json`` and
``qa_report.txt``).

The report includes:
- Overall quality score and label
- Per-dimension breakdown with issues
- Regression comparison (when baseline available)
- Video encoding details
- Recommendations
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from .. import __version__
from .quality_dashboard import QualityDashboard, build_quality_dashboard


def generate_qa_report_dict(
    metadata: dict,
    *,
    movie_name: str = "",
    baseline_path: Optional[str] = None,
) -> dict:
    """Generate a structured QA report as a dict.

    Parameters
    ----------
    metadata
        The ``ctx.metadata`` dict with all QA results.
    movie_name
        Movie name for the report header.
    baseline_path
        Optional path to previous metadata.json for regression comparison.
    """
    dashboard = build_quality_dashboard(metadata, baseline_path=baseline_path)
    dashboard_dict = dashboard.to_dict()

    # Collect all issues across dimensions
    all_issues: list[dict] = []
    for dim in dashboard_dict.get("dimensions", []):
        if dim.get("issues_count", 0) > 0:
            all_issues.append({
                "dimension": dim["name"],
                "label": dim["label"],
                "issues_count": dim["issues_count"],
                "score": dim["score"],
            })

    # Collect recommendations from video_qa
    video_qa = metadata.get("video_qa", {})
    recommendations = video_qa.get("recommendations", [])

    report = {
        "report_version": "1.0",
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "tool_version": __version__,
        "movie_name": movie_name,
        "overall": {
            "score": dashboard_dict["overall_score"],
            "label": dashboard_dict["label"],
            "total_issues": dashboard_dict["total_issues"],
        },
        "dimensions": dashboard_dict["dimensions"],
        "regression": {
            "summary": dashboard_dict["regression_summary"],
            "deltas": dashboard_dict["regression_deltas"],
        },
        "issue_summary": all_issues,
        "recommendations": recommendations,
        # Raw QA data for programmatic access
        "raw_reports": {
            "script_qa": metadata.get("script_qa"),
            "audio_quality": _summarize_audio(metadata.get("audio_quality")),
            "alignment_qa": metadata.get("alignment_qa"),
            "match_quality": metadata.get("match_quality"),
            "subtitle_qa": _summarize_subtitle(metadata.get("subtitle_qa")),
            "translation_glossary": metadata.get("translation_glossary"),
            "qa_report": metadata.get("qa_report"),
            "video_qa": metadata.get("video_qa"),
        },
    }
    return report


def _summarize_audio(aq: Optional[dict]) -> Optional[dict]:
    """Summarize audio quality for the report (omit per-segment details)."""
    if not aq:
        return None
    summary = aq.get("summary", {})
    return {
        "summary": summary,
        "segment_count": len(aq.get("segments", [])),
        "prosody": aq.get("prosody"),
        "duration_v2_speed": aq.get("duration_v2_speed"),
    }


def _summarize_subtitle(sq: Optional[dict]) -> Optional[dict]:
    """Summarize subtitle QA for the report."""
    if not sq:
        return None
    result = {}
    for key in ("original", "translated"):
        track = sq.get(key)
        if track:
            result[key] = {
                "total_cues": track.get("total_cues", 0),
                "issues_count": track.get("issues_count", 0),
            }
    if sq.get("display_fit_issues"):
        result["display_fit_issues_count"] = len(sq["display_fit_issues"])
    return result if result else None


def format_qa_report_text(report_dict: dict) -> str:
    """Format the QA report dict as a human-readable text report."""
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("  QUALITY ASSURANCE REPORT")
    lines.append("=" * 60)
    lines.append("")

    # Header
    lines.append(f"Movie:        {report_dict.get('movie_name', 'N/A')}")
    lines.append(f"Generated:    {report_dict.get('generated_at', 'N/A')}")
    lines.append(f"Tool Version: {report_dict.get('tool_version', 'N/A')}")
    lines.append("")

    # Overall
    overall = report_dict.get("overall", {})
    lines.append("-" * 60)
    lines.append("OVERALL QUALITY")
    lines.append("-" * 60)
    score = overall.get("score", 0.0)
    label = overall.get("label", "unknown")
    lines.append(f"  Score:   {score:.1%} [{label}]")
    lines.append(f"  Issues:  {overall.get('total_issues', 0)} total")
    lines.append("")

    # Dimensions
    lines.append("-" * 60)
    lines.append("PER-DIMENSION BREAKDOWN")
    lines.append("-" * 60)
    dims = report_dict.get("dimensions", [])
    if dims:
        # Table header
        lines.append(f"  {'Dimension':<20} {'Score':>8} {'Label':<12} {'Issues':>7}")
        lines.append(f"  {'-' * 20} {'-' * 8} {'-' * 12} {'-' * 7}")
        for dim in dims:
            name = dim.get("name", "")
            sc = dim.get("score", 0.0)
            lbl = dim.get("label", "")
            iss = dim.get("issues_count", 0)
            lines.append(f"  {name:<20} {sc:>7.1%} {lbl:<12} {iss:>7}")
    else:
        lines.append("  (no quality dimensions available)")
    lines.append("")

    # Regression
    regression = report_dict.get("regression", {})
    if regression.get("summary") != "no_baseline":
        lines.append("-" * 60)
        lines.append("REGRESSION ANALYSIS")
        lines.append("-" * 60)
        lines.append(f"  Summary: {regression.get('summary', 'unknown')}")
        deltas = regression.get("deltas", [])
        if deltas:
            lines.append(f"  {'Dimension':<20} {'Current':>8} {'Baseline':>10} {'Delta':>8} {'Direction':<12}")
            lines.append(f"  {'-' * 20} {'-' * 8} {'-' * 10} {'-' * 8} {'-' * 12}")
            for d in deltas:
                lines.append(
                    f"  {d['name']:<20} {d['current']:>7.1%} {d['baseline']:>9.1%} "
                    f"{d['delta']:>+7.1%} {d['direction']:<12}"
                )
        lines.append("")

    # Issue summary
    issues = report_dict.get("issue_summary", [])
    if issues:
        lines.append("-" * 60)
        lines.append("ISSUE SUMMARY")
        lines.append("-" * 60)
        for issue in issues:
            lines.append(
                f"  [{issue['dimension']}] {issue['issues_count']} issue(s) "
                f"(score: {issue['score']:.1%}, label: {issue['label']})"
            )
        lines.append("")

    # Recommendations
    recs = report_dict.get("recommendations", [])
    if recs:
        lines.append("-" * 60)
        lines.append("RECOMMENDATIONS")
        lines.append("-" * 60)
        for i, rec in enumerate(recs, 1):
            lines.append(f"  {i}. {rec}")
        lines.append("")

    lines.append("=" * 60)
    lines.append("  END OF REPORT")
    lines.append("=" * 60)
    return "\n".join(lines)


def export_qa_report(
    metadata: dict,
    output_dir: str | Path,
    *,
    movie_name: str = "",
    baseline_path: Optional[str] = None,
) -> dict:
    """Export QA report as JSON and text files alongside deliverables.

    Writes ``qa_report.json`` and ``qa_report.txt`` to ``output_dir``.
    Returns the report dict.
    """
    report_dict = generate_qa_report_dict(
        metadata,
        movie_name=movie_name,
        baseline_path=baseline_path,
    )

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # JSON report
    json_path = output_path / "qa_report.json"
    json_path.write_text(
        json.dumps(report_dict, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # Text report
    text_path = output_path / "qa_report.txt"
    text_path.write_text(
        format_qa_report_text(report_dict),
        encoding="utf-8",
    )

    return report_dict
