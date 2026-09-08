# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ``mn resume`` command — resume a paused pipeline from its checkpoint."""

from pathlib import Path

import typer

from movie_narrator.cli._helpers import InteractiveCLIController
from movie_narrator.utils.log import resolve_log_level


def resume(
    state: str = typer.Option(
        ..., "--state", help="pipeline_state.json 路径 / Path to pipeline state file"
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
    """Resume a paused pipeline — continue from the last checkpoint.


    Examples:
            mn resume --state output/movie/pipeline_state.json
    """
    from movie_narrator.pipeline.runner import (
        _load_pipeline_state,
        _next_step_after,
        run_pipeline,
    )
    from movie_narrator.pipeline.errors import PipelinePaused
    from movie_narrator.pipeline.preflight import PreflightError
    from movie_narrator.utils.console import Console, build_console

    state_path = Path(state)
    if not state_path.is_file():
        typer.echo(f"State file not found: {state}", err=True)
        raise typer.Exit(code=1)

    ctx, completed_step = _load_pipeline_state(state_path)

    _resolved_level = resolve_log_level(log_level)

    # Re-inject a real console (serialized state has SilentConsole)
    from movie_narrator.models import Services

    console: Console = build_console(
        Path(ctx.output_dir),
        log_level=_resolved_level,
        verbose=verbose,
    )
    ctx.services = Services(
        console=console,
        logger=getattr(console, "_log", None),
    )

    # Determine the step to start from (the step AFTER the completed one)
    start_step = _next_step_after(completed_step)
    if start_step is None:
        typer.echo(f"Pipeline already completed (last step: {completed_step}). Nothing to resume.")
        raise typer.Exit(code=0)

    console = ctx.services.console
    console.debug(f"Resuming from step '{start_step}' (completed: {completed_step})")

    controller = InteractiveCLIController() if retry else None
    try:
        ctx = run_pipeline(ctx, controller=controller, start_step=start_step)
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
