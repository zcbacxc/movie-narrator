# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

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
- ``metrics`` — Prometheus counters/gauges/histograms and the text
  exposition renderer behind ``GET /metrics`` (v0.8.1)
- ``build_readiness_payload`` / ``build_health_payload`` — probe logic
  behind ``GET /ready`` and ``GET /health?deep=1`` (v0.8.2)
- ``build_openapi_spec`` — OpenAPI 3.1 document for the REST API (v0.8.2)
- ``Batch`` / ``BatchRequest`` / ``BatchProgress`` — batch task submission
  and aggregate tracking (v0.9.3)
- ``JobScheduler`` / ``ScheduleRequest`` / ``ScheduleError`` — cron-driven
  scheduled job submission (v0.9.3)
- ``DeadLetterStore`` / ``DeadLetterRecord`` / ``replay_dead_letter`` —
  dead-letter queue for failed tasks (v0.9.4)
- ``NodeRegistry`` / ``DistributedRenderPlanner`` /
  ``render_task_dispatcher`` — conditional distributed rendering (v0.9.4)

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

from .metrics import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    MetricsRegistry,
    get_registry,
    render_prometheus_text,
)
from .models import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    Batch,
    BatchProgress,
    BatchRequest,
    BatchStatus,
    Task,
    TaskPriority,
    TaskProgress,
    TaskRequest,
    TaskResult,
    TaskStatus,
)
from .storage import JsonModelStore, TaskStorage
from .artifact_store import (
    ArtifactInfo,
    ArtifactNotFoundError,
    ArtifactStoreError,
    LocalArtifactStore,
    S3ArtifactStore,
    StorageBackend,
    UnsafeKeyError,
    get_artifact_store,
    get_task_artifact_store,
)
from .lifecycle import (
    ArtifactLifecyclePolicy,
    ArtifactSweeper,
    CleanupReport,
    cleanup_artifacts,
)
from .checkpoint import CheckpointStore, ResumePlan, TaskCheckpoint
from .queue import LocalTaskQueue, QueueShutdownError, TaskQueue
from .worker import CancelController, ProgressConsole, run_task
from .health import build_health_payload, build_readiness_payload
from .openapi import build_openapi_spec
from .api import TaskAPIServer
from .remote_queue import RemoteQueueError, RemoteTaskQueue
from .scheduler import JobScheduler, ScheduleError, ScheduleRequest, ScheduleRun
from .daemon import WorkerDaemon, run_daemon
from .dlq import DeadLetterRecord, DeadLetterStore, replay_dead_letter  # v0.9.4
from .distributed import (  # v0.9.4
    DistributedRenderError,
    DistributedRenderPlanner,
    NodeRegistry,
    estimate_render_seconds,
    render_task_dispatcher,
)
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
    # Artifact store (v0.8.3)
    "ArtifactInfo",
    "ArtifactNotFoundError",
    "ArtifactStoreError",
    "LocalArtifactStore",
    "S3ArtifactStore",
    "StorageBackend",
    "UnsafeKeyError",
    "get_artifact_store",
    "get_task_artifact_store",
    # Artifact lifecycle (v0.8.3)
    "ArtifactLifecyclePolicy",
    "ArtifactSweeper",
    "CleanupReport",
    "cleanup_artifacts",
    # Queue
    "TaskQueue",
    "LocalTaskQueue",
    "QueueShutdownError",
    # Task lifecycle / checkpoints (v0.9.2)
    "TaskCheckpoint",
    "CheckpointStore",
    "ResumePlan",
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
    # Metrics (v0.8.1)
    "CONTENT_TYPE_LATEST",
    "Counter",
    "Gauge",
    "Histogram",
    "MetricsRegistry",
    "get_registry",
    "render_prometheus_text",
    # Health / readiness probes (v0.8.2)
    "build_health_payload",
    "build_readiness_payload",
    # OpenAPI spec (v0.8.2)
    "build_openapi_spec",
    # Batch & Schedule (v0.9.3)
    "Batch",
    "BatchRequest",
    "BatchProgress",
    "BatchStatus",
    "JsonModelStore",
    "JobScheduler",
    "ScheduleError",
    "ScheduleRequest",
    "ScheduleRun",
    # Dead-letter queue (v0.9.4)
    "DeadLetterRecord",
    "DeadLetterStore",
    "replay_dead_letter",
    # Conditional distributed rendering (v0.9.4)
    "NodeRegistry",
    "DistributedRenderPlanner",
    "DistributedRenderError",
    "render_task_dispatcher",
    "estimate_render_seconds",
]
