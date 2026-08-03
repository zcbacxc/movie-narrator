# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Remote task queue — delegates to a remote API server (v0.6.1).

Implements the ``TaskQueue`` protocol by making HTTP requests to a
remote ``TaskAPIServer`` instance. This allows CLI clients to submit
and monitor tasks on a remote worker machine without any local
pipeline execution.

Uses Python stdlib ``urllib.request`` — no additional dependencies.

Typical usage::

    from movie_narrator.cloud import RemoteTaskQueue, TaskRequest

    queue = RemoteTaskQueue("http://worker-host:8765")
    task_id = queue.submit(TaskRequest(movie_name="飞驰人生"))
    result = queue.wait(task_id, timeout=600)
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from typing import List, Optional

from .models import (
    Batch,
    BatchRequest,
    Task,
    TaskProgress,
    TaskRequest,
    TaskResult,
    TaskStatus,
)

logger = logging.getLogger(__name__)

# Default polling interval for ``wait()``
_POLL_INTERVAL: float = 1.0

# Default request timeout (seconds)
_REQUEST_TIMEOUT: float = 30.0


class RemoteQueueError(Exception):
    """Error communicating with the remote task queue."""


class RemoteTaskQueue:
    """Client for a remote ``TaskAPIServer``.

    Implements the same ``TaskQueue`` protocol as ``LocalTaskQueue``
    but delegates all operations to a remote HTTP endpoint. No local
    pipeline execution occurs — the client only submits requests and
    polls for results.

    Args:
        base_url: Base URL of the remote API server
            (e.g. ``http://worker-host:8765``).
        timeout: Request timeout in seconds.
        api_key: Optional API key for authentication (sent as
            ``X-API-Key`` header).
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = _REQUEST_TIMEOUT,
        api_key: Optional[str] = None,
    ) -> None:
        # Normalize base URL (strip trailing slash)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._api_key = api_key

    # ── HTTP helpers ────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        body: Optional[dict] = None,
    ) -> dict:
        """Make an HTTP request to the remote server.

        Returns the parsed JSON response. Raises ``RemoteQueueError``
        on network or protocol errors.
        """
        url = f"{self._base_url}{path}"
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self._api_key:
            headers["X-API-Key"] = self._api_key

        data = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers=headers,
            method=method,
        )

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
                return json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as e:
            # Parse error response
            try:
                err_body = json.loads(e.read().decode("utf-8"))
                msg = err_body.get("error", str(e))
            except Exception:  # noqa: BLE001
                msg = str(e)
            raise RemoteQueueError(f"HTTP {e.code}: {msg}") from e
        except urllib.error.URLError as e:
            raise RemoteQueueError(f"Connection error: {e.reason}") from e
        except Exception as e:  # noqa: BLE001
            raise RemoteQueueError(f"Request failed: {e}") from e

    # ── TaskQueue protocol ──────────────────────────────────

    def submit(self, request: TaskRequest) -> str:
        """Submit a task to the remote server.

        Returns the task ID assigned by the server.
        """
        body = request.model_dump(mode="json", exclude_none=True)
        resp = self._request("POST", "/tasks", body=body)
        task_id = resp.get("task_id")
        if not task_id:
            raise RemoteQueueError("Server did not return a task_id")
        return task_id

    def get_task(self, task_id: str) -> Optional[Task]:
        """Get full task details from the remote server."""
        try:
            resp = self._request("GET", f"/tasks/{task_id}")
            return Task(**resp)
        except RemoteQueueError as e:
            if "404" in str(e):
                return None
            raise

    def get_status(self, task_id: str) -> Optional[TaskStatus]:
        """Get task status from the remote server."""
        task = self.get_task(task_id)
        return task.status if task else None

    def get_progress(self, task_id: str) -> Optional[TaskProgress]:
        """Get task progress from the remote server."""
        task = self.get_task(task_id)
        return task.progress if task else None

    def get_result(self, task_id: str) -> Optional[TaskResult]:
        """Get task result from the remote server."""
        try:
            resp = self._request("GET", f"/tasks/{task_id}/result")
            return TaskResult(**resp)
        except RemoteQueueError as e:
            if "404" in str(e):
                return None
            raise

    def cancel(self, task_id: str) -> bool:
        """Request cancellation of a remote task.

        Returns True if the cancellation was accepted by the server.
        """
        try:
            resp = self._request("DELETE", f"/tasks/{task_id}")
            return resp.get("cancelled", False)
        except RemoteQueueError as e:
            if "404" in str(e):
                return False
            raise

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 50,
    ) -> List[Task]:
        """List tasks from the remote server."""
        params = f"?limit={limit}"
        if status:
            params += f"&status={status.value}"
        resp = self._request("GET", f"/tasks{params}")
        # The server returns summaries; fetch full details for each
        # (summaries are sufficient for listing, but we return Task
        # objects for protocol compliance)
        tasks: List[Task] = []
        for summary in resp.get("tasks", []):
            # Reconstruct a minimal Task from summary
            task = self.get_task(summary.get("id", ""))
            if task:
                tasks.append(task)
        return tasks

    def wait(
        self,
        task_id: str,
        timeout: Optional[float] = None,
        poll_interval: float = _POLL_INTERVAL,
    ) -> Optional[TaskResult]:
        """Block until the remote task reaches a terminal state.

        Polls the remote server using exponential backoff starting at
        ``poll_interval`` seconds. The interval grows by 1.5x each
        iteration and is capped at 10 seconds to avoid excessive
        delays for long-running tasks.

        Returns the ``TaskResult`` if the task completed, or None
        if not found, cancelled, or timed out.
        """
        start = time.time()
        interval = poll_interval
        while True:
            task = self.get_task(task_id)
            if task is None:
                return None
            if task.is_terminal:
                return task.result if task.result else None

            if timeout is not None and (time.time() - start) > timeout:
                return None

            time.sleep(min(interval, 10.0))
            interval = min(interval * 1.5, 10.0)

    # ── Batch operations (v0.9.3) ──────────────────────────

    def submit_batch(self, request: BatchRequest) -> Batch:
        """Submit a batch of tasks to the remote server.

        Returns the full ``Batch`` record assigned by the server.
        """
        body = request.model_dump(mode="json", exclude_none=True)
        resp = self._request("POST", "/tasks/batch", body=body)
        batch_id = resp.get("batch_id")
        if not batch_id:
            raise RemoteQueueError("Server did not return a batch_id")
        batch = self.get_batch(batch_id)
        if batch is None:
            raise RemoteQueueError("Server did not return the created batch")
        return batch

    def get_batch(self, batch_id: str) -> Optional[Batch]:
        """Get a batch with aggregated progress from the remote server."""
        try:
            resp = self._request("GET", f"/batches/{batch_id}")
            return Batch(**resp)
        except RemoteQueueError as e:
            if "404" in str(e):
                return None
            raise

    def list_batches(self, limit: int = 50) -> List[Batch]:
        """List batches from the remote server, newest first."""
        resp = self._request("GET", f"/batches?limit={limit}")
        return [Batch(**b) for b in resp.get("batches", [])]

    def cancel_batch(self, batch_id: str) -> bool:
        """Request cancellation of every active task in a remote batch."""
        try:
            resp = self._request("DELETE", f"/batches/{batch_id}")
            return resp.get("cancelled", False)
        except RemoteQueueError as e:
            if "404" in str(e):
                return False
            raise

    def shutdown(self, wait: bool = True) -> None:
        """No-op for remote queue — no local resources to clean up."""
        pass

    # ── Health check ────────────────────────────────────────

    def health_check(self) -> bool:
        """Check if the remote server is reachable.

        Returns True if the server responds to /health.
        """
        try:
            resp = self._request("GET", "/health")
            return resp.get("status") == "ok"
        except RemoteQueueError:
            return False

    def server_info(self) -> dict:
        """Get server info (version, active tasks, etc.)."""
        return self._request("GET", "/info")

    # ── Properties ──────────────────────────────────────────

    @property
    def base_url(self) -> str:
        """The base URL of the remote server."""
        return self._base_url
