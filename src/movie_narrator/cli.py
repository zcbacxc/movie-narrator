import json
import re
from pathlib import Path
from typing import Any, Dict, Optional

import typer

from . import __version__
from .models import Context
from .pipeline.resolve import resolve_video
from .pipeline.research import research_plot
from .pipeline.runner import build_context, run_pipeline


def _format_match_summary(ctx: Context) -> Optional[str]:
    """Format a one-line match summary from ctx.metadata for CLI output.

    Returns None if no match_summary is available (e.g. match step skipped).
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
    """Return human-readable degradation hints for the CLI output."""
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
                "match: all segments fell back to heuristic — "
                "check embedding model availability"
            )
        elif degraded:
            hints.append(f"match degraded: {degraded}")

    degraded_steps = ctx.metadata.get("_degraded_steps", [])
    if degraded_steps:
        hints.append(
            f"degraded steps: {', '.join(degraded_steps)} — "
            f"see metadata.json for details"
        )

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
        return self._cancelled

    def on_step_error(self, step_name: str, error: Exception, attempt: int):
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

from .utils.sanitize import sanitize_filename as _sanitize_filename


@app.command()
def create(
    movie: Optional[str] = typer.Option(None, "--movie", "-m", help="电影名称 / Movie name"),
    style: str = typer.Option("热血搞笑", "--style", "-s", help="解说风格 / Narration style"),
    duration: int = typer.Option(60, "--duration", "-d", help="目标时长(秒) / Target duration (seconds)"),
    voice: Optional[str] = typer.Option(None, "--voice", "-v", help="TTS 语音 / TTS voice (Edge TTS)"),
    format: str = typer.Option("16:9", "--format", "-f", help="视频格式 16:9 或 9:16 / Video format: 16:9 or 9:16"),
    keep_cache: bool = typer.Option(False, "--keep-cache", help="保留 TTS 缓存 / Keep TTS cache files"),
    video: Optional[str] = typer.Option(None, "--video", help="源视频文件路径 / Source movie file path"),
    library_dir: Optional[str] = typer.Option(None, "--library-dir", help="影视库目录 / Movie library directory"),
    research: Optional[bool] = typer.Option(None, "--research/--no-research", help="启用剧情研究 / Enable plot research"),
    bgm: Optional[str] = typer.Option(None, "--bgm", help="背景音乐文件 / Background music file"),
    no_bgm: bool = typer.Option(False, "--no-bgm", help="禁用 BGM / Disable BGM even if default set"),
    no_clips: bool = typer.Option(False, "--no-clips", help="跳过片段导出 / Skip clips/export"),
    strict: bool = typer.Option(False, "--strict", help="软步骤失败即中止 / Abort on soft step failure"),
    retry: bool = typer.Option(False, "--retry", help="硬步骤失败时交互重试 / Enable interactive retry on hard step failure"),
    config: Optional[str] = typer.Option(None, "--config", help="job YAML 配置路径 / Path to job YAML config"),
    # Multi-language subtitle (v0.3).
    subtitle_lang: Optional[str] = typer.Option(
        None, "--subtitle-lang", help="目标语言标签(如 en, ja, zh-TW) / Target language tag; empty = off",
    ),
    subtitle_mode: Optional[str] = typer.Option(
        None, "--subtitle-mode", help="字幕模式 original|translated|bilingual / Overlay mode",
    ),
    narration_preset: Optional[str] = typer.Option(
        None, "--narration-preset", "-p",
        help="解说风格预设 douyin-fast | mainstream-dry | bilibili-long / Narration style preset",
    ),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o",
        help="输出目录(默认 output/<电影名>) / Output directory (default: output/<movie>)",
    ),
    pause_at: Optional[str] = typer.Option(
        None, "--pause-at",
        help="在指定步骤后暂停(人在环 EP9) / Pause after this step name "
             "(e.g. match_clips, generate_script). Resume with: mn resume --state <path>",
    ),
):
    """生成解说短视频 — 从电影名到成片一站式产出.

    \b
    快速开始 / Quick start:
        mn create -m 满江红 -p douyin-fast
        mn create -m 满江红 -p mainstream-dry --bgm music.mp3
        mn create --config job.yaml

    \b
    查看可用预设 / List available presets:
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
        "format": format,
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
    out_dir = Path(output_dir) if output_dir else Path("output") / _sanitize_filename(resolved.movie)
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx = build_context(
        movie=resolved.movie,
        style=resolved.style,
        duration=resolved.duration,
        voice=resolved.voice,
        format=resolved.format,
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
    )
    controller = InteractiveCLIController() if retry else None

    # EP9: Store pause-at request in context metadata
    if pause_at:
        ctx.metadata["pause_at"] = pause_at

    try:
        ctx = run_pipeline(ctx, controller=controller)
    except Exception as e:
        # EP9: PipelinePaused — state saved, inform user how to resume
        from .pipeline.errors import PipelinePaused
        if isinstance(e, PipelinePaused):
            typer.echo(
                f"\n⏸ Pipeline paused after '{e.completed_step}'. "
                f"Resume with: mn resume --state \"{Path(ctx.output_dir) / 'pipeline_state.json'}\""
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
            "⚠ 警告：旁白为占位内容——LLM 不可达。请检查 LLM 连接后重试。",
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
def resume(
    state: str = typer.Option(..., "--state", help="pipeline_state.json 路径 / Path to pipeline state file"),
    retry: bool = typer.Option(False, "--retry", help="硬步骤失败时交互重试 / Enable interactive retry on hard step failure"),
):
    """恢复暂停的管线 (EP9) — 从上次暂停点继续执行.

    \b
    用法 / Usage:
        mn resume --state output/movie/pipeline_state.json
    """
    from .pipeline.runner import _load_pipeline_state, _next_step_after, run_pipeline
    from .pipeline.errors import PipelinePaused
    from .pipeline.preflight import PreflightError
    from .utils.console import build_console

    state_path = Path(state)
    if not state_path.is_file():
        typer.echo(f"State file not found: {state}", err=True)
        raise typer.Exit(code=1)

    ctx, completed_step = _load_pipeline_state(state_path)

    # Re-inject a real console (serialized state has SilentConsole)
    from .models import Services
    ctx.services = Services(console=build_console(Path(ctx.output_dir)))

    # Determine the step to start from (the step AFTER the completed one)
    start_step = _next_step_after(completed_step)
    if start_step is None:
        typer.echo(
            f"Pipeline already completed (last step: {completed_step}). "
            f"Nothing to resume."
        )
        raise typer.Exit(code=0)

    console = ctx.services.console
    console.debug(f"Resuming from step '{start_step}' (completed: {completed_step})")

    controller = InteractiveCLIController() if retry else None
    try:
        ctx = run_pipeline(ctx, controller=controller, start_step=start_step)
    except Exception as e:
        if isinstance(e, PipelinePaused):
            typer.echo(
                f"\n⏸ Pipeline paused after '{e.completed_step}'. "
                f"Resume with: mn resume --state \"{Path(ctx.output_dir) / 'pipeline_state.json'}\""
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


@app.command()
def resolve(
    movie: str = typer.Option(..., "--movie", "-m", help="电影名称 / Movie name to resolve"),
    library_dir: Optional[str] = typer.Option(None, "--library-dir", help="影视库目录 / Movie library directory"),
    json_output: bool = typer.Option(False, "--json", help="JSON 格式输出 / Output result as JSON"),
    output_dir: Optional[str] = typer.Option(
        None, "--output-dir", "-o",
        help="输出目录(默认 output/<电影名>) / Output directory (default: output/<movie>)",
    ),
):
    """从影视库中查找电影 / Resolve a movie from library directory."""
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
        None, "--output-dir", "-o",
        help="输出目录(默认 output/<电影名>) / Output directory (default: output/<movie>)",
    ),
):
    """运行剧情研究并输出 research.json / Run plot research."""
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
    threshold: float = typer.Option(27.0, "--threshold", help="场景检测阈值 / Scene detection threshold"),
    output: Optional[str] = typer.Option(None, "--output", help="输出目录 / Output directory"),
):
    """检测视频中的场景 / Detect scenes in a video file."""
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
    script: Optional[str] = typer.Option(None, "--script", help="脚本文本文件(每行一句) / Script text file"),
    output: Optional[str] = typer.Option(None, "--output", help="输出目录 / Output directory"),
):
    """使用 WhisperX 对齐音频与脚本 / Align audio with script using WhisperX."""
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
    """从 scenes.json 导出片段 / Export clips from scenes.json."""
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
def version():
    """显示版本号 / Show version."""
    typer.echo(f"movie-narrator v{__version__}")


@app.command()
def preset(
    name: Optional[str] = typer.Argument(
        None, help="预设名称(省略则列出全部) / Preset name (omitted = list all)"
    ),
):
    """列出解说预设或查看指定预设详情 / List presets or show details.

    \b
    示例 / Examples:
        mn preset                  # 列出所有可用预设
        mn preset mainstream-dry   # 查看 mainstream-dry 的参数和标签
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


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="绑定地址 / Bind host"),
    port: int = typer.Option(8760, "--port", help="绑定端口 / Bind port"),
    reload: bool = typer.Option(False, "--reload", help="文件变更时自动重载 / Auto-reload on file changes"),
):
    """启动 Web UI (FastAPI + React) / Launch the Web UI."""
    from .web_api import launch_web_api
    launch_web_api(host=host, port=port, reload=reload)
