# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""REST API server for remote task management (v0.6.1).

Provides a lightweight HTTP API built on Python stdlib ``http.server``
so that no additional dependencies are required. The server wraps a
``LocalTaskQueue`` and exposes REST endpoints for task submission,
status polling, cancellation, and result retrieval.

    Endpoints::

        POST   /tasks                       — submit a new task
        GET    /tasks                       — list tasks (optional ?status= filter)
        GET    /tasks/{id}                  — get task details
        DELETE /tasks/{id}                  — cancel a task
        GET    /tasks/{id}/result           — get task result (terminal only)
        GET    /tasks/{id}/artifacts        — list output files
        GET    /tasks/{id}/download/{file}  — download an output file
        GET    /health                      — health check (?deep=1 for the
                                              full report, see cloud.health)
        GET    /ready                       — readiness probe (v0.8.2)
        GET    /info                        — server info (version, worker count)
        GET    /metrics                     — Prometheus metrics (v0.8.1)
        GET    /openapi.json                — OpenAPI 3.1 spec (v0.8.2)
        POST   /tasks/batch                 — submit a batch of tasks (v0.9.3)
        GET    /batches                     — list batches (v0.9.3)
        GET    /batches/{id}                — get a batch with aggregate progress
        DELETE /batches/{id}                — cancel every task in a batch
        POST   /schedules                   — create a cron scheduled job (v0.9.3)
        GET    /schedules                   — list scheduled jobs
        DELETE /schedules/{id}              — delete a scheduled job
        GET    /schedules/{id}/runs         — recent trigger records
        GET    /api/v1/webhooks/deliveries  — webhook delivery records (v1.4.0)
        POST   /api/v1/webhooks/redeliver/{event_id} — re-post a webhook (v1.4.0)

Typical usage::

    from movie_narrator.cloud import TaskAPIServer

    server = TaskAPIServer(host="127.0.0.1", port=8765)
    server.start(blocking=True)

This package re-exports the public surface of the former monolithic
``cloud/api.py``. Route handlers are split by family under
:mod:`~movie_narrator.cloud.api.routes`, shared plumbing lives in
:mod:`~movie_narrator.cloud.api._base` and
:mod:`~movie_narrator.cloud.api.handlers`, and the server lives in
:mod:`~movie_narrator.cloud.api.server`.
"""

from ._base import (
    TENANT_HEADER,
    _api_principal,
    _artifact_size_limit,
    _concurrency_limit,
    _estimate_artifact_bytes,
    _is_loopback_host,
    _metrics_public,
    _route_registry,
    _route_template,
    PayloadTooLargeError,
)
from .handlers import _APIHandler
from .server import TaskAPIServer

__all__ = [
    "PayloadTooLargeError",
    "TENANT_HEADER",
    "TaskAPIServer",
    "_APIHandler",
    "_api_principal",
    "_artifact_size_limit",
    "_concurrency_limit",
    "_estimate_artifact_bytes",
    "_is_loopback_host",
    "_metrics_public",
    "_route_registry",
    "_route_template",
]
