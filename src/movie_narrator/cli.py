import json
import re
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .models import Context
from .pipeline.resolve import resolve_video
from .pipeline.research import research_plot
from .pipeline.runner import build_context, run_pipeline

app = typer.Typer(help="Generate narrated movie recap videos from a single prompt.")

_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _sanitize_filename(name: str) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = name.strip().rstrip(".")
    if not name:
        name = "movie"
    if name.upper() in _RESERVED_NAMES:
        name = f"_{name}"
    return name


@app.command()
def create(
    movie: Optional[str] = typer.Option(None, "--movie", "-m", help="Movie name"),
    style: str = typer.Option("热血搞笑", "--style", "-s", help="Narration style"),
    duration: int = typer.Option(60, "--duration", "-d", help="Target duration (seconds)"),
    voice: Optional[str] = typer.Option(None, "--voice", "-v", help="TTS voice (Edge TTS)"),
    format: str = typer.Option("16:9", "--format", "-f", help="Video format: 16:9 or 9:16"),
    keep_cache: bool = typer.Option(False, "--keep-cache", help="Keep TTS cache files"),
    video: Optional[str] = typer.Option(None, "--video", help="Source movie file path"),
    library_dir: Optional[str] = typer.Option(None, "--library-dir", help="Movie library directory"),
    research: Optional[bool] = typer.Option(None, "--research/--no-research", help="Enable plot research"),
    bgm: Optional[str] = typer.Option(None, "--bgm", help="Background music file"),
    no_bgm: bool = typer.Option(False, "--no-bgm", help="Disable BGM even if default set"),
    no_clips: bool = typer.Option(False, "--no-clips", help="Skip clips/export"),
    strict: bool = typer.Option(False, "--strict", help="Abort on soft step failure"),
    config: Optional[str] = typer.Option(None, "--config", help="Path to job YAML config"),
    # Multi-language subtitle (v0.3).
    subtitle_lang: Optional[str] = typer.Option(
        None, "--subtitle-lang", help="Target language tag (e.g. en, ja, zh-TW); empty = feature off",
    ),
    subtitle_mode: Optional[str] = typer.Option(
        None, "--subtitle-mode", help="Overlay mode: original|translated|bilingual",
    ),
):
    from .config import get_settings
    from .workflow import JobConfigError, load_job_config, merge_job

    if config is None and movie is None:
        raise typer.BadParameter(
            "movie is required (set --movie or config.movie)",
            param_hint="--movie",
        )

    job = None
    config_path = None
    if config is not None:
        config_path = str(Path(config))
        if not Path(config_path).is_file():
            raise typer.BadParameter(
                f"config not found: {config_path}",
                param_hint="--config",
            )
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

    output_dir = Path("output") / _sanitize_filename(resolved.movie)
    output_dir.mkdir(parents=True, exist_ok=True)

    ctx = build_context(
        movie=resolved.movie,
        style=resolved.style,
        duration=resolved.duration,
        voice=resolved.voice,
        format=resolved.format,
        output_dir=output_dir,
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
    )
    try:
        ctx = run_pipeline(ctx)
    except Exception as e:
        # step_err already printed the single-line summary and wrote the
        # full traceback to the log file.  Suppress Typer's Rich
        # traceback to keep the console output clean.
        raise typer.Exit(code=1)
    typer.echo(f"{ctx.video_path}")


@app.command()
def resolve(
    movie: str = typer.Option(..., "--movie", "-m", help="Movie name to resolve"),
    library_dir: Optional[str] = typer.Option(None, "--library-dir", help="Movie library directory"),
    json_output: bool = typer.Option(False, "--json", help="Output result as JSON"),
):
    """Resolve a movie from library directory."""
    output_dir = Path("output") / _sanitize_filename(movie)
    output_dir.mkdir(parents=True, exist_ok=True)

    ctx = Context(movie_name=movie, output_dir=str(output_dir))
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
    movie: str = typer.Option(..., "--movie", "-m", help="Movie name to research"),
):
    """Run plot research and write research.json to output/<movie>/."""
    output_dir = Path("output") / _sanitize_filename(movie)
    output_dir.mkdir(parents=True, exist_ok=True)

    ctx = Context(movie_name=movie, output_dir=str(output_dir))
    ctx.metadata["research_enabled"] = True
    research_plot(ctx)

    if ctx.status.research == "failed":
        raise typer.Exit(1)

    research_path = output_dir / "research.json"
    if research_path.exists():
        typer.echo(f"Research written to: {research_path}")
    else:
        typer.echo("Research completed.")


@app.command()
def scenes(
    video: str = typer.Option(..., "--video", help="Video file path"),
    threshold: float = typer.Option(27.0, "--threshold", help="Scene detection threshold"),
    output: Optional[str] = typer.Option(None, "--output", help="Output directory"),
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
    audio: str = typer.Option(..., "--audio", help="Audio file path"),
    script: Optional[str] = typer.Option(None, "--script", help="Script text file (one per line)"),
    output: Optional[str] = typer.Option(None, "--output", help="Output directory"),
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
    video: str = typer.Option(..., "--video", help="Source video path"),
    scenes_path: str = typer.Option(..., "--scenes", help="scenes.json path"),
    output: Optional[str] = typer.Option(None, "--output", help="Output directory"),
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
def version():
    """Show version."""
    typer.echo(f"movie-narrator v{__version__}")


@app.command()
def web(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host"),
    port: int = typer.Option(7860, "--port", help="Bind port"),
    share: bool = typer.Option(False, "--share", help="Create public Gradio link"),
):
    """Launch the browser UI (local Gradio app)."""
    try:
        from .web import launch_web
    except ImportError:
        typer.echo(
            "Web UI requires the 'web' extra. Install with:\n"
            "  pip install \"movie-narrator[web]\"",
            err=True,
        )
        raise typer.Exit(code=1)
    launch_web(host=host, port=port, share=share)
