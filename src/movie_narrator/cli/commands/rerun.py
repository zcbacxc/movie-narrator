# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ``mn rerun`` command — deliberately re-execute the pipeline from a step."""

from pathlib import Path
from typing import Optional

import typer

from movie_narrator.cli._helpers import InteractiveCLIController
from movie_narrator.utils.log import resolve_log_level


def rerun(
    state: Optional[str] = typer.Argument(
        None, help="pipeline_state.json 路径 / Path to pipeline state file"
    ),
    from_step: Optional[str] = typer.Option(
        None,
        "--from",
        help=(
            "从此步骤重新执行（含该步骤及之后所有步骤，前置步骤状态保留）；"
            "若晚于已完成的步骤则退化为 resume 行为 / "
            "Restart from this step (the step and everything after it re-executes; "
            "steps before it keep their saved state). If --from is after the saved "
            "completed step, this degenerates to resume behavior (no upstream "
            "re-execution)."
        ),
    ),
    list_steps: bool = typer.Option(
        False,
        "--list-steps",
        help="列出有序步骤名（标注软步骤）后退出 / Print ordered step names (soft steps marked) and exit",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help=(
            "只打印重跑计划（起始步骤/将失效步骤/可复用上游步骤）后退出，"
            "不执行管线 / Print the rerun plan (from-step, invalidated steps, "
            "reusable upstream steps) and exit WITHOUT running the pipeline"
        ),
    ),
    retry: bool = typer.Option(
        False,
        "--retry",
        help="硬步骤失败时交互重试 / Enable interactive retry on hard step failure",
    ),
    log_level: str = typer.Option(
        "DEBUG",
        "--log-level",
        help="日志级别 DEBUG|INFO|WARNING|ERROR / Log level (default: DEBUG)",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="在控制台显示 DEBUG 日志 / Show debug logs in console",
    ),
):
    """Deliberately re-execute the pipeline from a chosen step.

    Unlike ``mn resume`` (crash recovery — continue from the step AFTER
    the last completed one), ``mn rerun --from STEP`` re-executes the
    named step and every step after it: soft-step status fields of the
    invalidated steps are reset so downstream steps re-run cleanly
    instead of skipping on stale success states. The decision is
    recorded in ``ctx.metadata["rerun"]`` for auditability.

    Examples:
            mn rerun --list-steps
            mn rerun output/movie/pipeline_state.json --from render_video
            mn rerun output/movie/pipeline_state.json --from render_video --dry-run
    """
    from movie_narrator.pipeline.runner import (
        SOFT_STATUS_STEPS,
        _load_pipeline_state,
        ordered_step_names,
        prepare_rerun,
        run_pipeline,
    )
    from movie_narrator.pipeline.errors import PipelinePaused
    from movie_narrator.pipeline.preflight import PreflightError
    from movie_narrator.models import Services
    from movie_narrator.utils.console import Console, build_console

    if list_steps:
        for name in ordered_step_names():
            typer.echo(f"{name} (soft)" if name in SOFT_STATUS_STEPS else name)
        raise typer.Exit(code=0)

    if not state:
        raise typer.BadParameter("STATE is required unless --list-steps is given.")

    ordered = ordered_step_names()
    if from_step not in ordered:
        raise typer.BadParameter(
            f"Unknown step '{from_step}'. Valid steps: {', '.join(ordered)}"
        )

    state_path = Path(state)
    if not state_path.is_file():
        typer.echo(f"State file not found: {state}", err=True)
        raise typer.Exit(code=1)

    ctx, completed_step = _load_pipeline_state(state_path)

    if dry_run:
        # v1.4.2: compute the invalidation plan via the same v1.3.0
        # helper the real rerun uses, print it, and stop — the pipeline
        # never executes (the state file is left untouched too).
        invalidated = prepare_rerun(ctx, completed_step, from_step)
        idx = ordered.index(from_step)
        reusable = ordered[:idx]
        typer.echo("▶ Rerun plan (dry run — no steps executed)")
        typer.echo(f"  from step:        {from_step}")
        typer.echo(f"  state completed:  {completed_step}")
        typer.echo(
            f"  reusable upstream ({len(reusable)}): "
            + (", ".join(reusable) if reusable else "(none)")
        )
        typer.echo(
            f"  invalidated ({len(invalidated)}): "
            + (", ".join(invalidated) if invalidated else "(none)")
        )
        typer.echo("✓ Dry run complete — pipeline not executed.")
        raise typer.Exit(code=0)

    _resolved_level = resolve_log_level(log_level)

    # Re-inject a real console (serialized state has SilentConsole)
    console: Console = build_console(
        Path(ctx.output_dir),
        log_level=_resolved_level,
        verbose=verbose,
    )
    ctx.services = Services(
        console=console,
        logger=getattr(console, "_log", None),
    )

    invalidated = prepare_rerun(ctx, completed_step, from_step)
    console.debug(
        f"Rerunning from step '{from_step}' (state completed: {completed_step}); "
        f"invalidated: {', '.join(invalidated)}"
    )

    controller = InteractiveCLIController() if retry else None
    try:
        ctx = run_pipeline(ctx, controller=controller, start_step=from_step)
    except Exception as e:  # noqa: BLE001 — CLI top-level error barrier
        if isinstance(e, PipelinePaused):
            typer.echo(
                f"\n⏸ Pipeline paused after '{e.completed_step}'. "
                f'Resume with: mn resume --state "{Path(ctx.output_dir) / "pipeline_state.json"}"'
            )
            raise typer.Exit(code=0)
        if isinstance(e, PreflightError):
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)
        raise typer.Exit(code=1)

    if ctx.metadata.get("script_degraded"):
        typer.echo(
            "⚠ 警告：旁白为占位内容——LLM 不可达。请检查 LLM 连接后重试。",
            err=True,
        )
    if ctx.video_path:
        typer.echo(f"{ctx.video_path}")
