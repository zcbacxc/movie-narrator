import time
from pathlib import Path
from typing import Optional

from .. import __version__
from ..config import get_settings
from ..models import Assets, Context
from ..utils.environment import collect_environment
from .align import align_audio
from .assets import prepare_assets
from .bgm import mix_bgm
from .errors import PipelineStrictError
from .export_clips import export_clips
from .match import match_clips
from .research import research_plot
from .resolve import resolve_video
from .scenes import detect_scenes
from .script import generate_script
from .script_export import export_script_md
from .subtitle import generate_subtitle
from .tts import generate_voice
from .render import render_video

# ANSI colors
_BLUE = "\033[94m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_RESET = "\033[0m"
_BOLD = "\033[1m"

SOFT_STATUS_STEPS = {
    "research_plot",
    "align_audio",
    "detect_scenes",
    "match_clips",
    "mix_bgm",
    "export_clips",
}

STEPS = [
    resolve_video,
    prepare_assets,
    research_plot,
    generate_script,
    export_script_md,
    generate_voice,
    align_audio,
    detect_scenes,
    match_clips,
    mix_bgm,
    generate_subtitle,
    render_video,
    export_clips,
]


def _fmt_time(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds*1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds/60:.1f}m"


def run_pipeline(
    movie: str,
    style: str,
    duration: int,
    voice: Optional[str],
    format: str,
    output_dir: Path,
    keep_cache: bool = False,
    *,
    video: Optional[str] = None,
    library_dir: Optional[str] = None,
    research: Optional[bool] = None,
    bgm: Optional[str] = None,
    no_bgm: bool = False,
    no_clips: bool = False,
    strict: bool = False,
) -> Context:
    settings = get_settings()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    lib = library_dir if library_dir is not None else settings.library_dir
    research_enabled = settings.research_enabled if research is None else research

    if no_bgm:
        bgm_path = None
        bgm_request = "none"
    elif bgm:
        bgm_path = bgm
        bgm_request = "explicit"
    elif settings.default_bgm:
        bgm_path = settings.default_bgm
        bgm_request = "default"
    else:
        bgm_path = None
        bgm_request = "none"

    ctx = Context(
        movie_name=movie,
        style=style,
        duration=duration,
        output_dir=str(output_dir),
        library_dir=lib,
        assets=Assets(bgm=bgm_path),
    )
    ctx.metadata.update(
        {
            "voice": voice,
            "format": format,
            "keep_cache": keep_cache,
            "video_arg": video,
            "research_enabled": research_enabled,
            "export_clips": (False if no_clips else settings.export_clips_default),
            "strict": strict,
            "bgm_request": bgm_request,
            "version": __version__,
            "environment": collect_environment(),
        }
    )

    total_start = time.time()

    for step in STEPS:
        name = step.__name__
        step_start = time.time()
        print(f"{_BLUE}▶ {name}{_RESET}", end="", flush=True)
        try:
            ctx = step(ctx)
        except Exception as e:
            elapsed = time.time() - step_start
            print(f"\r{_RED}✗ {name}{_RESET}: {e} {_YELLOW}({_fmt_time(elapsed)}){_RESET}")
            raise

        elapsed = time.time() - step_start

        if name not in SOFT_STATUS_STEPS:
            print(f"\r{_GREEN}✓ {name}{_RESET}  {_BOLD}{_fmt_time(elapsed)}{_RESET}")
        else:
            status_map = {
                "research_plot": ctx.status.research,
                "align_audio": ctx.status.align,
                "detect_scenes": ctx.status.scene,
                "match_clips": ctx.status.match,
                "mix_bgm": ctx.status.bgm,
                "export_clips": ctx.status.export,
            }
            st = status_map.get(name)
            if st == "success":
                print(f"\r{_GREEN}✓ {name}{_RESET}  {_BOLD}{_fmt_time(elapsed)}{_RESET}")
            # skipped/disabled/failed already logged inside step

        _check_strict(ctx, name)

    total_elapsed = time.time() - total_start
    print(f"\n{_BOLD}Done in {_fmt_time(total_elapsed)}{_RESET}")

    return ctx


def _check_strict(ctx: Context, step_name: str) -> None:
    """Raise PipelineStrictError if --strict and any status.* == 'failed'."""
    if ctx.metadata.get("strict"):
        failed = [k for k, v in ctx.status.model_dump().items() if v == "failed"]
        if failed:
            raise PipelineStrictError(step=step_name, status=ctx.status.model_dump())
