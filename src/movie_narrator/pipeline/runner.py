import time
from pathlib import Path
from typing import Any, Dict, Optional

from .. import __version__
from ..config import get_settings
from ..models import Assets, Context, Services, StepResult, StepState
from ..utils.console import build_console
from ..utils.environment import collect_environment
from .align import align_audio
from .assets import prepare_assets
from .bgm import mix_bgm
from .errors import PipelineCancelled, PipelineStrictError, RunController, StepAction, check_cancelled
from .export_clips import export_clips
from .match import match_clips
from .preflight import PreflightError, run_preflight
from .research import research_plot
from .resolve import resolve_video
from .scenes import detect_scenes
from .script import generate_script
from .script_export import export_script_md
from .subtitle import generate_subtitle
from .translate import translate_subtitles
from .tts import generate_voice
from .render import render_video

# ── Step metadata (module-level constants) ──────────────────
# Single source of truth: a soft step whose exception is caught by the runner
# (rendered as ⚠ + continue) instead of being re-raised as a hard failure.
SOFT_STATUS_STEPS = {
    "research_plot",
    "align_audio",
    "detect_scenes",
    "match_clips",
    "mix_bgm",
    "export_clips",
    "translate_subtitles",
}

# Map soft step name → PipelineStatus field name. Steps not in this map
# (e.g. resolve_video, generate_script, render_video) do not write to
# PipelineStatus because a hard failure there aborts the pipeline anyway.
STATUS_FIELD_FOR_STEP: Dict[str, str] = {
    "research_plot": "research",
    "align_audio": "align",
    "detect_scenes": "scene",
    "match_clips": "match",
    "mix_bgm": "bgm",
    "export_clips": "export",
    "translate_subtitles": "translate",
}

# Safety: every soft step must have a status field mapping.
# A typo in either dict would silently break status tracking.
assert SOFT_STATUS_STEPS == set(STATUS_FIELD_FOR_STEP), (
    "SOFT_STATUS_STEPS and STATUS_FIELD_FOR_STEP keys must match"
)

# Short alias mapping for workflow_steps keys (spec §9 back-compat).
# Allows users to write `{"translate": False}` in addition to the
# function-name key `{"translate_subtitles": False}`.
_STEP_ALIASES: Dict[str, str] = {
    "translate_subtitles": "translate",
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
    # Multi-language subtitle (v0.3) — soft step before generate_subtitle.
    # Produces ctx.translated_texts; downstream formatter writes three SRTs.
    translate_subtitles,
    generate_subtitle,
    render_video,
    export_clips,
]


# ── Context construction (shared by CLI and Web) ───────────


def build_context(
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
    subtitle_lang: Optional[str] = None,
    subtitle_mode: Optional[str] = None,
    services: Optional[Services] = None,
) -> Context:
    """Assemble a :class:`Context` ready for :func:`run_pipeline`.

    Handles Settings merge, BGM resolution, console/logger wiring, and
    metadata initialisation. Both CLI and Web call this — the only
    difference is the ``services`` inject (Web passes a
    ``GradioConsole``-backed ``Services``; CLI passes ``None`` and gets
    the default ``PlainConsole``).

    This function does **not** run any pipeline steps.
    """
    settings = get_settings()
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    lib = library_dir
    research_enabled = research if research is not None else False

    if no_bgm:
        bgm_path = None
        bgm_request = "none"
    elif bgm:
        bgm_path = bgm
        bgm_request = "explicit"
    else:
        bgm_path = None
        bgm_request = "none"

    if services is None:
        services = Services(console=build_console(output_dir))

    ctx = Context(
        movie_name=movie,
        style=style,
        duration=duration,
        output_dir=str(output_dir),
        library_dir=lib,
        assets=Assets(bgm=bgm_path),
        services=services,
    )
    ctx.metadata.update(
        {
            "voice": voice,
            "format": format,
            "keep_cache": keep_cache,
            "video_arg": video,
            "research_enabled": research_enabled,
            "export_clips": (False if no_clips else True),
            "strict": strict,
            "bgm_request": bgm_request,
            "version": __version__,
            "environment": collect_environment(),
            # Multi-language subtitle. Empty lang → feature off.
            "subtitle_lang": (subtitle_lang or None),
            "subtitle_mode": (subtitle_mode or "original"),
            "translate_provider": (params or {}).get("translate_provider", "llm"),
            "translate_retries": (params or {}).get("translate_retries", 3),
            "research_provider": (params or {}).get("research_provider", "llm"),
        }
    )

    if workflow_steps:
        ctx.metadata["workflow_steps"] = dict(workflow_steps)
    if params:
        for key in (
            "scene_threshold", "scene_frame_skip", "match_min_score",
            "match_speed_clamp_min", "match_speed_clamp_max",
            "scene_merge_min_duration", "embedding_model_name",
            "bgm_gain_db", "tts_pause_ms",
            "tts_max_concurrent", "tts_audio_format", "tts_audio_bitrate",
            "translate_source_lang", "translate_chunk_chars", "translate_chunk_size",
            "whisperx_device", "whisperx_model", "whisperx_language",
            "render_fps", "render_video_codec", "render_audio_codec", "render_threads",
            "render_bg_color", "render_font_size", "render_output_name", "render_ffmpeg_timeout",
            "async_timeout", "async_max_workers",
            "video_sizes",
        ):
            if key in params and params[key] is not None:
                ctx.metadata[key] = params[key]
    if config_path:
        ctx.metadata["config_path"] = config_path

    return ctx


# ── Pipeline execution ─────────────────────────────────────


def run_pipeline(
    ctx: Context,
    *,
    controller: Optional[RunController] = None,
) -> Context:
    """Execute the 14-step pipeline against *ctx*.

    ``controller=None`` means CLI mode — no cancel checks fire. Web
    passes a ``GradioController`` so the user can request a cooperative
    cancel at step boundaries.

    ``PipelineCancelled`` raises before ``_check_strict``, so ``--strict``
    never trips on cancellation. Cancel is a distinct terminal path —
    it is NOT a soft-step warning and does NOT set status fields to
    ``failed``.
    """
    console = ctx.services.console
    workflow_steps: Optional[Dict[str, bool]] = ctx.metadata.get("workflow_steps")

    # ── Preflight: fail fast if LLM / TTS is not usable ────
    # Avoids silent degradation to mock content when services are down.
    try:
        run_preflight(ctx)
    except PreflightError:
        raise

    total_start = time.time()

    for step in STEPS:
        name = step.__name__

        check_cancelled(controller)

        # ── Pre-check: workflow_steps disabled? ──────────────
        # Authoritative path: runner short-circuits before step runs.
        # Checks both the function-name key and any short alias (spec §9).
        alias = _STEP_ALIASES.get(name)
        if workflow_steps and (
            not workflow_steps.get(name, True)
            or (alias and not workflow_steps.get(alias, True))
        ):
            ctx.step_state = StepState(
                result=StepResult.SKIPPED, message="disabled by workflow config"
            )
            _set_pipeline_status_disabled(ctx, name)
            console.step_skip(name, ctx.step_state.message)
            _check_strict(ctx, name)
            continue

        check_cancelled(controller)

        # ── Execute step with soft/hard exception fork ───────
        # Soft steps: exception → ⚠ + continue (no abort).
        # Hard steps: exception → ✗ + re-raise (abort pipeline),
        #   unless the controller offers interactive retry.
        ctx.step_state = StepState()  # reset before execution
        step_start = time.time()
        console.step(name)

        attempt = 0
        while True:
            attempt += 1
            try:
                ctx = step(ctx)
                break  # success — exit retry loop
            except PipelineCancelled:
                console.cancelled("Pipeline cancelled.")
                raise
            except Exception as e:
                elapsed = time.time() - step_start
                if name in SOFT_STATUS_STEPS:
                    _set_pipeline_status_failed(ctx, name)
                    ctx.step_state = StepState(
                        result=StepResult.WARNING, message=str(e)
                    )
                    console.step_warn(name, ctx.step_state.message)
                    _check_strict(ctx, name)
                    break  # exit retry loop, continue to next step

                # Hard step failure — check for interactive retry.
                action = _handle_step_error(controller, name, e, attempt, console)
                if action is StepAction.RETRY:
                    console.debug(f"  retrying {name} (attempt {attempt + 1})...")
                    ctx.step_state = StepState()
                    continue
                elif action is StepAction.SKIP:
                    console.step_warn(name, f"skipped after {attempt} attempt(s): {e}")
                    ctx.step_state = StepState(
                        result=StepResult.WARNING, message=f"skipped: {e}"
                    )
                    break  # exit retry loop, continue to next step

                console.step_err(name, e, elapsed)
                raise

        elapsed = time.time() - step_start

        # ── Render step result ───────────────────────────────
        _render_step_result(ctx, name, elapsed, console)
        _check_strict(ctx, name)

        # ── Reset step_state for next iteration ──────────────
        ctx.step_state = StepState()

    total_elapsed = time.time() - total_start
    console.done(total_elapsed)

    return ctx


def _set_pipeline_status_disabled(ctx: Context, step_name: str) -> None:
    """Set the corresponding PipelineStatus field to 'disabled'."""
    field = STATUS_FIELD_FOR_STEP.get(step_name)
    if field:
        setattr(ctx.status, field, "disabled")


def _set_pipeline_status_failed(ctx: Context, step_name: str) -> None:
    """Set the corresponding PipelineStatus field to 'failed'."""
    field = STATUS_FIELD_FOR_STEP.get(step_name)
    if field:
        setattr(ctx.status, field, "failed")


def _render_step_result(
    ctx: Context,
    name: str,
    elapsed: float,
    console,
) -> None:
    """Read ctx.step_state and call the appropriate console method."""
    result = ctx.step_state.result
    msg = ctx.step_state.message

    if result is StepResult.SUCCESS:
        console.step_ok(name, elapsed)
    elif result is StepResult.SKIPPED:
        console.step_skip(name, msg or "skipped")
    elif result is StepResult.WARNING:
        console.step_warn(name, msg or "warning")


def _check_strict(ctx: Context, step_name: str) -> None:
    """Raise PipelineStrictError if --strict and any status.* == 'failed'."""
    if ctx.metadata.get("strict"):
        failed = [k for k, v in ctx.status.model_dump().items() if v == "failed"]
        if failed:
            raise PipelineStrictError(step=step_name, status=ctx.status.model_dump())


def _handle_step_error(
    controller: Optional[RunController],
    name: str,
    error: Exception,
    attempt: int,
    console,
) -> StepAction:
    """Ask the controller how to handle a hard step failure.

    If the controller does not implement ``on_step_error`` (e.g. the
    GradioController or ``controller=None``), returns ``ABORT`` to
    preserve the existing fail-fast behavior.
    """
    if controller is None:
        return StepAction.ABORT
    handler = getattr(controller, "on_step_error", None)
    if handler is None:
        return StepAction.ABORT
    return handler(name, error, attempt)
