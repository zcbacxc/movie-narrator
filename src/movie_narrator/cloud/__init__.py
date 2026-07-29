"""Cloud package — async job system and cloud infrastructure (v0.6.x).

This package provides the task queue infrastructure that enables
cloud deployment of the movie-narrator pipeline. The core components
are:

- ``TaskQueue`` / ``LocalTaskQueue`` — async job submission and tracking
- ``RemoteTaskQueue`` — client for remote API servers (v0.6.1)
- ``TaskAPIServer`` — REST API server for remote task management (v0.6.1)
- ``TaskStorage`` — JSON-based task persistence
- ``CancelController`` — cooperative cancellation (implements ``RunController``)
- ``ProgressConsole`` — progress-tracking console wrapper
- ``run_task`` — pipeline execution with retry support
- ``run_daemon`` / ``WorkerDaemon`` — server-side worker process (v0.6.1)

Typical usage (local)::

    from movie_narrator.cloud import LocalTaskQueue, TaskRequest

    queue = LocalTaskQueue()
    task_id = queue.submit(TaskRequest(movie_name="飞驰人生", style="热血搞笑"))
    result = queue.wait(task_id, timeout=600)

Typical usage (remote)::

    from movie_narrator.cloud import RemoteTaskQueue, TaskRequest

    queue = RemoteTaskQueue("http://worker-host:8765")
    task_id = queue.submit(TaskRequest(movie_name="飞驰人生"))
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
from .api import TaskAPIServer
from .remote_queue import RemoteQueueError, RemoteTaskQueue
from .daemon import WorkerDaemon, run_daemon
from .remote_provider import (
    download_all_artifacts,
    download_artifact,
    list_artifacts,
    register_remote_llm,
    register_remote_tts,
)

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
    # API server (v0.6.1)
    "TaskAPIServer",
    # Remote queue (v0.6.1)
    "RemoteTaskQueue",
    "RemoteQueueError",
    # Daemon (v0.6.1)
    "WorkerDaemon",
    "run_daemon",
    # Artifact management (v0.6.1)
    "download_artifact",
    "download_all_artifacts",
    "list_artifacts",
    # Remote providers (v0.6.1)
    "register_remote_llm",
    "register_remote_tts",
]
