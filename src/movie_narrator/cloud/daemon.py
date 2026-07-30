# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Worker daemon — runs the API server + task executor (v0.6.1).

The daemon is the server-side component that:
1. Starts a ``LocalTaskQueue`` for actual pipeline execution
2. Exposes a ``TaskAPIServer`` for remote task submission
3. Handles graceful shutdown on SIGINT/SIGTERM

Typical usage::

    from movie_narrator.cloud import run_daemon

    run_daemon(host="0.0.0.0", port=8765, max_workers=4)
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path
from typing import Optional

from .api import TaskAPIServer
from .queue import LocalTaskQueue

logger = logging.getLogger(__name__)


def run_daemon(
    host: str = "0.0.0.0",
    port: int = 8765,
    *,
    storage_dir: Optional[Path] = None,
    max_workers: int = 2,
    blocking: bool = True,
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

    Returns:
        The running ``TaskAPIServer`` instance.
    """
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
    )

    if blocking:
        # Set up signal handlers for graceful shutdown
        def _shutdown(signum, frame):
            sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else str(signum)
            logger.info("Received %s, shutting down...", sig_name)
            server.stop()
            sys.exit(0)

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
        host: str = "0.0.0.0",
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
