# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Command-line interface for Movie Narrator.

The ``create`` / ``race`` / ``imitate`` / ``submit`` commands keep their
option signatures defined here (their bodies delegate to the handlers in
:mod:`movie_narrator.cli.commands`) because ``tests/test_cli_options.py``
statically parses this module for the command signatures to enforce that the
shared option aliases (help/default text) do not drift across commands.
"""

from typing import Optional

import typer

# These are re-exported on the ``movie_narrator.cli`` scope because tests
# monkeypatch them (e.g. ``patch("movie_narrator.cli.build_context")``) and
# expect the command handlers to observe the patched values.  Handlers
# therefore resolve them through the ``movie_narrator.cli`` module object.
from ..pipeline.resolve import resolve_video  # noqa: F401  (patchable CLI scope)
from ..pipeline.research import research_plot  # noqa: F401  (patchable CLI scope)
from ..pipeline.runner import (  # noqa: F401  (patchable CLI scope)
    build_context,
    run_pipeline,
)

from .options import (
    BgmOpt,
    ConfigOpt,
    DryRunOpt,
    DurationOpt,
    KeepCacheOpt,
    LibraryDirOpt,
    MovieOpt,
    MovieRequired,
    NarrationPresetOpt,
    NoBgmOpt,
    NoClipsOpt,
    OutputDirCreate,
    OutputDirImitate,
    OutputDirPlain,
    OutputDirRace,
    ResearchOpt,
    RetryOpt,
    StrictOpt,
    StyleOpt,
    SubtitleLangOpt,
    SubtitleModeOpt,
    VideoFormatOpt,
    VideoOpt,
    VoicePlain,
    VoiceWithSign,
)

# Shared, patchable helper surface (tests reference these on movie_narrator.cli).
from movie_narrator.cli._helpers import (
    InteractiveCLIController,  # noqa: F401  (patchable CLI scope)
    _EXAMPLE_YAML,  # noqa: F401  (patchable CLI scope)
    _format_degradation_hints,  # noqa: F401  (patchable CLI scope)
    _format_match_summary,  # noqa: F401  (patchable CLI scope)
)

# Encoder-benchmark machinery (tests import / monkeypatch these on the cli scope).
from movie_narrator.cli.commands.misc import (
    _BENCHMARK_MOD_NAME,  # noqa: F401  (patchable CLI scope)
    _benchmark_script_path,  # noqa: F401  (patchable CLI scope)
    _load_encoder_benchmark,  # noqa: F401  (patchable CLI scope)
    benchmark,
    doctor,
    plugin,
    version,
)

# The four signature-bearing commands live here (see module docstring); their
# implementations are imported from the command modules and delegated to below.
# The remaining command handlers are imported and registered by name.
from movie_narrator.cli.commands.create import create as _create_impl
from movie_narrator.cli.commands.race import race as _race_impl
from movie_narrator.cli.commands.imitate import imitate as _imitate_impl
from movie_narrator.cli.commands.queue import submit as _submit_impl
from movie_narrator.cli.commands.resume import resume
from movie_narrator.cli.commands.rerun import rerun
from movie_narrator.cli.commands.utility import align, clips, research, resolve, scenes
from movie_narrator.cli.commands.presets import (
    preset,
    presets_install,
    presets_list,
    presets_remove,
    presets_show,
)
from movie_narrator.cli.commands.queue import (
    cancel,
    cleanup,
    serve,
    status,
    tasks,
    wait,
)
from movie_narrator.cli.commands.cloud import api_spec, download
from movie_narrator.cli.commands.artifacts import artifacts_cleanup, artifacts_list

app = typer.Typer(
    help="Movie Narrator — 从一个提示词生成解说短视频 / Generate narrated movie recap videos from a single prompt.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)


@app.command()
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

    

    Examples:
            mn create -m Inception -p douyin-fast
            mn create -m Inception -p mainstream-dry --bgm music.mp3
            mn create --config job.yaml

        
        List available presets:
            mn preset
    """
    return _create_impl(
        movie=movie,
        style=style,
        duration=duration,
        voice=voice,
        video_format=video_format,
        keep_cache=keep_cache,
        video=video,
        library_dir=library_dir,
        research=research,
        bgm=bgm,
        no_bgm=no_bgm,
        no_clips=no_clips,
        strict=strict,
        retry=retry,
        dry_run=dry_run,
        config=config,
        subtitle_lang=subtitle_lang,
        subtitle_mode=subtitle_mode,
        narration_preset=narration_preset,
        output_dir=output_dir,
        narrator_perspective=narrator_perspective,
        focus_character=focus_character,
        pause_at=pause_at,
        log_level=log_level,
        verbose=verbose,
    )


@app.command()
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
    return _race_impl(
        movie=movie,
        style=style,
        duration=duration,
        voice=voice,
        video_format=video_format,
        video=video,
        library_dir=library_dir,
        research=research,
        bgm=bgm,
        no_bgm=no_bgm,
        config=config,
        output_dir=output_dir,
        candidates=candidates,
        presets=presets,
        auto_pick=auto_pick,
    )


@app.command()
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

    
    Analyzes sentence density, cut density, and rhythm of the reference video, automatically generates a temporary preset,
    then runs the standard pipeline with that preset to generate new narration.

    

    Examples:
            mn imitate -r viral_ref.mp4 -m Inception --video movie.mp4
            mn imitate -r viral_ref.mp4 --analyze-only
            mn imitate -r viral_ref.mp4 -m Inception --video movie.mp4 --strict
    """
    return _imitate_impl(
        movie=movie,
        style=style,
        duration=duration,
        voice=voice,
        video_format=video_format,
        keep_cache=keep_cache,
        video=video,
        library_dir=library_dir,
        research=research,
        bgm=bgm,
        no_bgm=no_bgm,
        no_clips=no_clips,
        strict=strict,
        retry=retry,
        config=config,
        subtitle_lang=subtitle_lang,
        subtitle_mode=subtitle_mode,
        output_dir=output_dir,
        reference=reference,
        analyze_only=analyze_only,
        log_level=log_level,
        verbose=verbose,
    )


@app.command()
def submit(
    movie: MovieRequired,

    style: StyleOpt = "热血搞笑",

    duration: DurationOpt = 60,

    voice: VoicePlain = None,

    video_format: VideoFormatOpt = "16:9",

    video: VideoOpt = None,

    library_dir: LibraryDirOpt = None,

    research: ResearchOpt = None,

    bgm: BgmOpt = None,

    no_bgm: NoBgmOpt = False,

    no_clips: NoClipsOpt = False,

    strict: StrictOpt = False,

    subtitle_lang: SubtitleLangOpt = None,

    subtitle_mode: SubtitleModeOpt = None,

    narration_preset: NarrationPresetOpt = None,

    output_dir: OutputDirPlain = None,


    lang: str = typer.Option("zh", "--lang", help="解说语言 / Narration language"),


    max_retries: int = typer.Option(3, "--max-retries", help="最大重试次数 / Max retries"),


    wait: bool = typer.Option(False, "--wait", help="提交后等待完成 / Wait for completion"),


    timeout: Optional[float] = typer.Option(
        None, "--timeout", help="等待超时(秒) / Wait timeout (seconds)"
    ),


    remote: Optional[str] = typer.Option(
        None, "--remote", "-r", help="远程服务器URL / Remote server URL (e.g. http://worker:8765)"
    )


):
    """Submit an async narration task.

    
    Examples:
        mn submit -m "The Dark Knight" -p douyin-fast
        mn submit -m Inception --wait --timeout 600
        mn submit -m The Dark Knight --remote http://worker:8765 --wait
    """
    return _submit_impl(
        movie=movie,
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
        output_dir=output_dir,
        lang=lang,
        max_retries=max_retries,
        wait=wait,
        timeout=timeout,
        remote=remote,
    )


# ── Remaining command registrations ────────────────────────
app.command()(resume)
app.command()(rerun)
app.command()(resolve)
app.command()(research)
app.command()(scenes)
app.command()(align)
app.command()(clips)
app.command()(plugin)
app.command()(version)
app.command()(doctor)
app.command()(benchmark)
app.command()(preset)
app.command()(status)
app.command()(tasks)
app.command()(cancel)
app.command()(wait)
app.command()(cleanup)
app.command()(serve)
app.command()(download)
app.command("api-spec")(api_spec)

# ── Community preset sharing (v1.5.1) ─────────────────────
presets_app = typer.Typer(
    help="Community preset sharing — install and manage YAML data presets "
    "(no code execution; see ADR-018).",
    no_args_is_help=True,
)
app.add_typer(presets_app, name="presets")
presets_app.command("list")(presets_list)
presets_app.command("install")(presets_install)
presets_app.command("remove")(presets_remove)
presets_app.command("show")(presets_show)

# ── Artifact lifecycle commands (v0.8.3) ───────────────────
artifacts_app = typer.Typer(
    help="产物存储与生命周期管理 / Artifact storage and TTL lifecycle management.",
    no_args_is_help=True,
)
app.add_typer(artifacts_app, name="artifacts")
artifacts_app.command("list")(artifacts_list)
artifacts_app.command("cleanup")(artifacts_cleanup)
