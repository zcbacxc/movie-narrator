# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Health and readiness probes for the task API server (v0.8.2).

This module holds the probe *logic* so that :mod:`movie_narrator.cloud.api`
only has to route requests. It backs two endpoints:

``GET /ready``
    Readiness probe for orchestrators (Kubernetes, Nomad, systemd).
    Runs the **core checks** only — no outbound network traffic — and
    answers ``200`` when every check passes, ``503`` otherwise.

``GET /health?deep=1``
    Deep health check. A superset of ``/ready``: the same core checks
    plus *optional* outbound dependency reachability probes.

A plain ``GET /health`` (no query string) keeps the exact v0.6.1
response shape — ``{"status": "ok"}`` — for backward compatibility.

Core checks
-----------

===========  ==============================================================
``queue``    Task queue is attached and started (accepting work).
``storage``  Task storage directory exists and is writable (verified by
             writing and removing a temporary probe file).
``workers``  Worker pool / executor is alive with a non-zero worker count.
``shutdown`` Server is not in the middle of shutting down.
===========  ==============================================================

Every check reports ``{"status": "pass"|"fail"|"skipped",
"detail": "...", "duration_ms": <float>}``. Checks never raise: an
unexpected exception is converted into a ``fail`` result, so a probe
endpoint can always answer.

Status policy
-------------

- All core checks pass, no failing dependency  → ``"ok"``, HTTP 200.
- All core checks pass, a dependency failed    → ``"degraded"``, HTTP 200.
  The service can still accept and queue work, so orchestrators must not
  restart or evict it; the degradation is reported for observability.
- Any core check failed                        → ``"error"``, HTTP 503.

Dependency probes
-----------------

Outbound probes are **opt-in** via ``MN_HEALTH_DEEP_DEPS=1`` and are
**always skipped when ``CI=1``** so that test runs never touch the
network. When enabled they run concurrently with a
:data:`DEP_PROBE_TIMEOUT`-second timeout each.

Typical usage::

    from movie_narrator.cloud.health import build_readiness_payload

    payload, status_code = build_readiness_payload(queue)
"""

from __future__ import annotations

import logging
import os
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import Future, ThreadPoolExecutor, wait
from http import HTTPStatus
from pathlib import Path
from typing import Any, Callable, Dict, Tuple

from .. import __version__

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────

#: Result status for a check that passed.
STATUS_PASS = "pass"  # nosec B105  # health-check status literal, not a credential
#: Result status for a check that failed.
STATUS_FAIL = "fail"
#: Result status for a check that was deliberately not run.
STATUS_SKIPPED = "skipped"

#: Names of the core checks, in report order.
CORE_CHECK_NAMES: Tuple[str, ...] = ("queue", "storage", "workers", "shutdown")

#: Names of the optional outbound dependency probes, in report order.
DEPENDENCY_NAMES: Tuple[str, ...] = ("llm", "tts", "remote_storage")

#: Env var that opts in to outbound dependency probes in deep health.
DEEP_DEPS_ENV = "MN_HEALTH_DEEP_DEPS"

#: Env var pointing at a remote storage endpoint to probe (optional).
REMOTE_STORAGE_ENV = "MN_REMOTE_STORAGE_URL"

#: Per-dependency probe timeout, in seconds.
DEP_PROBE_TIMEOUT = 2.0

#: Query values that enable deep mode. A bare ``?deep`` (empty value)
#: counts as opting in; ``?deep=0`` does not.
_DEEP_TRUTHY = frozenset({"", "1", "true", "yes", "on"})

#: Env values that count as "enabled".
_ENV_TRUTHY = frozenset({"1", "true", "yes", "on"})


# ── Helpers ────────────────────────────────────────────────


def parse_deep_flag(query: Dict[str, str]) -> bool:
    """Return True when a parsed query string asks for a deep check.

    Accepts ``?deep=1``, ``?deep=true``, ``?deep=yes``, ``?deep=on`` and
    a bare ``?deep``. Anything else (including ``?deep=0``) is False, so
    a plain ``GET /health`` keeps the v0.6.1 shallow behaviour.
    """
    if "deep" not in query:
        return False
    return query["deep"].strip().lower() in _DEEP_TRUTHY


def _env_enabled(name: str) -> bool:
    """Return True when environment variable *name* is set to a truthy value."""
    return os.environ.get(name, "").strip().lower() in _ENV_TRUTHY


def _result(status: str, detail: str, duration_ms: float) -> Dict[str, Any]:
    """Build a single check result entry."""
    return {
        "status": status,
        "detail": detail,
        "duration_ms": round(duration_ms, 3),
    }


def _run_check(check: Callable[[], Tuple[bool, str]]) -> Dict[str, Any]:
    """Run *check*, timing it and swallowing any exception.

    A probe endpoint must never raise, so an unexpected exception from a
    check function is reported as a failed check instead of propagating.
    """
    start = time.perf_counter()
    try:
        ok, detail = check()
    except Exception as e:  # noqa: BLE001 — probes must never raise
        logger.debug("Health check raised", exc_info=True)
        ok, detail = False, f"check raised {type(e).__name__}: {e}"
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    return _result(STATUS_PASS if ok else STATUS_FAIL, detail, elapsed_ms)


# ── Core checks ────────────────────────────────────────────


def _check_queue(queue: Any) -> Tuple[bool, str]:
    """Task queue is attached to the server and accepting work."""
    if queue is None:
        return False, "no task queue attached to the server"
    if not bool(getattr(queue, "is_started", False)):
        return False, "task queue is not started"
    active = getattr(queue, "active_count", 0)
    return True, f"queue started, {active} active task(s)"


def _check_storage(queue: Any) -> Tuple[bool, str]:
    """Task storage directory exists and is writable.

    Writability is proven rather than assumed: a uniquely named probe
    file is written and then removed. The unique name keeps concurrent
    probes (and concurrent workers) from colliding.
    """
    storage = getattr(queue, "storage", None)
    if storage is None:
        return False, "no task storage attached to the queue"
    raw_dir = getattr(storage, "storage_dir", None)
    if raw_dir is None:
        return False, "task storage exposes no storage_dir"

    directory = Path(raw_dir)
    if not directory.is_dir():
        return False, f"storage directory does not exist: {directory}"

    probe = directory / f".mn-health-{os.getpid()}-{uuid.uuid4().hex[:8]}.tmp"
    try:
        probe.write_text("ok", encoding="utf-8")
    except OSError as e:
        return False, f"storage directory is not writable: {e}"
    finally:
        try:
            probe.unlink(missing_ok=True)
        except OSError:  # pragma: no cover — best-effort cleanup
            logger.debug("Failed to remove health probe file %s", probe)
    return True, f"storage directory is writable: {directory}"


def _check_workers(queue: Any) -> Tuple[bool, str]:
    """Worker pool is alive with a non-zero worker count."""
    executor = getattr(queue, "_executor", None)
    if executor is None:
        return False, "worker executor is not running"
    if getattr(executor, "_shutdown", False):
        return False, "worker executor is shutting down"
    max_workers = int(getattr(queue, "_max_workers", 0) or 0)
    if max_workers <= 0:
        return False, f"worker pool has no workers (max_workers={max_workers})"
    return True, f"worker pool alive with {max_workers} worker slot(s)"


def _check_not_shutting_down(shutting_down: bool) -> Tuple[bool, str]:
    """Server is not in the middle of a graceful shutdown."""
    if shutting_down:
        return False, "server is shutting down"
    return True, "server is accepting requests"


def run_core_checks(
    queue: Any,
    *,
    shutting_down: bool = False,
) -> Dict[str, Dict[str, Any]]:
    """Run every core check and return a name → result mapping.

    Args:
        queue: The task queue backing the server. Duck-typed on purpose
            so alternative queue backends work; ``None`` is tolerated and
            simply reported as a failed check.
        shutting_down: Whether the server has begun shutting down.

    Returns:
        A mapping keyed by :data:`CORE_CHECK_NAMES`. Never raises.
    """
    return {
        "queue": _run_check(lambda: _check_queue(queue)),
        "storage": _run_check(lambda: _check_storage(queue)),
        "workers": _run_check(lambda: _check_workers(queue)),
        "shutdown": _run_check(lambda: _check_not_shutting_down(shutting_down)),
    }


def _all_passed(checks: Dict[str, Dict[str, Any]]) -> bool:
    """True when no check in *checks* reports a failure."""
    return all(c.get("status") != STATUS_FAIL for c in checks.values())


# ── Optional dependency probes ─────────────────────────────


def _dependency_targets() -> Dict[str, str]:
    """Resolve the outbound endpoints to probe.

    Returns a mapping of :data:`DEPENDENCY_NAMES` to a URL. Names whose
    dependency is not configured (e.g. the offline ``edge`` TTS backend)
    are omitted and reported as ``skipped`` by the caller.
    """
    targets: Dict[str, str] = {}
    try:
        from ..config import get_settings

        settings: Any = get_settings()
    except Exception:  # noqa: BLE001 — probes must never raise
        logger.debug("Failed to load settings for dependency probes", exc_info=True)
        return targets

    llm_url = getattr(settings, "llm_base_url", "") or ""
    if llm_url:
        targets["llm"] = llm_url

    provider = getattr(settings, "tts_provider", None)
    provider_name = str(getattr(provider, "value", provider) or "")
    if provider_name == "openai":
        tts_url = getattr(settings, "openai_tts_base_url", "") or ""
    elif provider_name == "mimo":
        tts_url = getattr(settings, "mimo_base_url", "") or ""
    else:
        # ``edge`` (the default) talks to Microsoft's websocket service
        # through the edge-tts client; there is no HTTP base URL to probe.
        tts_url = ""
    if tts_url:
        targets["tts"] = tts_url

    remote_storage = os.environ.get(REMOTE_STORAGE_ENV, "").strip()
    if remote_storage:
        targets["remote_storage"] = remote_storage

    return targets


def _probe_url(url: str) -> Tuple[bool, str]:
    """Probe *url* with a HEAD request.

    Any HTTP answer — including 4xx/5xx — proves the dependency is
    reachable; only transport-level errors count as a failure.
    """
    request = urllib.request.Request(url, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=DEP_PROBE_TIMEOUT) as resp:  # nosec B310  # HEAD probe to configured dependency
            return True, f"reachable (HTTP {resp.status})"
    except urllib.error.HTTPError as e:
        return True, f"reachable (HTTP {e.code})"
    except Exception as e:  # noqa: BLE001 — probes must never raise
        return False, f"unreachable: {type(e).__name__}: {e}"


def _skipped_dependencies(reason: str) -> Dict[str, Dict[str, Any]]:
    """Return a fully ``skipped`` dependency report with a shared reason."""
    return {name: _result(STATUS_SKIPPED, reason, 0.0) for name in DEPENDENCY_NAMES}


def run_dependency_checks() -> Dict[str, Dict[str, Any]]:
    """Probe configured outbound dependencies concurrently.

    All probes are started at once and awaited with a shared
    :data:`DEP_PROBE_TIMEOUT` budget, so the whole step costs about one
    timeout in the worst case rather than one per dependency.
    """
    targets = _dependency_targets()
    results: Dict[str, Dict[str, Any]] = {
        name: _result(STATUS_SKIPPED, "not configured", 0.0)
        for name in DEPENDENCY_NAMES
    }
    if not targets:
        return results

    executor = ThreadPoolExecutor(
        max_workers=len(targets),
        thread_name_prefix="mn-health",
    )
    try:
        started: Dict[str, float] = {}
        futures: Dict[str, "Future[Tuple[bool, str]]"] = {}
        for name, url in targets.items():
            started[name] = time.perf_counter()
            futures[name] = executor.submit(_probe_url, url)

        wait(list(futures.values()), timeout=DEP_PROBE_TIMEOUT)

        for name, future in futures.items():
            elapsed_ms = (time.perf_counter() - started[name]) * 1000.0
            if not future.done():
                future.cancel()
                results[name] = _result(
                    STATUS_FAIL,
                    f"probe timed out after {DEP_PROBE_TIMEOUT:g}s",
                    elapsed_ms,
                )
                continue
            try:
                ok, detail = future.result()
            except Exception as e:  # noqa: BLE001 — probes must never raise
                ok, detail = False, f"probe raised {type(e).__name__}: {e}"
            results[name] = _result(
                STATUS_PASS if ok else STATUS_FAIL, detail, elapsed_ms
            )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    return results


def dependency_report() -> Dict[str, Dict[str, Any]]:
    """Return the dependency section of a deep health report.

    Honours the opt-in / CI rules: probes only run when
    ``MN_HEALTH_DEEP_DEPS`` is truthy and ``CI`` is not.
    """
    if _env_enabled("CI"):
        return _skipped_dependencies("skipped: CI=1")
    if not _env_enabled(DEEP_DEPS_ENV):
        return _skipped_dependencies(f"skipped: set {DEEP_DEPS_ENV}=1 to enable")
    return run_dependency_checks()


# ── Payload builders ───────────────────────────────────────


def build_readiness_payload(
    queue: Any,
    *,
    shutting_down: bool = False,
) -> Tuple[Dict[str, Any], int]:
    """Build the ``GET /ready`` response body and HTTP status code.

    Returns:
        ``(payload, status_code)`` — ``200`` when every core check
        passes, ``503`` otherwise. Never raises.
    """
    started = time.perf_counter()
    checks = run_core_checks(queue, shutting_down=shutting_down)
    ready = _all_passed(checks)
    payload: Dict[str, Any] = {
        "ready": ready,
        "checks": checks,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }
    return payload, int(HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE)


def build_health_payload(
    queue: Any,
    *,
    shutting_down: bool = False,
    deep: bool = False,
) -> Tuple[Dict[str, Any], int]:
    """Build the ``GET /health`` response body and HTTP status code.

    When *deep* is False the exact v0.6.1 payload — ``{"status": "ok"}``
    — is returned with HTTP 200; this shape is part of the public API
    contract and must not change.

    When *deep* is True the payload is a superset of ``/ready`` plus the
    dependency report, and the status follows the policy documented in
    the module docstring (``ok`` / ``degraded`` / ``error``).

    Returns:
        ``(payload, status_code)``. Never raises.
    """
    if not deep:
        # v0.6.1 compatibility: byte-for-byte the original response.
        return {"status": "ok"}, int(HTTPStatus.OK)

    started = time.perf_counter()
    checks = run_core_checks(queue, shutting_down=shutting_down)
    dependencies = dependency_report()

    core_ok = _all_passed(checks)
    degraded = not _all_passed(dependencies)

    if not core_ok:
        status, code = "error", int(HTTPStatus.SERVICE_UNAVAILABLE)
    elif degraded:
        # A failing dependency does not stop the server from queueing
        # work, so we stay "up" (200) but advertise the degradation.
        status, code = "degraded", int(HTTPStatus.OK)
    else:
        status, code = "ok", int(HTTPStatus.OK)

    payload: Dict[str, Any] = {
        "status": status,
        "version": __version__,
        "deep": True,
        "ready": core_ok,
        "checks": checks,
        "dependencies": dependencies,
        "duration_ms": round((time.perf_counter() - started) * 1000.0, 3),
    }
    return payload, code
