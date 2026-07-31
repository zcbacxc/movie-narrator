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
from typing import Any, Callable, Optional

from ..models import Services
from ..pipeline.errors import PipelineCancelled
from ..pipeline.runner import build_context, common_build_kwargs, run_pipeline, STEPS
from ..utils.console import (
    BaseConsole,
    Console,
    SilentConsole,
    build_console,
)
from ..utils.log import resolve_log_level
from .models import Task, TaskProgress, TaskRequest, TaskResult, TaskStatus

logger = logging.getLogger(__name__)

# Total pipeline steps for progress calculation
_TOTAL_STEPS = len(STEPS)


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
    """

    def __init__(
        self,
        inner: Console,
        progress: TaskProgress,
        start_time: float,
    ) -> None:
        self._inner = inner
        self._progress = progress
        self._start_time = start_time
        self._step_start: float = 0.0
        self._step_index = 0

    def step(self, name: str) -> None:
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
        self._progress.mark_completed(name)
        self._step_index += 1
        self._progress.update_step(
            step_name=name,
            index=self._step_index,
            total=_TOTAL_STEPS,
            elapsed=elapsed,
        )
        self._progress.elapsed_seconds = time.time() - self._start_time
        self._inner.step_ok(name, elapsed)

    def step_skip(self, name: str, reason: str) -> None:
        self._progress.mark_skipped(name)
        self._step_index += 1
        self._inner.step_skip(name, reason)

    def step_warn(self, name: str, reason: str) -> None:
        self._inner.step_warn(name, reason)

    def step_err(self, name: str, exc: Exception, elapsed: float) -> None:
        self._progress.mark_failed(name)
        self._inner.step_err(name, exc, elapsed)

    def warn(self, msg: str) -> None:
        self._inner.warn(msg)

    def debug(self, msg: str) -> None:
        self._inner.debug(msg)

    def inline_warn(self, msg: str) -> None:
        self._inner.inline_warn(msg)

    def final(self, msg: str) -> None:
        self._inner.final(msg)

    def done(self, elapsed: float) -> None:
        self._inner.done(elapsed)

    def cancelled(self, msg: str) -> None:
        self._inner.cancelled(msg)

    def progress(self, *args, **kwargs):
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


def _execute_task(
    task: Task,
    controller: CancelController,
    on_progress: Optional[Callable[..., Any]] = None,
) -> Task:
    """Execute a single pipeline attempt (no retry).

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

    # Wrap with progress-tracking console
    progress_console = ProgressConsole(
        inner=inner_console,
        progress=progress,
        start_time=start_time,
    )

    services = Services(
        console=progress_console,
        logger=getattr(inner_console, "_log", None),
    )

    # Build pipeline context
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

    # Execute pipeline
    try:
        ctx = run_pipeline(ctx, controller=controller)

        # Extract results
        result = TaskResult(
            video_path=ctx.video_path,
            audio_path=ctx.audio_path,
            output_dir=ctx.output_dir,
            subtitle_path=ctx.subtitle_path,
            script_md_path=ctx.script_md_path,
            clips_dir=ctx.clips_dir,
            metadata=ctx.metadata,
        )
        task.result = result
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
) -> Task:
    """Execute a pipeline task with retry support.

    Args:
        task: The task to execute.
        controller: Cancellation controller.
        on_progress: Optional callback called on each progress update.
        on_status_change: Optional callback called when status changes.

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

        # Execute the task
        task = _execute_task(task, controller, on_progress=on_progress)

        # Check result
        if task.status == TaskStatus.COMPLETED:
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
        if on_status_change:
            on_status_change(task)
        return task

    return task
