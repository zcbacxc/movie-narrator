# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scheduled-job route handlers (``/schedules``).

A mixin consumed by ``movie_narrator.cloud.api.handlers._APIHandler``.
"""

from __future__ import annotations

from http import HTTPStatus

from ...models import TaskRequest
from ...scheduler import ScheduleError
from .._base import logger, _route_registry, PayloadTooLargeError
from ..handlers import _CoreHandler


class _SchedulesRoutes(_CoreHandler):
    """``/schedules`` route handlers (CRUD + run history)."""

    @_route_registry.register("GET", r"^/schedules$")
    def _handle_get_schedules(self) -> None:
        schedules = self.scheduler.list_schedules()
        self._send_json(
            {
                "schedules": [s.model_dump(mode="json") for s in schedules],
                "count": len(schedules),
            }
        )

    @_route_registry.register("GET", r"^/schedules/(?P<schedule_id>[a-f0-9]+)/runs$")
    def _handle_get_schedule_runs(self, schedule_id: str) -> None:
        if self.scheduler.get_schedule(schedule_id) is None:
            self._send_error(HTTPStatus.NOT_FOUND, f"Schedule {schedule_id} not found")
            return
        runs = self.scheduler.get_runs(schedule_id)
        self._send_json(
            {
                "runs": [r.model_dump(mode="json") for r in runs],
                "count": len(runs),
            }
        )

    @_route_registry.register("POST", r"^/schedules$")
    def _handle_post_schedules(self) -> None:
        try:
            body = self._read_body()
        except PayloadTooLargeError as e:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, str(e))
            return
        except ValueError as e:
            self._send_error(HTTPStatus.BAD_REQUEST, str(e))
            return
        try:
            cron = body.get("cron")
            if not cron or not isinstance(cron, str):
                raise ScheduleError("'cron' must be a 5-field cron string")
            task_request = TaskRequest(**body.get("task_request", {}))
            enabled = body.get("enabled", True)
            schedule = self.scheduler.register_schedule(
                cron,
                task_request,
                enabled=bool(enabled),
            )
        except ScheduleError as e:
            logger.debug("POST /schedules rejected: %s", e)
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid schedule: {e}")
            return
        except Exception as e:  # noqa: BLE001
            logger.debug("POST /schedules rejected: invalid payload: %s", e)
            self._send_error(HTTPStatus.BAD_REQUEST, f"Invalid schedule: {e}")
            return
        self._send_json(
            schedule.model_dump(mode="json"),
            status=HTTPStatus.CREATED,
        )

    @_route_registry.register("DELETE", r"^/schedules/(?P<schedule_id>[a-f0-9]+)$")
    def _handle_delete_schedule(self, schedule_id: str) -> None:
        # Delete a scheduled job (v0.9.3)
        if self.scheduler.cancel_schedule(schedule_id):
            self._send_json({"schedule_id": schedule_id, "deleted": True})
        else:
            self._send_error(HTTPStatus.NOT_FOUND, f"Schedule {schedule_id} not found")
