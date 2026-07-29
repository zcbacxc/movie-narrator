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
    GET    /health                      — health check
    GET    /info                        — server info (version, worker count)

Typical usage::

    from movie_narrator.cloud import TaskAPIServer

    server = TaskAPIServer(host="0.0.0.0", port=8765)
    server.start(blocking=True)
"""

from __future__ import annotations

import json
import logging
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, Optional

from .models import Task, TaskRequest, TaskStatus
from .queue import LocalTaskQueue

logger = logging.getLogger(__name__)

# ── Route patterns ─────────────────────────────────────────

_TASK_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)$")
_TASK_RESULT_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)/result$")
_TASK_ARTIFACTS_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)/artifacts$")
_TASK_DOWNLOAD_PATTERN = re.compile(r"^/tasks/([a-f0-9]+)/download/(.+)$")


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

    @property
    def queue(self) -> LocalTaskQueue:
        """Access the task queue from the server instance."""
        return self.server.queue  # type: ignore[attr-defined]

    # ── GET ─────────────────────────────────────────────────

    def do_GET(self) -> None:
        path = self.path.split("?")[0]

        # Health check
        if path == "/health":
            self._send_json({"status": "ok"})
            return

        # Server info
        if path == "/info":
            self._send_json({
                "version": "0.6.1",
                "active_tasks": self.queue.active_count,
                "is_started": self.queue.is_started,
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

        self._send_error(HTTPStatus.NOT_FOUND, f"Unknown path: {path}")

    # ── POST ────────────────────────────────────────────────

    def do_POST(self) -> None:
        path = self.path.split("?")[0]

        if path == "/tasks":
            try:
                body = self._read_body()
            except ValueError as e:
                self._send_error(HTTPStatus.BAD_REQUEST, str(e))
                return

            try:
                request = TaskRequest(**body)
            except Exception as e:
                self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid task request: {e}")
                return

            task_id = self.queue.submit(request)
            self._send_json(
                {"task_id": task_id, "status": "pending"},
                status=HTTPStatus.CREATED,
            )
            return

        self._send_error(HTTPStatus.NOT_FOUND, f"Unknown path: {path}")

    # ── DELETE ──────────────────────────────────────────────

    def do_DELETE(self) -> None:
        path = self.path.split("?")[0]
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
        """List available output files for a task."""
        from pathlib import Path

        result = task.result
        if not result or not result.output_dir:
            return []

        output_dir = Path(result.output_dir)
        if not output_dir.exists():
            return []

        artifacts = []
        for item in sorted(output_dir.iterdir()):
            if item.is_file() and not item.name.startswith("."):
                artifacts.append({
                    "filename": item.name,
                    "size": item.stat().st_size,
                    "path": str(item),
                })
        return artifacts

    def _serve_task_artifact(self, task: Task, filename: str) -> None:
        """Serve a file from the task's output directory."""
        from pathlib import Path
        from urllib.parse import unquote

        filename = unquote(filename)
        result = task.result
        if not result or not result.output_dir:
            self._send_error(HTTPStatus.NOT_FOUND, "Task has no output directory")
            return

        output_dir = Path(result.output_dir)
        file_path = output_dir / filename

        # Security: prevent path traversal
        try:
            file_path = file_path.resolve()
            output_dir = output_dir.resolve()
            if not str(file_path).startswith(str(output_dir)):
                self._send_error(HTTPStatus.FORBIDDEN, "Access denied")
                return
        except Exception:
            self._send_error(HTTPStatus.BAD_REQUEST, "Invalid filename")
            return

        if not file_path.exists() or not file_path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, f"File '{filename}' not found")
            return

        # Determine content type
        content_type = "application/octet-stream"
        ext = file_path.suffix.lower()
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

        file_size = file_path.stat().st_size
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.end_headers()

        with open(file_path, "rb") as f:
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
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        queue: Optional[LocalTaskQueue] = None,
        storage_dir=None,
        max_workers: int = 2,
    ) -> None:
        self.host = host
        self.port = port
        self._owns_queue = queue is None
        self._queue = queue or LocalTaskQueue(
            storage_dir=storage_dir,
            max_workers=max_workers,
        )
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def queue(self) -> LocalTaskQueue:
        """The underlying task queue."""
        return self._queue

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

        self._server = ThreadingHTTPServer(
            (self.host, self.port),
            _APIHandler,
        )
        self._server.queue = self._queue  # type: ignore[attr-defined]
        # Update actual port (in case port=0 was used)
        self.port = self._server.server_address[1]

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

    def stop(self) -> None:
        """Stop the HTTP server."""
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

        if self._owns_queue:
            self._queue.shutdown()

    def __enter__(self) -> "TaskAPIServer":
        self.start(blocking=False)
        return self

    def __exit__(self, *args: Any) -> None:
        self.stop()
