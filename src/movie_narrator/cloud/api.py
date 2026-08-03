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

Typical usage::

    from movie_narrator.cloud import TaskAPIServer

    server = TaskAPIServer(host="127.0.0.1", port=8765)
    server.start(blocking=True)
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, Optional

from .. import __version__
from ..utils.logging_config import (
    CORRELATION_HEADER,
    REQUEST_ID_HEADER,
    correlation_scope,
    get_correlation_id,
)
from .artifact_store import (  # v0.8.3 — artifact storage abstraction
    ArtifactNotFoundError,
    ArtifactStoreError,
    StorageBackend,
    UnsafeKeyError,
    artifact_location,
    get_artifact_store,
    get_task_artifact_store,
)
from .dlq import DeadLetterStore, replay_dead_letter  # v0.9.4 — dead letters
from .health import build_health_payload, build_readiness_payload, parse_deep_flag
from .lifecycle import (  # v0.8.3 — artifact lifecycle / TTL cleanup
    ArtifactLifecyclePolicy,
    ArtifactSweeper,
    sweep_interval_from_env,
)
from .metrics import (
    CONTENT_TYPE_LATEST,
    record_error,
    record_http_request,
    render_prometheus_text,
)
from .models import BatchRequest, Task, TaskRequest, TaskStatus
from .openapi import build_openapi_spec
from .queue import LocalTaskQueue
from .scheduler import JobScheduler, ScheduleError

logger = logging.getLogger(__name__)

# ── Route patterns ─────────────────────────────────────────

_TASK_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)$")
_TASK_RESULT_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)/result$")
_TASK_ARTIFACTS_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)/artifacts$")
_TASK_DOWNLOAD_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)/download/(.+)$")
# v0.9.4: dead-letter queue routes
_DEADLETTER_PATTERN = re.compile(r"^/deadletters/([a-f0-9]+)$")
_DEADLETTER_REPLAY_PATTERN = re.compile(r"^/deadletters/([a-f0-9]+)/replay$")

# v0.9.3: batch aggregates and scheduled jobs. Note ``/tasks/batch`` is a
# static path — the task-ID pattern above cannot match it because "batch"
# contains letters outside ``[a-f0-9]``.
_BATCH_PATTERN = re.compile(r"^/batches/([a-f0-9]+)$")
_SCHEDULE_PATTERN = re.compile(r"^/schedules/([a-f0-9]+)$")
_SCHEDULE_RUNS_PATTERN = re.compile(r"^/schedules/([a-f0-9]+)/runs$")

# ── Observability (v0.8.1) ─────────────────────────────────

_METRICS_PATH = "/metrics"

#: Environment variable opting ``/metrics`` out of API-key auth.
_ENV_METRICS_PUBLIC = "MN_METRICS_PUBLIC"

#: Paths that are already templates (no variable segment).
_STATIC_PATHS = frozenset(
    {
        "/health", "/info", "/tasks", "/ready", "/openapi.json",
        _METRICS_PATH, "/tasks/batch", "/batches", "/schedules",
        "/deadletters",
    }
)

#: Concrete path -> route template. Labelling the HTTP metric with the
#: raw path would give every task ID its own time series, so each match
#: is folded back into the template that produced it. Order matters:
#: the most specific patterns come first.
_ROUTE_TEMPLATES = (
    (_TASK_RESULT_PATTERN, "/tasks/{id}/result"),
    (_TASK_ARTIFACTS_PATTERN, "/tasks/{id}/artifacts"),
    (_TASK_DOWNLOAD_PATTERN, "/tasks/{id}/download/{filename}"),
    (_SCHEDULE_RUNS_PATTERN, "/schedules/{id}/runs"),
    (_SCHEDULE_PATTERN, "/schedules/{id}"),
    (_BATCH_PATTERN, "/batches/{id}"),
    (_DEADLETTER_REPLAY_PATTERN, "/deadletters/{id}/replay"),
    (_DEADLETTER_PATTERN, "/deadletters/{id}"),
    (_TASK_PATTERN, "/tasks/{id}"),
)


def _route_template(path: str) -> str:
    """Map a concrete request path onto a bounded route template.

    Unrecognised paths collapse to ``/other`` so that a scanner probing
    random URLs cannot grow the metric's cardinality without bound.
    """
    if path in _STATIC_PATHS:
        return path
    for pattern, template in _ROUTE_TEMPLATES:
        if pattern.match(path):
            return template
    return "/other"


def _metrics_public() -> bool:
    """Whether ``/metrics`` may be scraped without an API key.

    In-cluster Prometheus scrapers usually cannot present a secret, so
    ``MN_METRICS_PUBLIC=1`` opts the endpoint out of authentication.
    It stays authenticated by default: the payload leaks task volumes
    and error rates.
    """
    return os.environ.get(_ENV_METRICS_PUBLIC, "").strip().lower() in {
        "1", "true", "yes", "on",
    }


class _APIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the task API."""

    # Suppress default logging
    def log_message(self, fmt: str, *args: Any) -> None:
        logger.debug("API %s - %s", self.address_string(), fmt % args)

    # ── Helpers ─────────────────────────────────────────────

    def _read_body(self) -> Dict[str, Any]:
        """Read and parse JSON body from the request."""
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Invalid JSON body: {e}")

    def _send_json(
        self,
        data: Any,
        status: int = HTTPStatus.OK,
    ) -> None:
        """Send a JSON response."""
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(
        self,
        status: int,
        message: str,
    ) -> None:
        """Send an error response."""
        self._send_json({"error": message}, status=status)

    # ── Observability (v0.8.1) ──────────────────────────────

    def _dispatch(self, handler: Callable[[], None]) -> None:
        """Run one request inside a correlation scope.

        The ID is adopted from ``X-Request-ID`` / ``X-Correlation-ID``
        when the client supplies one, so a trace started upstream (load
        balancer, another service) continues here; otherwise a fresh one
        is generated. :meth:`send_response` echoes it on every response.
        """
        inbound = (
            self.headers.get(REQUEST_ID_HEADER)
            or self.headers.get(CORRELATION_HEADER)
            or None
        )
        with correlation_scope(inbound):
            handler()

    def send_response(self, code: int, message: Optional[str] = None) -> None:
        """Echo the correlation ID and count the request.

        Overriding the single point every response funnels through —
        including ``send_error`` and the artifact download path — means
        the header and the metric cannot be forgotten at a call site.
        """
        super().send_response(code, message)
        correlation_id = get_correlation_id()
        if correlation_id:
            self.send_header(CORRELATION_HEADER, correlation_id)
        try:
            # ``path`` / ``command`` are unset when the request line
            # itself failed to parse, hence the broad guard.
            path = self.path.split("?")[0]
            status = int(code)
            record_http_request(self.command or "", _route_template(path), status)
            if status >= 400:
                record_error(f"http_{status}")
        except Exception:  # noqa: BLE001 — telemetry must never break a response
            logger.debug("Failed to record request metrics", exc_info=True)

    def _send_metrics(self) -> None:
        """Serve the Prometheus text exposition payload."""
        try:
            body = render_prometheus_text().encode("utf-8")
        except Exception:  # noqa: BLE001 — a bad scrape must not take the server down
            logger.exception("Failed to render metrics")
            self._send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "metrics unavailable")
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", CONTENT_TYPE_LATEST)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @property
    def queue(self) -> LocalTaskQueue:
        """Access the task queue from the server instance."""
        return self.server.queue  # type: ignore[attr-defined]

    @property
    def scheduler(self) -> JobScheduler:
        """Access the job scheduler from the server instance (v0.9.3)."""
        return self.server.scheduler  # type: ignore[attr-defined]

    @property
    def dead_letter_store(self) -> DeadLetterStore:
        """Access the dead-letter store for this server (v0.9.4).

        Uses the server's injected store when present, otherwise the
        process-wide default — which is also what the worker writes to,
        so ``GET /deadletters`` always reflects freshly dead tasks.
        """
        override = getattr(self.server, "dead_letter_store_override", None)
        if isinstance(override, DeadLetterStore):
            return override
        from .dlq import get_default_store

        return get_default_store()

    def _is_shutting_down(self) -> bool:
        """Whether the owning ``TaskAPIServer`` has begun shutting down."""
        event = getattr(self.server, "shutting_down", None)
        return bool(event is not None and event.is_set())

    # ── Authentication ─────────────────────────────────────

    def _check_auth(self) -> bool:
        """Check ``X-API-Key`` authentication.

        When the server has no ``api_key`` configured, all requests are
        allowed — this keeps the local default (loopback) frictionless
        and backwards compatible. When an ``api_key`` is configured, the
        request's ``X-API-Key`` header is compared to it using a
        constant-time comparison (:func:`hmac.compare_digest`) to
        mitigate timing attacks.

        Returns:
            True if the request is authorized (and routing should
            continue); False if unauthorized (a 401 response has already
            been sent and the handler should return immediately).
        """
        api_key: Optional[str] = getattr(self.server, "api_key", None)
        if api_key is None:
            return True
        provided = self.headers.get("X-API-Key", "")
        if hmac.compare_digest(provided, api_key):
            return True
        self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
        return False

    # ── GET ─────────────────────────────────────────────────

    def do_GET(self) -> None:
        self._dispatch(self._do_GET)

    def _do_GET(self) -> None:
        path = self.path.split("?")[0]

        # Health check — exempt from authentication (always allowed).
        # A plain GET /health keeps the v0.6.1 shape; ?deep=1 opts in to
        # the full report (see cloud.health).
        if path == "/health":
            payload, status = build_health_payload(
                self.queue,
                shutting_down=self._is_shutting_down(),
                deep=parse_deep_flag(self._parse_query()),
            )
            self._send_json(payload, status=status)
            return

        # Readiness probe — exempt from authentication (v0.8.2).
        # Orchestrator probes cannot present an API key.
        if path == "/ready":
            payload, status = build_readiness_payload(
                self.queue,
                shutting_down=self._is_shutting_down(),
            )
            self._send_json(payload, status=status)
            return

        # OpenAPI spec — exempt from authentication (v0.8.2).
        # A spec is not sensitive and tooling needs it unauthenticated.
        if path == "/openapi.json":
            host = self.headers.get("Host")
            self._send_json(
                build_openapi_spec(server_url=f"http://{host}" if host else None)
            )
            return

        # Metrics (v0.8.1) — authenticated like every other route unless
        # MN_METRICS_PUBLIC opts in to unauthenticated scraping.
        if path == _METRICS_PATH:
            if not _metrics_public() and not self._check_auth():
                return
            self._send_metrics()
            return

        # Authenticate all other routes
        if not self._check_auth():
            return

        # Server info
        if path == "/info":
            self._send_json({
                "version": __version__,
                "active_tasks": self.queue.active_count,
                "is_started": self.queue.is_started,
                # v0.9.2: orchestration tooling can watch this to detect
                # that the server has begun its graceful shutdown.
                "shutting_down": self._is_shutting_down(),
            })
            return

        # List tasks
        if path == "/tasks":
            query = self._parse_query()
            status_filter = None
            if "status" in query:
                try:
                    status_filter = TaskStatus(query["status"])
                except ValueError:
                    self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid status: {query['status']}")
                    return
            limit = int(query.get("limit", "50"))
            tasks = self.queue.list_tasks(status=status_filter, limit=limit)
            self._send_json({
                "tasks": [t.to_summary() for t in tasks],
                "count": len(tasks),
            })
            return

        # Get task result
        match = _TASK_RESULT_PATTERN.match(path)
        if match:
            task_id = match.group(1)
            result = self.queue.get_result(task_id)
            if result is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Result not available")
                return
            self._send_json(result.model_dump(mode="json"))
            return

        # List task artifacts
        match = _TASK_ARTIFACTS_PATTERN.match(path)
        if match:
            task_id = match.group(1)
            task = self.queue.get_task(task_id)
            if task is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Task {task_id} not found")
                return
            artifacts = self._list_task_artifacts(task)
            self._send_json({"artifacts": artifacts, "count": len(artifacts)})
            return

        # Download task artifact
        match = _TASK_DOWNLOAD_PATTERN.match(path)
        if match:
            task_id = match.group(1)
            filename = match.group(2)
            task = self.queue.get_task(task_id)
            if task is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Task {task_id} not found")
                return
            self._serve_task_artifact(task, filename)
            return

        # Get task details
        match = _TASK_PATTERN.match(path)
        if match:
            task_id = match.group(1)
            task = self.queue.get_task(task_id)
            if task is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Task {task_id} not found")
                return
            self._send_json(task.model_dump(mode="json"))
            return

        # List batches (v0.9.3)
        if path == "/batches":
            query = self._parse_query()
            try:
                limit = int(query.get("limit", "50"))
            except ValueError:
                self._send_error(HTTPStatus.BAD_REQUEST, "Invalid limit")
                return
            batches = self.queue.list_batches(limit=limit)
            self._send_json({
                "batches": [b.model_dump(mode="json") for b in batches],
                "count": len(batches),
            })
            return

        # Get batch with aggregated progress (v0.9.3)
        match = _BATCH_PATTERN.match(path)
        if match:
            batch_id = match.group(1)
            batch = self.queue.get_batch(batch_id)
            if batch is None:
                self._send_error(HTTPStatus.NOT_FOUND, f"Batch {batch_id} not found")
                return
            self._send_json(batch.model_dump(mode="json"))
            return

        # List schedules (v0.9.3)
        if path == "/schedules":
            schedules = self.scheduler.list_schedules()
            self._send_json({
                "schedules": [s.model_dump(mode="json") for s in schedules],
                "count": len(schedules),
            })
            return

        # Get recent runs for a schedule (v0.9.3)
        match = _SCHEDULE_RUNS_PATTERN.match(path)
        if match:
            schedule_id = match.group(1)
            if self.scheduler.get_schedule(schedule_id) is None:
                self._send_error(
                    HTTPStatus.NOT_FOUND, f"Schedule {schedule_id} not found"
                )
                return
            runs = self.scheduler.get_runs(schedule_id)
            self._send_json({
                "runs": [r.model_dump(mode="json") for r in runs],
                "count": len(runs),
            })

        # List dead letters (v0.9.4)
        if path == "/deadletters":
            records = self.dead_letter_store.list()
            self._send_json({
                "deadletters": [r.model_dump(mode="json") for r in records],
                "count": len(records),
            })
            return

        # Get a dead letter (v0.9.4)
        match = _DEADLETTER_PATTERN.match(path)
        if match:
            task_id = match.group(1)
            record = self.dead_letter_store.get(task_id)
            if record is None:
                self._send_error(
                    HTTPStatus.NOT_FOUND, f"Dead letter {task_id} not found"
                )
                return
            self._send_json(record.model_dump(mode="json"))
            return

        self._send_error(HTTPStatus.NOT_FOUND, f"Unknown path: {path}")

    # ── POST ────────────────────────────────────────────────

    def do_POST(self) -> None:
        self._dispatch(self._do_POST)

    def _do_POST(self) -> None:
        path = self.path.split("?")[0]

        # Authenticate all routes
        if not self._check_auth():
            return

        if path == "/tasks":
            # v0.9.2: draining servers stop accepting new work. Probes
            # (/ready, /health) still answer so orchestrators see a clean
            # shutdown instead of a connection error.
            if self._is_shutting_down():
                self._send_error(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    "server is shutting down — not accepting new tasks",
                )
                return

            try:
                body = self._read_body()
            except ValueError as e:
                self._send_error(HTTPStatus.BAD_REQUEST, str(e))
                return

            try:
                request = TaskRequest(**body)
            except Exception as e:  # noqa: BLE001
                logger.debug("POST /tasks rejected: invalid TaskRequest payload: %s", e)
                self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid task request: {e}")
                return

            task_id = self.queue.submit(request)
            self._send_json(
                {"task_id": task_id, "status": "pending"},
                status=HTTPStatus.CREATED,
            )
            return

        # Submit a batch of tasks (v0.9.3)
        if path == "/tasks/batch":
            try:
                body = self._read_body()
            except ValueError as e:
                self._send_error(HTTPStatus.BAD_REQUEST, str(e))
                return
            try:
                request = BatchRequest(**body)
            except Exception as e:  # noqa: BLE001
                logger.debug("POST /tasks/batch rejected: invalid BatchRequest: %s", e)
                self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid batch request: {e}")
                return
            batch = self.queue.submit_batch(request)
            self._send_json(
                {
                    "batch_id": batch.batch_id,
                    "status": batch.status.value,
                    "task_ids": batch.task_ids,
                },
                status=HTTPStatus.CREATED,
            )
            return

        # Replay a dead letter (v0.9.4) — resubmits the original request
        # with a fresh task ID.
        match = _DEADLETTER_REPLAY_PATTERN.match(path)
        if match:
            task_id = match.group(1)
            try:
                new_task_id = replay_dead_letter(task_id, queue=self.queue)
            except KeyError:
                self._send_error(
                    HTTPStatus.NOT_FOUND, f"Dead letter {task_id} not found"
                )
                return
            self._send_json(
                {
                    "original_task_id": task_id,
                    "task_id": new_task_id,
        
                },
                status=HTTPStatus.CREATED,
            )
            return

        # Create a scheduled job (v0.9.3)
        if path == "/schedules":
            try:
                body = self._read_body()
            except ValueError as e:
                self._send_error(HTTPStatus.BAD_REQUEST, str(e))
                return
            try:
                cron = body.get("cron")
                if not cron or not isinstance(cron, str):
                    raise ScheduleError("'cron' must be a 5-field cron string")
                task_request = TaskRequest(**body.get("task_request", {}))
                enabled = body.get("enabled", True)
                schedule = self.scheduler.register_schedule(
                    cron,
                    task_request,
                    enabled=bool(enabled),
                )
            except ScheduleError as e:
                logger.debug("POST /schedules rejected: %s", e)
                self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid schedule: {e}")
                return
            except Exception as e:  # noqa: BLE001
                logger.debug("POST /schedules rejected: invalid payload: %s", e)
                self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid schedule: {e}")
                return
            self._send_json(
                schedule.model_dump(mode="json"),
                status=HTTPStatus.CREATED,
            )
            return
        self._send_error(HTTPStatus.NOT_FOUND, f"Unknown path: {path}")

    # ── DELETE ──────────────────────────────────────────────

    def do_DELETE(self) -> None:
        self._dispatch(self._do_DELETE)

    def _do_DELETE(self) -> None:
        path = self.path.split("?")[0]

        # Authenticate all routes
        if not self._check_auth():
            return

        match = _TASK_PATTERN.match(path)
        if match:
            task_id = match.group(1)
            cancelled = self.queue.cancel(task_id)
            if cancelled:
                self._send_json({"task_id": task_id, "cancelled": True})
            else:
                self._send_error(
                    HTTPStatus.NOT_FOUND,
                    f"Task {task_id} not found or already terminal",
                )
            return

        # Cancel every active task in a batch (v0.9.3)
        match = _BATCH_PATTERN.match(path)
        if match:
            batch_id = match.group(1)
            if self.queue.cancel_batch(batch_id):
                self._send_json({"batch_id": batch_id, "cancelled": True})
            else:
                self._send_error(HTTPStatus.NOT_FOUND, f"Batch {batch_id} not found")
            return

        # Delete a scheduled job (v0.9.3)
        match = _SCHEDULE_PATTERN.match(path)
        if match:
            schedule_id = match.group(1)
            if self.scheduler.cancel_schedule(schedule_id):
                self._send_json({"schedule_id": schedule_id, "deleted": True})
            else:
                self._send_error(
                    HTTPStatus.NOT_FOUND, f"Schedule {schedule_id} not found"
                )
            return

        # Remove a dead letter (v0.9.4)
        match = _DEADLETTER_PATTERN.match(path)
        if match:
            task_id = match.group(1)
            removed = self.dead_letter_store.remove(task_id)
            if removed:
                self._send_json({"task_id": task_id, "removed": True})
            else:
                self._send_error(
                    HTTPStatus.NOT_FOUND, f"Dead letter {task_id} not found"
                )
            return

        self._send_error(HTTPStatus.NOT_FOUND, f"Unknown path: {path}")

    # ── Query parsing ───────────────────────────────────────

    def _parse_query(self) -> Dict[str, str]:
        """Parse query string from URL."""
        parts = self.path.split("?", 1)
        if len(parts) < 2:
            return {}
        result: Dict[str, str] = {}
        for pair in parts[1].split("&"):
            if "=" in pair:
                key, val = pair.split("=", 1)
                result[key] = val
            else:
                result[pair] = ""
        return result

    # ── Artifact helpers ────────────────────────────────────

    def _list_task_artifacts(self, task: Task) -> list:
        """List available output files for a task (v0.8.3: via the artifact store)."""
        store = get_task_artifact_store(task.id, task.result.output_dir if task.result else None)
        if store is None:
            return []

        artifacts = []
        for info in sorted(store.list(), key=lambda i: i.key):
            # Preserve v0.6.1 semantics: top-level files only, no dotfiles.
            if "/" in info.key or info.key.startswith("."):
                continue
            artifacts.append({
                "filename": info.key,
                "size": info.size,
                "path": artifact_location(store, info.key),
            })
        return artifacts

    def _serve_task_artifact(self, task: Task, filename: str) -> None:
        """Serve a file from the task's output directory (v0.8.3: via the artifact store)."""
        from pathlib import Path
        from urllib.parse import unquote

        filename = unquote(filename)
        result = task.result
        if not result or not result.output_dir:
            self._send_error(HTTPStatus.NOT_FOUND, "Task has no output directory")
            return

        store = get_task_artifact_store(task.id, result.output_dir)
        if store is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"File '{filename}' not found")
            return

        # Security: prevent path traversal (enforced by the store's key guard)
        try:
            info = store.stat(filename)
        except UnsafeKeyError:
            logger.debug("Rejected artifact download: unsafe key %r", filename)
            self._send_error(HTTPStatus.FORBIDDEN, "Access denied")
            return
        except ArtifactNotFoundError:
            self._send_error(HTTPStatus.NOT_FOUND, f"File '{filename}' not found")
            return
        except Exception:  # noqa: BLE001
            logger.debug("Rejected artifact download: unsafe path %r failed resolution", filename)
            self._send_error(HTTPStatus.BAD_REQUEST, "Invalid filename")
            return

        # Determine content type
        content_type = "application/octet-stream"
        ext = Path(info.key).suffix.lower()
        if ext == ".mp4":
            content_type = "video/mp4"
        elif ext == ".mp3":
            content_type = "audio/mpeg"
        elif ext == ".srt":
            content_type = "text/plain"
        elif ext == ".json":
            content_type = "application/json"
        elif ext == ".md":
            content_type = "text/markdown"

        file_size = info.size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()

        with store.open(info.key) as f:
            while True:
                chunk = f.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)


# ── Server ─────────────────────────────────────────────────


class TaskAPIServer:
    """HTTP API server wrapping a ``LocalTaskQueue``.

    Provides REST endpoints for remote task management. The server
    runs in a background thread by default, or can block the calling
    thread.

    Args:
        host: Bind address.
        port: Listen port.
        queue: An existing ``LocalTaskQueue`` to wrap. If None, a
            new one is created.
        storage_dir: Storage directory for the queue (if creating).
        max_workers: Max worker threads for the queue (if creating).
        api_key: Optional X-API-Key for authenticating requests. When
            None (default), the server runs unauthenticated — safe only
            on loopback. Required when binding to a public interface.
        artifact_store: Backend swept by the artifact lifecycle thread
            (v0.8.3). Defaults to the store resolved from the
            ``MN_STORAGE_*`` environment variables.
        artifact_policy: Retention policy for that sweeper (v0.8.3).
            Defaults to ``ArtifactLifecyclePolicy.from_env()``; when no
            retention rule is configured no sweeper thread is started.
        drain_timeout: Graceful-shutdown drain budget in seconds (v0.9.2).
            When the server owns its task queue, ``stop()`` waits up to
            this long for in-flight tasks. None defers to
            ``MN_GRACEFUL_SHUTDOWN_TIMEOUT``.

        dead_letter_store: Dead-letter store used by the ``/deadletters``
            endpoints (v0.9.4). Defaults to the process-wide store —
            which is also where the worker writes records, so the two
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        queue: Optional[LocalTaskQueue] = None,
        storage_dir=None,
        max_workers: int = 2,
        api_key: Optional[str] = None,
        artifact_store: Optional[StorageBackend] = None,
        artifact_policy: Optional[ArtifactLifecyclePolicy] = None,
        drain_timeout: Optional[float] = None,
        scheduler: Optional[JobScheduler] = None,

        dead_letter_store: Optional[DeadLetterStore] = None,
    ) -> None:
        self.host = host
        self.port = port
        self.api_key = api_key
        self._owns_queue = queue is None
        self._queue = queue or LocalTaskQueue(
            storage_dir=storage_dir,
            max_workers=max_workers,
        )
        # v0.9.3: the scheduler backs the /schedules routes. When none is
        # supplied a scheduler is created against the queue's storage; the
        # scheduling *loop* is only started by the daemon (see daemon.py),
        # so a bare API server still accepts CRUD without triggering runs.
        self._scheduler = scheduler or JobScheduler(
            queue=self._queue,
            storage_dir=self._queue.storage.storage_dir,
        )
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        # Set by stop() so the /ready and /health probes can report that
        # the server is draining (v0.8.2).
        self._shutting_down = threading.Event()
        self._artifact_store = artifact_store
        self._artifact_policy = artifact_policy
        self._sweeper: Optional[ArtifactSweeper] = None
        # v0.9.2: graceful-shutdown drain budget (seconds). None defers to
        # ``MN_GRACEFUL_SHUTDOWN_TIMEOUT`` at ``stop()`` time.
        self._drain_timeout = drain_timeout

        self._dead_letter_store = dead_letter_store

    @property
    def queue(self) -> LocalTaskQueue:
        """The underlying task queue."""
        return self._queue

    @property
    def scheduler(self) -> JobScheduler:
        """The scheduler backing the ``/schedules`` routes (v0.9.3)."""
        return self._scheduler

    @scheduler.setter
    def scheduler(self, scheduler: JobScheduler) -> None:
        """Replace the scheduler (used by the daemon for Settings tuning)."""
        self._scheduler = scheduler
        if self._server is not None:
            self._server.scheduler = scheduler  # type: ignore[attr-defined]

    def dead_letter_store(self) -> DeadLetterStore:
        """The dead-letter store backing this server (v0.9.4).

        The process-wide default when no explicit store was injected.
        """
        if self._dead_letter_store is not None:
            return self._dead_letter_store
        from .dlq import get_default_store

        return get_default_store()

    @property
    def is_shutting_down(self) -> bool:
        """Whether ``stop()`` has been called (readiness probes fail)."""
        return self._shutting_down.is_set()

    @property
    def is_running(self) -> bool:
        """Whether the server is currently running."""
        return self._server is not None

    @property
    def base_url(self) -> str:
        """Base URL of the running server."""
        return f"http://{self.host}:{self.port}"

    def start(self, blocking: bool = False) -> None:
        """Start the HTTP server.

        Args:
            blocking: If True, block the calling thread. If False,
                run in a background thread.
        """
        if self._server is not None:
            raise RuntimeError("Server is already running")

        self._shutting_down.clear()
        self._server = ThreadingHTTPServer(
            (self.host, self.port),
            _APIHandler,
        )
        self._server.queue = self._queue  # type: ignore[attr-defined]
        self._server.api_key = self.api_key  # type: ignore[attr-defined]
        self._server.shutting_down = self._shutting_down  # type: ignore[attr-defined]
        self._server.scheduler = self._scheduler  # type: ignore[attr-defined]

        # v0.9.4: optional explicit dead-letter store (None → default)
        self._server.dead_letter_store_override = self._dead_letter_store  # type: ignore[attr-defined]
        # Update actual port (in case port=0 was used)
        self.port = self._server.server_address[1]

        # v0.8.3: artifact TTL sweeper (no-op unless retention is configured)
        self._start_artifact_sweeper()

        if blocking:
            logger.info("API server listening on %s:%d", self.host, self.port)
            try:
                self._server.serve_forever()
            except KeyboardInterrupt:
                logger.info("API server interrupted")
            finally:
                self.stop()
        else:
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                name="mn-api-server",
                daemon=True,
            )
            self._thread.start()
            logger.info("API server started on %s:%d", self.host, self.port)

    # ── Artifact lifecycle (v0.8.3) ─────────────────────────

    @property
    def sweeper(self) -> Optional[ArtifactSweeper]:
        """The running artifact sweeper, if artifact retention is enabled."""
        return self._sweeper

    def _active_task_ids(self) -> list:
        """IDs of tasks that are still pending/running — never sweep those."""
        return [t.id for t in self._queue.list_tasks(limit=1000) if t.is_active]

    def _start_artifact_sweeper(self) -> None:
        """Start the TTL sweeper when a retention rule is configured."""
        if self._sweeper is not None:
            return
        policy = self._artifact_policy or ArtifactLifecyclePolicy.from_env()
        if not policy.enabled:
            return
        store = self._artifact_store
        if store is None:
            try:
                store = get_artifact_store()
            except ArtifactStoreError as e:
                logger.warning("Artifact sweeper disabled — store unavailable: %s", e)
                return
        self._sweeper = ArtifactSweeper(
            store,
            policy,
            interval=sweep_interval_from_env(),
            protected_ids=self._active_task_ids,
        )
        self._sweeper.start()

    def begin_drain(self, drain_timeout: Optional[float] = None) -> None:
        """Enter draining mode: reject new tasks and drain in-flight ones.

        v0.9.2 graceful-shutdown lifecycle, in order:

        1. Flag ``_shutting_down`` — new ``POST /tasks`` are rejected and
           the ``/ready`` / ``/health`` / ``/info`` endpoints report the
           draining state.
        2. Stop the artifact sweeper (its thread may not outlive us).
        3. When this server owns the task queue, drain it: wait up to
           ``drain_timeout`` (default ``MN_GRACEFUL_SHUTDOWN_TIMEOUT``)
           for in-flight tasks, force-cancelling whatever remains.

        The HTTP loop is *not* stopped here — extracted from ``stop()`` so
        the daemon's signal path can drain while probes still answer.
        Idempotent; safe to call more than once.
        """
        self._shutting_down.set()
        if self._sweeper is not None:
            self._sweeper.stop()
            self._sweeper = None

        if self._owns_queue:
            timeout = (
                drain_timeout
                if drain_timeout is not None
                else self._drain_timeout
            )
            if timeout is None:
                from .daemon import graceful_shutdown_timeout

                timeout = graceful_shutdown_timeout()
            self._queue.shutdown(wait=True, timeout=timeout)

    def stop(self, drain_timeout: Optional[float] = None) -> None:
        """Stop the HTTP server, draining in-flight tasks first.

        v0.9.2 drain semantics: new submissions are rejected immediately,
        in-flight tasks get a bounded chance to finish, and only then is
        the HTTP loop torn down. ``drain_timeout`` overrides the value
        given at construction / ``MN_GRACEFUL_SHUTDOWN_TIMEOUT``.
        """
        self.begin_drain(drain_timeout)
        # v0.9.3: stop the scheduler loop (no-op when it was never started).
        self._scheduler.stop()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> "TaskAPIServer":
        self.start(blocking=False)
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()
