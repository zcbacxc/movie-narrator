# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Task-family route handlers (``/tasks`` and task artifacts).

These handlers are methods on :class:`_TasksRoutes`, a mixin consumed by
``movie_narrator.cloud.api.handlers._APIHandler``. They rely on the shared
plumbing from :class:`~movie_narrator.cloud.api.handlers._CoreHandler` and
the module-level :data:`~movie_narrator.cloud.api._base._route_registry`.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Optional, Tuple

from ...artifact_store import (
    ArtifactNotFoundError,
    UnsafeKeyError,
    artifact_location,
    get_task_artifact_store,
)
from ...models import Task, TaskRequest, TaskStatus
from .._base import logger, _route_registry, PayloadTooLargeError
from ..handlers import _CoreHandler


class _TasksRoutes(_CoreHandler):
    """``/tasks`` route handlers (CRUD, result, artifacts, download)."""

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
        # v1.5.1: opt-in per-tenant token-bucket throttle (submissions only).
        rate_rejection = self._rate_limit_rejection()
        if rate_rejection is not None:
            status, rate_body, retry_after = rate_rejection
            self._send_json(
                rate_body,
                status=status,
                extra_headers={"Retry-After": self._retry_after_header(retry_after)},
            )
            return
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
