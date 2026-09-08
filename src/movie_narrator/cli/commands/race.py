# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""The ``mn race`` command — run N variants in parallel and pick the best."""

from pathlib import Path
from typing import Optional, cast

import typer

from movie_narrator.cli._helpers import _sanitize_filename
from movie_narrator.cli.options import (
    BgmOpt,
    ConfigOpt,
    DurationOpt,
    LibraryDirOpt,
    MovieOpt,
    NoBgmOpt,
    OutputDirRace,
    ResearchOpt,
    StyleOpt,
    VideoFormatOpt,
    VideoOpt,
    VoiceWithSign,
)


def race(
    movie: MovieOpt = None,

    style: StyleOpt = "热血搞笑",

    duration: DurationOpt = 60,

    voice: VoiceWithSign = None,

    video_format: VideoFormatOpt = "16:9",

    video: VideoOpt = None,

    library_dir: LibraryDirOpt = None,

    research: ResearchOpt = None,

    bgm: BgmOpt = None,

    no_bgm: NoBgmOpt = False,

    config: ConfigOpt = None,

    output_dir: OutputDirRace = None,


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
    )

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
    from movie_narrator.race import (
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
