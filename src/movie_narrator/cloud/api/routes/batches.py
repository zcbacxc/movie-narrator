# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Batch route handlers (``/batches`` and ``POST /tasks/batch``).

A mixin consumed by ``movie_narrator.cloud.api.handlers._APIHandler``.
"""

from __future__ import annotations

from http import HTTPStatus

from ...models import BatchRequest
from .._base import logger, _route_registry, PayloadTooLargeError
from ..handlers import _CoreHandler


class _BatchesRoutes(_CoreHandler):
    """``/batches`` route handlers plus ``POST /tasks/batch``."""

    @_route_registry.register("GET", r"^/batches$")
    def _handle_get_batches(self) -> None:
        query = self._parse_query()
        try:
            limit = self._parse_limit(query.get("limit"))
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "Invalid limit")
            return
        batches = self.queue.list_batches(limit=limit)
        self._send_json(
            {
                "batches": [b.model_dump(mode="json") for b in batches],
                "count": len(batches),
            }
        )

    @_route_registry.register("GET", r"^/batches/(?P<batch_id>[a-f0-9]+)$")
    def _handle_get_batch(self, batch_id: str) -> None:
        batch = self.queue.get_batch(batch_id)
        if batch is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Batch {batch_id} not found")
            return
        self._send_json(batch.model_dump(mode="json"))

    @_route_registry.register("POST", r"^/tasks/batch$")
    def _handle_post_tasks_batch(self) -> None:
        try:
            body = self._read_body()
        except PayloadTooLargeError as e:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(e))
            return
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
            return
        try:
            request = BatchRequest(**body)
        except Exception as e:  # noqa: BLE001
            logger.debug("POST /tasks/batch rejected: invalid BatchRequest: %s", e)
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid batch request: {e}")
            return

        # v1.3.1: stamp the resolved principal/tenant onto every member task
        # and enforce the request's plan (Feature 4 / Feature 5).
        principal, tenant = self._current_identity()
        for member in request.requests:
            member.principal = principal
            member.tenant_id = tenant
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
        for member in request.requests:
            member.plan = plan_name
        plan_rejection = self._plan_entitlement_rejection(plan_name, request.requests)
        if plan_rejection is not None:
            status, body = plan_rejection
            self._send_json(body, status=status)
            return

        rejection = self._admission_rejection(request.requests)
        if rejection is not None:
            status, message = rejection
            self._send_error(status, message)
            return

        batch = self.queue.submit_batch(request)
        # v1.3.1: structured audit record for batch submissions.
        self._audit("task_submit_batch", batch.batch_id, count=len(batch.task_ids))
        self._send_json(
            {
                "batch_id": batch.batch_id,
                "status": batch.status.value,
                "task_ids": batch.task_ids,
            },
            status=HTTPStatus.CREATED,
        )

    @_route_registry.register("DELETE", r"^/batches/(?P<batch_id>[a-f0-9]+)$")
    def _handle_delete_batch(self, batch_id: str) -> None:
        # Cancel every active task in a batch (v0.9.3)
        if self.queue.cancel_batch(batch_id):
            self._send_json({"batch_id": batch_id, "cancelled": True})
        else:
            self._send_error(HTTPStatus.NOT_FOUND, f"Batch {batch_id} not found")
