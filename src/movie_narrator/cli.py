# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Command-line interface for Movie Narrator."""

import json
from pathlib import Path
from typing import Any, Dict, Optional, cast

import typer

from . import __version__
from .models import Context
from .pipeline.resolve import resolve_video
from .pipeline.research import research_plot
from .pipeline.runner import build_context, common_build_kwargs, run_pipeline
from .utils.log import resolve_log_level


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


app = typer.Typer(
    help="Movie Narrator — 从一个提示词生成解说短视频 / Generate narrated movie recap videos from a single prompt.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

# Packaged example YAML — used as fallback when no --config and no cwd/job.yaml.
_EXAMPLE_YAML = Path(__file__).resolve().parent.parent.parent / "examples" / "job.example.yaml"


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
        from .pipeline.errors import StepAction

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


from .utils.sanitize import sanitize_filename as _sanitize_filename  # noqa: E402


@app.command()
def create(
    movie: Optional[str] = typer.Option(None, "--movie", "-m", help="电影名称 / Movie name"),
    style: str = typer.Option("热血搞笑", "--style", "-s", help="解说风格 / Narration style"),
    duration: int = typer.Option(
        60, "--duration", "-d", help="目标时长(秒) / Target duration (seconds)"
    ),
    voice: Optional[str] = typer.Option(
        None, "--voice", "-v", help="TTS 语音 / TTS voice (Edge TTS)"
    ),
    video_format: str = typer.Option(
        "16:9",
        "--video-format",
        "--format",
        "-f",
        help="视频格式 16:9 或 9:16 / Video format: 16:9 or 9:16",
    ),
    keep_cache: bool = typer.Option(
        False, "--keep-cache", help="保留 TTS 缓存 / Keep TTS cache files"
    ),
    video: Optional[str] = typer.Option(
        None, "--video", help="源视频文件路径 / Source movie file path"
    ),
    library_dir: Optional[str] = typer.Option(
        None, "--library-dir", help="影视库目录 / Movie library directory"
    ),
    research: Optional[bool] = typer.Option(
        None, "--research/--no-research", help="启用剧��研究 / Enable plot research"
    ),
    bgm: Optional[str] = typer.Option(None, "--bgm", help="背景音乐文件 / Background music file"),
    no_bgm: bool = typer.Option(
        False, "--no-bgm", help="禁用 BGM / Disable BGM even if default set"
    ),
    no_clips: bool = typer.Option(False, "--no-clips", help="跳过片段导出 / Skip clips/export"),
    strict: bool = typer.Option(
        False, "--strict", help="软步骤失败即中止 / Abort on soft step failure"
    ),
    retry: bool = typer.Option(
        False,
        "--retry",
        help="硬步骤失败时交互重试 / Enable interactive retry on hard step failure",
    ),
    config: Optional[str] = typer.Option(
        None, "--config", help="job YAML ��置路径 / Path to job YAML config"
    ),
    # Multi-language subtitle (v0.3).
    subtitle_lang: Optional[str] = typer.Option(
        None,
        "--subtitle-lang",
        help="目标语言标签(如 en, ja, zh-TW) / Target language tag; empty = off",
    ),
    subtitle_mode: Optional[str] = typer.Option(
        None,
        "--subtitle-mode",
        help="字幕模式 original|translated|bilingual / Overlay mode",
    ),
    narration_preset: Optional[str] = typer.Option(
        None,
        "--narration-preset",
        "-p",
        help="解说风格预设 douyin-fast | mainstream-dry | bilibili-long / Narration style preset",
    ),
    narrator_perspective: Optional[str] = typer.Option(
        None,
        "--narrator-perspective",
        help="解说视角 omniscient | character | detective / Narrator perspective mode",
    ),
    focus_character: Optional[str] = typer.Option(
        None,
        "--focus-character",
        help="聚焦角色名(��合 character 视角) / Focus character name (used with 'character' perspective)",
    ),
    output_dir: Optional[str] = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="输出目录(默认 output/<电影名>) / Output directory (default: output/<movie>)",
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
    ),
):
    """Generate a narrated short video — end-to-end from movie name to final output.

    

    Examples:
            mn create -m Inception -p douyin-fast
            mn create -m Inception -p mainstream-dry --bgm music.mp3
            mn create --config job.yaml

        
        List available presets:
            mn preset
    """
    from .config import get_settings
    from .workflow import JobConfigError, load_job_config, merge_job

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
            if _EXAMPLE_YAML.is_file():
                config_path = str(_EXAMPLE_YAML)

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

    ctx = build_context(
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
            workflow_steps=resolved.workflow_steps or None,
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

    try:
        ctx = run_pipeline(ctx, controller=controller)
    except Exception as e:  # noqa: BLE001 — CLI top-level error barrier
        # PipelinePaused — state saved, inform user how to resume
        from .pipeline.errors import PipelinePaused

        if isinstance(e, PipelinePaused):
            typer.echo(
                f"\n⏸ Pipeline paused after '{e.completed_step}'. "
                f'Resume with: mn resume --state "{Path(ctx.output_dir) / "pipeline_state.json"}"'
            )
            raise typer.Exit(code=0)
        # PreflightError gets a targeted remediation hint.
        from .pipeline.preflight import PreflightError

        if isinstance(e, PreflightError):
            typer.echo(str(e), err=True)
            raise typer.Exit(code=1)
        # step_err already printed the single-line summary and wrote the
        # full traceback to the log file.  Suppress Typer's Rich
        # traceback to keep the console output clean.
        raise typer.Exit(code=1)
    if ctx.metadata.get("script_degraded"):
        typer.echo(
            "⚠ 警告：旁白为占位��容——LLM 不可达。请检查 LLM 连接后重试。",
            err=True,
        )
    # E.5: one-line match summary + degradation hints
    match_line = _format_match_summary(ctx)
    if match_line:
        typer.echo(f"  {match_line}", err=True)
    for hint in _format_degradation_hints(ctx):
        typer.echo(f"  ⚠ {hint}", err=True)
    typer.echo(f"{ctx.video_path}")


@app.command()
def race(
    movie: Optional[str] = typer.Option(None, "--movie", "-m", help="电影名称 / Movie name"),
    style: str = typer.Option("热血搞笑", "--style", "-s", help="解说风格 / Narration style"),
    duration: int = typer.Option(
        60, "--duration", "-d", help="目标时长(秒) / Target duration (seconds)"
    ),
    voice: Optional[str] = typer.Option(
        None, "--voice", "-v", help="TTS 语音 / TTS voice (Edge TTS)"
    ),
    video_format: str = typer.Option(
        "16:9", "--video-format", "--format", "-f", help="视频格式 16:9 或 9:16 / Video format"
    ),
    video: Optional[str] = typer.Option(
        None, "--video", help="源视频文件路径 / Source movie file path"
    ),
    library_dir: Optional[str] = typer.Option(
        None, "--library-dir", help="影视库目录 / Movie library directory"
    ),
    research: Optional[bool] = typer.Option(
        None, "--research/--no-research", help="启用剧��研究 / Enable plot research"
    ),
    bgm: Optional[str] = typer.Option(None, "--bgm", help="背景音乐文件 / Background music file"),
    no_bgm: bool = typer.Option(False, "--no-bgm", help="禁用 BGM / Disable BGM"),
    config: Optional[str] = typer.Option(
        None, "--config", help="job YAML ��置路径 / Path to job YAML config"
    ),
    candidates: int = typer.Option(
        3, "--candidates", "-n", help="候选数量(1-6) / Number of candidates (1-6)"
    ),
    presets: Optional[str] = typer.Option(
        None,
        "--presets",
        help="自定义预设列表(逗号分隔) / Custom presets (comma-separated, e.g. douyin-fast,mainstream-dry)",
    ),
    auto_pick: bool = typer.Option(
        False,
        "--auto-pick",
        help="自动选优并复制到输出根目录 / Auto-pick best and copy to output root",
    ),
    output_dir: Optional[str] = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="输出目录(默认 output/<电影名>_race) / Output directory",
    ),
):
    """Run N variants in parallel and pick the best by score.

    Each candidate uses a different preset x match seed combination. After running,
    scored by match quality / duration fit / diversity / scene coverage,
    outputs a ranking table for manual or automatic selection.

    Examples:
            mn race -m Inception --video movie.mp4
            mn race -m Inception --video movie.mp4 -n 3 --auto-pick
            mn race -m Inception --presets douyin-fast,mainstream-dry,bilibili-long
    """
    from .race import (
        generate_candidates,
        run_race,
        format_race_report,
        save_race_report,
    )

    if movie is None and config is None:
        raise typer.BadParameter(
            "movie is required (set --movie or config.movie)",
            param_hint="--movie",
        )

    # Resolve config (same logic as `mn create`)
    config_path = None
    if config is not None:
        config_path = str(Path(config))
        if not Path(config_path).is_file():
            raise typer.BadParameter(
                f"config not found: {config_path}",
                param_hint="--config",
            )

    out_base = (
        Path(output_dir)
        if output_dir
        else Path("output") / f"{_sanitize_filename(cast(str, movie))}_race"
    )
    out_base.mkdir(parents=True, exist_ok=True)

    # Parse custom presets
    preset_list = None
    if presets:
        preset_list = [p.strip() for p in presets.split(",") if p.strip()]
        candidates = len(preset_list)

    candidate_configs = generate_candidates(n=candidates, presets=preset_list)

    typer.echo(f"Starting race with {len(candidate_configs)} candidates...")
    typer.echo(f"Output base: {out_base}")
    typer.echo("")

    results = run_race(
        candidate_configs,
        movie=movie or "",
        style=style,
        duration=duration,
        voice=voice,
        video_format=video_format,
        output_base=out_base,
        video=video,
        library_dir=library_dir,
        research=research,
        bgm=bgm,
        no_bgm=no_bgm,
        config_path=config_path,
        auto_pick=auto_pick,
    )

    report = format_race_report(results)
    typer.echo(report)

    # Save JSON report
    report_path = out_base / "race_report.json"
    save_race_report(results, report_path)
    typer.echo(f"\nReport saved to: {report_path}")

    if results and results[0].error is None:
        typer.echo(f"\nBest candidate: {results[0].config.label}")
        if results[0].video_path:
            typer.echo(f"Video: {results[0].video_path}")


@app.command()
def imitate(
    reference: str = typer.Option(
        ...,
        "--reference",
        "-r",
        help="参考视频路径 / Reference video path (viral narration to imitate)",
    ),
    movie: Optional[str] = typer.Option(None, "--movie", "-m", help="电影名称 / Movie name"),
    style: str = typer.Option("热血搞笑", "--style", "-s", help="解说风格 / Narration style"),
    duration: int = typer.Option(
        60, "--duration", "-d", help="目标时长(秒) / Target duration (seconds)"
    ),
    voice: Optional[str] = typer.Option(None, "--voice", "-v", help="TTS 语音 / TTS voice"),
    video_format: str = typer.Option(
        "16:9", "--video-format", "--format", "-f", help="视频格式 / Video format"
    ),
    keep_cache: bool = typer.Option(False, "--keep-cache", help="保留 TTS 缓存 / Keep TTS cache"),
    video: Optional[str] = typer.Option(
        None, "--video", help="源视频文件路径 / Source movie file path"
    ),
    library_dir: Optional[str] = typer.Option(None, "--library-dir", help="影视库目录"),
    research: Optional[bool] = typer.Option(None, "--research/--no-research", help="启用剧��研究"),
    bgm: Optional[str] = typer.Option(None, "--bgm", help="背景音乐文件 / Background music file"),
    no_bgm: bool = typer.Option(False, "--no-bgm", help="禁用 BGM / Disable BGM"),
    no_clips: bool = typer.Option(False, "--no-clips", help="跳过片段导出 / Skip clips"),
    strict: bool = typer.Option(
        False, "--strict", help="软步骤失败即中止 / Abort on soft step failure"
    ),
    retry: bool = typer.Option(False, "--retry", help="硬步骤失败时交互重试"),
    config: Optional[str] = typer.Option(None, "--config", help="job YAML ��置路径"),
    subtitle_lang: Optional[str] = typer.Option(None, "--subtitle-lang", help="目标语言标签"),
    subtitle_mode: Optional[str] = typer.Option(None, "--subtitle-mode", help="字幕模式"),
    output_dir: Optional[str] = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="输出目录(默认 output/<电影名>_imitate) / Output directory",
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
    ),
):
    """Reference video imitation — extract style from a hit video and generate new content in the same style.

    
    Analyzes sentence density, cut density, and rhythm of the reference video, automatically generates a temporary preset,
    then runs the standard pipeline with that preset to generate new narration.

    

    Examples:
            mn imitate -r viral_ref.mp4 -m Inception --video movie.mp4
            mn imitate -r viral_ref.mp4 --analyze-only
            mn imitate -r viral_ref.mp4 -m Inception --video movie.mp4 --strict
    """
    from .imitate import (
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
    from .pipeline.runner import build_context, run_pipeline
    from .pipeline.errors import PipelinePaused
    from .pipeline.preflight import PreflightError

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
            "⚠ 警告：旁白为占位��容——LLM 不可达。请检查 LLM 连接后重试。",
            err=True,
        )
    match_line = _format_match_summary(ctx)
    if match_line:
        typer.echo(f"  {match_line}", err=True)
    typer.echo(f"{ctx.video_path}")


@app.command()
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

    

    Examples:
            mn resume --state output/movie/pipeline_state.json
    """
    from .pipeline.runner import _load_pipeline_state, _next_step_after, run_pipeline
    from .pipeline.errors import PipelinePaused
    from .pipeline.preflight import PreflightError
    from .utils.console import Console, build_console

    state_path = Path(state)
    if not state_path.is_file():
        typer.echo(f"State file not found: {state}", err=True)
        raise typer.Exit(code=1)

    ctx, completed_step = _load_pipeline_state(state_path)

    _resolved_level = resolve_log_level(log_level)

    # Re-inject a real console (serialized state has SilentConsole)
    from .models import Services

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
            "⚠ 警告：旁白为占位��容——LLM 不可达。请检查 LLM 连接后重试。",
            err=True,
        )
    if ctx.video_path:
        typer.echo(f"{ctx.video_path}")


@app.command()
def resolve(
    movie: str = typer.Option(..., "--movie", "-m", help="电影名称 / Movie name to resolve"),
    library_dir: Optional[str] = typer.Option(
        None, "--library-dir", help="影视库目录 / Movie library directory"
    ),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出 / Output result as JSON"),
    output_dir: Optional[str] = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="输出目录(默认 output/<电影名>) / Output directory (default: output/<movie>)",
    ),
):
    """Resolve a movie from library directory."""
    out_dir = Path(output_dir) if output_dir else Path("output") / _sanitize_filename(movie)
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx = Context(movie_name=movie, output_dir=str(out_dir))
    if library_dir:
        ctx.library_dir = library_dir
    resolve_video(ctx)

    if json_output:
        result = {"matched": ctx.source_video_path is not None, "path": ctx.source_video_path}
        typer.echo(json.dumps(result, ensure_ascii=False))
    else:
        if ctx.source_video_path:
            typer.echo(ctx.source_video_path)
        else:
            typer.echo("No match found", err=True)
            raise typer.Exit(1)


@app.command()
def research(
    movie: str = typer.Option(..., "--movie", "-m", help="电影名称 / Movie name to research"),
    output_dir: Optional[str] = typer.Option(
        None,
        "--output-dir",
        "-o",
        help="输出目录(默认 output/<电影名>) / Output directory (default: output/<movie>)",
    ),
):
    """Run plot research."""
    out_dir = Path(output_dir) if output_dir else Path("output") / _sanitize_filename(movie)
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx = Context(movie_name=movie, output_dir=str(out_dir))
    ctx.metadata["research_enabled"] = True
    research_plot(ctx)

    if ctx.status.research == "failed":
        raise typer.Exit(1)

    research_path = out_dir / "research.json"
    if research_path.exists():
        typer.echo(f"Research written to: {research_path}")
    else:
        typer.echo("Research completed.")


@app.command()
def scenes(
    video: str = typer.Option(..., "--video", help="视频文件路径 / Video file path"),
    threshold: float = typer.Option(
        27.0, "--threshold", help="场景检测阈值 / Scene detection threshold"
    ),
    output: Optional[str] = typer.Option(None, "--output", help="输出目录 / Output directory"),
):
    """Detect scenes in a video file."""
    from movie_narrator.pipeline.scenes import detect_scenes
    from movie_narrator.models import Context

    out = Path(output) if output else Path("output") / "scenes_debug"
    out.mkdir(parents=True, exist_ok=True)
    ctx = Context(movie_name="debug", output_dir=str(out), source_video_path=video)
    ctx.metadata["scene_threshold"] = threshold
    detect_scenes(ctx)
    if ctx.status.scene == "disabled":
        typer.echo(
            "scenes: required dependency missing — install with `pip install movie-narrator[media]`",
            err=True,
        )
        raise typer.Exit(code=1)
    scenes_json = out / "scenes.json"
    scenes_json.write_text(
        json.dumps([s.model_dump() for s in ctx.scenes], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    typer.echo(f"Scenes: {len(ctx.scenes)} (written to {scenes_json})")


@app.command()
def align(
    audio: str = typer.Option(..., "--audio", help="音频文件路径 / Audio file path"),
    script: Optional[str] = typer.Option(
        None, "--script", help="脚本文本文件(每行一句) / Script text file"
    ),
    output: Optional[str] = typer.Option(None, "--output", help="输出目录 / Output directory"),
):
    """Align audio with script using WhisperX."""
    from movie_narrator.pipeline.align import align_audio
    from movie_narrator.models import Context, TimedSegment

    out = Path(output) if output else Path("output") / "align_debug"
    out.mkdir(parents=True, exist_ok=True)
    segments = []
    if script and Path(script).is_file():
        for line in Path(script).read_text(encoding="utf-8").strip().split("\n"):
            line = line.strip()
            if line:
                segments.append(TimedSegment(text=line, start=0.0, end=2.0))
    ctx = Context(
        movie_name="align_debug",
        output_dir=str(out),
        audio_path=audio,
        timed_segments=segments,
    )
    align_audio(ctx)
    if ctx.status.align == "disabled":
        typer.echo(
            "align: required dependency missing — install with `pip install movie-narrator[ml]`",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"Align status: {ctx.status.align}")
    typer.echo(f"Segments: {len(ctx.timed_segments)}")


@app.command()
def clips(
    video: str = typer.Option(..., "--video", help="源视频路径 / Source video path"),
    scenes_path: str = typer.Option(..., "--scenes", help="scenes.json 路径 / scenes.json path"),
    output: Optional[str] = typer.Option(None, "--output", help="输出目录 / Output directory"),
):
    """Export clips from scenes.json."""
    from movie_narrator.pipeline.export_clips import export_clips
    from movie_narrator.models import Context, Scene
    import json

    out = Path(output) if output else Path("output") / "clips_debug"
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads(Path(scenes_path).read_text(encoding="utf-8"))
    scenes = [Scene(**s) for s in data]
    ctx = Context(
        movie_name="clips_debug",
        output_dir=str(out),
        source_video_path=video,
        scenes=scenes,
        metadata={"export_clips": True},
    )
    export_clips(ctx)
    if ctx.status.export == "disabled":
        typer.echo(
            "clips: required dependency missing — install with `pip install movie-narrator[media]`",
            err=True,
        )
        raise typer.Exit(code=1)
    typer.echo(f"Export status: {ctx.status.export}")
    typer.echo(f"Clips dir: {ctx.clips_dir}")


@app.command()
def plugin(
    action: str = typer.Argument(..., help="list | discover | registries | version"),
):
    """Plugin system commands — list, discover, inspect registries.

    

    Examples:
            mn plugin list          # list installed entry_points plugins
            mn plugin discover      # discover and load all plugins
            mn plugin registries    # show all registered providers/steps
            mn plugin version       # show CONTRACT_VERSION
    """
    if action == "list":
        from .plugin_loader import list_available_plugins

        plugins = list_available_plugins()
        if not plugins:
            typer.echo("No plugins found via entry_points.")
            typer.echo("")
            typer.echo("Plugins are discovered via the 'movie_narrator.plugins'")
            typer.echo("entry point group. Install a plugin package to see it here.")
            return
        typer.echo(f"Available plugins ({len(plugins)}):")
        for name in sorted(plugins):
            typer.echo(f"  {name}")

    elif action == "discover":
        from .plugin_loader import discover_plugins

        results = discover_plugins()
        if not results:
            typer.echo("No plugins found to discover.")
            return
        succeeded = [r for r in results if r.success]
        failed = [r for r in results if not r.success]
        typer.echo(f"Discovery complete: {len(succeeded)} succeeded, {len(failed)} failed")
        for r in succeeded:
            typer.echo(f"  [OK] {r.name}")
        for r in failed:
            typer.echo(f"  [FAIL] {r.name}: {r.error}", err=True)

    elif action == "registries":
        # Import factory modules to ensure built-in providers are registered
        import movie_narrator.tts.factory  # noqa: F401
        import movie_narrator.vision.factory  # noqa: F401
        import movie_narrator.utils.llm  # noqa: F401
        import movie_narrator.pipeline.research  # noqa: F401

        from .pipeline.registry import step_registry
        from .providers import (
            tts_registry,
            vision_registry,
            llm_registry,
            research_registry,
        )

        typer.echo("=== Step Registry ===")
        for info in step_registry.info():
            soft_tag = " (soft)" if info["soft"] else ""
            after_tag = f" after={info['insert_after']}" if info["insert_after"] else ""
            before_tag = f" before={info['insert_before']}" if info["insert_before"] else ""
            typer.echo(f"  {info['name']:<25}{soft_tag}{after_tag}{before_tag}")

        typer.echo("")
        typer.echo("=== TTS Registry ===")
        for info in tts_registry.info():
            proto = " [protocol]" if info["protocol_validated"] else ""
            typer.echo(f"  {info['name']:<25}{proto}")

        typer.echo("")
        typer.echo("=== Vision Registry ===")
        for info in vision_registry.info():
            proto = " [protocol]" if info["protocol_validated"] else ""
            typer.echo(f"  {info['name']:<25}{proto}")

        typer.echo("")
        typer.echo("=== LLM Registry ===")
        for info in llm_registry.info():
            proto = " [protocol]" if info["protocol_validated"] else ""
            typer.echo(f"  {info['name']:<25}{proto}")

        typer.echo("")
        typer.echo("=== Research Registry ===")
        for info in research_registry.info():
            proto = " [protocol]" if info["protocol_validated"] else ""
            typer.echo(f"  {info['name']:<25}{proto}")

    elif action == "version":
        from .contract import CONTRACT_VERSION

        typer.echo(f"CONTRACT_VERSION = {CONTRACT_VERSION}")
        typer.echo(f"  semver: {'.'.join(str(v) for v in CONTRACT_VERSION)}")

    else:
        raise typer.BadParameter(
            f"Unknown action: {action!r}. Use: list | discover | registries | version",
            param_hint="action",
        )


@app.command()
def version():
    """Show version."""
    typer.echo(f"movie-narrator v{__version__}")


@app.command()
def preset(
    name: Optional[str] = typer.Argument(
        None, help="预设名称(省略则列出��部) / Preset name (omitted = list all)"
    ),
):
    """List presets or show details.

    
    Examples:
        mn preset                  # list all available presets
        mn preset mainstream-dry   # show params and tags for mainstream-dry
    """
    from .presets import get_preset, list_presets

    if name is None:
        # List mode
        presets = list_presets()
        if not presets:
            typer.echo("No narration presets available.")
            return
        typer.echo("Available narration presets:")
        typer.echo("")
        for pname, pdesc in presets.items():
            typer.echo(f"  {pname:<20} {pdesc}")
        typer.echo("")
        typer.echo("Use 'mn preset <name>' to see full details.")
        typer.echo("Use '--narration-preset <name>' with 'mn create' to apply.")
    else:
        # Show mode
        try:
            p = get_preset(name)
        except KeyError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

        typer.echo(f"Preset: {p.name}")
        typer.echo(f"Description: {p.desc}")
        typer.echo("")
        typer.echo("Parameters:")
        for key in sorted(p.param_dict):
            typer.echo(f"  {key:<40} {p.param_dict[key]}")
        typer.echo("")
        typer.echo("Prompt tags:")
        for key in sorted(p.tag_dict):
            typer.echo(f"  {key:<40} {p.tag_dict[key]}")


# ── Task Queue commands (v0.6.0) ──────────────────────────


def _get_queue(remote: Optional[str] = None):
    """Create a task queue for CLI use.

    Args:
        remote: If provided, returns a RemoteTaskQueue pointing to the
            given URL. Otherwise, returns a LocalTaskQueue.
    """
    from .cloud import LocalTaskQueue

    if remote:
        from .cloud import RemoteTaskQueue

        return RemoteTaskQueue(remote)
    return LocalTaskQueue(auto_start=False)


@app.command()
def submit(
    movie: str = typer.Option(..., "--movie", "-m", help="电影名称 / Movie name"),
    style: str = typer.Option("热血搞笑", "--style", "-s", help="解说风格 / Narration style"),
    duration: int = typer.Option(60, "--duration", "-d", help="目标时长(秒) / Target duration"),
    voice: Optional[str] = typer.Option(None, "--voice", "-v", help="TTS 语音 / TTS voice"),
    video_format: str = typer.Option(
        "16:9", "--video-format", "--format", "-f", help="视频格式 / Video format"
    ),
    video: Optional[str] = typer.Option(None, "--video", help="源视频路径 / Source video path"),
    library_dir: Optional[str] = typer.Option(
        None, "--library-dir", help="影视库目录 / Movie library"
    ),
    research: Optional[bool] = typer.Option(
        None, "--research/--no-research", help="启用研究 / Enable research"
    ),
    bgm: Optional[str] = typer.Option(None, "--bgm", help="背景音乐 / Background music"),
    no_bgm: bool = typer.Option(False, "--no-bgm", help="禁用BGM / Disable BGM"),
    no_clips: bool = typer.Option(False, "--no-clips", help="跳过片段导出 / Skip clips"),
    strict: bool = typer.Option(False, "--strict", help="严格模式 / Strict mode"),
    subtitle_lang: Optional[str] = typer.Option(
        None, "--subtitle-lang", help="字幕语言 / Subtitle language"
    ),
    subtitle_mode: Optional[str] = typer.Option(
        None, "--subtitle-mode", help="字幕模式 / Subtitle mode"
    ),
    narration_preset: Optional[str] = typer.Option(
        None, "--narration-preset", "-p", help="解说预设 / Narration preset"
    ),
    lang: str = typer.Option("zh", "--lang", help="解说语言 / Narration language"),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o", help="输出目录 / Output directory"
    ),
    max_retries: int = typer.Option(3, "--max-retries", help="最大重试次数 / Max retries"),
    wait: bool = typer.Option(False, "--wait", help="提交后等��完成 / Wait for completion"),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="等����时(秒) / Wait timeout (seconds)"
    ),
    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL (e.g. http://worker:8765)"
    ),
):
    """Submit an async narration task.

    
    Examples:
        mn submit -m "The Dark Knight" -p douyin-fast
        mn submit -m Inception --wait --timeout 600
        mn submit -m The Dark Knight --remote http://worker:8765 --wait
    """
    from .cloud import TaskRequest

    request = TaskRequest(
        movie_name=movie,
        style=style,
        duration=duration,
        voice=voice,
        video_format=video_format,
        video=video,
        library_dir=library_dir,
        research=research,
        bgm=bgm,
        no_bgm=no_bgm,
        no_clips=no_clips,
        strict=strict,
        subtitle_lang=subtitle_lang,
        subtitle_mode=subtitle_mode,
        narration_preset=narration_preset,
        lang=lang,
        output_dir=output_dir,
        max_retries=max_retries,
    )

    queue = _get_queue(remote=remote)
    try:
        task_id = queue.submit(request)
        typer.echo(f"Task submitted: {task_id}")
        typer.echo(f"  Movie: {movie}")
        typer.echo("  Status: pending")
        if remote:
            typer.echo(f"  Remote: {remote}")
        typer.echo(f"  Track: mn status {task_id}" + (f" --remote {remote}" if remote else ""))

        if wait:
            typer.echo(f"\nWaiting for task {task_id}...")
            result = queue.wait(task_id, timeout=timeout)
            if result is None:
                typer.echo(f"Task {task_id} did not complete (timeout or cancelled).", err=True)
                raise typer.Exit(1)
            if result.succeeded:
                typer.echo(f"\n✓ Task completed: {result.video_path}")
            else:
                typer.echo(f"\n✗ Task failed: {result.error}", err=True)
                raise typer.Exit(1)
    finally:
        queue.shutdown()


@app.command()
def status(
    task_id: str = typer.Argument(..., help="任务ID / Task ID"),
    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL"
    ),
):
    """Show task status.

    
    Example:
        mn status abc123def456
        mn status abc123def456 --remote http://worker:8765
    """
    queue = _get_queue(remote=remote)
    task = queue.get_task(task_id)
    if not task:
        typer.echo(f"Task not found: {task_id}", err=True)
        raise typer.Exit(1)

    typer.echo(f"Task: {task.id}")
    typer.echo(f"  Movie: {task.request.movie_name}")
    typer.echo(f"  Status: {task.status.value}")
    typer.echo(f"  Created: {task.created_at}")
    if task.started_at:
        typer.echo(f"  Started: {task.started_at}")
    if task.completed_at:
        typer.echo(f"  Completed: {task.completed_at}")
    if task.retries > 0:
        typer.echo(f"  Retries: {task.retries}")

    if task.progress:
        p = task.progress
        typer.echo(f"  Progress: {p.current_step_index}/{p.total_steps} ({p.percentage:.0f}%)")
        if p.current_step:
            typer.echo(f"  Current step: {p.current_step}")
        if p.elapsed_seconds > 0:
            typer.echo(f"  Elapsed: {p.elapsed_seconds:.1f}s")
        if p.steps_completed:
            typer.echo(f"  Completed steps: {', '.join(p.steps_completed)}")
        if p.steps_skipped:
            typer.echo(f"  Skipped steps: {', '.join(p.steps_skipped)}")
        if p.steps_failed:
            typer.echo(f"  Failed steps: {', '.join(p.steps_failed)}")

    if task.result:
        r = task.result
        if r.error:
            typer.echo(f"  Error: {r.error}")
            if r.error_type:
                typer.echo(f"  Error type: {r.error_type}")
        else:
            typer.echo(f"  Video: {r.video_path}")
            if r.audio_path:
                typer.echo(f"  Audio: {r.audio_path}")
            if r.subtitle_path:
                typer.echo(f"  Subtitle: {r.subtitle_path}")
            typer.echo(f"  Output: {r.output_dir}")


@app.command()
def tasks(
    status_filter: Optional[str] = typer.Option(
        None,
        "--status",
        "-s",
        help="过滤状态 pending|running|completed|failed|cancelled / Filter by status",
    ),
    limit: int = typer.Option(20, "--limit", "-n", help="显示数量 / Number of tasks to show"),
    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL"
    ),
):
    """List tasks.

    
    Examples:
        mn tasks                 # list last 20 tasks
        mn tasks --status running # show only running tasks
        mn tasks --remote http://worker:8765
    """
    from .cloud.models import TaskStatus

    status_enum = None
    if status_filter:
        try:
            status_enum = TaskStatus(status_filter.lower())
        except ValueError:
            typer.echo(
                f"Invalid status: {status_filter}. "
                f"Valid: pending, running, completed, failed, cancelled, retrying",
                err=True,
            )
            raise typer.Exit(1)

    queue = _get_queue(remote=remote)
    task_list = queue.list_tasks(status=status_enum, limit=limit)

    if not task_list:
        typer.echo("No tasks found.")
        return

    typer.echo(f"{'ID':<14} {'Movie':<20} {'Status':<12} {'Progress':<10} {'Step':<20} {'Created'}")
    typer.echo("-" * 100)
    for t in task_list:
        progress = "—"
        step = ""
        if t.progress and t.progress.current_step:
            progress = f"{t.progress.percentage:.0f}%"
            step = t.progress.current_step
        typer.echo(
            f"{t.id:<14} {t.request.movie_name:<20} {t.status.value:<12} "
            f"{progress:<10} {step:<20} {t.created_at[:19]}"
        )


@app.command()
def cancel(
    task_id: str = typer.Argument(..., help="任务ID / Task ID"),
    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL"
    ),
):
    """Cancel a running task.

    
    Example:
        mn cancel abc123def456
        mn cancel abc123def456 --remote http://worker:8765
    """
    queue = _get_queue(remote=remote)
    success = queue.cancel(task_id)
    if success:
        typer.echo(f"Task {task_id}: cancellation requested.")
    else:
        typer.echo(
            f"Task {task_id}: could not cancel (not found or already in terminal state).",
            err=True,
        )
        raise typer.Exit(1)


@app.command()
def wait(
    task_id: str = typer.Argument(..., help="任务ID / Task ID"),
    timeout: Optional[float] = typer.Option(
        None, "--timeout", "-t", help="等����时(秒) / Timeout in seconds (default: infinite)"
    ),
    poll_interval: float = typer.Option(
        1.0, "--poll-interval", help="轮询间隔(秒) / Poll interval in seconds"
    ),
    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL"
    ),
):
    """Wait for task completion.

    Examples:
        mn wait abc123def456              # wait indefinitely
        mn wait abc123def456 -t 600       # 10 minute timeout
        mn wait abc123def456 --remote http://worker:8765
    """
    queue = _get_queue(remote=remote)
    result = queue.wait(task_id, timeout=timeout, poll_interval=poll_interval)
    if result is None:
        typer.echo(
            f"Task {task_id}: did not complete (timeout, cancelled, or not found).", err=True
        )
        raise typer.Exit(1)
    if result.succeeded:
        typer.echo(f"✓ Task {task_id} completed: {result.video_path}")
    else:
        typer.echo(f"✗ Task {task_id} failed: {result.error}", err=True)
        raise typer.Exit(1)


@app.command()
def cleanup(
    all_tasks: bool = typer.Option(
        False, "--all", help="��除所有任务(��括运行中) / Clear all tasks including active ones"
    ),
):
    """Clean up terminal tasks.

    Examples:
        mn cleanup           # remove completed/failed/cancelled tasks
        mn cleanup --all     # remove all tasks
    """
    queue = _get_queue()
    if all_tasks:
        count = queue.cleanup_all()
    else:
        count = queue.cleanup_terminal()
    typer.echo(f"Cleaned up {count} task(s).")


# ── Remote serve command (v0.6.1) ──────────────────────────


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", "--host", help="绑定地址 / Bind address"),
    port: int = typer.Option(8765, "--port", help="监听端口 / Listen port"),
    max_workers: int = typer.Option(2, "--max-workers", help="最大并发任务 / Max concurrent tasks"),
    storage_dir: Optional[str] = typer.Option(
        None, "--storage-dir", help="任务存储目录 / Task storage directory"
    ),
    public: bool = typer.Option(
        False,
        "--public",
        help="监听所有网络接口(默认��本机) / Listen on all interfaces (default: localhost only)",
    ),
    api_key: Optional[str] = typer.Option(
        None,
        "--api-key",
        help="API key for X-API-Key authentication. Reads MN_API_KEY env var by default.",
    ),
    insecure: bool = typer.Option(
        False,
        "--insecure",
        help="Allow starting on public interface without API key (not recommended).",
    ),
    log_format: Optional[str] = typer.Option(
        None,
        "--log-format",
        help="日志格式 / Log format: text|json (default: MN_LOG_FORMAT, else text).",
    ),
    log_level: Optional[str] = typer.Option(
        None,
        "--log-level",
        help="日志级别 / Log level: DEBUG|INFO|WARNING|ERROR (default: MN_LOG_LEVEL, else INFO).",
    ),
):
    """Start the remote inference API server.

    Starts an HTTP API server that allows remote clients to submit and
    manage narration tasks. Suitable for offloading inference workload
    to GPU machines or cloud servers.

    Start a worker daemon that accepts remote task submissions:
        mn serve --port 8765
        mn serve --max-workers 4
        mn serve --public --api-key secret   # listen on all interfaces + auth

    From another machine, submit tasks:
        mn submit -m The Dark Knight --remote http://worker:8765 --wait

    Security note: By default, listens on 127.0.0.1 (local access only),
    no authentication required. When using --public to listen on 0.0.0.0,
    you must provide --api-key (or set the MN_API_KEY environment variable),
    otherwise startup is rejected. Use --insecure to skip this security
    check (not recommended). v0.8.0 adds X-API-Key authentication support,
    enabled via --api-key.

    v0.8.1 Observability:
        mn serve --log-format json --log-level INFO
        Prometheus metrics (requires X-API-Key by default;
        set MN_METRICS_PUBLIC=1 to allow unauthenticated scraping within the cluster).
        Every response echoes back X-Correlation-ID.
    """
    from .cloud import run_daemon
    from .config import get_settings
    from .utils.logging_config import configure_logging
    from pathlib import Path

    # v0.8.1: configure structured logging before anything can log.
    # Flags are left at None by default so MN_LOG_FORMAT / MN_LOG_LEVEL
    # still apply; an explicit flag overrides the environment.
    if log_format is not None and log_format.lower() not in {"text", "json"}:
        typer.echo("--log-format must be 'text' or 'json'", err=True)
        raise typer.Exit(2)
    configure_logging(
        json_mode=(log_format.lower() == "json") if log_format else None,
        level=log_level,
    )

    if public:
        host = "0.0.0.0"  # nosec B104  # explicit opt-in via --public; guarded by API-key check below

    settings = get_settings()
    effective_api_key = api_key or settings.api_key

    if host == "0.0.0.0":  # nosec B104  # comparing the explicit --public host, not a listener
        if effective_api_key is None and not insecure:
            typer.echo(
                "WARNING: serving on 0.0.0.0 without an API key.\n"
                "Use --api-key to enable X-API-Key authentication, "
                "or --insecure to proceed without it.",
                err=True,
            )
        elif effective_api_key is None and insecure:
            typer.echo(
                "WARNING: serving on 0.0.0.0 without authentication (--insecure).\n"
                "Anyone with network access can submit tasks and download artifacts.",
                err=True,
            )

    storage = Path(storage_dir) if storage_dir else None
    run_daemon(
        host=host,
        port=port,
        storage_dir=storage,
        max_workers=max_workers,
        api_key=effective_api_key,
        allow_insecure=insecure,
        blocking=True,
    )


@app.command()
def download(
    task_id: str = typer.Argument(..., help="任务ID / Task ID"),
    remote: str = typer.Option(..., "--remote", "-r", help="远程服务器URL / Remote server URL"),
    filename: Optional[str] = typer.Option(
        None, "--filename", "-f", help="指定文件名(不指定则下载��部) / Specific file (default: all)"
    ),
    dest_dir: Optional[str] = typer.Option(
        None, "--dest-dir", "-o", help="保存目录 / Destination directory"
    ),
):
    """Download artifacts from a remote server.

    
    Examples:
        mn download abc123 --remote http://worker:8765
        mn download abc123 -r http://worker:8765 -f final.mp4
        mn download abc123 -r http://worker:8765 -o ./output
    """
    from .cloud import download_all_artifacts, download_artifact

    if filename:
        path = download_artifact(remote, task_id, filename, dest_dir=dest_dir)
        typer.echo(f"Downloaded: {path}")
    else:
        paths = download_all_artifacts(remote, task_id, dest_dir=dest_dir)
        if not paths:
            typer.echo("No artifacts found.", err=True)
            raise typer.Exit(1)
        typer.echo(f"Downloaded {len(paths)} file(s):")
        for p in paths:
            typer.echo(f"  {p}")


@app.command("api-spec")
def api_spec(
    output: Optional[str] = typer.Option(
        None,
        "--output",
        "-o",
        help="输出文件路径(默认输出到 stdout) / Output file path (default: stdout)",
    ),
    indent: int = typer.Option(
        2, "--indent", help="JSON 缩进空格数(0 表示紧凑输出) / JSON indent width (0 = compact)"
    ),
):
    """Dump the REST API OpenAPI 3.1 spec.

    
    Examples:
        mn api-spec
        mn api-spec -o openapi.json
        mn api-spec --indent 0 -o openapi.min.json

    The same document is served live at ``GET /openapi.json`` by
    ``mn serve``.
    """
    from .cloud.openapi import build_openapi_spec

    spec = build_openapi_spec()
    text = json.dumps(
        spec,
        ensure_ascii=False,
        indent=indent if indent > 0 else None,
        sort_keys=False,
    )

    if output:
        path = Path(output)
        if path.parent and not path.parent.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n", encoding="utf-8")
        typer.echo(f"OpenAPI spec written to {path}")
    else:
        typer.echo(text)


# ── Artifact lifecycle commands (v0.8.3) ───────────────────

artifacts_app = typer.Typer(
    help="产物存储与生命周期管理 / Artifact storage and TTL lifecycle management.",
    no_args_is_help=True,
)
app.add_typer(artifacts_app, name="artifacts")


def _artifacts_open_store(backend: Optional[str], root: Optional[str]):
    """Resolve the artifact store for the ``mn artifacts`` commands."""
    from .cloud.artifact_store import ArtifactStoreError, get_artifact_store

    try:
        return get_artifact_store(backend=backend, root=root)
    except ArtifactStoreError as e:
        typer.echo(f"Artifact store unavailable: {e}", err=True)
        raise typer.Exit(1)


@artifacts_app.command("list")
def artifacts_list(
    prefix: str = typer.Option(
        "", "--prefix", help="仅列出该前缀下的产物 / Only list keys under this prefix"
    ),
    backend: Optional[str] = typer.Option(
        None, "--backend", help="local 或 s3(默认读 MN_STORAGE_BACKEND) / Backend override"
    ),
    root: Optional[str] = typer.Option(
        None, "--root", help="本地后端根目录(默认读 MN_STORAGE_ROOT) / Local store root"
    ),
):
    """List artifacts in the configured store.

    
    Examples:
        mn artifacts list
        mn artifacts list --root output --prefix abc123
    """
    from .cloud.lifecycle import format_bytes

    store = _artifacts_open_store(backend, root)
    total = 0
    count = 0
    for info in store.list(prefix):
        count += 1
        total += info.size
        typer.echo(f"  {info.key}  ({format_bytes(info.size)})")
    if count == 0:
        typer.echo("No artifacts found.")
        return
    typer.echo(f"{count} artifact(s), {format_bytes(total)} total.")


@artifacts_app.command("cleanup")
def artifacts_cleanup(
    ttl: Optional[int] = typer.Option(
        None,
        "--ttl",
        help="过期秒数,0=永久保留(默认读 MN_ARTIFACT_TTL) / TTL in seconds, 0 = keep forever",
    ),
    max_bytes: Optional[int] = typer.Option(
        None,
        "--max-bytes",
        help="总容量上限字节数,0=不限(默认读 MN_ARTIFACT_MAX_BYTES) / Total size cap in bytes",
    ),
    keep_last: Optional[int] = typer.Option(
        None,
        "--keep-last",
        help="始终保留最新 N 个产物(默认读 MN_ARTIFACT_KEEP_LAST) / Always keep the N newest",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="仅预览不删除 / Preview without deleting"
    ),
    prefix: str = typer.Option(
        "", "--prefix", help="仅清理该前缀下的产物 / Restrict cleanup to this prefix"
    ),
    backend: Optional[str] = typer.Option(
        None, "--backend", help="local 或 s3(默认读 MN_STORAGE_BACKEND) / Backend override"
    ),
    root: Optional[str] = typer.Option(
        None, "--root", help="本地后端根目录(默认读 MN_STORAGE_ROOT) / Local store root"
    ),
):
    """Clean up artifacts by TTL and size cap.

    Options not explicitly specified fall back to MN_ARTIFACT_* environment variables.
    Options left unset fall back to the MN_ARTIFACT_* environment variables.

    
    Examples:
        mn artifacts cleanup --dry-run
        mn artifacts cleanup --ttl 604800 --keep-last 5
        mn artifacts cleanup --max-bytes 10737418240
    """
    from .cloud.lifecycle import (
        ArtifactLifecyclePolicy,
        cleanup_artifacts,
        describe_policy,
    )

    policy = ArtifactLifecyclePolicy.from_env(dry_run=dry_run)
    if ttl is not None:
        policy.ttl_seconds = max(ttl, 0)
    if max_bytes is not None:
        policy.max_total_bytes = max(max_bytes, 0)
    if keep_last is not None:
        policy.keep_last_n = max(keep_last, 0)

    store = _artifacts_open_store(backend, root)

    typer.echo("Artifact retention policy:")
    for line in describe_policy(policy):
        typer.echo(f"  {line}")

    if not policy.enabled:
        typer.echo("No retention rule active (--ttl / --max-bytes are both 0) — nothing to do.")
        return

    report = cleanup_artifacts(store, policy, prefix=prefix)

    if report.deleted:
        header = "Would delete:" if report.dry_run else "Deleted:"
        typer.echo(header)
        for key in report.deleted:
            typer.echo(f"  - {key}")
    if report.skipped:
        typer.echo(f"Kept (protected / keep-last): {len(report.skipped)}")
    if report.errors:
        typer.echo("Errors:", err=True)
        for key, message in report.errors:
            typer.echo(f"  ! {key}: {message}", err=True)

    typer.echo(report.summary())
    if report.errors:
        raise typer.Exit(1)
