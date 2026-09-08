# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared private helpers for the CLI commands.

This module keeps the patchable lifetime-scope names (e.g. ``_EXAMPLE_YAML``,
``InteractiveCLIController``, the match-summary formatters) available to the
``movie_narrator.cli`` package surface.  Command handlers live in
:mod:`movie_narrator.cli.commands` and import from here.
"""

from pathlib import Path
from typing import Any, Dict, Optional

import typer

from ..models import Context
from ..utils.sanitize import sanitize_filename as _sanitize_filename  # noqa: F401

# Packaged example YAML — used as fallback when no --config and no cwd/job.yaml.
_EXAMPLE_YAML = (
    Path(__file__).resolve().parent.parent.parent.parent / "examples" / "job.example.yaml"
)


def _format_match_summary(ctx: Context) -> Optional[str]:
    """Format a one-line match summary from ctx.metadata for CLI output.

    Returns:
        None if no match_summary is available (e.g. match step skipped).
    """
    ms: Optional[Dict[str, Any]] = ctx.metadata.get("match_summary")
    if not ms:
        return None

    segments = ms.get("segments", 0)
    sc = ms.get("source_counts", {})
    emb = sc.get("embedding", 0)
    heur = sc.get("heuristic", 0)
    fb = sc.get("fallback", 0)
    scene = sc.get("scene", 0)

    parts: list[str] = [f"match: {segments} segs"]

    source_parts: list[str] = []
    if emb:
        pct = round(emb / segments * 100) if segments else 0
        source_parts.append(f"emb {emb}({pct}%)")
    if heur:
        pct = round(heur / segments * 100) if segments else 0
        source_parts.append(f"heur {heur}({pct}%)")
    if fb:
        source_parts.append(f"fb {fb}")
    if scene:
        source_parts.append(f"scene {scene}")
    if source_parts:
        parts.append(" | ".join(source_parts))

    score = ms.get("score")
    if score and isinstance(score, dict):
        avg = score.get("avg")
        if avg is not None:
            parts.append(f"avg {avg:.2f}")

    degraded = ms.get("degraded_reason")
    if degraded:
        parts.append(f"degraded: {degraded}")

    return " | ".join(parts)


def _format_degradation_hints(ctx: Context) -> list[str]:
    """
    Returns:
        Human-readable degradation hints for the CLI output.
    """
    hints: list[str] = []
    ms: Optional[Dict[str, Any]] = ctx.metadata.get("match_summary")
    if ms:
        degraded = ms.get("degraded_reason")
        if degraded == "fake_captions":
            hints.append(
                "match: using fake captions (no WhisperX) — "
                "scene matching is heuristic-only, install [ml] extras for embedding match"
            )
        elif degraded == "all_heuristic":
            hints.append(
                "match: all segments fell back to heuristic — check embedding model availability"
            )
        elif degraded:
            hints.append(f"match degraded: {degraded}")

    degraded_steps = ctx.metadata.get("_degraded_steps", [])
    if degraded_steps:
        hints.append(f"degraded steps: {', '.join(degraded_steps)} — see metadata.json for details")

    return hints


class InteractiveCLIController:
    """RunController with interactive retry/skip/abort on hard step failure.

    Used when ``--retry`` is passed to ``mn create``.  When a hard step
    raises an exception, the user is prompted to choose:

    - **R** — retry the step (ctx state is preserved, so cached partial
      results like TTS segments are reused)
    - **S** — skip the step and continue (downstream may fail)
    - **A** — abort the pipeline
    """

    def __init__(self):
        self._cancelled = False

    def is_cancelled(self) -> bool:
        """Mark the pipeline as cancelled."""
        return self._cancelled

    def on_step_error(self, step_name: str, error: Exception, attempt: int):
        """Handle errors during pipeline step execution."""
        from ..pipeline.errors import StepAction

        typer.echo(
            f"\n  Step '{step_name}' failed (attempt {attempt}): {error}",
            err=True,
        )
        typer.echo("  [R]etry  [S]kip  [A]bort", err=True)
        try:
            choice = input("  > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return StepAction.ABORT
        if choice.startswith("r"):
            return StepAction.RETRY
        elif choice.startswith("s"):
            return StepAction.SKIP
        return StepAction.ABORT
