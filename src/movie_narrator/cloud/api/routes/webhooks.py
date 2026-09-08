# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Webhook route handlers (``/api/v1/webhooks/*``).

A mixin consumed by ``movie_narrator.cloud.api.handlers._APIHandler``.
"""

from __future__ import annotations

from http import HTTPStatus

from ...webhooks import (
    DELIVERY_LOG_FILENAME,
    EVENT_LOG_FILENAME,
    load_webhook_event,
    read_delivery_records,
)
from .._base import _route_registry
from ..handlers import _CoreHandler


class _WebhooksRoutes(_CoreHandler):
    """``/api/v1/webhooks/*`` route handlers (deliveries + redeliver)."""

    @_route_registry.register("GET", r"^/api/v1/webhooks/deliveries$")
    def _handle_get_webhook_deliveries(self) -> None:
        """Webhook delivery-attempt records, newest first (v1.4.0).

        Query parameters: ``event_id`` (exact match), ``task_id`` (exact
        match; pre-v1.4.0 records carry no task_id and never match) and
        ``limit`` (default 50, clamped like every list endpoint).

        Auth: same rules as every other read route — loopback binds are
        open, non-loopback binds require the API key.
        """
        query = self._parse_query()
        try:
            limit = self._parse_limit(query.get("limit"))
        except ValueError:
            self._send_error(HTTPStatus.BAD_REQUEST, "Invalid limit")
            return
        log_path = self._webhook_delivery_log_path()
        records = read_delivery_records(
            log_path,
            event_id=(query.get("event_id") or "").strip() or None,
            task_id=(query.get("task_id") or "").strip() or None,
            limit=limit,
        )
        self._send_json({"deliveries": records, "count": len(records)})

    def _webhook_delivery_log_path(self):
        """Path of the delivery log next to the task store (v1.4.0)."""
        return self.queue.storage.storage_dir / DELIVERY_LOG_FILENAME

    def _webhook_event_log_path(self):
        """Path of the raw-event log next to the task store (v1.4.0)."""
        return self.queue.storage.storage_dir / EVENT_LOG_FILENAME

    @_route_registry.register(
        "POST", r"^/api/v1/webhooks/redeliver/(?P<event_id>[a-f0-9]+)$"
    )
    def _handle_post_webhook_redeliver(self, event_id: str) -> None:
        """Re-dispatch the original webhook event (v1.4.0).

        The stored payload (``webhook_events.jsonl``) is re-posted through
        the normal dispatcher path: same event id (consumers deduplicate),
        re-signed with the configured secret, retries and delivery
        records as for any fresh event. Auth follows the admin-route
        rules (loopback open, non-loopback requires the API key); a
        non-default caller tenant may only redeliver its own events.
        """
        dispatcher = self.queue.webhooks
        if dispatcher is None or not dispatcher.enabled:
            self._send_error(
                HTTPStatus.NOT_FOUND,
                "webhook redelivery unavailable: MN_WEBHOOK_URLS is not configured",
            )
            return
        payload = load_webhook_event(self._webhook_event_log_path(), event_id)
        if payload is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Webhook event {event_id} not found")
            return
        _, tenant = self._current_identity()
        event_tenant = str(payload.get("tenant_id") or "default")
        if tenant != "default" and event_tenant != tenant:
            self._send_error(
                HTTPStatus.FORBIDDEN,
                "webhook event belongs to another tenant",
            )
            return
        queued = dispatcher.redeliver(payload)
        # v1.4.0: structured audit record for redeliveries.
        self._audit(
            "webhook_redeliver",
            str(payload.get("task_id") or ""),
            event_id=event_id,
        )
        self._send_json(
            {"event_id": event_id, "redelivered": bool(queued)},
            status=HTTPStatus.ACCEPTED,
        )
