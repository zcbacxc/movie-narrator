# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging
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
from .errors import PipelineCancelled, PipelinePaused, PipelineStrictError, RunController, StepAction, check_cancelled
from .export_clips import export_clips
from .match import match_clips
from .preflight import PreflightError, run_preflight
from .registry import step_registry
from .research import research_plot
from .resolve import resolve_video
from .scenes import detect_scenes
from .script import generate_script
from .script_export import export_script_md
from .subtitle import generate_subtitle
from .translate import translate_subtitles
from .tts import generate_voice
from .qa_gate import run_qa_gate
from .render import render_video
from .qa import validate_deliverable
from ..workflow.schema import JobParams

# ── Unified parameter schema (single source of truth) ──────
# PARAM_WHITELIST is derived from JobParams model fields, eliminating
# the drift between the hardcoded frozenset and the Pydantic model.
# presets/base.py:ALLOWED_PARAM_KEYS is a validated subset of this.

PARAM_WHITELIST: frozenset[str] = frozenset(JobParams.model_fields.keys())

# ── Register built-in steps in the StepRegistry ────────────
# Each step is registered with its metadata (soft/hard, status_field,
# consequence message). External plugins register via @register_step.
# The registry preserves registration order for built-in steps and
# supports after=/before= hints for plugin step insertion.

_BUILTIN_STEP_META = {
    # name: (func, soft, status_field, consequence)
    "resolve_video":       (resolve_video,       False, None, ""),
    "prepare_assets":      (prepare_assets,      False, None, ""),
    "research_plot":       (research_plot,       True,  "research",
        "research unavailable — script will use generic plot description"),
    "generate_script":     (generate_script,     False, None, ""),
    "export_script_md":    (export_script_md,    False, None, ""),
    "generate_voice":      (generate_voice,      False, None, ""),
    "align_audio":         (align_audio,         True,  "align",
        "audio alignment skipped — subtitle timestamps may drift from actual speech"),
    "detect_scenes":       (detect_scenes,       True,  "scene",
        "scene detection skipped — clips will use fixed-duration segments"),
    "match_clips":         (match_clips,         True,  "match",
        "clip matching skipped — segments mapped to sequential clips without embedding search"),
    "mix_bgm":             (mix_bgm,             True,  "bgm",
        "BGM mixing failed — final video will have narration audio only, no background music"),
    "translate_subtitles": (translate_subtitles, True,  "translate",
        "translation failed — only original-language subtitles will be available"),
    "generate_subtitle":   (generate_subtitle,   False, None, ""),
    "run_qa_gate":         (run_qa_gate,         True,  "qa_gate",
        "QA gate skipped — intermediate product validation not performed"),
    "render_video":        (render_video,        False, None, ""),
    "validate_deliverable":(validate_deliverable,False, None, ""),
    "export_clips":        (export_clips,        True,  "export",
        "clip export skipped — no standalone clip files will be produced"),
}

for _name, (_func, _soft, _field, _consequence) in _BUILTIN_STEP_META.items():
    if not step_registry.contains(_name):
        step_registry.register(
            _name, _func,
            soft=_soft,
            status_field=_field,
            consequence=_consequence,
        )

# ── Derived constants (backward-compatible with existing code) ──

STEPS = step_registry.ordered_steps()

SOFT_STATUS_STEPS: set[str] = step_registry.soft_step_names()

STATUS_FIELD_FOR_STEP: Dict[str, str] = {
    name: step_registry.status_field_for(name)
    for name in SOFT_STATUS_STEPS
    if step_registry.status_field_for(name) is not None
}

SOFT_STEP_CONSEQUENCES: Dict[str, str] = {
    name: step_registry.consequence_for(name)
    for name in SOFT_STATUS_STEPS
}

# Safety: every soft step must have a status field mapping.
assert SOFT_STATUS_STEPS == set(STATUS_FIELD_FOR_STEP), (
    "SOFT_STATUS_STEPS and STATUS_FIELD_FOR_STEP keys must match"
)

# Short alias mapping for workflow_steps keys (spec §9 back-compat).
# Allows users to write `{"scene": False}` in addition to the
# function-name key `{"detect_scenes": False}`.
_SHORT_TO_STEP: Dict[str, str] = {
    "research": "research_plot",
    "align": "align_audio",
    "scene": "detect_scenes",
    "match": "match_clips",
    "bgm": "mix_bgm",
    "export": "export_clips",
    "translate": "translate_subtitles",
}

# Reverse map for backward compat with old _STEP_ALIASES usage.
_STEP_ALIASES: Dict[str, str] = {v: k for k, v in _SHORT_TO_STEP.items()}


def _step_enabled(workflow_steps: Optional[Dict[str, bool]], step_name: str) -> bool:
    """Return False if either function-name or short alias is explicitly false.

    Replaces the old inline `workflow_steps.get(name, True)` check.
    Now covers all 7 SOFT_STATUS_STEPS short keys, not just translate.
    """
    if not workflow_steps:
        return True
    if workflow_steps.get(step_name) is False:
        return False
    # Reverse lookup: is there a short key for this step, and is it False?
    for short, full in _SHORT_TO_STEP.items():
        if full == step_name and workflow_steps.get(short) is False:
            return False
    return True


# ── Context construction (shared by CLI and Web) ───────────


def common_build_kwargs(
    *,
    movie: str,
    style: str,
    duration: int,
    voice: Optional[str],
    format: str,
    output_dir: Path,
    keep_cache: bool = False,
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
    narration_preset: Optional[str] = None,
    lang: str = "zh",
    log_level: int = logging.DEBUG,
    verbose: bool = False,
) -> dict:
    """Build a kwargs dict for :func:`build_context` from common parameters.

    Eliminates duplicated kwargs construction across ``cli.py`` (create,
    imitate), ``race.py``, and ``cloud/worker.py``.  Callers spread the
    result with ``**`` and may override individual keys before passing
    to :func:`build_context`.
    """
    return {
        "movie": movie,
        "style": style,
        "duration": duration,
        "voice": voice,
        "format": format,
        "output_dir": output_dir,
        "keep_cache": keep_cache,
        "video": video,
        "library_dir": library_dir,
        "research": research,
        "bgm": bgm,
        "no_bgm": no_bgm,
        "no_clips": no_clips,
        "strict": strict,
        "workflow_steps": workflow_steps,
        "params": params,
        "config_path": config_path,
        "subtitle_lang": subtitle_lang,
        "subtitle_mode": subtitle_mode,
        "services": services,
        "narration_preset": narration_preset,
        "lang": lang,
        "log_level": log_level,
        "verbose": verbose,
    }


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
    narration_preset: Optional[str] = None,
    lang: str = "zh",  # R2-NA-LANG: narration language
    log_level: int = logging.DEBUG,
    verbose: bool = False,
    json_format: bool = False,
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
        console = build_console(
            output_dir,
            log_level=log_level,
            verbose=verbose,
            json_format=json_format,
        )
        services = Services(
            console=console,
            logger=getattr(console, "_log", None),
        )

    ctx = Context(
        movie_name=movie,
        style=style,
        duration=duration,
        output_dir=str(output_dir),
        library_dir=lib,
        assets=Assets(bgm=bgm_path),
        services=services,
    )
    from ..utils.cost_tracker import CostTracker
    ctx.cost_tracker = CostTracker()
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
            # R2-NA-LANG: single source of truth for narration language.
            "lang": lang,
            # v0.7.2: preview mode — render only the first N seconds for quick
            # iteration.  OFF by default (backward compatible).  These keys are
            # also propagated via PARAM_WHITELIST below when explicitly set;
            # setting defaults here keeps them visible in metadata.json.
            "render_preview_mode": (params or {}).get("render_preview_mode", False),
            "render_preview_sec": (params or {}).get("render_preview_sec", 10.0),
        }
    )

    # R2-NA-LANG: consistency check — warn if subtitle target language
    # matches narration language (translation would be a no-op).
    if subtitle_lang and subtitle_lang.lower() == lang.lower():
        services.console.inline_warn(
            f"subtitle_lang ({subtitle_lang}) matches narration lang ({lang}) — "
            f"subtitle translation will be a no-op."
        )

    if workflow_steps:
        ctx.metadata["workflow_steps"] = dict(workflow_steps)

    # ── Preset merge: preset params are the BASELINE; user params override ──
    # The preset provides style defaults (match cadence, BGM ducking, subtitle
    # layout, prompt shaping).  User-supplied params always win, so users can
    # do e.g. --narration-preset mainstream-dry while still overriding a
    # single knob via job.yaml params.
    effective_params: Dict[str, Any] = {}
    if narration_preset:
        from ..presets import get_preset
        preset = get_preset(narration_preset)
        effective_params.update(preset.param_dict)
        ctx.metadata["narration_preset"] = narration_preset
        ctx.metadata["narration_preset_tags"] = dict(preset.tag_dict)
    if params:
        effective_params.update(params)

    if effective_params:
        for key in PARAM_WHITELIST:
            if key in effective_params and effective_params[key] is not None:
                ctx.metadata[key] = effective_params[key]

    # ── WP7: Draft profile — fast iteration override ──
    # When render_profile=draft, override render params for speed.
    # User-supplied params (via job.yaml or preset) always take precedence
    # over draft defaults — draft only fills gaps.
    render_profile = (params or {}).get("render_profile") or (effective_params.get("render_profile"))
    if render_profile == "draft":
        _DRAFT_RENDER_DEFAULTS = {
            "render_crf": 28,
            "render_preset": "ultrafast",
            "render_faststart": True,
        }
        for dk, dv in _DRAFT_RENDER_DEFAULTS.items():
            # Only set if user didn't explicitly set this param
            if dk not in effective_params or effective_params.get(dk) is None:
                ctx.metadata[dk] = dv
        ctx.metadata["render_profile"] = "draft"

    if config_path:
        ctx.metadata["config_path"] = config_path

    return ctx


# ── Pipeline execution ─────────────────────────────────────


def _save_pipeline_state(ctx: Context, completed_step: str) -> Path:
    """Serialize pipeline state for resume (EP9).

    Writes ``pipeline_state.json`` to ``ctx.output_dir``. Excludes the
    non-serializable ``services`` field — the ``Context`` model_validator
    auto-injects ``SilentConsole`` on load.

    Returns the path to the saved state file.
    """
    import json
    state = {
        "completed_step": completed_step,
        "context": ctx.model_dump(mode="json", exclude={"services"}),
    }
    state_path = Path(ctx.output_dir) / "pipeline_state.json"
    state_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return state_path


def _load_pipeline_state(state_path: Path) -> tuple[Context, str]:
    """Load pipeline state from a file (EP9).

    Returns ``(context, completed_step)``. The context's ``services``
    field is auto-filled with ``SilentConsole`` by the model_validator —
    callers should assign a real console if interactive output is needed.
    """
    import json
    data = json.loads(state_path.read_text(encoding="utf-8"))
    completed_step = data["completed_step"]
    ctx = Context(**data["context"])
    return ctx, completed_step


def _next_step_after(completed_step: str) -> Optional[str]:
    """Return the name of the step after *completed_step*, or None if last."""
    for i, step in enumerate(STEPS):
        if step.__name__ == completed_step and i + 1 < len(STEPS):
            return STEPS[i + 1].__name__
    return None


def run_pipeline(
    ctx: Context,
    *,
    controller: Optional[RunController] = None,
    start_step: Optional[str] = None,
) -> Context:
    """Execute the 16-step pipeline against *ctx*.

    ``controller=None`` means CLI mode — no cancel checks fire. Web
    passes a ``GradioController`` so the user can request a cooperative
    cancel at step boundaries.

    ``start_step`` (EP9): when set, skip all steps before this step name.
    Used by ``mn resume`` to avoid re-running already-completed steps.

    ``PipelineCancelled`` raises before ``_check_strict``, so ``--strict``
    never trips on cancellation. Cancel is a distinct terminal path —
    it is NOT a soft-step warning and does NOT set status fields to
    ``failed``.

    ``PipelinePaused`` (EP9) raises after a step completes when
    ``ctx.metadata["pause_at"]`` matches the step name. The pipeline
    state is serialized before raising so ``mn resume`` can continue.
    """
    console = ctx.services.console
    workflow_steps: Optional[Dict[str, bool]] = ctx.metadata.get("workflow_steps")

    # ── Run ID for log correlation ──────────────────────
    # Extracted from the console (set by build_console). Stored in
    # metadata so metadata.json can cross-reference log files.
    run_id = getattr(console, "_run_id", None)
    if run_id:
        ctx.metadata["run_id"] = run_id

    # ── Preflight: fail fast if LLM / TTS is not usable ────
    # Avoids silent degradation to mock content when services are down.
    try:
        run_preflight(ctx)
    except PreflightError:
        raise

    total_start = time.time()

    # EP9: When resuming, skip steps before start_step
    _resume_started = start_step is None

    for step in STEPS:
        name = step.__name__

        # EP9: Skip already-completed steps when resuming
        if not _resume_started:
            if name == start_step:
                _resume_started = True
            else:
                continue

        check_cancelled(controller)

        # ── Pre-check: workflow_steps disabled? ──────────────
        # Authoritative path: runner short-circuits before step runs.
        # Checks both the function-name key and any short alias (spec §9).
        # WP1: now uses _step_enabled() for full short-key coverage.
        if not _step_enabled(workflow_steps, name):
            ctx.step_state = StepState(
                result=StepResult.SKIPPED, message="disabled by workflow config"
            )
            _set_pipeline_status_disabled(ctx, name)
            console.step_skip(name, ctx.step_state.message)
            _check_strict(ctx, name)
            continue

        check_cancelled(controller)

        # v0.7.2: Skip non-essential steps in preview mode.
        # Only SOFT steps (research_plot, translate_subtitles, run_qa_gate,
        # export_clips) are skipped — hard steps always run so the preview is
        # a faithful representation of the final output.
        if ctx.metadata.get("render_preview_mode"):
            from ..utils.preview import should_skip_step_for_preview
            if should_skip_step_for_preview(name, True):
                ctx.step_state = StepState(
                    result=StepResult.SKIPPED, message="skipped in preview mode"
                )
                console.debug(f"  Preview: skipping {name}")
                continue

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
                # R2-NA-ORCH: detect retryable (transient) errors via the
                # `retryable` attribute on ProviderError subclasses (and any
                # wrapped exception that sets it). Network timeouts / rate
                # limits / temporary-unavailable set it True; config and
                # logic errors default to False (non-retryable).
                is_retryable = bool(getattr(e, "retryable", False))
                ctx.step_state.step_retryable = is_retryable
                if name in SOFT_STATUS_STEPS:
                    _set_pipeline_status_failed(ctx, name)
                    consequence = SOFT_STEP_CONSEQUENCES.get(name, "")
                    msg = str(e)
                    if consequence:
                        msg = f"{msg} — {consequence}"
                    ctx.step_state = StepState(
                        result=StepResult.WARNING, message=msg,
                        step_retryable=is_retryable,
                    )
                    console.step_warn(name, ctx.step_state.message)
                    ctx.metadata.setdefault("_degraded_steps", []).append(name)
                    # AQ-10: write per-step error to metadata for audit
                    if name == "mix_bgm":
                        ctx.metadata["bgm_error"] = str(e)
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
                        result=StepResult.WARNING, message=f"skipped: {e}",
                        step_retryable=is_retryable,
                    )
                    break  # exit retry loop, continue to next step

                console.step_err(name, e, elapsed)
                raise

        elapsed = time.time() - step_start

        # ── F3: surface soft-step degradation from non-exception paths ──
        # Some soft steps (e.g. align_audio in C1 fix) internally catch
        # exceptions and set status.<field>='failed' + step_state.result
        # = WARNING without re-raising. The runner's outer except block
        # (line 336-348) only accumulates _degraded_steps for steps that
        # raise; without this check, internal fallbacks stay invisible
        # in the runner's degradation summary (even though metadata.json
        # records them via align_degraded / scene_detection_degraded).
        #
        # Fix: after a step returns normally, if it's a soft step whose
        # status field is 'failed' or 'skipped' AND step_state.result is
        # WARNING, accumulate it into _degraded_steps (idempotent — the
        # exception path may have already added it).
        if name in SOFT_STATUS_STEPS and ctx.step_state.result is StepResult.WARNING:
            field = STATUS_FIELD_FOR_STEP.get(name)
            status_val = getattr(ctx.status, field, None) if field else None
            if status_val in ("failed", "skipped"):
                degraded_list = ctx.metadata.setdefault("_degraded_steps", [])
                if name not in degraded_list:  # dedupe vs exception path
                    degraded_list.append(name)

        # ── Render step result ───────────────────────────────
        _render_step_result(ctx, name, elapsed, console)
        _check_strict(ctx, name)

        # ── EP9: Pause-at check ─────────────────────────────
        # If the user requested a pause after this step, serialize state
        # and raise PipelinePaused so the CLI can inform the user.
        pause_at = ctx.metadata.get("pause_at")
        if pause_at and pause_at == name:
            state_path = _save_pipeline_state(ctx, name)
            console.inline_warn(
                f"Pipeline paused after '{name}'. "
                f"State saved to {state_path.name}. "
                f"Resume with: mn resume --state \"{state_path}\""
            )
            raise PipelinePaused(name)

        # ── Reset step_state for next iteration ──────────────
        ctx.step_state = StepState()

    total_elapsed = time.time() - total_start
    console.done(total_elapsed)

    # ── Degradation summary ─────────────────────────────
    # If any soft steps degraded during this run, warn the user about
    # the quality impact on the final output.
    degraded = ctx.metadata.get("_degraded_steps", [])
    if degraded:
        console.inline_warn(
            f"Pipeline completed with {len(degraded)} degraded step(s): "
            f"{', '.join(degraded)}. The final output may have reduced quality."
        )

    # ── WP1: Re-export metadata.json after all steps ────
    # render_video writes metadata.json before QA runs, so qa_report and
    # other late-stage diagnostics are missing. Re-export here to capture
    # the full picture (qa_report, degraded_steps, match_summary, etc.).
    if ctx.video_path:
        try:
            import json
            from pathlib import Path
            from ..utils.metadata_export import build_metadata_json

            output_dir = Path(ctx.output_dir)
            meta = build_metadata_json(ctx)
            with open(output_dir / "metadata.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        except Exception as e:
            # Best-effort: metadata re-export is diagnostic, not critical.
            # If it fails, the render-time metadata.json is still valid.
            # B5 fix: leave a debug trace so disk-full / readonly paths
            # are visible in verbose logs (not silently swallowed).
            console.debug(f"metadata.json re-export failed: {e}")

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

    R2-NA-ORCH: before delegating, inspect the exception's ``retryable``
    attribute. A retryable (transient, network-type) failure is a good
    candidate for an interactive [R]etry/[S]kip/[A]bort choice:

    - If ``--retry`` is enabled (the controller exposes ``on_step_error``,
      wired up by :class:`InteractiveCLIController`), the existing prompt
      fires unchanged — the retryable flag simply makes the transient
      nature of the failure explicit.
    - If ``--retry`` is *not* enabled, log a warning suggesting the flag
      so the user knows the failure may clear on retry, then fall through
      to the existing fail-fast (ABORT) path.
    - Non-retryable errors (config/logic) skip the hint entirely and keep
      the existing behavior.
    """
    is_retryable = bool(getattr(error, "retryable", False))
    handler = (
        getattr(controller, "on_step_error", None)
        if controller is not None
        else None
    )
    retry_enabled = handler is not None

    if is_retryable and not retry_enabled:
        # Transient failure but the user did not enable --retry. Surface a
        # hint that the error may clear on retry, then proceed with the
        # existing fail-fast (ABORT) behavior below.
        console.inline_warn(
            f"Step '{name}' failed with a retryable (transient) error: {error}. "
            f"Re-run with --retry to choose [R]etry/[S]kip/[A]bort."
        )

    if controller is None:
        return StepAction.ABORT
    if handler is None:
        return StepAction.ABORT
    return handler(name, error, attempt)
