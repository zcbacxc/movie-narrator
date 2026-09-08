# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""HTTP server wrapping :class:`TaskAPIServer` around a task queue."""

from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from ..artifact_store import (  # v0.8.3 — artifact storage abstraction
    ArtifactStoreError,
    StorageBackend,
    get_artifact_store,
)
from ..dlq import DeadLetterStore
from ..entitlements import resolve_plan
from ..lifecycle import (  # v0.8.3 — artifact lifecycle / TTL cleanup
    ArtifactLifecyclePolicy,
    ArtifactSweeper,
    sweep_interval_from_env,
)
from ..queue import LocalTaskQueue
from ..ratelimit import RateLimiter  # v1.5.1 — per-tenant submission throttling
from ..scheduler import JobScheduler
from ._base import logger
from .handlers import _APIHandler


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
        # v1.5.1: opt-in per-tenant submission rate limiting, resolved
        # from the MN_RATE_LIMIT_* process-env variables (disabled by
        # default). Replaceable via the rate_limiter property (tests).
        self._rate_limiter = RateLimiter.from_env()

    @property
    def queue(self) -> LocalTaskQueue:
        """The underlying task queue."""
        return self._queue

    @property
    def rate_limiter(self) -> RateLimiter:
        """The per-tenant submission rate limiter (v1.5.1)."""
        return self._rate_limiter

    @rate_limiter.setter
    def rate_limiter(self, limiter: RateLimiter) -> None:
        """Replace the rate limiter (used by tests / custom deployments)."""
        self._rate_limiter = limiter
        if self._server is not None:
            self._server.rate_limiter = limiter  # type: ignore[attr-defined]

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
        from ..dlq import get_default_store

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
        # v1.5.1: per-tenant submission rate limiter (disabled by default)
        self._server.rate_limiter = self._rate_limiter  # type: ignore[attr-defined]
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
            ttl_for=self._plan_ttl_resolver(policy),  # v1.4.0 — plan TTL narrowing
        )
        self._sweeper.start()

    def _plan_ttl_resolver(self, policy: ArtifactLifecyclePolicy):
        """Per-artifact TTL resolver narrowing the policy with plan TTLs (v1.4.0).

        Artifact keys laid out as ``<task_id>/<filename>`` (the remote /
        S3 convention, see ``make_task_protection``) are mapped back to
        the owning task; the task's plan ``artifact_ttl_hours`` narrows
        the policy TTL. Keys that do not resolve to a task — e.g. the
        local backend's bare/movie-relative keys — fall back to the
        uniform policy TTL.
        """
        from ..lifecycle import effective_ttl_seconds

        def _ttl_for(info):
            head = info.key.split("/", 1)[0]
            if not head:
                return 0
            try:
                task = self._queue.storage.load(head)
            except Exception:  # noqa: BLE001 — never let the resolver break a sweep
                logger.debug("plan TTL lookup failed for %r", head, exc_info=True)
                return 0
            if task is None:
                return 0
            try:
                plan = resolve_plan(task.plan or "default")
            except KeyError:
                return 0
            return effective_ttl_seconds(plan.artifact_ttl_hours, policy.ttl_seconds)

        return _ttl_for

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
                from ..daemon import graceful_shutdown_timeout

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
