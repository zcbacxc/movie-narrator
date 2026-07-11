import re
from pathlib import Path
from typing import Optional

import typer

from . import __version__
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
):
    output_dir = Path("output") / _sanitize_filename(movie)
    output_dir.mkdir(parents=True, exist_ok=True)

    video_path = run_pipeline(
        movie=movie,
        style=style,
        duration=duration,
        voice=voice,
        format=format,
        output_dir=output_dir,
        keep_cache=keep_cache,
    )
    typer.echo(f"{video_path}")


@app.command()
def version():
    """Show version."""
    typer.echo(f"movie-narrator v{__version__}")
