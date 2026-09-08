# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ``mn create`` end-to-end narration command."""

from pathlib import Path
from typing import Optional

import typer

import movie_narrator.cli as _cli

from movie_narrator.cli._helpers import (
    InteractiveCLIController,
    _format_degradation_hints,
    _format_match_summary,
    _sanitize_filename,
)
from movie_narrator.cli.options import (
    BgmOpt,
    ConfigOpt,
    DryRunOpt,
    DurationOpt,
    KeepCacheOpt,
    LibraryDirOpt,
    MovieOpt,
    NarrationPresetOpt,
    NoBgmOpt,
    NoClipsOpt,
    OutputDirCreate,
    ResearchOpt,
    RetryOpt,
    StrictOpt,
    StyleOpt,
    SubtitleLangOpt,
    SubtitleModeOpt,
    VideoFormatOpt,
    VideoOpt,
    VoiceWithSign,
)
from movie_narrator.pipeline.runner import apply_dry_run_steps, common_build_kwargs
from movie_narrator.utils.log import resolve_log_level


def create(
    movie: MovieOpt = None,

    style: StyleOpt = "热血搞笑",

    duration: DurationOpt = 60,

    voice: VoiceWithSign = None,

    video_format: VideoFormatOpt = "16:9",

    keep_cache: KeepCacheOpt = False,

    video: VideoOpt = None,

    library_dir: LibraryDirOpt = None,

    research: ResearchOpt = None,

    bgm: BgmOpt = None,

    no_bgm: NoBgmOpt = False,

    no_clips: NoClipsOpt = False,

    strict: StrictOpt = False,

    retry: RetryOpt = False,

    dry_run: DryRunOpt = False,

    config: ConfigOpt = None,
    # Multi-language subtitle (v0.3).

    subtitle_lang: SubtitleLangOpt = None,

    subtitle_mode: SubtitleModeOpt = None,

    narration_preset: NarrationPresetOpt = None,

    output_dir: OutputDirCreate = None,


    narrator_perspective: Optional[str] = typer.Option(
        None,
        "--narrator-perspective",
        help="解说视角 omniscient | character | detective / Narrator perspective mode",
    ),


    focus_character: Optional[str] = typer.Option(
        None,
        "--focus-character",
        help="聚焦角色名(配合 character 视角) / Focus character name (used with 'character' perspective)",
    ),


    pause_at: Optional[str] = typer.Option(
        None,
        "--pause-at",
        help="在指定步骤后暂停(人在环) / Pause after this step name "
        "(e.g. match_clips, generate_script). Resume with: mn resume --state <path>",
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
    )


):
    """Generate a narrated short video — end-to-end from movie name to final output.


    Examples:
            mn create -m Inception -p douyin-fast
            mn create -m Inception -p mainstream-dry --bgm music.mp3
            mn create --config job.yaml

        List available presets:
            mn preset
    """
    from movie_narrator.config import get_settings
    from movie_narrator.workflow import JobConfigError, load_job_config, merge_job

    if config is None and movie is None:
        raise typer.BadParameter(
            "movie is required (set --movie or config.movie)",
            param_hint="--movie",
        )

    # Auto-discover YAML config: explicit --config > job.yaml (cwd) >
    # job.example.yaml (package examples dir) > none.
    job = None
    config_path = None
    if config is not None:
        config_path = str(Path(config))
        if not Path(config_path).is_file():
            raise typer.BadParameter(
                f"config not found: {config_path}",
                param_hint="--config",
            )
    else:
        # Try cwd/job.yaml first (user's project-level config).
        cwd_yaml = Path.cwd() / "job.yaml"
        if cwd_yaml.is_file():
            config_path = str(cwd_yaml)
        else:
            # Fall back to the packaged example so new users get sensible
            # defaults without needing to create a YAML manually.
            if _cli._EXAMPLE_YAML.is_file():
                config_path = str(_cli._EXAMPLE_YAML)

    if config_path is not None:
        try:
            job = load_job_config(config_path)
        except JobConfigError as e:
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)

    cli_snapshot = {
        "movie": movie,
        "style": style,
        "duration": duration,
        "voice": voice,
        "video_format": video_format,
        "keep_cache": keep_cache,
        "video": video,
        "library_dir": library_dir,
        "research": research,
        "bgm": bgm,
        "no_bgm": no_bgm,
        "no_clips": no_clips,
        "strict": strict,
        "retry": retry,
        "config_path": config_path,
        "subtitle_lang": subtitle_lang,
        "subtitle_mode": subtitle_mode,
        "narration_preset": narration_preset,
        "narrator_perspective": narrator_perspective,
        "focus_character": focus_character,
    }
    resolved = merge_job(cli_snapshot, job, get_settings())

    if not resolved.movie:
        raise typer.BadParameter(
            "movie is required (set --movie or config.movie)",
            param_hint="--movie",
        )

    if resolved.video and not Path(resolved.video).is_file():
        raise typer.BadParameter(
            f"video not found: {resolved.video}",
            param_hint="--video",
        )

    # --output-dir / -o: user-specified output directory.
    # Default: output/<sanitized-movie-name>
    out_dir = (
        Path(output_dir) if output_dir else Path("output") / _sanitize_filename(resolved.movie)
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    _resolved_level = resolve_log_level(log_level)

    # --dry-run: keep planning steps only. Heavy steps (TTS / FFmpeg /
    # render / QA) are disabled via the existing workflow_steps mechanism,
    # never by a second hardcoded pipeline. User step flags are applied
    # first, then dry-run disables its authoritative step set.
    workflow_steps = resolved.workflow_steps or None
    if dry_run:
        workflow_steps = apply_dry_run_steps(resolved.workflow_steps)
        typer.echo(
            "Dry-run mode: generating research/script/storyboard only — "
            "skipping TTS, alignment, rendering and QA. No final.mp4 will be produced."
        )

    ctx = _cli.build_context(
        **common_build_kwargs(
            movie=resolved.movie,
            style=resolved.style,
            duration=resolved.duration,
            voice=resolved.voice,
            video_format=resolved.video_format,
            output_dir=out_dir,
            keep_cache=resolved.keep_cache,
            video=resolved.video,
            library_dir=resolved.library_dir,
            research=resolved.research,
            bgm=resolved.bgm,
            no_bgm=resolved.no_bgm,
            no_clips=resolved.no_clips,
            strict=resolved.strict,
            workflow_steps=workflow_steps,
            params=resolved.params or None,
            config_path=resolved.config_path,
            subtitle_lang=resolved.subtitle_lang,
            subtitle_mode=resolved.subtitle_mode,
            narration_preset=resolved.narration_preset or narration_preset,
            lang=resolved.lang,
            log_level=_resolved_level,
            verbose=verbose,
        )
    )
    controller = InteractiveCLIController() if retry else None

    # Store pause-at request in context metadata
    if pause_at:
        ctx.metadata["pause_at"] = pause_at
    if dry_run:
        ctx.metadata["dry_run"] = True

    try:
        ctx = _cli.run_pipeline(ctx, controller=controller)
    except Exception as e:  # noqa: BLE001 — CLI top-level error barrier
        # PipelinePaused — state saved, inform user how to resume
        from movie_narrator.pipeline.errors import PipelinePaused

        if isinstance(e, PipelinePaused):
            typer.echo(
                f"\n⏸ Pipeline paused after '{e.completed_step}'. "
                f'Resume with: mn resume --state "{Path(ctx.output_dir) / "pipeline_state.json"}"'
            )
            raise typer.Exit(code=0)
        # PreflightError gets a targeted remediation hint.
        from movie_narrator.pipeline.preflight import PreflightError

        if isinstance(e, PreflightError):
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)
        # step_err already printed the single-line summary and wrote the
        # full traceback to the log file.  Suppress Typer's Rich
        # traceback to keep the console output clean.
        raise typer.Exit(code=1)
    if ctx.metadata.get("script_degraded"):
        typer.echo(
            "⚠ 警告：旁白为占位内容——LLM 不可达。请检查 LLM 连接后重试。",
            err=True,
        )
    # E.5: one-line match summary + degradation hints
    match_line = _format_match_summary(ctx)
    if match_line:
        typer.echo(f"  {match_line}", err=True)
    for hint in _format_degradation_hints(ctx):
        typer.echo(f"  ⚠ {hint}", err=True)
    if dry_run:
        if ctx.script_md_path:
            typer.echo(f"Dry-run complete — script written to: {ctx.script_md_path}")
        else:
            typer.echo("Dry-run complete — no final.mp4 produced.")
    else:
        typer.echo(f"{ctx.video_path}")
