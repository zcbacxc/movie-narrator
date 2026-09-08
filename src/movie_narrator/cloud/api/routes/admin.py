# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Admin / ops route handlers (health, info, metrics, dead letters, dashboard).

A mixin consumed by ``movie_narrator.cloud.api.handlers._APIHandler``.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any, Optional

from .... import __version__
from ...dlq import replay_dead_letter  # v0.9.4 — dead letters
from ...health import build_health_payload, build_readiness_payload, parse_deep_flag
from ...openapi import build_openapi_spec
from .._base import logger, _metrics_public, _route_registry
from ..handlers import _CoreHandler


class _AdminRoutes(_CoreHandler):
    """Admin/ops route handlers (probes, metrics, dashboard, dead letters)."""

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
        from ...dashboard import build_dashboard_summary

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
            from ...artifact_store import get_artifact_store

            return get_artifact_store()
        except Exception:  # noqa: BLE001 — ArtifactStoreError or config errors
            logger.debug("dashboard: artifact store unavailable", exc_info=True)
            return None

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

    @_route_registry.register("DELETE", r"^/deadletters/(?P<task_id>[a-f0-9]+)$")
    def _handle_delete_deadletter(self, task_id: str) -> None:
        # Remove a dead letter (v0.9.4)
        removed = self.dead_letter_store.remove(task_id)
        if removed:
            self._send_json({"task_id": task_id, "removed": True})
        else:
            self._send_error(HTTPStatus.NOT_FOUND, f"Dead letter {task_id} not found")
