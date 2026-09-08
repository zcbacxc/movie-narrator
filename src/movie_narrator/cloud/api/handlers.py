# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared request-handler infrastructure for the REST API.

:class:`_CoreHandler` carries the request plumbing every route shares —
body reading, response writing, correlation/observability, auth, tenant/
plan/rate-limit/admission checks, HTTP-method dispatch and query parsing.
The concrete :class:`_APIHandler` combines it with the per-family route
mixins from :mod:`~movie_narrator.cloud.api.routes`.
"""

from __future__ import annotations

import hmac
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..dlq import DeadLetterStore
from ..entitlements import (
    DEFAULT as _DEFAULT_PLAN,
    PLAN_HEADER,
    EntitlementError,
    available_plan_names,
    check_submission,
    default_plan_name,
    resolve_plan,
    submission_resolution,
)
from ..metrics import (
    CONTENT_TYPE_LATEST,
    record_error,
    record_http_request,
    render_prometheus_text,
)
from ..models import TaskRequest
from ..queue import LocalTaskQueue
from ..ratelimit import RateLimiter  # v1.5.1 — per-tenant submission throttling
from ..scheduler import JobScheduler
from ...utils.logging_config import (
    CORRELATION_HEADER,
    REQUEST_ID_HEADER,
    correlation_scope,
    get_correlation_id,
)
from ._base import (
    TENANT_HEADER,
    _MAX_BODY_BYTES,
    _MAX_LIST_LIMIT,
    _api_principal,
    _artifact_size_limit,
    _concurrency_limit,
    _estimate_artifact_bytes,
    _is_loopback_host,
    _route_registry,
    _route_template,
    logger,
    PayloadTooLargeError,
)


class _CoreHandler(BaseHTTPRequestHandler):
    """Request handler shared infra (route handlers live in the mixins)."""

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
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Send a JSON response."""
        body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
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
        from ..dlq import get_default_store

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

    # ── Rate limiting (v1.5.1) ─────────────────────────────

    def _rate_limit_rejection(self) -> Optional[Tuple[int, Dict[str, Any], float]]:
        """Per-tenant submission throttle (v1.5.1, opt-in).

        When the server's :class:`~movie_narrator.cloud.ratelimit.RateLimiter`
        is enabled (``MN_RATE_LIMIT_ENABLED``), each task submission costs
        one token from the caller's tenant bucket (resolved via the
        existing tenant rules — unauthenticated loopback callers share
        the ``"default"`` bucket). Reads are never throttled.

        Returns:
            ``(HTTPStatus.TOO_MANY_REQUESTS, body, retry_after_seconds)``
            when the tenant is out of tokens, else None.
        """
        limiter = getattr(self.server, "rate_limiter", None)
        if not isinstance(limiter, RateLimiter) or not limiter.enabled:
            return None
        _, tenant = self._current_identity()
        retry_after = limiter.try_acquire(tenant)
        if retry_after <= 0.0:
            return None
        return (
            HTTPStatus.TOO_MANY_REQUESTS,
            {"error": "rate_limited", "retry_after_s": round(retry_after, 2)},
            retry_after,
        )

    @staticmethod
    def _retry_after_header(retry_after: float) -> str:
        """Format an HTTP ``Retry-After`` (integer seconds, min 1)."""
        whole = int(retry_after)
        return str(whole + 1 if retry_after > whole else max(whole, 1))

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


from .routes import _AdminRoutes, _BatchesRoutes, _SchedulesRoutes, _TasksRoutes, _WebhooksRoutes  # noqa: E402


class _APIHandler(_TasksRoutes, _BatchesRoutes, _SchedulesRoutes, _WebhooksRoutes, _AdminRoutes):
    """Combined API request handler: shared plumbing + every route family."""
