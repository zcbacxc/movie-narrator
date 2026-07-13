import time
from pathlib import Path
from typing import Any, Dict, Optional

from .. import __version__
from ..config import get_settings
from ..models import Assets, Context, Services, StepState
from ..utils.console import build_console
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
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


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
    workflow_steps: Optional[Dict[str, bool]] = None,
    params: Optional[Dict[str, Any]] = None,
    config_path: Optional[str] = None,
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

    if workflow_steps:
        ctx.metadata["workflow_steps"] = dict(workflow_steps)
    if params:
        for key in ("scene_threshold", "match_min_score", "research_provider"):
            if key in params and params[key] is not None:
                ctx.metadata[key] = params[key]
    if config_path:
        ctx.metadata["config_path"] = config_path

    # Build console and inject into ctx
    console = build_console(output_dir)
    ctx.services = Services(console=console)

    total_start = time.time()

    for step in STEPS:
        name = step.__name__

        # ── Pre-check: workflow_steps disabled? ──────────────
        if workflow_steps and not workflow_steps.get(name, True):
            ctx.step_state = StepState()  # reset first
            ctx.step_state.result = "skipped"  # type: ignore[assignment]
            ctx.step_state.message = "disabled by workflow config"
            console.step_skip(name, ctx.step_state.message)
            _set_pipeline_status_disabled(ctx, name)
            _check_strict(ctx, name)
            continue

        # ── Execute step ────────────────────────────────────
        ctx.step_state = StepState()  # reset before each step
        step_start = time.time()
        console.step(name)

        try:
            ctx = step(ctx)
        except Exception as e:
            elapsed = time.time() - step_start
            console.step_err(name, e, elapsed)
            raise

        elapsed = time.time() - step_start

        # ── Render step result ───────────────────────────────
        _render_step_result(ctx, name, elapsed, console)

        # ── Reset step_state for next iteration ──────────────
        ctx.step_state = StepState()

        _check_strict(ctx, name)

    total_elapsed = time.time() - total_start
    print(f"\n\033[1mDone in {_fmt_time(total_elapsed)}\033[0m")

    return ctx


def _set_pipeline_status_disabled(ctx: Context, name: str) -> None:
    """Set the corresponding PipelineStatus field to 'disabled'."""
    status_field_map = {
        "research_plot": "research",
        "align_audio": "align",
        "detect_scenes": "scene",
        "match_clips": "match",
        "mix_bgm": "bgm",
        "export_clips": "export",
    }
    field = status_field_map.get(name)
    if field:
        setattr(ctx.status, field, "disabled")


def _render_step_result(
    ctx: Context,
    name: str,
    elapsed: float,
    console,
) -> None:
    """Read ctx.step_state and call the appropriate console method."""
    result = ctx.step_state.result
    msg = ctx.step_state.message

    if result.value == "success":
        console.step_ok(name, elapsed)
    elif result.value == "skipped":
        console.step_skip(name, msg or "skipped")
    elif result.value == "warning":
        console.step_warn(name, msg or "warning")


def _check_strict(ctx: Context, step_name: str) -> None:
    """Raise PipelineStrictError if --strict and any status.* == 'failed'."""
    if ctx.metadata.get("strict"):
        failed = [k for k, v in ctx.status.model_dump().items() if v == "failed"]
        if failed:
            raise PipelineStrictError(step=step_name, status=ctx.status.model_dump())
