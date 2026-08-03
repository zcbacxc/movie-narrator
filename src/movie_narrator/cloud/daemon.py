# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Worker daemon — runs the API server + task executor (v0.6.1).

The daemon is the server-side component that:
1. Starts a ``LocalTaskQueue`` for actual pipeline execution
2. Exposes a ``TaskAPIServer`` for remote task submission
3. Handles graceful shutdown on SIGINT/SIGTERM

Typical usage::

    from movie_narrator.cloud import run_daemon

    run_daemon(host="127.0.0.1", port=8765, max_workers=4)

    To listen on all interfaces (e.g. inside a container or behind a
    reverse proxy), pass ``host="0.0.0.0"`` explicitly.  Binding to
    loopback by default prevents unintended public exposure of the
    unauthenticated API server.
    """

from __future__ import annotations

import logging
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from .api import TaskAPIServer
from .queue import LocalTaskQueue

logger = logging.getLogger(__name__)

# Hosts considered "loopback" (local-only) and therefore safe to run
# without authentication. Binding to anything else exposes the API to
# the network and requires an ``api_key`` (or an explicit opt-in via
# ``allow_insecure=True``).
_LOOPBACK_HOSTS = frozenset({
    "127.0.0.1",
    "localhost",
    "::1",
    "0:0:0:0:0:0:0:1",  # expanded ::1
})

#: Fallback drain budget when ``MN_GRACEFUL_SHUTDOWN_TIMEOUT`` is unset
#: or unreadable (v0.9.2).
_DEFAULT_DRAIN_TIMEOUT = 30.0


def graceful_shutdown_timeout() -> float:
    """Return the graceful-shutdown drain budget in seconds (v0.9.2).

    Reads ``MN_GRACEFUL_SHUTDOWN_TIMEOUT`` through :func:`get_settings`
    (default :data:`_DEFAULT_DRAIN_TIMEOUT`). Falls back to the default
    when settings cannot be loaded, so a signal handler never raises.
    """
    try:
        from ..config import get_settings

        value = get_settings().graceful_shutdown_timeout
        if value is not None and value > 0:
            return float(value)
    except Exception:  # noqa: BLE001 — a signal path must never raise
        logger.debug("Failed to resolve graceful shutdown timeout", exc_info=True)
    return _DEFAULT_DRAIN_TIMEOUT


def drain_inflight(
    server: TaskAPIServer,
    queue: LocalTaskQueue,
    timeout: float,
) -> None:
    """Drain a server + queue pair during graceful shutdown (v0.9.2).

    Orders the shutdown so that new work stops being accepted first and
    in-flight tasks get a bounded chance to finish:

    1. ``server.begin_drain`` flips the draining flag (``/ready`` and
       ``/info`` report it, ``POST /tasks`` is rejected) and stops the
       artifact sweeper. It does not tear the HTTP loop down, so probes
       keep answering while we wait.
    2. ``queue.shutdown(wait=True, timeout=timeout)`` waits for in-flight
       tasks up to the budget, force-cancelling whatever remains.

    Extracted from the signal handler so tests can exercise the exact
    shutdown ordering without sending real signals.
    """
    logger.info("Entering draining mode (timeout=%.0fs)...", timeout)
    server.begin_drain(timeout)
    queue.shutdown(wait=True, timeout=timeout)
    logger.info("Drain complete.")


def _is_loopback(host: str) -> bool:
    """Return True if ``host`` is a loopback / local-only address."""
    return host.lower() in _LOOPBACK_HOSTS


def run_daemon(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    storage_dir: Optional[Path] = None,
    max_workers: int = 2,
    blocking: bool = True,
    api_key: Optional[str] = None,
    allow_insecure: bool = False,
) -> TaskAPIServer:
    """Start a worker daemon with API server and task queue.

    This is the main entry point for ``mn serve``. It creates a
    ``LocalTaskQueue`` and wraps it with a ``TaskAPIServer`` so that
    remote clients can submit and monitor tasks.

    Args:
        host: Bind address for the API server.
        port: Listen port for the API server.
        storage_dir: Directory for task persistence.
        max_workers: Maximum concurrent task executions.
        blocking: If True, block until interrupted. If False, return
            the running server (for testing).
        api_key: Optional X-API-Key for authenticating requests. When
            None and ``host`` is a loopback address, the server runs
            unauthenticated (backwards compatible). When None and
            ``host`` is a public interface, the daemon refuses to start
            unless ``allow_insecure=True`` is set.
        allow_insecure: When True, allow binding to a non-loopback
            interface without an ``api_key`` (the caller assumes the
            security risk). Defaults to False.

    Returns:
        The running ``TaskAPIServer`` instance.
    """
    # ── Startup guard ──────────────────────────────────────
    # Refuse to expose an unauthenticated API server on a public
    # interface unless the caller explicitly opts in.
    if not _is_loopback(host) and api_key is None and not allow_insecure:
        print(
            "ERROR: refusing to start an unauthenticated API server on a "
            "public interface.\n"
            f"  bind host: {host}\n"
            "To fix, either:\n"
            "  1. Set an API key:  mn serve --public --api-key YOUR_KEY\n"
            "     (or MN_API_KEY in your .env / environment)\n"
            "  2. Listen on loopback only (default, no --public)\n"
            "  3. Explicitly accept the risk:  --insecure\n",
            file=sys.stderr,
        )
        raise SystemExit(1)

    # Create the task queue
    queue = LocalTaskQueue(
        storage_dir=storage_dir,
        max_workers=max_workers,
    )

    # Create and start the API server
    server = TaskAPIServer(
        host=host,
        port=port,
        queue=queue,
        api_key=api_key,
    )

    if blocking:
        # v0.9.2: graceful shutdown — drain in-flight tasks with a bounded
        # timeout before exiting. ``server.begin_drain`` (via
        # ``drain_inflight``) makes /ready + /info report the draining
        # state and rejects new submissions, but deliberately does not
        # stop the HTTP loop from inside the signal handler (that would
        # deadlock: serve_forever runs on the same thread as the handler).
        drain_timeout = graceful_shutdown_timeout()

        def _shutdown(signum, frame):
            sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
            logger.info("Received %s, shutting down...", sig_name)
            drain_inflight(server, queue, drain_timeout)
            # Hard exit: the daemon's worker threads are non-daemon, so a
            # plain ``sys.exit`` would wait for a stuck render to finish.
            logging.shutdown()
            os._exit(0)

        signal.signal(signal.SIGINT, _shutdown)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _shutdown)

        logger.info(
            "Worker daemon starting on %s:%d (max_workers=%d)",
            host, port, max_workers,
        )
        logger.info(
            "Storage: %s",
            storage_dir or Path.home() / ".mn_tasks",
        )
        logger.info("Press Ctrl+C to stop")

        server.start(blocking=True)
    else:
        server.start(blocking=False)
        logger.info(
            "Worker daemon started on %s:%d (non-blocking)",
            host, port,
        )

    return server


class WorkerDaemon:
    """Object-oriented wrapper for the worker daemon.

    Provides start/stop lifecycle management for embedding in
    larger applications.

    Args:
        host: Bind address.
        port: Listen port.
        storage_dir: Task storage directory.
        max_workers: Max concurrent tasks.
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        storage_dir: Optional[Path] = None,
        max_workers: int = 2,
    ) -> None:
        self._host = host
        self._port = port
        self._storage_dir = storage_dir
        self._max_workers = max_workers
        self._server: Optional[TaskAPIServer] = None

    @property
    def is_running(self) -> bool:
        """Whether the daemon is running."""
        return self._server is not None and self._server.is_running

    @property
    def base_url(self) -> str:
        """Base URL of the running server."""
        if self._server:
            return self._server.base_url
        return f"http://{self._host}:{self._port}"

    def start(self, blocking: bool = False) -> None:
        """Start the daemon."""
        if self._server is not None:
            raise RuntimeError("Daemon is already running")

        self._server = TaskAPIServer(
            host=self._host,
            port=self._port,
            storage_dir=self._storage_dir,
            max_workers=self._max_workers,
        )
        self._port = self._server.port  # update actual port
        self._server.start(blocking=blocking)

    def stop(self) -> None:
        """Stop the daemon."""
        if self._server is not None:
            self._server.stop()
            self._server = None

    def __enter__(self) -> "WorkerDaemon":
        self.start(blocking=False)
        return self

    def __exit__(self, *args) -> None:
        self.stop()
