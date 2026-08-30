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
import ipaddress
import json
import logging
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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
from .entitlements import (  # v1.3.1 — plans & entitlements
    DEFAULT as _DEFAULT_PLAN,
    PLAN_HEADER,
    EntitlementError,
    available_plan_names,
    check_submission,
    default_plan_name,
    resolve_plan,
    submission_resolution,
)
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

# ── Route patterns (for metrics cardinality / _route_template) ──

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

#: Max accepted request body size in bytes (v0.9.5). A batch of up to 50
#: task requests is well under this limit; anything larger is rejected as
#: payload-too-large before the body is read, protecting the server from
#: unbounded memory consumption on a single request.
_MAX_BODY_BYTES = 1024 * 1024  # 1 MiB

#: Max ``limit`` query value for list endpoints (v0.9.5). Bounds the
#: number of records a single request can retrieve.
_MAX_LIST_LIMIT = 500


class PayloadTooLargeError(Exception):
    """Raised when a request body exceeds ``_MAX_BODY_BYTES`` (v0.9.5).

    Distinct from :class:`ValueError` so the API layer can return an
    HTTP 413 instead of a generic 400 for an oversized body.
    """


#: Environment variable opting ``/metrics`` out of API-key auth.
_ENV_METRICS_PUBLIC = "MN_METRICS_PUBLIC"

#: v1.3.1: principal reported for API-key-authenticated requests when
#: ``MN_API_PRINCIPAL`` is unset. Unauthenticated loopback requests keep
#: the pre-v1.3.1 identity: principal ``"local"``, tenant ``"default"``.
_ENV_API_PRINCIPAL = "MN_API_PRINCIPAL"
_DEFAULT_API_PRINCIPAL = "api-key"

#: v1.3.1: optional tenant-scoping header. A non-default tenant sees only
#: its own tasks' artifacts; ``"default"`` (and unauthenticated loopback)
#: keeps the single-tenant backward-compatible view of everything.
TENANT_HEADER = "X-MN-Tenant"

#: Paths that are already templates (no variable segment).
_STATIC_PATHS = frozenset(
    {
        "/health",
        "/info",
        "/tasks",
        "/ready",
        "/openapi.json",
        _METRICS_PATH,
        "/tasks/batch",
        "/batches",
        "/schedules",
        "/deadletters",
        "/api/v1/dashboard/summary",
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
        "1",
        "true",
        "yes",
        "on",
    }


def _api_principal() -> str:
    """Principal recorded for API-key-authenticated requests (v1.3.1).

    Reads ``MN_API_PRINCIPAL`` from the process environment (the same
    resolution style as the other operational ``MN_*`` admission and
    lifecycle variables); falls back to ``"api-key"``.
    """
    raw = os.environ.get(_ENV_API_PRINCIPAL, "").strip()
    return raw or _DEFAULT_API_PRINCIPAL


# ── Loopback detection (v1.2) ──────────────────────────────


def _is_loopback_host(host: str) -> bool:
    """Return True when *host* binds only to the loopback interface.

    ``0.0.0.0`` / ``::`` bind *every* interface, so they are deliberately
    treated as non-loopback: a server listening there is reachable from
    the network and must require authentication. ``localhost`` is a name,
    not an IP literal, so it is special-cased.

    Args:
        host: The bind address passed to :class:`TaskAPIServer`.

    Returns:
        True for ``127.0.0.1``, ``localhost``, ``::1`` and equivalent
        loopback literals; False for a public/external bind, ``0.0.0.0``,
        ``::`` and any other host name.
    """
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not an IP literal — treat "localhost" as loopback and every
        # other name as potentially remote (needs auth).
        return host.strip().lower() == "localhost"


# ── Submission admission limits (v1.2) ─────────────────────


#: Env var capping the number of concurrently active tasks accepted by
#: ``POST /tasks`` / ``POST /tasks/batch``. Unset (or <= 0) disables the
#: cap, preserving the pre-v1.2 unlimited behaviour.
_ENV_MAX_CONCURRENT_TASKS = "MN_MAX_CONCURRENT_TASKS"

#: Env var capping the estimated output size of a single submission.
#: Unset (or <= 0) disables the cap.
_ENV_MAX_ESTIMATED_ARTIFACT_BYTES = "MN_MAX_ESTIMATED_ARTIFACT_BYTES"

#: Fixed non-video bytes (script, subtitle, metadata, cache) always added
#: on top of the A/V estimate. Keeps the estimate simple yet safe.
_ARTIFACT_FIXED_OVERHEAD_BYTES = 8 * 1024 * 1024  # 8 MiB

#: Rough per-``video_format`` video bitrate in bits/second for admission
#: estimation (v1.2). A generous over-estimate, so a request is refused
#: only when its output would clearly exceed the configured cap.
_ESTIMATED_VIDEO_BITRATE_BPS = {
    "16:9": 4_000_000,  # ≈ 4 Mbps (1080p-ish horizontal)
    "9:16": 3_000_000,  # ≈ 3 Mbps portrait
}
_ESTIMATED_AUDIO_BITRATE_BPS = 192_000


def _env_positive_int(name: str) -> Optional[int]:
    """Parse a positive integer from environment variable *name* (v1.2).

    An unset, empty, non-integer or non-positive value yields None, which
    callers interpret as "limit disabled" for backwards compatibility.

    Args:
        name: The environment variable name.

    Returns:
        The positive integer, or None when it is absent/invalid.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r", name, raw)
        return None
    if value <= 0:
        return None
    return value


def _concurrency_limit() -> Optional[int]:
    """Active-task admission cap from ``MN_MAX_CONCURRENT_TASKS``.

    Returns:
        The cap, or None to disable the concurrency admission check.
    """
    return _env_positive_int(_ENV_MAX_CONCURRENT_TASKS)


def _artifact_size_limit() -> Optional[int]:
    """Estimated-artifact-size cap, in bytes (v1.2).

    Returns:
        The cap, or None to disable the artifact-size admission check.
    """
    return _env_positive_int(_ENV_MAX_ESTIMATED_ARTIFACT_BYTES)


def _estimate_artifact_bytes(request: TaskRequest) -> int:
    """Roughly estimate the total output bytes for *request* (v1.2).

    Render output is dominated by the encoded video stream, so this uses
    a per-``video_format`` A/V bitrate multiplied by ``duration`` plus a
    fixed allowance for the script/subtitle/metadata artifacts. It is
    intentionally approximate and exists only to refuse submissions whose
    output would clearly exceed the configured cap.

    Args:
        request: The validated task request.

    Returns:
        The estimated number of output bytes.
    """
    video_bps = _ESTIMATED_VIDEO_BITRATE_BPS.get(
        request.video_format,
        _ESTIMATED_VIDEO_BITRATE_BPS["16:9"],
    )
    media_bytes = (video_bps + _ESTIMATED_AUDIO_BITRATE_BPS) * request.duration // 8
    return media_bytes + _ARTIFACT_FIXED_OVERHEAD_BYTES


# ── Route registry (v1.0 refactor) ─────────────────────────


class _RouteRegistry:
    """Registry of API routes with regex-based dispatch.

    Each route is a ``(method, pattern, handler, auth_required)`` tuple.
    Routes are matched in registration order, so more specific patterns
    must be registered before less specific ones (e.g. ``/tasks/{id}/result``
    before ``/tasks/{id}``).

    Path parameters use named capture groups in the regex pattern and are
    passed to the handler as keyword arguments.
    """

    def __init__(self) -> None:
        self._routes: list[Tuple[str, re.Pattern[str], Callable[..., None], bool]] = []

    def register(
        self,
        method: str,
        pattern: str,
        *,
        auth_required: bool = True,
    ) -> Callable[[Callable[..., None]], Callable[..., None]]:
        """Decorator: register a handler method for *method* + *pattern*.

        Args:
            method: HTTP method (``"GET"``, ``"POST"``, ``"DELETE"``, ...).
            pattern: Regex pattern with optional named capture groups for
                path parameters (e.g. ``r"^/tasks/(?P<task_id>[a-f0-9]+)$"``).
            auth_required: If True, ``_check_auth()`` is called before the
                handler and the request is rejected on failure.  Routes
                that are always public (``/health``) or manage auth
                themselves (``/metrics``) set this to False.
        """
        compiled = re.compile(pattern)

        def decorator(handler: Callable[..., None]) -> Callable[..., None]:
            """Register a decorator function."""
            self._routes.append((method, compiled, handler, auth_required))
            return handler

        return decorator

    def dispatch(self, handler_instance: "_APIHandler", method: str, path: str) -> None:
        """Find the first route matching *method* + *path* and invoke it.

        If the route requires authentication, ``_check_auth()`` is called
        first; on failure the handler is not invoked (a 401 response has
        already been sent).

        If no route matches, a 404 response is sent.
        """
        for route_method, pattern, handler, auth_required in self._routes:
            if route_method != method:
                continue
            match = pattern.match(path)
            if match:
                if auth_required and not handler_instance._check_auth():
                    return
                handler(handler_instance, **match.groupdict())
                return
        handler_instance._send_error(HTTPStatus.NOT_FOUND, f"Unknown path: {path}")


#: Module-level route registry populated by ``_APIHandler`` decorators.
_route_registry = _RouteRegistry()


# ── Request handler ────────────────────────────────────────


class _APIHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the task API."""

    # Suppress default logging
    def log_message(self, fmt: str, *args: Any) -> None:
        """Log a message with correlation context."""
        logger.debug("API %s - %s", self.address_string(), fmt % args)

    # ── Helpers ─────────────────────────────────────────────

    def _read_body(self) -> Dict[str, Any]:
        """Read and parse JSON body from the request.

        v0.9.5: requests whose declared ``Content-Length`` exceeds
        ``_MAX_BODY_BYTES`` are rejected with :class:`PayloadTooLargeError`
        before any bytes are read, so a hostile client cannot force the
        server to buffer an unbounded body.
        """
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        if length > _MAX_BODY_BYTES:
            # Drain the body before rejecting so the TCP connection is left
            # in a clean state and the client can read the 413 response
            # instead of failing with a broken pipe mid-send.
            self._drain_body(length)
            raise PayloadTooLargeError(f"request body too large (max {_MAX_BODY_BYTES} bytes)")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise ValueError(f"Invalid JSON body: {e}")

    def _drain_body(self, length: int) -> None:
        """Read and discard *length* request-body bytes without buffering.

        v0.9.5: called after rejecting an oversized request so the client
        can finish uploading and read the 413 response. Bytes are read in
        chunks and discarded, never accumulated, so memory stays bounded
        regardless of the declared ``Content-Length``.
        """
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(8192, remaining))
            if not chunk:
                break
            remaining -= len(chunk)

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
            self.headers.get(REQUEST_ID_HEADER) or self.headers.get(CORRELATION_HEADER) or None
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

        Loopback binds keep the pre-v1.2 behaviour: with no ``api_key``
        configured every request is anonymous but allowed. A non-loopback
        bind is reachable from the network, so anonymous access is
        refused with 401 when no ``api_key`` is configured — a key must
        be set (``MN_API_KEY``). When an ``api_key`` *is* configured, the
        request's ``X-API-Key`` header is compared against it using a
        constant-time comparison (:func:`hmac.compare_digest`) to
        mitigate timing attacks.

        ``self.server.host`` is read defensively (defaulting to loopback)
        so mock server objects in tests never crash this method.

        Returns:
            True if the request is authorized (and routing should
            continue); False if unauthorized (a 401 response has already
            been sent and the handler should return immediately).
        """
        api_key: Optional[str] = getattr(self.server, "api_key", None)
        host: str = getattr(self.server, "host", "127.0.0.1")
        if api_key is None:
            if _is_loopback_host(host):
                return True
            self._send_error(
                HTTPStatus.UNAUTHORIZED,
                "unauthenticated access denied: non-loopback bind requires MN_API_KEY",
            )
            return False
        provided = self.headers.get("X-API-Key", "")
        if hmac.compare_digest(provided, api_key):
            return True
        self._send_error(HTTPStatus.UNAUTHORIZED, "unauthorized")
        return False

    def _current_identity(self) -> Tuple[str, str]:
        """Resolve the ``(principal, tenant_id)`` of the caller (v1.3.1).

        Must be called *after* :meth:`_check_auth` has passed:

        - A valid API key → principal from ``MN_API_PRINCIPAL`` (default
          ``"api-key"``); tenant from the optional ``X-MN-Tenant`` header,
          else ``"default"``.
        - Unauthenticated loopback (the v1.2 frictionless path) →
          principal ``"local"``, tenant ``"default"``.
        - Unauthenticated non-loopback never reaches here (``_check_auth``
          already answered 401).
        """
        api_key: Optional[str] = getattr(self.server, "api_key", None)
        if api_key is None:
            # Loopback anonymous — the v1.2 frictionless path.
            return ("local", "default")
        provided = self.headers.get("X-API-Key", "")
        if not hmac.compare_digest(provided, api_key):
            # Defensive: _check_auth should have rejected already.
            return ("local", "default")
        principal = _api_principal()
        tenant = (self.headers.get(TENANT_HEADER) or "").strip() or "default"
        return (principal, tenant)

    def _audit(self, route: str, task_id: str = "", **fields: Any) -> None:
        """Emit a structured audit record for a state-reading or mutating route (v1.3.1).

        The record carries ``task_id``, ``route``, ``tenant_id`` and
        ``principal`` as top-level JSON keys when ``MN_LOG_FORMAT=json`` is
        active, so SIEM/audit pipelines can join submissions, status reads
        and artifact downloads with the tasks they touched. Auditing is
        best-effort and must never affect the response.
        """
        try:
            principal, tenant = self._current_identity()
            logger.info(
                "audit %s %s",
                route,
                task_id or "-",
                extra={
                    "event": "audit",
                    "route": route,
                    "task_id": task_id,
                    "tenant_id": tenant,
                    "principal": principal,
                    **fields,
                },
            )
        except Exception:  # noqa: BLE001 — telemetry must never break a response
            logger.debug("Failed to write audit record", exc_info=True)

    def _resolve_plan_name(self) -> Tuple[str, Optional[str]]:
        """Resolve the plan for this request (v1.3.1, Feature 5).

        - Unauthenticated (loopback) requests always get the unlimited
          ``"default"`` plan — the frictionless local path is never
          restricted, even when ``MN_DEFAULT_PLAN`` is set.
        - An explicit ``X-MN-Plan`` header is validated against the
          configured plans; an unknown name yields an error message.
        - Otherwise ``MN_DEFAULT_PLAN`` applies (tolerant: unknown names
          fall back to ``"default"`` with a warning).

        Returns:
            ``(plan_name, error_message)`` — ``error_message`` is None when
            the name is usable.
        """
        api_key: Optional[str] = getattr(self.server, "api_key", None)
        if api_key is None:
            return (_DEFAULT_PLAN.name, None)
        header = (self.headers.get(PLAN_HEADER) or "").strip()
        if header:
            try:
                resolve_plan(header)
            except KeyError:
                configured = ", ".join(available_plan_names())
                return (
                    header,
                    f"unknown plan {header!r} (configured plans: {configured})",
                )
            return (header.lower(), None)
        return (default_plan_name(), None)

    def _plan_entitlement_rejection(
        self,
        plan_name: str,
        requests: List[TaskRequest],
    ) -> Optional[Tuple[int, Dict[str, Any]]]:
        """Validate *requests* against the resolved plan (v1.3.1, Feature 5).

        Runs :func:`check_submission` per request using the request's
        duration, its would-be render resolution and the v1.2 artifact-size
        estimate heuristic. The unlimited default plan passes trivially, so
        submissions are byte-for-byte unaffected when no plan is active.

        Returns:
            ``(HTTPStatus.FORBIDDEN, body)`` on the first violation, or
            None when every request is admissible.
        """
        plan = resolve_plan(plan_name)
        for request in requests:
            try:
                check_submission(
                    plan,
                    duration_s=float(request.duration),
                    resolution=submission_resolution(request),
                    estimated_bytes=_estimate_artifact_bytes(request),
                )
            except EntitlementError as e:
                return (
                    HTTPStatus.FORBIDDEN,
                    {
                        "error": "entitlement_denied",
                        "plan": e.plan,
                        "limit": e.limit,
                        "actual": e.actual,
                    },
                )
        return None

    # ── Submission admission (v1.2) ────────────────────────

    def _admission_rejection(
        self,
        requests: List[TaskRequest],
    ) -> Optional[Tuple[int, str]]:
        """Evaluate admission limits for *requests* before submission.

        Two opt-in limits protect the server from a single tenant/anon
        blowing through resources. Both are disabled when their env var
        is unset, so the pre-v1.2 unlimited behaviour is preserved.

        - Concurrency: when ``MN_MAX_CONCURRENT_TASKS`` is set, a
          submission that would push the active task count (pending/
          running/retrying) over the cap is refused with 429.
        - Estimated artifact size: each request's output is estimated
          from its ``duration`` and ``video_format``; when
          ``MN_MAX_ESTIMATED_ARTIFACT_BYTES`` is set, an over-budget
          request is refused with 413.

        Args:
            requests: The validated task requests about to be submitted
                (one for ``POST /tasks``, many for ``POST /tasks/batch``).

        Returns:
            A ``(status, message)`` pair to send as the rejection, or
            None when the submission is admissible.
        """
        limit = _concurrency_limit()
        if limit is not None and self.queue.active_count + len(requests) > limit:
            return (
                HTTPStatus.TOO_MANY_REQUESTS,
                f"too many active tasks: {self.queue.active_count} active "
                f"+ {len(requests)} new would exceed limit {limit} "
                f"(MN_MAX_CONCURRENT_TASKS)",
            )

        max_bytes = _artifact_size_limit()
        if max_bytes is not None:
            for request in requests:
                estimate = _estimate_artifact_bytes(request)
                if estimate > max_bytes:
                    return (
                        HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                        f"estimated artifact size {estimate} bytes exceeds "
                        f"limit {max_bytes} (MN_MAX_ESTIMATED_ARTIFACT_BYTES)",
                    )
        return None

    # ── GET route handlers ──────────────────────────────────
    #
    # Order matters: more specific patterns must be registered before
    # less specific ones (e.g. /tasks/{id}/result before /tasks/{id}).

    @_route_registry.register("GET", r"^/health$", auth_required=False)
    def _handle_get_health(self) -> None:
        payload, status = build_health_payload(
            self.queue,
            shutting_down=self._is_shutting_down(),
            deep=parse_deep_flag(self._parse_query()),
        )
        self._send_json(payload, status=status)

    @_route_registry.register("GET", r"^/ready$", auth_required=False)
    def _handle_get_ready(self) -> None:
        payload, status = build_readiness_payload(
            self.queue,
            shutting_down=self._is_shutting_down(),
        )
        self._send_json(payload, status=status)

    @_route_registry.register("GET", r"^/openapi\.json$", auth_required=False)
    def _handle_get_openapi(self) -> None:
        host = self.headers.get("Host")
        self._send_json(build_openapi_spec(server_url=f"http://{host}" if host else None))

    @_route_registry.register("GET", r"^/metrics$", auth_required=False)
    def _handle_get_metrics(self) -> None:
        # Metrics — authenticated like every other route unless
        # MN_METRICS_PUBLIC opts in to unauthenticated scraping.
        if not _metrics_public() and not self._check_auth():
            return
        self._send_metrics()

    @_route_registry.register("GET", r"^/info$")
    def _handle_get_info(self) -> None:
        self._send_json(
            {
                "version": __version__,
                "active_tasks": self.queue.active_count,
                "is_started": self.queue.is_started,
                # v0.9.2: orchestration tooling can watch this to detect
                # that the server has begun its graceful shutdown.
                "shutting_down": self._is_shutting_down(),
            }
        )

    @_route_registry.register("GET", r"^/tasks$")
    def _handle_get_tasks(self) -> None:
        query = self._parse_query()
        status_filter = None
        if "status" in query:
            try:
                status_filter = TaskStatus(query["status"])
            except ValueError:
                self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid status: {query['status']}")
                return
        try:
            limit = self._parse_limit(query.get("limit"))
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "Invalid limit")
            return
        tasks = self.queue.list_tasks(status=status_filter, limit=limit)
        self._send_json(
            {
                "tasks": [t.to_summary() for t in tasks],
                "count": len(tasks),
            }
        )

    @_route_registry.register("GET", r"^/tasks/(?P<task_id>[a-f0-9]+)/result$")
    def _handle_get_task_result(self, task_id: str) -> None:
        result = self.queue.get_result(task_id)
        if result is None:
            self._send_error(HTTPStatus.NOT_FOUND, "Result not available")
            return
        self._send_json(result.model_dump(mode="json"))

    @_route_registry.register("GET", r"^/tasks/(?P<task_id>[a-f0-9]+)/artifacts$")
    def _handle_get_task_artifacts(self, task_id: str) -> None:
        task = self.queue.get_task(task_id)
        if task is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Task {task_id} not found")
            return
        rejection = self._artifact_scope_rejection(task)
        if rejection is not None:
            status, message = rejection
            self._send_error(status, message)
            return
        artifacts = self._list_task_artifacts(task)
        self._send_json({"artifacts": artifacts, "count": len(artifacts)})

    @_route_registry.register("GET", r"^/tasks/(?P<task_id>[a-f0-9]+)/download/(?P<filename>.+)$")
    def _handle_get_task_download(self, task_id: str, filename: str) -> None:
        task = self.queue.get_task(task_id)
        if task is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Task {task_id} not found")
            return
        rejection = self._artifact_scope_rejection(task)
        if rejection is not None:
            status, message = rejection
            self._send_error(status, message)
            return
        # v1.3.1: structured audit record for artifact downloads.
        # (``artifact`` rather than ``filename`` — LogRecord reserves the
        # latter and logging raises KeyError on extra-key collisions.)
        self._audit("artifact_download", task_id, artifact=filename)
        self._serve_task_artifact(task, filename)

    @_route_registry.register("GET", r"^/tasks/(?P<task_id>[a-f0-9]+)$")
    def _handle_get_task(self, task_id: str) -> None:
        task = self.queue.get_task(task_id)
        if task is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Task {task_id} not found")
            return
        # v1.3.1: structured audit record for status reads.
        self._audit("task_status", task_id)
        self._send_json(task.model_dump(mode="json"))

    @_route_registry.register("GET", r"^/batches$")
    def _handle_get_batches(self) -> None:
        query = self._parse_query()
        try:
            limit = self._parse_limit(query.get("limit"))
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "Invalid limit")
            return
        batches = self.queue.list_batches(limit=limit)
        self._send_json(
            {
                "batches": [b.model_dump(mode="json") for b in batches],
                "count": len(batches),
            }
        )

    @_route_registry.register("GET", r"^/batches/(?P<batch_id>[a-f0-9]+)$")
    def _handle_get_batch(self, batch_id: str) -> None:
        batch = self.queue.get_batch(batch_id)
        if batch is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Batch {batch_id} not found")
            return
        self._send_json(batch.model_dump(mode="json"))

    @_route_registry.register("GET", r"^/schedules$")
    def _handle_get_schedules(self) -> None:
        schedules = self.scheduler.list_schedules()
        self._send_json(
            {
                "schedules": [s.model_dump(mode="json") for s in schedules],
                "count": len(schedules),
            }
        )

    @_route_registry.register("GET", r"^/schedules/(?P<schedule_id>[a-f0-9]+)/runs$")
    def _handle_get_schedule_runs(self, schedule_id: str) -> None:
        if self.scheduler.get_schedule(schedule_id) is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Schedule {schedule_id} not found")
            return
        runs = self.scheduler.get_runs(schedule_id)
        self._send_json(
            {
                "runs": [r.model_dump(mode="json") for r in runs],
                "count": len(runs),
            }
        )

    @_route_registry.register("GET", r"^/deadletters$")
    def _handle_get_deadletters(self) -> None:
        records = self.dead_letter_store.list()
        self._send_json(
            {
                "deadletters": [r.model_dump(mode="json") for r in records],
                "count": len(records),
            }
        )

    @_route_registry.register("GET", r"^/deadletters/(?P<task_id>[a-f0-9]+)$")
    def _handle_get_deadletter(self, task_id: str) -> None:
        record = self.dead_letter_store.get(task_id)
        if record is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Dead letter {task_id} not found")
            return
        self._send_json(record.model_dump(mode="json"))

    @_route_registry.register("GET", r"^/api/v1/dashboard/summary$")
    def _handle_get_dashboard_summary(self) -> None:
        """Aggregated, versioned dashboard summary (v1.3.1).

        Auth: same rules as every other read route — loopback binds are
        open, non-loopback binds require the API key.
        """
        from .dashboard import build_dashboard_summary

        summary = build_dashboard_summary(
            self.queue,
            self.queue.storage,
            self._dashboard_artifact_store,
        )
        self._send_json(summary)

    @property
    def _dashboard_artifact_store(self) -> Optional[Any]:
        """Artifact store for the dashboard (None → zeroed artifacts).

        Prefers the store configured on the server; falls back to the
        default resolution. Any resolution failure yields None so the
        summary reports zeros instead of erroring.
        """
        configured = getattr(self.server, "_artifact_store", None)
        if configured is not None:
            return configured
        try:
            from .artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:  # noqa: BLE001 — ArtifactStoreError or config errors
            logger.debug("dashboard: artifact store unavailable", exc_info=True)
            return None

    # ── POST route handlers ─────────────────────────────────

    @_route_registry.register("POST", r"^/tasks$")
    def _handle_post_tasks(self) -> None:
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
        except PayloadTooLargeError as e:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(e))
            return
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
            return

        try:
            request = TaskRequest(**body)
        except Exception as e:  # noqa: BLE001
            logger.debug("POST /tasks rejected: invalid TaskRequest payload: %s", e)
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid task request: {e}")
            return

        # v1.3.1: stamp the resolved principal/tenant onto the task and
        # enforce the request's plan (Feature 4 / Feature 5).
        principal, tenant = self._current_identity()
        request.principal = principal
        request.tenant_id = tenant
        plan_name, plan_error = self._resolve_plan_name()
        if plan_error is not None:
            self._send_error(HTTPStatus.BAD_REQUEST, plan_error)
            return
        request.plan = plan_name
        plan_rejection = self._plan_entitlement_rejection(plan_name, [request])
        if plan_rejection is not None:
            status, body = plan_rejection
            self._send_json(body, status=status)
            return

        rejection = self._admission_rejection([request])
        if rejection is not None:
            status, message = rejection
            self._send_error(status, message)
            return

        task_id = self.queue.submit(request)
        # v1.3.1: structured audit record for submissions.
        self._audit("task_submit", task_id)
        self._send_json(
            {"task_id": task_id, "status": "pending"},
            status=HTTPStatus.CREATED,
        )

    @_route_registry.register("POST", r"^/tasks/batch$")
    def _handle_post_tasks_batch(self) -> None:
        try:
            body = self._read_body()
        except PayloadTooLargeError as e:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(e))
            return
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
            return
        try:
            request = BatchRequest(**body)
        except Exception as e:  # noqa: BLE001
            logger.debug("POST /tasks/batch rejected: invalid BatchRequest: %s", e)
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid batch request: {e}")
            return

        # v1.3.1: stamp the resolved principal/tenant onto every member task
        # and enforce the request's plan (Feature 4 / Feature 5).
        principal, tenant = self._current_identity()
        for member in request.requests:
            member.principal = principal
            member.tenant_id = tenant
        plan_name, plan_error = self._resolve_plan_name()
        if plan_error is not None:
            self._send_error(HTTPStatus.BAD_REQUEST, plan_error)
            return
        for member in request.requests:
            member.plan = plan_name
        plan_rejection = self._plan_entitlement_rejection(plan_name, request.requests)
        if plan_rejection is not None:
            status, body = plan_rejection
            self._send_json(body, status=status)
            return

        rejection = self._admission_rejection(request.requests)
        if rejection is not None:
            status, message = rejection
            self._send_error(status, message)
            return

        batch = self.queue.submit_batch(request)
        # v1.3.1: structured audit record for batch submissions.
        self._audit("task_submit_batch", batch.batch_id, count=len(batch.task_ids))
        self._send_json(
            {
                "batch_id": batch.batch_id,
                "status": batch.status.value,
                "task_ids": batch.task_ids,
            },
            status=HTTPStatus.CREATED,
        )

    @_route_registry.register("POST", r"^/deadletters/(?P<task_id>[a-f0-9]+)/replay$")
    def _handle_post_deadletter_replay(self, task_id: str) -> None:
        # Replay a dead letter — resubmits the original request
        # with a fresh task ID.
        try:
            new_task_id = replay_dead_letter(task_id, queue=self.queue)
        except KeyError:
            self._send_error(HTTPStatus.NOT_FOUND, f"Dead letter {task_id} not found")
            return
        self._send_json(
            {
                "original_task_id": task_id,
                "task_id": new_task_id,
            },
            status=HTTPStatus.CREATED,
        )

    @_route_registry.register("POST", r"^/schedules$")
    def _handle_post_schedules(self) -> None:
        try:
            body = self._read_body()
        except PayloadTooLargeError as e:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(e))
            return
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

    # ── DELETE route handlers ───────────────────────────────

    @_route_registry.register("DELETE", r"^/tasks/(?P<task_id>[a-f0-9]+)$")
    def _handle_delete_task(self, task_id: str) -> None:
        cancelled = self.queue.cancel(task_id)
        if cancelled:
            self._send_json({"task_id": task_id, "cancelled": True})
        else:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                f"Task {task_id} not found or already terminal",
            )

    @_route_registry.register("DELETE", r"^/batches/(?P<batch_id>[a-f0-9]+)$")
    def _handle_delete_batch(self, batch_id: str) -> None:
        # Cancel every active task in a batch (v0.9.3)
        if self.queue.cancel_batch(batch_id):
            self._send_json({"batch_id": batch_id, "cancelled": True})
        else:
            self._send_error(HTTPStatus.NOT_FOUND, f"Batch {batch_id} not found")

    @_route_registry.register("DELETE", r"^/schedules/(?P<schedule_id>[a-f0-9]+)$")
    def _handle_delete_schedule(self, schedule_id: str) -> None:
        # Delete a scheduled job (v0.9.3)
        if self.scheduler.cancel_schedule(schedule_id):
            self._send_json({"schedule_id": schedule_id, "deleted": True})
        else:
            self._send_error(HTTPStatus.NOT_FOUND, f"Schedule {schedule_id} not found")

    @_route_registry.register("DELETE", r"^/deadletters/(?P<task_id>[a-f0-9]+)$")
    def _handle_delete_deadletter(self, task_id: str) -> None:
        # Remove a dead letter (v0.9.4)
        removed = self.dead_letter_store.remove(task_id)
        if removed:
            self._send_json({"task_id": task_id, "removed": True})
        else:
            self._send_error(HTTPStatus.NOT_FOUND, f"Dead letter {task_id} not found")

    # ── HTTP method dispatch ────────────────────────────────

    def do_GET(self) -> None:
        """Handle HTTP GET requests."""
        self._dispatch(self._do_GET)

    def _do_GET(self) -> None:
        path = self.path.split("?")[0]
        _route_registry.dispatch(self, "GET", path)

    def do_POST(self) -> None:
        """Handle HTTP POST requests."""
        self._dispatch(self._do_POST)

    def _do_POST(self) -> None:
        path = self.path.split("?")[0]
        _route_registry.dispatch(self, "POST", path)

    def do_DELETE(self) -> None:
        """Handle HTTP DELETE requests."""
        self._dispatch(self._do_DELETE)

    def _do_DELETE(self) -> None:
        path = self.path.split("?")[0]
        _route_registry.dispatch(self, "DELETE", path)

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

    def _parse_limit(self, raw: Optional[str]) -> int:
        """Parse and clamp a ``limit`` query parameter (v0.9.5).

        Returns:
            The clamped value and raises :class:`ValueError` for a
            non-numeric or negative input, so callers can return a 400
            instead of letting :func:`int` raise an unhandled 500.
        """
        if raw is None:
            return 50
        value = int(raw)
        if value < 0:
            raise ValueError("limit must be >= 0")
        return min(value, _MAX_LIST_LIMIT)

    # ── Artifact helpers ────────────────────────────────────

    def _artifact_scope_rejection(self, task: Task) -> Optional[Tuple[int, str]]:
        """Tenant scoping for artifact listing/download (v1.3.1).

        A non-default tenant sees only its own tasks' artifacts. The
        ``"default"`` tenant (and unauthenticated loopback callers, who
        resolve to ``"default"``) keeps the single-tenant backward-compatible
        view of everything — a labeling/scoping MVP, not row isolation.

        Returns:
            A ``(status, message)`` rejection pair, or None when the caller
            may access the task's artifacts.
        """
        _, tenant = self._current_identity()
        if tenant == "default":
            return None
        if (task.tenant_id or "default") != tenant:
            return (
                HTTPStatus.FORBIDDEN,
                "task artifacts belong to another tenant",
            )
        return None

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
            artifacts.append(
                {
                    "filename": info.key,
                    "size": info.size,
                    "path": artifact_location(store, info.key),
                }
            )
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
        api_key: Optional X-API-Key for authenticating requests. On a
            loopback bind (default) the server runs unauthenticated when
            this is None. On a non-loopback bind a None value makes the
            handler reject *anonymous* requests with 401, so a key (e.g.
            ``MN_API_KEY``) is effectively required to serve the
            network.
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
        storage_dir: Optional[Path] = None,
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
        # v1.2: the handler inspects this to decide whether anonymous
        # access is allowed on this bind address (loopback vs public).
        self._server.host = self.host  # type: ignore[attr-defined]
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
            timeout = drain_timeout if drain_timeout is not None else self._drain_timeout
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
