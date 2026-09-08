# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""One-shot utility commands: ``resolve``, ``research``, ``scenes``, ``align``, ``clips``."""

import json
from pathlib import Path
from typing import Optional

import typer

import movie_narrator.cli as _cli

from movie_narrator.cli._helpers import _sanitize_filename
from movie_narrator.models import Context


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
    _cli.resolve_video(ctx)

    if json_output:
        result = {"matched": ctx.source_video_path is not None, "path": ctx.source_video_path}
        typer.echo(json.dumps(result, ensure_ascii=False))
    else:
        if ctx.source_video_path:
            typer.echo(ctx.source_video_path)
        else:
            typer.echo("No match found", err=True)
            raise typer.Exit(1)


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
    _cli.research_plot(ctx)

    if ctx.status.research == "failed":
        raise typer.Exit(1)

    research_path = out_dir / "research.json"
    if research_path.exists():
        typer.echo(f"Research written to: {research_path}")
    else:
        typer.echo("Research completed.")


def scenes(
    video: str = typer.Option(..., "--video", help="视频文件路径 / Video file path"),
    threshold: float = typer.Option(
        27.0, "--threshold", help="场景检测阈值 / Scene detection threshold"
    ),
    output: Optional[str] = typer.Option(None, "--output", help="输出目录 / Output directory"),
):
    """Detect scenes in a video file."""
    from movie_narrator.pipeline.scenes import detect_scenes

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


def align(
    audio: str = typer.Option(..., "--audio", help="音频文件路径 / Audio file path"),
    script: Optional[str] = typer.Option(
        None, "--script", help="脚本文本文件(每行一句) / Script text file"
    ),
    output: Optional[str] = typer.Option(None, "--output", help="输出目录 / Output directory"),
):
    """Align audio with script using WhisperX."""
    from movie_narrator.pipeline.align import align_audio
    from movie_narrator.models import TimedSegment

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


def clips(
    video: str = typer.Option(..., "--video", help="源视频路径 / Source video path"),
    scenes_path: str = typer.Option(..., "--scenes", help="scenes.json 路径 / scenes.json path"),
    output: Optional[str] = typer.Option(None, "--output", help="输出目录 / Output directory"),
):
    """Export clips from scenes.json."""
    from movie_narrator.pipeline.export_clips import export_clips
    from movie_narrator.models import Scene

    out = Path(output) if output else Path("output") / "clips_debug"
    out.mkdir(parents=True, exist_ok=True)
    data = json.loads(Path(scenes_path).read_text(encoding="utf-8"))
    scenes_list = [Scene(**s) for s in data]
    ctx = Context(
        movie_name="clips_debug",
        output_dir=str(out),
        source_video_path=video,
        scenes=scenes_list,
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
