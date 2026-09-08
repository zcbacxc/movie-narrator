# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared infrastructure for the REST API package.

Holds the module-level logger, route patterns, admission/observability
constants, helper functions, :class:`PayloadTooLargeError`, the
:class:`_RouteRegistry` and the shared :data:`_route_registry` that the
route-handler mixins decorate. Splitting these one level below
``movie_narrator.cloud.api`` lets each route family live in its own file
without a circular import, while ``__init__`` re-exports the public names.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Callable, Optional, Tuple

from ..models import TaskRequest

if TYPE_CHECKING:
    from .handlers import _CoreHandler  # noqa: F401  (dispatch signature forward ref)

logger = logging.getLogger("movie_narrator.cloud.api")

# ── Route patterns (for metrics cardinality / _route_template) ──

_TASK_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)$")
_TASK_RESULT_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)/result$")
_TASK_ARTIFACTS_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)/artifacts$")
_TASK_DOWNLOAD_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)/download/(.+)$")
# v0.9.4: dead-letter queue routes
_DEADLETTER_PATTERN = re.compile(r"^/deadletters/([a-f0-9]+)$")
_DEADLETTER_REPLAY_PATTERN = re.compile(r"^/deadletters/([a-f0-9]+)/replay$")

# v1.4.0: webhook delivery records + redelivery
_WEBHOOK_REDELIVER_PATTERN = re.compile(r"^/api/v1/webhooks/redeliver/([a-f0-9]+)$")

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
        "/api/v1/webhooks/deliveries",
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
    (_WEBHOOK_REDELIVER_PATTERN, "/api/v1/webhooks/redeliver/{event_id}"),
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

    def dispatch(self, handler_instance: "_CoreHandler", method: str, path: str) -> None:
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


#: Module-level route registry populated by the handler mixins' decorators.
_route_registry = _RouteRegistry()
