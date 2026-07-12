import json
import re
from pathlib import Path
from typing import Optional

import typer

from . import __version__
from .models import Context
from .pipeline.resolve import resolve_video
from .pipeline.research import research_plot
from .pipeline.runner import run_pipeline

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
    movie: str = typer.Option(..., "--movie", "-m", help="Movie name"),
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
):
    if video is not None and not Path(video).is_file():
        raise typer.BadParameter(f"video not found: {video}", param_hint="--video")

    output_dir = Path("output") / _sanitize_filename(movie)
    output_dir.mkdir(parents=True, exist_ok=True)

    ctx = run_pipeline(
        movie=movie,
        style=style,
        duration=duration,
        voice=voice,
        format=format,
        output_dir=output_dir,
        keep_cache=keep_cache,
        video=video,
        library_dir=library_dir,
        research=research,
        bgm=bgm,
        no_bgm=no_bgm,
        no_clips=no_clips,
        strict=strict,
    )
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
def version():
    """Show version."""
    typer.echo(f"movie-narrator v{__version__}")
