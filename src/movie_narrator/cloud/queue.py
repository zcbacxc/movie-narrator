# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Task queue — async job submission and tracking (v0.6.0).

Provides the ``TaskQueue`` protocol and a ``LocalTaskQueue``
implementation using ``ThreadPoolExecutor`` for in-process async
execution.

Future cloud backends (Celery, RQ, SQS, etc.) can implement the
same protocol by providing a duck-typed replacement.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Protocol, runtime_checkable

from ..utils.logging_config import correlation_scope, get_correlation_id
from .metrics import (
    observe_task_duration,
    record_error,
    record_task_submitted,
    record_task_terminal,
    set_active_tasks,
    set_queue_depth,
)
from .models import Task, TaskProgress, TaskRequest, TaskResult, TaskStatus
from .storage import TaskStorage
from .worker import CancelController, run_task

logger = logging.getLogger(__name__)

# Default polling interval for ``wait()``
_POLL_INTERVAL: float = 0.5


# ── Protocol ───────────────────────────────────────────────


@runtime_checkable
class TaskQueue(Protocol):
    """Abstract task queue for async pipeline execution.

    Implementations may be local (in-process), remote (Redis/RQ),
    or cloud (SQS + Lambda). The protocol covers the essential
    operations: submit, query, cancel, and wait.
    """

    def submit(self, request: TaskRequest) -> str:
        """Submit a new task. Returns the task ID."""
        ...

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get full task details by ID."""
        ...

    def get_status(self, task_id: str) -> Optional[TaskStatus]:
        """Get task status. Returns None if task not found."""
        ...

    def get_progress(self, task_id: str) -> Optional[TaskProgress]:
        """Get task progress. Returns None if task not found or not started."""
        ...

    def get_result(self, task_id: str) -> Optional[TaskResult]:
        """Get task result. Returns None if task not found or not completed."""
        ...

    def cancel(self, task_id: str) -> bool:
        """Request cancellation of a running task.

        Returns True if cancellation was requested (task was active),
        False if the task was not found or already terminal.
        """
        ...

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 50,
    ) -> List[Task]:
        """List tasks, optionally filtered by status."""
        ...

    def wait(
        self,
        task_id: str,
        timeout: Optional[float] = None,
        poll_interval: float = _POLL_INTERVAL,
    ) -> Optional[TaskResult]:
        """Block until task reaches a terminal state.

        Returns the ``TaskResult`` if the task completed (success or
        failure), or None if the task was not found, was cancelled,
        or timed out.
        """
        ...

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the queue. Optionally wait for running tasks."""
        ...


# ── LocalTaskQueue ─────────────────────────────────────────


class LocalTaskQueue:
    """In-process task queue using ``ThreadPoolExecutor``.

    Tasks run in background threads. State is persisted to disk via
    ``TaskStorage`` so tasks survive process restarts (though running
    tasks are lost on crash — they remain in ``RUNNING`` state and
    can be manually cleaned up).

    Args:
        storage_dir: Directory for task persistence.
        max_workers: Maximum concurrent task executions.
        auto_start: If True, the executor starts immediately.
    """

    def __init__(
        self,
        *,
        storage_dir: Optional[Path] = None,
        max_workers: int = 2,
        auto_start: bool = True,
    ) -> None:
        self._storage = TaskStorage(storage_dir)
        self._max_workers = max_workers
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: Dict[str, Future] = {}
        self._controllers: Dict[str, CancelController] = {}
        self._lock = threading.Lock()
        self._started = False
        # O(1) active task counter (maintained by submit/cancel/completion)
        self._active_count: int = 0
        # Per-task completion events for efficient wait() (no busy-polling)
        self._completion_events: Dict[str, threading.Event] = {}

        if auto_start:
            self.start()

    # ── Lifecycle ────────────────────────────────────────────

    def start(self) -> None:
        """Start the executor if not already started.

        Also initializes the active task counter by scanning storage
        for pre-existing active tasks (handles process restart with
        leftover RUNNING/PENDING/RETRYING tasks).
        """
        if self._started:
            return
        self._executor = ThreadPoolExecutor(
            max_workers=self._max_workers,
            thread_name_prefix="mn-worker",
        )
        self._started = True
        # Initialize active_count from storage (handles process restart
        # with leftover RUNNING/PENDING/RETRYING tasks)
        self._init_active_count()

    def shutdown(self, wait: bool = True) -> None:
        """Shut down the executor.

        The executor is shut down *after* releasing ``self._lock`` so that
        worker threads are not blocked from completing their ``finally``
        blocks (which acquire the lock to decrement ``_active_count`` and
        signal completion events). Holding the lock across
        ``executor.shutdown(wait=True)`` deadlocks: the shutdown thread waits
        for workers that are waiting for the lock. See issue #127.
        """
        executor = None
        with self._lock:
            executor = self._executor
            self._executor = None
            self._started = False
        if executor is not None:
            executor.shutdown(wait=wait, cancel_futures=not wait)

    # ── TaskQueue protocol ───────────────────────────────────

    def submit(self, request: TaskRequest) -> str:
        """Submit a new task for async execution.

        Returns the task ID immediately. The task will be queued and
        executed when a worker thread becomes available.
        """
        if not self._started or not self._executor:
            raise RuntimeError("TaskQueue is not started. Call start() first.")

        # v0.8.1: inherit the caller's correlation ID (set per-request by
        # the API server) so the worker's logs join the access log.
        task = Task(request=request, correlation_id=get_correlation_id())
        self._storage.save(task)

        # Create cancellation controller and completion event
        controller = CancelController()
        event = threading.Event()
        with self._lock:
            self._controllers[task.id] = controller
            self._completion_events[task.id] = event
            self._active_count += 1

        record_task_submitted()
        self._publish_queue_metrics()

        # Submit to executor
        future = self._executor.submit(
            self._run_task_threadsafe,
            task.id,
            controller,
        )
        with self._lock:
            self._futures[task.id] = future

        return task.id

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get full task details."""
        return self._storage.load(task_id)

    def get_status(self, task_id: str) -> Optional[TaskStatus]:
        """Get task status."""
        task = self._storage.load(task_id)
        return task.status if task else None

    def get_progress(self, task_id: str) -> Optional[TaskProgress]:
        """Get task progress."""
        task = self._storage.load(task_id)
        return task.progress if task else None

    def get_result(self, task_id: str) -> Optional[TaskResult]:
        """Get task result (only available for terminal tasks)."""
        task = self._storage.load(task_id)
        if task and task.is_terminal:
            return task.result
        return None

    def cancel(self, task_id: str) -> bool:
        """Request cancellation of a running task.

        Returns True if the task was active and cancellation was
        requested. Returns False if the task was not found, already
        terminal, or not yet started.
        """
        task = self._storage.load(task_id)
        if not task or task.is_terminal:
            return False

        with self._lock:
            controller = self._controllers.get(task_id)
        if controller:
            controller.cancel()
            return True

        # Task is pending but not yet running — mark as cancelled
        task.status = TaskStatus.CANCELLED
        from datetime import datetime, timezone
        task.completed_at = datetime.now(timezone.utc).isoformat()
        self._storage.save(task)
        # Task transitioned from active to terminal — update counter
        # and wake up any waiters blocked on the completion event
        with self._lock:
            self._active_count = max(0, self._active_count - 1)
            event = self._completion_events.pop(task_id, None)
        record_task_terminal(TaskStatus.CANCELLED.value)
        self._publish_queue_metrics()
        if event is not None:
            event.set()
        return True

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 50,
    ) -> List[Task]:
        """List tasks, optionally filtered by status."""
        return self._storage.list_tasks(status=status, limit=limit)

    def wait(
        self,
        task_id: str,
        timeout: Optional[float] = None,
        poll_interval: float = _POLL_INTERVAL,
    ) -> Optional[TaskResult]:
        """Block until task reaches a terminal state.

        Returns the ``TaskResult`` if the task completed (success or
        failure). Returns None if:
        - The task was not found
        - The task was cancelled
        - The timeout was reached

        Uses a ``threading.Event`` for efficient non-busy waiting when
        the task was submitted through this queue instance. Falls back
        to storage polling for cross-process scenarios (where no Event
        is available). The ``poll_interval`` parameter is retained for
        protocol compatibility and is only used in the polling fallback
        path.
        """
        start = time.time()

        # Load task metadata; a missing task cannot be waited on.
        task = self._storage.load(task_id)
        if not task:
            return None

        # NOTE: We deliberately do NOT short-circuit on ``task.is_terminal``
        # here. The in-memory accounting (``active_count`` and
        # ``_completion_events``) is only finalized inside the worker's
        # ``finally`` block, which runs *after* the terminal state has been
        # persisted to storage. Returning early on a terminal task would let
        # a caller observe ``active_count`` before it has been decremented,
        # producing intermittent ``active_count != 0`` races (see the
        # active_count concurrency tests). Instead we always block on the
        # completion Event below — it is set *after* the decrement — or fall
        # back to storage polling only once the Event is already gone
        # (which means the worker's finally has already run).

        # Try the in-process Event path (efficient, no busy-waiting)
        with self._lock:
            event = self._completion_events.get(task_id)

        if event is not None:
            # Event path: block efficiently until signaled or timeout
            if timeout is None:
                event.wait(timeout=None)
            else:
                remaining = max(0.0, timeout - (time.time() - start))
                event.wait(timeout=remaining)
        else:
            # No Event available (cross-process or externally-created task)
            # — fall back to storage polling
            while True:
                task = self._storage.load(task_id)
                if not task:
                    return None
                if task.is_terminal:
                    return task.result if task.result else None
                if timeout is not None and (time.time() - start) > timeout:
                    return None
                time.sleep(poll_interval)

        # Reload final state from storage after Event is signaled
        task = self._storage.load(task_id)
        if not task:
            return None
        if task.is_terminal:
            return task.result if task.result else None
        return None  # timed out without reaching terminal

    # ── Cleanup ──────────────────────────────────────────────

    def cleanup_terminal(self) -> int:
        """Remove all tasks in terminal states. Returns count removed."""
        return self._storage.clear_terminal()

    def cleanup_all(self) -> int:
        """Remove all tasks. Returns count removed."""
        return self._storage.clear_all()

    # ── Internal ─────────────────────────────────────────────

    def _run_task_threadsafe(
        self,
        task_id: str,
        controller: CancelController,
    ) -> None:
        """Worker thread entry point.

        Loads the task from storage, runs it, and saves the result.
        All exceptions are caught and logged — the worker thread must
        never raise.
        """
        try:
            task = self._storage.load(task_id)
            if not task:
                logger.error("Task %s not found in storage", task_id)
                return

            # v0.8.1: re-bind the submitter's correlation ID inside the
            # worker thread. contextvars are copied per-thread at thread
            # creation, so the executor thread would otherwise start with
            # an empty context and its logs would not join the request.
            with correlation_scope(task.correlation_id):
                started = time.monotonic()

                def on_status_change(updated: Task) -> None:
                    self._storage.save(updated)
                    self._publish_queue_metrics()

                def on_progress(updated: Task) -> None:
                    self._storage.save(updated)

                task = run_task(
                    task,
                    controller=controller,
                    on_progress=on_progress,
                    on_status_change=on_status_change,
                )
                self._storage.save(task)
                self._record_task_outcome(task, time.monotonic() - started)

        except Exception as e:  # noqa: BLE001 — worker top-level must never crash the executor
            logger.exception("Worker thread error for task %s: %s", task_id, e)
            # Try to mark the task as failed
            try:
                task = self._storage.load(task_id)
                if task and not task.is_terminal:
                    task.status = TaskStatus.FAILED
                    task.last_error = f"Worker thread error: {e}"
                    from datetime import datetime, timezone
                    task.completed_at = datetime.now(timezone.utc).isoformat()
                    self._storage.save(task)
            except (OSError, ValueError):
                logger.debug("Failed to mark task as failed after worker error", exc_info=True)
            record_error("worker_thread")
            record_task_terminal(TaskStatus.FAILED.value)

        finally:
            with self._lock:
                self._futures.pop(task_id, None)
                self._controllers.pop(task_id, None)
                self._active_count = max(0, self._active_count - 1)
                event = self._completion_events.pop(task_id, None)
            self._publish_queue_metrics()
            if event is not None:
                event.set()

    # ── Observability (v0.8.1) ───────────────────────────────

    def _publish_queue_metrics(self) -> None:
        """Refresh the queue depth / active task gauges.

        Reads from ``TaskStorage``, whose counts are served from an
        in-memory dict, so this is a cheap scan rather than disk I/O.
        It runs on task lifecycle events (submit, status change,
        completion) instead of on scrape, which keeps ``/metrics`` free
        of side effects.
        """
        try:
            set_queue_depth(self._storage.count(TaskStatus.PENDING))
            set_active_tasks(self._storage.count(TaskStatus.RUNNING))
        except Exception:  # noqa: BLE001 — telemetry must never break the queue
            logger.debug("Failed to publish queue gauges", exc_info=True)

    def _record_task_outcome(self, task: Task, duration: float) -> None:
        """Count a terminal transition and record its duration.

        ``duration`` is the worker-side execution span (all retry
        attempts included) rather than time since submission, so queue
        waiting time does not inflate the histogram.
        """
        try:
            if not task.is_terminal:
                return
            record_task_terminal(task.status.value)
            observe_task_duration(duration)
            if task.status == TaskStatus.FAILED:
                error_type = (
                    task.result.error_type
                    if task.result and task.result.error_type
                    else "unknown"
                )
                record_error(error_type)
        except Exception:  # noqa: BLE001 — telemetry must never break the queue
            logger.debug("Failed to record task outcome metrics", exc_info=True)

    # ── Properties ───────────────────────────────────────────

    @property
    def storage(self) -> TaskStorage:
        """The underlying task storage."""
        return self._storage

    @property
    def is_started(self) -> bool:
        """Whether the executor is running."""
        return self._started

    @property
    def active_count(self) -> int:
        """Number of currently running/pending tasks.

        When the queue is started, returns an O(1) counter maintained
        by ``submit()`` / ``cancel()`` / task completion. When not
        started, falls back to scanning storage for compatibility
        with direct storage manipulation (e.g., in tests or
        cross-process inspection).
        """
        if not self._started:
            tasks = self._storage.list_tasks()
            return sum(1 for t in tasks if t.is_active)
        with self._lock:
            return self._active_count

    # ── Internal helpers ─────────────────────────────────────

    def _init_active_count(self) -> None:
        """Initialize ``_active_count`` by scanning storage.

        This handles the process-restart scenario where leftover
        RUNNING/PENDING/RETRYING tasks exist in storage from a
        previous process that crashed.
        """
        try:
            active = (
                self._storage.count(TaskStatus.PENDING)
                + self._storage.count(TaskStatus.RUNNING)
                + self._storage.count(TaskStatus.RETRYING)
            )
            with self._lock:
                self._active_count = active
        except Exception:  # noqa: BLE001 — best-effort counter init, must not crash startup
            logger.debug(
                "Failed to initialize active_count from storage",
                exc_info=True,
            )
