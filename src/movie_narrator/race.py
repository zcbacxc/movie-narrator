# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Multi-candidate horse race (multi-candidate comparison).

Runs the pipeline N times with different parameter combinations (preset x
match seed), then scores each output on match quality, duration fit,
diversity, and scene coverage. The best-scoring candidate is recommended
to the user.

This module is orchestration-only: it calls ``build_context`` +
``run_pipeline`` with varied ``params`` dicts. The scoring formula reads
``match_summary`` and ``duration_metrics`` from ``ctx.metadata`` — no
new instrumentation is added to the pipeline itself.

Variation strategy
------------------
Each candidate gets a different combination of:

- **narration_preset** — changes prompt shaping, speed clamps, TTS pacing
- **match_topk** — top-K rerank width (wider = more exploration)
- **match_topk_reuse_penalty** — diversity pressure on scene reuse
- **match_diversity_window** — lookback window for reuse detection

Default 3 candidates:

==============  =============  =====  ===============  ================
Candidate       Preset         topk   reuse_penalty    diversity_window
==============  =============  =====  ===============  ================
aggressive      douyin-fast    8      0.25             5
balanced        mainstream-dry 5      0.15             3
conservative    bilibili-long  3      0.10             2
==============  =============  =====  ===============  ================

Scoring formula (0-100)
-----------------------
::

    score = match_quality * 0.40
          + duration_fit   * 0.25
          + diversity      * 0.20
          + scene_coverage * 0.15

- **match_quality** (0-1): ``embedding_ratio * avg_score`` (clamped)
- **duration_fit** (0-1): ``1 - |1.0 - ratio|`` (clamped to [0, 1])
- **diversity** (0-1): ``swaps / segments`` mapped so 0.05-0.30 is ideal
- **scene_coverage** (0-1): ``footage_segments / total_segments``
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import Context

logger = logging.getLogger(__name__)


# ── Data structures ────────────────────────────────────────


@dataclass
class CandidateConfig:
    """A single variation's parameter set."""

    label: str
    narration_preset: str
    match_topk: int
    match_topk_reuse_penalty: float
    match_diversity_window: int
    extra_params: Dict[str, Any] = field(default_factory=dict)

    def to_params(self) -> Dict[str, Any]:
        """Convert to a ``params`` dict for ``build_context``."""
        p: Dict[str, Any] = {
            "match_topk": self.match_topk,
            "match_topk_reuse_penalty": self.match_topk_reuse_penalty,
            "match_diversity_window": self.match_diversity_window,
        }
        p.update(self.extra_params)
        return p


@dataclass
class CandidateResult:
    """Outcome of one candidate run."""

    config: CandidateConfig
    output_dir: Path
    video_path: Optional[str] = None
    score: float = 0.0
    score_breakdown: Dict[str, float] = field(default_factory=dict)
    match_summary: Optional[Dict[str, Any]] = None
    duration_metrics: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    metadata_path: Optional[Path] = None


# ── Candidate generation ──────────────────────────────────


_DEFAULT_CANDIDATES: List[CandidateConfig] = [
    CandidateConfig(
        label="aggressive",
        narration_preset="douyin-fast",
        match_topk=8,
        match_topk_reuse_penalty=0.25,
        match_diversity_window=5,
    ),
    CandidateConfig(
        label="balanced",
        narration_preset="mainstream-dry",
        match_topk=5,
        match_topk_reuse_penalty=0.15,
        match_diversity_window=3,
    ),
    CandidateConfig(
        label="conservative",
        narration_preset="bilibili-long",
        match_topk=3,
        match_topk_reuse_penalty=0.10,
        match_diversity_window=2,
    ),
]


def generate_candidates(
    n: int = 3,
    presets: Optional[List[str]] = None,
) -> List[CandidateConfig]:
    """Generate N candidate configurations.

    Args:
        n: Number of candidates (1-6).
        presets: Optional list of preset names to cycle through.
            If None, uses the default preset rotation.

    Returns:
        List of :class:`CandidateConfig` with distinct parameter sets.
    """
    if n < 1:
        n = 1
    if n > 6:
        n = 6

    if presets:
        # Build candidates from user-specified presets, cycling
        # match parameters across a gradient.
        topk_gradient = [8, 5, 3, 6, 4, 7]
        penalty_gradient = [0.25, 0.15, 0.10, 0.20, 0.12, 0.18]
        window_gradient = [5, 3, 2, 4, 3, 4]
        candidates = []
        for i in range(min(n, len(presets))):
            candidates.append(
                CandidateConfig(
                    label=f"candidate-{i+1}",
                    narration_preset=presets[i],
                    match_topk=topk_gradient[i % len(topk_gradient)],
                    match_topk_reuse_penalty=penalty_gradient[i % len(penalty_gradient)],
                    match_diversity_window=window_gradient[i % len(window_gradient)],
                )
            )
        return candidates

    return _DEFAULT_CANDIDATES[:n]


# ── Scoring ───────────────────────────────────────────────


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def score_candidate(metadata: Dict[str, Any]) -> tuple[float, Dict[str, float]]:
    """Compute a composite 0-100 score from pipeline metadata.

    Args:
        metadata: The ``ctx.metadata`` dict after pipeline completion.

    Returns:
        Tuple of ``(total_score, breakdown)`` where breakdown maps
        each sub-metric name to its 0-1 contribution.
    """
    ms: Dict[str, Any] = metadata.get("match_summary") or {}
    dm: Dict[str, Any] = metadata.get("duration_metrics") or {}

    # If the pipeline didn't produce a match_summary, score is 0 —
    # there's nothing to evaluate.
    if not ms:
        return 0.0, {
            "match_quality": 0.0,
            "duration_fit": 0.0,
            "diversity": 0.0,
            "scene_coverage": 0.0,
        }

    segments = ms.get("segments", 0) or 1

    # ── Match quality (40%) ──
    # embedding_ratio * avg_score, both 0-1
    emb_ratio = ms.get("embedding_ratio", 0.0) or 0.0
    score_stats = ms.get("score") or {}
    avg_score = score_stats.get("avg", 0.0) or 0.0
    # Cosine similarity can be negative; clamp to [0, 1]
    avg_score_norm = _clamp(avg_score)
    match_quality = _clamp(emb_ratio * avg_score_norm)

    # ── Duration fit (25%) ──
    # ratio = actual / target; ideal = 1.0
    ratio = dm.get("ratio", 1.0) or 1.0
    duration_fit = _clamp(1.0 - abs(1.0 - ratio))

    # ── Diversity (20%) ──
    # swaps/segments: ideal range 0.05-0.30
    # Below 0.05 = too repetitive, above 0.30 = too chaotic
    diversity_info = ms.get("diversity") or {}
    swaps = diversity_info.get("swaps", 0) or 0
    swap_rate = swaps / segments if segments else 0.0
    if swap_rate < 0.05:
        diversity = _clamp(swap_rate / 0.05 * 0.5)  # ramp up
    elif swap_rate <= 0.30:
        diversity = 1.0  # ideal zone
    else:
        diversity = _clamp(1.0 - (swap_rate - 0.30) / 0.30 * 0.5)  # ramp down

    # ── Scene coverage (15%) ──
    # footage_segments / total_segments
    # Look for coverage info in metadata (set by render.py)
    footage_info = metadata.get("footage_coverage") or {}
    footage_count = footage_info.get("segments_with_footage", 0)
    total_segs = footage_info.get("total_segments", segments)
    scene_coverage = _clamp(footage_count / total_segs) if total_segs else 0.0
    # Fallback: if no footage_coverage, use embedding_ratio as proxy
    if scene_coverage == 0.0 and emb_ratio > 0:
        scene_coverage = emb_ratio

    breakdown = {
        "match_quality": round(match_quality, 4),
        "duration_fit": round(duration_fit, 4),
        "diversity": round(diversity, 4),
        "scene_coverage": round(scene_coverage, 4),
    }

    total = (
        match_quality * 0.40
        + duration_fit * 0.25
        + diversity * 0.20
        + scene_coverage * 0.15
    ) * 100.0

    return round(total, 2), breakdown


# ── Race orchestration ────────────────────────────────────


def run_race(
    candidates: List[CandidateConfig],
    *,
    movie: str,
    style: str,
    duration: int,
    voice: Optional[str],
    video_format: str,
    output_base: Path,
    keep_cache: bool = False,
    video: Optional[str] = None,
    library_dir: Optional[str] = None,
    research: Optional[bool] = None,
    bgm: Optional[str] = None,
    no_bgm: bool = False,
    no_clips: bool = True,  # clips not needed during race
    strict: bool = False,
    config_path: Optional[str] = None,
    subtitle_lang: Optional[str] = None,
    subtitle_mode: Optional[str] = None,
    auto_pick: bool = False,
) -> List[CandidateResult]:
    """Run all candidates and return ranked results.

    Each candidate runs in its own subdirectory under ``output_base``.
    After all runs, results are sorted by score (descending).

    Args:
        candidates: List of candidate configurations to run.
        auto_pick: If True, copy the best candidate's output to
            ``output_base`` root after all runs complete.
        All other args mirror ``build_context``.

    Returns:
        List of :class:`CandidateResult`, sorted by score descending.
    """
    from .pipeline.runner import build_context, common_build_kwargs, run_pipeline
    from .pipeline.errors import PipelinePaused
    from .pipeline.preflight import PreflightError

    results: List[CandidateResult] = []

    for i, cand in enumerate(candidates):
        cand_dir = output_base / f"candidate-{i+1}-{cand.label}"
        cand_dir.mkdir(parents=True, exist_ok=True)

        result = CandidateResult(
            config=cand,
            output_dir=cand_dir,
        )

        try:
            ctx = build_context(**common_build_kwargs(
                movie=movie,
                style=style,
                duration=duration,
                voice=voice,
                video_format=video_format,
                output_dir=cand_dir,
                keep_cache=keep_cache,
                video=video,
                library_dir=library_dir,
                research=research,
                bgm=bgm,
                no_bgm=no_bgm,
                no_clips=no_clips,
                strict=strict,
                params=cand.to_params(),
                config_path=config_path,
                subtitle_lang=subtitle_lang,
                subtitle_mode=subtitle_mode,
                narration_preset=cand.narration_preset,
                lang="zh",  # race mode defaults to Chinese
            ))

            ctx = run_pipeline(ctx)

            # Collect metrics
            result.video_path = ctx.video_path
            result.match_summary = ctx.metadata.get("match_summary")
            result.duration_metrics = ctx.metadata.get("duration_metrics")

            # Extract footage coverage for scoring
            footage_coverage = _extract_footage_coverage(ctx)
            if footage_coverage:
                ctx.metadata["footage_coverage"] = footage_coverage

            result.score, result.score_breakdown = score_candidate(ctx.metadata)
            result.metadata_path = cand_dir / "metadata.json"

        except PipelinePaused:
            result.error = "paused"
        except PreflightError as e:
            result.error = f"preflight: {e}"
        except (OSError, RuntimeError) as e:
            result.error = str(e)
            logger.exception(f"Candidate '{cand.label}' failed: {e}")

        results.append(result)

    # Sort by score descending
    results.sort(key=lambda r: r.score, reverse=True)

    # Auto-pick: copy best candidate to output_base
    if auto_pick and results and results[0].video_path:
        _promote_best(results[0], output_base)

    return results


def _extract_footage_coverage(ctx: Context) -> Optional[Dict[str, Any]]:
    """Extract footage coverage stats from context metadata.

    The render step stores coverage info in metadata under various keys.
    This consolidates them into a standard structure for scoring.
    """
    md = ctx.metadata
    # render.py stores coverage_ratio and related stats
    coverage_ratio = md.get("footage_coverage_ratio")
    if coverage_ratio is not None:
        total = md.get("total_segments", len(ctx.matched_clips))
        with_footage = int(coverage_ratio * total) if total else 0
        return {
            "segments_with_footage": with_footage,
            "total_segments": total,
            "ratio": coverage_ratio,
        }

    # Fallback: count clips with actual video footage
    total = len(ctx.matched_clips)
    if total == 0:
        return None
    with_footage = sum(
        1 for mc in ctx.matched_clips if mc.scene_index is not None and mc.scene_index >= 0
    )
    return {
        "segments_with_footage": with_footage,
        "total_segments": total,
        "ratio": with_footage / total if total else 0.0,
    }


def _promote_best(best: CandidateResult, output_base: Path) -> None:
    """Copy the best candidate's video to the output base directory."""
    if not best.video_path or not Path(best.video_path).exists():
        return
    dest = output_base / Path(best.video_path).name
    try:
        shutil.copy2(best.video_path, dest)
        logger.info(f"Best candidate '{best.config.label}' promoted to {dest}")
    except (OSError, RuntimeError) as e:
        logger.warning(f"Failed to promote best candidate: {e}")


# ── Report formatting ─────────────────────────────────────


def format_race_report(results: List[CandidateResult]) -> str:
    """Format a human-readable comparison table.

    Args:
        results: List of candidate results (should be pre-sorted).

    Returns:
        Multi-line string suitable for CLI output.
    """
    if not results:
        return "No candidates were run."

    lines: List[str] = []
    lines.append("=" * 72)
    lines.append("  Multi-Candidate Race Results")
    lines.append("=" * 72)
    lines.append("")

    # Header
    header = (
        f"  {'#':<3} {'Candidate':<14} {'Score':>7}  "
        f"{'Match':>6}  {'Dur':>6}  {'Diver':>6}  {'Cover':>6}  "
        f"{'Status':<10}"
    )
    lines.append(header)
    lines.append(f"  {'-'*3} {'-'*14} {'-'*7}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*6}  {'-'*10}")

    for i, r in enumerate(results):
        bd = r.score_breakdown
        match_v = f"{bd.get('match_quality', 0):.2f}"
        dur_v = f"{bd.get('duration_fit', 0):.2f}"
        div_v = f"{bd.get('diversity', 0):.2f}"
        cov_v = f"{bd.get('scene_coverage', 0):.2f}"
        status = "OK" if r.error is None else f"ERR: {r.error[:20]}"

        medal = ""
        if i == 0:
            medal = " *"
        lines.append(
            f"  {i+1:<3} {r.config.label:<14} {r.score:>6.1f}{medal}  "
            f"{match_v:>6}  {dur_v:>6}  {div_v:>6}  {cov_v:>6}  "
            f"{status:<10}"
        )

    lines.append("")
    lines.append("  * = best candidate")

    # Detailed breakdown for the winner
    if results and results[0].error is None:
        winner = results[0]
        lines.append("")
        lines.append("-" * 72)
        lines.append(f"  Winner: {winner.config.label}")
        lines.append(f"    Preset:           {winner.config.narration_preset}")
        lines.append(f"    Match topk:       {winner.config.match_topk}")
        lines.append(f"    Reuse penalty:    {winner.config.match_topk_reuse_penalty}")
        lines.append(f"    Diversity window: {winner.config.match_diversity_window}")
        lines.append(f"    Output:           {winner.video_path or 'N/A'}")

        if winner.match_summary:
            ms = winner.match_summary
            lines.append(f"    Segments:         {ms.get('segments', '?')}")
            lines.append(f"    Embedding ratio:  {ms.get('embedding_ratio', '?')}")
            lines.append(f"    Heuristic ratio:  {ms.get('heuristic_ratio', '?')}")
            score = ms.get("score") or {}
            if score.get("avg") is not None:
                lines.append(f"    Avg match score:  {score['avg']:.4f}")

        if winner.duration_metrics:
            dm = winner.duration_metrics
            lines.append(
                f"    Duration:         {dm.get('actual_sec', '?')}s "
                f"/ target {dm.get('target_sec', '?')}s "
                f"(ratio {dm.get('ratio', '?'):.2f})"
            )

    lines.append("")
    lines.append("=" * 72)

    return "\n".join(lines)


def save_race_report(
    results: List[CandidateResult],
    output_path: Path,
) -> None:
    """Save the race report as JSON for programmatic consumption.

    Args:
        results: List of candidate results.
        output_path: Path to write the JSON report.
    """
    report = {
        "version": 1,
        "candidates": [],
    }

    for i, r in enumerate(results):
        report["candidates"].append({
            "rank": i + 1,
            "label": r.config.label,
            "preset": r.config.narration_preset,
            "match_topk": r.config.match_topk,
            "reuse_penalty": r.config.match_topk_reuse_penalty,
            "diversity_window": r.config.match_diversity_window,
            "score": r.score,
            "score_breakdown": r.score_breakdown,
            "video_path": r.video_path,
            "output_dir": str(r.output_dir),
            "error": r.error,
            "match_summary": r.match_summary,
            "duration_metrics": r.duration_metrics,
        })

    output_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
