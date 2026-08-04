# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Pipeline worker — executes tasks and reports progress (v0.6.0).

Wraps the existing ``run_pipeline`` function with:
- Cancellation via ``CancelController`` (implements ``RunController``)
- Progress tracking via ``ProgressConsole`` (wraps the real console)
- Retry with exponential backoff for transient failures
- Result extraction and error capture
"""

from __future__ import annotations

import logging
import os
import threading
import time
import traceback as tb_module
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from ..models import Context, Services
from ..pipeline.errors import PipelineCancelled
from ..pipeline.runner import STEPS, build_context, common_build_kwargs, run_pipeline
from ..utils.console import (
    BaseConsole,
    Console,
    SilentConsole,
    build_console,
)
from ..utils.log import resolve_log_level
from .checkpoint import CheckpointStore, ResumePlan, TaskCheckpoint
from .dlq import DeadLetterRecord, DeadLetterStore
from .metrics import observe_render_duration
from .models import Task, TaskProgress, TaskRequest, TaskResult, TaskStatus

logger = logging.getLogger(__name__)

# Total pipeline steps for progress calculation
_TOTAL_STEPS = len(STEPS)

# v0.8.1: name of the pipeline step whose duration feeds
# ``mn_render_duration_seconds``. The runner labels each step with the
# step function's ``__name__``, so this is the hook point that does not
# require touching the pipeline package.
_RENDER_STEP = "render_video"


# ── CancelController ───────────────────────────────────────


class CancelController:
    """``RunController`` implementation for cooperative task cancellation.

    The worker thread checks ``is_cancelled()`` at step boundaries
    via the pipeline runner's ``check_cancelled`` calls. The main
    thread (or CLI) calls ``cancel()`` to request termination.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """Check if cancellation was requested."""
        return self._event.is_set()

    def reset(self) -> None:
        """Reset the cancellation flag (for retry)."""
        self._event.clear()


# ── ProgressConsole ────────────────────────────────────────


class ProgressConsole(BaseConsole):
    """Console wrapper that intercepts step events for progress tracking.

    Delegates all output to the wrapped ``Console`` (e.g. ``PlainConsole``
    for real runs, ``SilentConsole`` for CI) while updating the
    ``TaskProgress`` model in real-time.

    v0.9.2: an optional ``on_step_complete`` callback fires whenever a
    pipeline step finishes (success, skip or soft-degrade) and the runner
    moves on to the next one. The worker uses it to write task
    checkpoints, so the step's ``Context`` state is captured at the
    earliest safe moment after the step ran.
    """

    def __init__(
        self,
        inner: Console,
        progress: TaskProgress,
        start_time: float,
        *,
        on_step_complete: Optional[Callable[[str], None]] = None,
        initial_step_index: int = 0,
    ) -> None:
        self._inner = inner
        self._progress = progress
        self._start_time = start_time
        self._step_start: float = 0.0
        self._step_index = initial_step_index
        self._on_step_complete = on_step_complete

    def _notify_complete(self, name: str) -> None:
        """Invoke the checkpoint hook, never letting it break the run."""
        if self._on_step_complete is not None:
            try:
                self._on_step_complete(name)
            except Exception:  # noqa: BLE001 — checkpointing must not break the pipeline
                logger.debug(
                    "Checkpoint callback failed for step %s", name, exc_info=True
                )

    def step(self, name: str) -> None:
        """Record the start of a pipeline step."""
        self._step_start = time.time()
        self._progress.update_step(
            step_name=name,
            index=self._step_index,
            total=_TOTAL_STEPS,
            elapsed=0.0,
        )
        self._progress.elapsed_seconds = time.time() - self._start_time
        self._inner.step(name)

    def step_ok(self, name: str, elapsed: float) -> None:
        """Mark the current step as successfully completed."""
        self._progress.mark_completed(name)
        if name == _RENDER_STEP:
            observe_render_duration(elapsed)
        self._step_index += 1
        self._progress.update_step(
            step_name=name,
            index=self._step_index,
            total=_TOTAL_STEPS,
            elapsed=elapsed,
        )
        self._progress.elapsed_seconds = time.time() - self._start_time
        self._inner.step_ok(name, elapsed)
        self._notify_complete(name)

    def set_step_index(self, index: int) -> None:
        """Set the current step index directly.

        Used by the distributed-rendering soft hook (v0.9.4): the render
        step ran on a remote node, so the local pipeline resumes after
        it and the step counter must skip ahead to stay in sync.
        """
        self._step_index = max(0, index)

    def step_skip(self, name: str, reason: str) -> None:
        """Mark the current step as skipped."""
        self._progress.mark_skipped(name)
        self._step_index += 1
        self._inner.step_skip(name, reason)
        self._notify_complete(name)

    def step_warn(self, name: str, reason: str) -> None:
        """Mark the current step as completed with warnings."""
        self._inner.step_warn(name, reason)
        self._notify_complete(name)

    def step_err(self, name: str, exc: Exception, elapsed: float) -> None:
        """Mark the current step as failed."""
        self._progress.mark_failed(name)
        self._inner.step_err(name, exc, elapsed)

    def warn(self, msg: str) -> None:
        """Emit a warning message."""
        self._inner.warn(msg)

    def info(self, msg: str) -> None:
        """Emit an informational message."""
        self._inner.info(msg)

    def debug(self, msg: str) -> None:
        """Emit a debug message."""
        self._inner.debug(msg)

    def inline_warn(self, msg: str) -> None:
        """Emit a warning message."""
        self._inner.inline_warn(msg)

    def final(self, msg: str) -> None:
        """Print the final pipeline summary."""
        self._inner.final(msg)

    def done(self, elapsed: float) -> None:
        """Mark the pipeline as complete."""
        self._inner.done(elapsed)

    def cancelled(self, msg: str) -> None:
        """Mark the pipeline as cancelled."""
        self._inner.cancelled(msg)

    def progress(self, *args, **kwargs):
        """Update progress display."""
        return self._inner.progress(*args, **kwargs)


# ── Task execution ─────────────────────────────────────────


def _build_output_dir(request: TaskRequest) -> Path:
    """Determine the output directory for a task."""
    if request.output_dir:
        return Path(request.output_dir)
    # Default: ./output/<movie_name>_<task_id>
    from ..utils.sanitize import sanitize_filename
    safe_name = sanitize_filename(request.movie_name) or "movie"
    return Path("output") / safe_name


def _is_retryable_error(exc: Exception) -> bool:
    """Check if an exception is retryable (transient/network error)."""
    return bool(getattr(exc, "retryable", False))


def _extract_result(ctx: Context, output_dir: Path) -> TaskResult:
    """Build a ``TaskResult`` from a finished pipeline context."""
    return TaskResult(
        video_path=ctx.video_path,
        audio_path=ctx.audio_path,
        output_dir=ctx.output_dir,
        subtitle_path=ctx.subtitle_path,
        script_md_path=ctx.script_md_path,
        clips_dir=ctx.clips_dir,
        metadata=ctx.metadata,
    )


def _restore_context(
    context_dump: Dict[str, Any],
    services: Services,
) -> Context:
    """Rebuild a ``Context`` from a checkpoint dump.

    The dump excludes ``services`` (auto-injected ``SilentConsole`` by the
    model validator) and ``cost_tracker`` (contains a non-serializable
    lock); both are re-seeded here so the resumed pipeline has live
    infrastructure.
    """
    ctx = Context(**context_dump)
    ctx.services = services
    from ..utils.cost_tracker import CostTracker
    ctx.cost_tracker = CostTracker()
    return ctx


def _step_index_of(step_name: str) -> int:
    """
    Returns:
        The zero-based index of ``step_name`` in ``STEPS``.

        Used to seed progress counters when resuming so the percentage does
        not restart at 0% for a task that already finished several steps.
    """
    for i, step in enumerate(STEPS):
        if step.__name__ == step_name:
            return i
    return 0


# ── Dead-letter queue (v0.9.4) ─────────────────────────────


def _dlq_store() -> DeadLetterStore:
    """Resolve the dead-letter store at call time.

    Imported inside the function so tests can monkeypatch
    ``movie_narrator.cloud.dlq.get_default_store`` (which the API server
    resolves the same way) and both sides stay in sync.
    """
    from .dlq import get_default_store

    return get_default_store()


def _route_to_dead_letter(task: Task) -> None:
    """Persist a dead-letter record and mark the task ``DEAD``.

    Best-effort: if persistence fails the task stays ``FAILED`` rather
    than crashing the worker thread. The record captures the original
    request so it can be replayed later via ``replay_dead_letter``.
    """
    try:
        record = DeadLetterRecord(
            task_id=task.id,
            original_request=task.request.model_copy(deep=True),
            reason=task.last_error or "unknown error",
            failed_at=datetime.now(timezone.utc).isoformat(),
            attempts=task.retries + 1,
        )
        _dlq_store().save(record)
        task.status = TaskStatus.DEAD
    except Exception as e:  # noqa: BLE001 — DLQ is best-effort
        logger.debug(
            "Failed to write dead-letter record for %s: %s", task.id, e
        )


# ── Conditional distributed rendering (v0.9.4) ─────────────


def _step_after_render() -> Optional[str]:
    """Name of the step following ``render_video``, or None if last."""
    names = [step.__name__ for step in STEPS]
    idx = names.index(_RENDER_STEP)
    if idx + 1 < len(names):
        return names[idx + 1]
    return None


def _maybe_dispatch_render(
    task: Task,
    progress: TaskProgress,
) -> Optional[TaskResult]:
    """Soft hook: try to dispatch the render phase to a remote node.

    Evaluates the distributed-rendering conditions and, when they hold,
    attempts the remote leg. Any failure is logged at DEBUG and returns
    ``None`` so the caller falls back to the local rendering path — the
    distributed feature never turns a would-be successful task into a
    failed one.

    Args:
        task: The task whose render phase may be dispatched.
        progress: Live ``TaskProgress`` (used for duration estimation).

    Returns:
        A ``TaskResult`` when the remote leg produced artifacts, or
        ``None`` when distribution was not applicable or failed.
    """
    from .distributed import (
        DistributedRenderError,
        DistributedRenderPlanner,
        estimate_render_seconds,
        render_task_dispatcher,
    )

    planner = DistributedRenderPlanner()
    estimated = estimate_render_seconds(task.request, progress)
    if not planner.should_distribute(estimated):
        return None
    nodes = planner.available_nodes
    if not nodes:
        return None
    node = nodes[0]
    output_dir = _build_output_dir(task.request)
    try:
        return render_task_dispatcher(
            request=task.request,
            node=node,
            download_dir=str(output_dir),
        )
    except DistributedRenderError as e:
        logger.debug(
            "Distributed render to %s failed, falling back to local: %s",
            node, e,
        )
        return None


def _apply_distributed_result(
    ctx: Context,
    result: TaskResult,
    console: ProgressConsole,
    elapsed: float,
) -> None:
    """Apply a remotely rendered result onto the local pipeline context.

    Copies the downloaded artifact paths into *ctx* so the post-render
    steps (validation, clip export) run locally against the remote
    output, and reports the render step through the console so progress
    and the render-duration metric stay accurate. ``ctx.output_dir`` is
    left untouched — it already points at the local task output
    directory, which is where the artifacts were downloaded.
    """
    if result.video_path:
        ctx.video_path = result.video_path
    if result.audio_path:
        ctx.audio_path = result.audio_path
    if result.subtitle_path:
        ctx.subtitle_path = result.subtitle_path
    ctx.metadata.setdefault("distributed_render", True)

    render_index = next(
        i for i, step in enumerate(STEPS) if step.__name__ == _RENDER_STEP
    )
    console.set_step_index(render_index)
    console.step_ok(_RENDER_STEP, elapsed)


def _execute_task(
    task: Task,
    controller: CancelController,
    on_progress: Optional[Callable[..., Any]] = None,
    *,
    resume: Optional[ResumePlan] = None,
    attempt: int = 0,
    checkpoint_store: Optional[CheckpointStore] = None,
) -> Task:
    """Execute a single pipeline attempt (no retry).

    v0.9.2 additions:
    - ``resume`` carries the task's checkpoint state; when present the
      pipeline starts from ``resume.start_step`` with the restored
      ``Context`` instead of a fresh ``build_context``.
    - When ``resume.done`` is True every step already finished before the
      crash, so the result is reconstructed from the saved context and
      ``run_pipeline`` is skipped entirely.
    - ``checkpoint_store`` enables per-step checkpoint writing through
      the ``ProgressConsole`` hook.

    Updates ``task`` in-place and returns it.
    """
    request = task.request
    output_dir = _build_output_dir(request)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build progress tracker
    progress = TaskProgress(total_steps=_TOTAL_STEPS)
    task.progress = progress
    start_time = time.time()

    log_level = resolve_log_level(request.log_level)

    # Build console — use SilentConsole in CI, real console otherwise
    is_ci = bool(os.environ.get("CI"))
    if is_ci:
        inner_console: Console = SilentConsole()
    else:
        inner_console = build_console(
            output_dir,
            log_level=log_level,
            verbose=request.verbose,
        )

    services = Services(
        console=inner_console,
        logger=getattr(inner_console, "_log", None),
    )

    # v0.9.2: restored context (from a checkpoint) or a fresh build_context
    if resume is not None and resume.context_dump is not None:
        ctx = _restore_context(resume.context_dump, services)
        logger.info(
            "Task %s: resuming from checkpoint (completed=%s, next=%s)",
            task.id,
            resume.completed_step,
            resume.start_step if resume.start_step else "<done>",
        )
    else:
        ctx = build_context(**common_build_kwargs(
            movie=request.movie_name,
            style=request.style,
            duration=request.duration,
            voice=request.voice,
            video_format=request.video_format,
            output_dir=output_dir,
            keep_cache=request.keep_cache,
            video=request.video,
            library_dir=request.library_dir,
            research=request.research,
            bgm=request.bgm,
            no_bgm=request.no_bgm,
            no_clips=request.no_clips,
            strict=request.strict,
            workflow_steps=request.workflow_steps,
            params=request.params,
            config_path=request.config_path,
            subtitle_lang=request.subtitle_lang,
            subtitle_mode=request.subtitle_mode,
            services=services,
            narration_preset=request.narration_preset,
            lang=request.lang,
            log_level=log_level,
            verbose=request.verbose,
        ))

    # v0.9.2: checkpoint hook — snapshot the context after each completed
    # step. The closure reads the current ``ctx`` (steps mutate it in
    # place per the shared-mutable-Context design), so the checkpoint
    # always reflects the state right after the step ran.
    if checkpoint_store is not None:

        def _write_checkpoint(step_name: str) -> None:
            checkpoint_store.save(TaskCheckpoint(
                task_id=task.id,
                completed_step=step_name,
                context_dump=ctx.model_dump(mode="json", exclude={"services", "cost_tracker"}),
                attempt=attempt,
            ))
            progress.latest_checkpoint_step = step_name
            progress.checkpoint_updated_at = datetime.now(timezone.utc).isoformat()

    def _noop_checkpoint(step_name: str) -> None:
        del step_name  # checkpointing disabled

    progress_console = ProgressConsole(
        inner=services.console,
        progress=progress,
        start_time=start_time,
        on_step_complete=_write_checkpoint if checkpoint_store is not None else _noop_checkpoint,
        initial_step_index=(
            _step_index_of(resume.start_step)
            if resume is not None and resume.start_step
            else 0
        ),
    )
    services.console = progress_console

    # v0.9.4: conditional distributed rendering (soft hook). When the
    # planner decides the render phase should be dispatched, the remote
    # leg runs before the local pipeline; on success the pipeline resumes
    # after the render step, and on failure we fall back to the full
    # local run below (the default path is untouched). Skipped when a
    # checkpoint already finished every step (``resume.done``) — nothing
    # is left to distribute.
    distributed_start = time.monotonic()
    distributed_result = None
    if not (resume is not None and resume.done):
        distributed_result = _maybe_dispatch_render(task, progress)
    distributed_elapsed = time.monotonic() - distributed_start

    # Execute pipeline
    try:
        if resume is not None and resume.done:
            # All steps already completed before the crash; only result
            # extraction was pending.
            task.result = _extract_result(ctx, output_dir)
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc).isoformat()
        elif distributed_result is not None:
            _apply_distributed_result(
                ctx, distributed_result, progress_console, distributed_elapsed
            )
            ctx = run_pipeline(
                ctx, controller=controller, start_step=_step_after_render()
            )
            task.result = _extract_result(ctx, output_dir)
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc).isoformat()
        else:
            start_step = resume.start_step if resume is not None else None
            ctx = run_pipeline(ctx, controller=controller, start_step=start_step)
            task.result = _extract_result(ctx, output_dir)
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc).isoformat()

    except PipelineCancelled:
        task.status = TaskStatus.CANCELLED
        task.completed_at = datetime.now(timezone.utc).isoformat()
        task.result = TaskResult(
            output_dir=str(output_dir),
            error="Task cancelled by user",
            error_type="PipelineCancelled",
        )

    except Exception as exc:  # noqa: BLE001
        task.status = TaskStatus.FAILED
        task.completed_at = datetime.now(timezone.utc).isoformat()
        task.last_error = str(exc)
        task.result = TaskResult(
            output_dir=str(output_dir),
            error=str(exc),
            error_type=type(exc).__name__,
            traceback=tb_module.format_exc(),
        )

    finally:
        # Final progress update
        if progress:
            progress.elapsed_seconds = time.time() - start_time
            if on_progress:
                on_progress(task)

    return task


def run_task(
    task: Task,
    controller: CancelController,
    on_progress: Optional[Callable[..., Any]] = None,
    on_status_change: Optional[Callable[..., Any]] = None,
    checkpoint_store: Optional[CheckpointStore] = None,
) -> Task:
    """Execute a pipeline task with retry support.

    Args:
        task: The task to execute.
        controller: Cancellation controller.
        on_progress: Optional callback called on each progress update.
        on_status_change: Optional callback called when status changes.
        checkpoint_store: Optional store for task checkpoints (v0.9.2).
            When provided, the task resumes from its latest checkpoint at
            the start of every attempt — a crash or a retryable failure
            does not redo already-completed pipeline steps. The
            checkpoint is removed once the task reaches ``COMPLETED``.

    Returns:
        The updated task with result and final status.
    """
    max_retries = task.request.max_retries

    for attempt in range(max_retries + 1):
        controller.reset()

        # Update status
        if attempt > 0:
            task.status = TaskStatus.RETRYING
            task.retries = attempt
        else:
            task.status = TaskStatus.RUNNING

        task.started_at = datetime.now(timezone.utc).isoformat()
        if on_status_change:
            on_status_change(task)

        # v0.9.2: resolve the resume point from the task's checkpoint at
        # the start of each attempt. A fresh task has no checkpoint; a
        # retry (or a crash-restarted run) picks up the most recent one.
        resume = None
        if checkpoint_store is not None:
            try:
                resume = checkpoint_store.resolve_resume(task.id)
            except Exception:  # noqa: BLE001 — checkpointing must never block a run
                logger.debug(
                    "Failed to resolve checkpoint for task %s", task.id, exc_info=True
                )
            if resume is not None and not resume.done:
                logger.info(
                    "Task %s: resuming from checkpoint (start at '%s')",
                    task.id,
                    resume.start_step or "<first step>",
                )

        # Execute the task
        task = _execute_task(
            task,
            controller,
            on_progress=on_progress,
            resume=resume,
            attempt=attempt,
            checkpoint_store=checkpoint_store,
        )

        # Check result
        if task.status == TaskStatus.COMPLETED:
            if checkpoint_store is not None:
                try:
                    checkpoint_store.delete(task.id)
                except Exception:  # noqa: BLE001 — best-effort cleanup
                    logger.debug(
                        "Failed to delete checkpoint for task %s", task.id, exc_info=True
                    )
            if on_status_change:
                on_status_change(task)
            return task

        if task.status == TaskStatus.CANCELLED:
            if on_status_change:
                on_status_change(task)
            return task

        # Check if retryable
        error_type = task.result.error_type if task.result else ""

        # Only retry on retryable errors
        is_retryable = False
        if task.result and task.result.error:
            # Check if the original exception had retryable attribute
            # We stored the error type; check common retryable patterns
            retryable_types = {
                "ConnectionError", "TimeoutError", "ConnectError",
                "ReadTimeout", "WriteTimeout", "PoolTimeout",
                "HTTPStatusError", "RateLimitError",
            }
            is_retryable = error_type in retryable_types

        if attempt < max_retries and is_retryable:
            # Wait with exponential backoff
            delay = task.request.retry_delay * (2 ** attempt)
            logger.info(
                "Task %s: retrying in %.1fs (attempt %d/%d)",
                task.id, delay, attempt + 1, max_retries,
            )
            # Interruptible sleep
            if controller._event.wait(timeout=delay):
                # Cancelled during sleep
                task.status = TaskStatus.CANCELLED
                task.completed_at = datetime.now(timezone.utc).isoformat()
                if on_status_change:
                    on_status_change(task)
                return task
            continue

        # Non-retryable or out of retries
        # v0.9.4: route to the dead-letter queue only when the retry
        # budget was actually exhausted — a retryable error persisted
        # past ``max_retries``. Non-retryable failures keep the
        # pre-v0.9.4 ``FAILED`` behaviour, so existing consumers that
        # depend on FAILED for immediate errors are unaffected.
        if (
            task.status == TaskStatus.FAILED
            and task.request.enable_dlq
            and is_retryable
            and attempt >= max_retries
        ):
            _route_to_dead_letter(task)
        if on_status_change:
            on_status_change(task)
        return task

    return task
