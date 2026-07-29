"""Cloud package — async job system and cloud infrastructure (v0.6.0).

This package provides the task queue infrastructure that enables
cloud deployment of the movie-narrator pipeline. The core components
are:

- ``TaskQueue`` / ``LocalTaskQueue`` — async job submission and tracking
- ``TaskStorage`` — JSON-based task persistence
- ``CancelController`` — cooperative cancellation (implements ``RunController``)
- ``ProgressConsole`` — progress-tracking console wrapper
- ``run_task`` — pipeline execution with retry support

Typical usage::

    from movie_narrator.cloud import LocalTaskQueue, TaskRequest

    queue = LocalTaskQueue()
    task_id = queue.submit(TaskRequest(movie_name="飞驰人生", style="热血搞笑"))
    result = queue.wait(task_id, timeout=600)
"""

from __future__ import annotations

from .models import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    Task,
    TaskPriority,
    TaskProgress,
    TaskRequest,
    TaskResult,
    TaskStatus,
)
from .storage import TaskStorage
from .queue import LocalTaskQueue, TaskQueue
from .worker import CancelController, ProgressConsole, run_task

__all__ = [
    # Models
    "Task",
    "TaskStatus",
    "TaskPriority",
    "TaskRequest",
    "TaskProgress",
    "TaskResult",
    "ACTIVE_STATES",
    "TERMINAL_STATES",
    # Storage
    "TaskStorage",
    # Queue
    "TaskQueue",
    "LocalTaskQueue",
    # Worker
    "CancelController",
    "ProgressConsole",
    "run_task",
]
