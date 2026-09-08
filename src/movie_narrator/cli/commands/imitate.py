# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ``mn imitate`` command — reference-video style imitation."""

from pathlib import Path

import typer

from movie_narrator.cli._helpers import (
    InteractiveCLIController,
    _format_match_summary,
    _sanitize_filename,
)
from movie_narrator.cli.options import (
    BgmOpt,
    ConfigOpt,
    DurationOpt,
    KeepCacheOpt,
    LibraryDirOpt,
    MovieOpt,
    NoBgmOpt,
    NoClipsOpt,
    OutputDirImitate,
    ResearchOpt,
    RetryOpt,
    StrictOpt,
    StyleOpt,
    SubtitleLangOpt,
    SubtitleModeOpt,
    VideoFormatOpt,
    VideoOpt,
    VoicePlain,
)
from movie_narrator.pipeline.runner import common_build_kwargs
from movie_narrator.utils.log import resolve_log_level


def imitate(
    movie: MovieOpt = None,

    style: StyleOpt = "热血搞笑",

    duration: DurationOpt = 60,

    voice: VoicePlain = None,

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

    config: ConfigOpt = None,

    subtitle_lang: SubtitleLangOpt = None,

    subtitle_mode: SubtitleModeOpt = None,

    output_dir: OutputDirImitate = None,


    reference: str = typer.Option(
        ...,
        "--reference",
        "-r",
        help="参考视频路径 / Reference video path (viral narration to imitate)",
    ),


    analyze_only: bool = typer.Option(
        False,
        "--analyze-only",
        help="只分析参考片不生成 / Only analyze reference, don't generate",
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
    """Reference video imitation — extract style from a hit video and generate new content in the same style.

    Analyzes sentence density, cut density, and rhythm of the reference video, automatically generates a temporary preset,
    then runs the standard pipeline with that preset to generate new narration.


    Examples:
            mn imitate -r viral_ref.mp4 -m Inception --video movie.mp4
            mn imitate -r viral_ref.mp4 --analyze-only
            mn imitate -r viral_ref.mp4 -m Inception --video movie.mp4 --strict
    """
    from movie_narrator.imitate import (
        analyze_reference,
        metrics_to_params,
        metrics_to_preset_name,
        format_analysis_report,
    )

    if not Path(reference).is_file():
        raise typer.BadParameter(
            f"reference video not found: {reference}",
            param_hint="--reference",
        )

    if movie is None and not analyze_only:
        raise typer.BadParameter(
            "movie is required (set --movie or use --analyze-only)",
            param_hint="--movie",
        )

    # Analyze the reference video
    out_dir = (
        Path(output_dir)
        if output_dir
        else Path("output") / f"{_sanitize_filename(movie or 'reference')}_imitate"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    typer.echo(f"Analyzing reference: {reference}")
    metrics = analyze_reference(reference, output_dir=out_dir)
    typer.echo("")

    report = format_analysis_report(metrics)
    typer.echo(report)

    if analyze_only:
        typer.echo(f"\nAnalysis saved to: {out_dir / 'reference_analysis.json'}")
        return

    # Generate params from metrics
    params = metrics_to_params(metrics)
    preset_name = metrics_to_preset_name(metrics)

    typer.echo(f"\nUsing preset: {preset_name}")
    typer.echo(f"Generated {len(params)} custom parameters")

    # Build context and run pipeline
    from movie_narrator.pipeline.runner import build_context, run_pipeline
    from movie_narrator.pipeline.errors import PipelinePaused
    from movie_narrator.pipeline.preflight import PreflightError

    _resolved_level = resolve_log_level(log_level)

    assert movie is not None
    ctx = build_context(
        **common_build_kwargs(
            movie=movie,
            style=style,
            duration=duration,
            voice=voice,
            video_format=video_format,
            output_dir=out_dir,
            keep_cache=keep_cache,
            video=video,
            library_dir=library_dir,
            research=research,
            bgm=bgm,
            no_bgm=no_bgm,
            no_clips=no_clips,
            strict=strict,
            params=params,
            config_path=config,
            subtitle_lang=subtitle_lang,
            subtitle_mode=subtitle_mode,
            narration_preset=preset_name,
            lang="zh",  # imitate command defaults to Chinese
            log_level=_resolved_level,
            verbose=verbose,
        )
    )

    controller = InteractiveCLIController() if retry else None

    try:
        ctx = run_pipeline(ctx, controller=controller)
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
    match_line = _format_match_summary(ctx)
    if match_line:
        typer.echo(f"  {match_line}", err=True)
    typer.echo(f"{ctx.video_path}")
