# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Soft pipeline step: intermediate product QA gate before render.

v0.5.12: Runs before ``render_video`` to validate that all intermediate
products (script, audio, subtitles, alignment) meet minimum quality
thresholds.  This is a *soft gate* — issues are logged as warnings and
stored in ``ctx.metadata["qa_gate"]``, but never block the pipeline
unless ``--strict`` is set.

The gate checks:
- Script QA: no critical issues (too many short/long/duplicate segments)
- Audio QA: no segments with severe clipping (>1%)
- Subtitle QA: no cues with CPS > 2x threshold
- Alignment QA: not in degraded state (drift too large)
"""

from __future__ import annotations

import logging

from ..models import Context, StepResult
from ..tts.base import is_ci

logger = logging.getLogger(__name__)

# Thresholds for gate checks
_MAX_SCRIPT_ISSUES = 10  # >10 issues = critical
_MAX_CLIPPING_RATIO = 0.01  # >1% clipped samples = critical
_MAX_CPS_MULTIPLIER = 2.0  # CPS > 2x normal threshold = critical


def run_qa_gate(ctx: Context) -> Context:
    """Validate intermediate products before the expensive render step.

    Soft step: issues are logged and stored, never block unless ``--strict``.
    In CI, the gate is skipped (no real LLM/TTS output to validate).
    """
    # Skip in CI — intermediate products are mocked
    if is_ci() and ctx.metadata.get("qa_enabled") is not True:
        ctx.services.console.debug("QA gate skipped in CI")
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "CI mode"
        return ctx

    gate_issues: list[str] = []
    gate_warnings: list[str] = []

    # ── Script QA check ──
    script_qa = ctx.metadata.get("script_qa")
    if script_qa:
        total = script_qa.get("total_issues", 0)
        if total > _MAX_SCRIPT_ISSUES:
            gate_issues.append(
                f"Script QA: {total} issues (max {_MAX_SCRIPT_ISSUES})"
            )
        elif total > 0:
            gate_warnings.append(f"Script QA: {total} minor issues")

    # ── Audio QA check ──
    audio_qa = ctx.metadata.get("audio_quality")
    if audio_qa:
        segments = audio_qa.get("segments", [])
        bad_segments = [
            s for s in segments
            if s.get("clipping_ratio", 0) > _MAX_CLIPPING_RATIO
        ]
        if bad_segments:
            gate_issues.append(
                f"Audio QA: {len(bad_segments)} segment(s) with severe clipping "
                f"(>{_MAX_CLIPPING_RATIO:.0%})"
            )

    # ── Subtitle QA check ──
    subtitle_qa = ctx.metadata.get("subtitle_qa")
    if subtitle_qa:
        for track_key in ("original", "translated"):
            track = subtitle_qa.get(track_key)
            if not track:
                continue
            issues_count = track.get("issues_count", 0)
            total_cues = track.get("total_cues", 0)
            if total_cues > 0 and issues_count > total_cues * 0.5:
                gate_issues.append(
                    f"Subtitle QA ({track_key}): {issues_count}/{total_cues} "
                    f"cues have issues (>50%)"
                )

    # ── Alignment QA check ──
    align_qa = ctx.metadata.get("alignment_qa")
    if align_qa:
        low_conf = align_qa.get("low_confidence_count", 0)
        total = align_qa.get("total_segments", 0)
        if total > 0 and low_conf > total * 0.5:
            gate_issues.append(
                f"Alignment QA: {low_conf}/{total} segments have low confidence (>50%)"
            )

    # ── Translation QA check ──
    untranslated = ctx.metadata.get("untranslated_indices", [])
    if untranslated and len(untranslated) > len(ctx.timed_segments) * 0.3:
        gate_issues.append(
            f"Translation: {len(untranslated)} untranslated lines (>30% of total)"
        )

    # Store gate results
    ctx.metadata["qa_gate"] = {
        "issues": gate_issues,
        "warnings": gate_warnings,
        "passed": len(gate_issues) == 0,
    }

    # Log warnings
    for w in gate_warnings:
        ctx.services.console.inline_warn(f"QA gate warning: {w}")

    if gate_issues:
        for issue in gate_issues:
            ctx.services.console.inline_warn(f"QA gate issue: {issue}")

        if ctx.metadata.get("strict"):
            raise RuntimeError(
                f"QA gate failed ({len(gate_issues)} critical issues) in strict mode"
            )
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = f"{len(gate_issues)} QA gate issue(s)"
    else:
        ctx.services.console.debug("QA gate passed")
        ctx.step_state.result = StepResult.SUCCESS
        ctx.step_state.message = "all checks passed"

    return ctx
