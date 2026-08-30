# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Dashboard summary — a stable, versioned aggregation for monitoring UIs (v1.3.1).

:func:`build_dashboard_summary` aggregates task, queue, artifact and plan
state into a single JSON-serializable dict with a **versioned schema**
(``schema_version: 1``), so dashboards can rely on the key set across
patch releases. It is read-only: nothing is mutated, and a missing or
failing artifact store simply contributes zeros.

Schema (v1)::

    {
      "schema_version": 1,
      "generated_at": "<ISO-8601 UTC>",
      "tasks": {
        "total": int,
        "by_status": {"pending": int, "running": int, ...},
        "recent": [<=10 × {task_id, movie, status, progress,
                           tenant_id, plan, created_at}]
      },
      "queue": {"depth": int, "active": int, "max_workers": int},
      "artifacts": {"count": int, "total_bytes": int},
      "plans": {"default": str, "configured": [str, ...]}
    }

Typical usage (API layer)::

    summary = build_dashboard_summary(queue, queue.storage, artifact_store)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

from .entitlements import available_plan_names, default_plan_name
from .models import ACTIVE_STATES, Task, TaskStatus

logger = logging.getLogger(__name__)

#: Bump when the schema changes incompatibly; consumers gate on this.
SCHEMA_VERSION = 1

#: Maximum number of recent task views included.
RECENT_TASKS_LIMIT = 10


def _utc_now_iso() -> str:
    """
    Returns:
        Current UTC time in ISO format.
    """
    return datetime.now(timezone.utc).isoformat()


def _int_or(value: Any, fallback: int) -> int:
    """Coerce *value* to int when possible, else *fallback*."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _queue_depth(queue: Any, storage: Any) -> int:
    """Pending-task count — the classic "queue depth" gauge."""
    try:
        return _int_or(storage.count(TaskStatus.PENDING), 0)
    except Exception:  # noqa: BLE001 — summary must never raise
        logger.debug("dashboard: failed to read queue depth", exc_info=True)
        return 0


def _queue_active(queue: Any, by_status: Dict[str, int]) -> int:
    """Active (pending/running/retrying) task count.

    Prefers the queue's own O(1) counter when it exposes one; falls back
    to the status aggregation for duck-typed or stopped queues.
    """
    try:
        active = getattr(queue, "active_count", None)
        if active is not None:
            return _int_or(active, 0)
    except Exception:  # noqa: BLE001 — summary must never raise
        logger.debug("dashboard: failed to read active count", exc_info=True)
    return sum(by_status[s.value] for s in ACTIVE_STATES)


def _queue_max_workers(queue: Any) -> int:
    """Configured worker parallelism (0 when the queue does not expose it)."""
    return _int_or(getattr(queue, "_max_workers", None), 0)


def _artifact_totals(artifact_store: Any) -> Dict[str, int]:
    """Count/size totals from the artifact store (zeros when unavailable)."""
    if artifact_store is None:
        return {"count": 0, "total_bytes": 0}
    try:
        infos = list(artifact_store.list())
    except Exception:  # noqa: BLE001 — store hiccups must not break the summary
        logger.debug("dashboard: artifact store unavailable", exc_info=True)
        return {"count": 0, "total_bytes": 0}
    return {
        "count": len(infos),
        "total_bytes": _int_or(sum(info.size for info in infos), 0),
    }


def _recent_task_view(task: Task) -> Dict[str, Any]:
    """Minimal, stable view of one task for the dashboard."""
    return {
        "task_id": task.id,
        "movie": task.request.movie_name,
        "status": task.status.value,
        "progress": (
            float(task.progress.percentage) if task.progress is not None else 0.0
        ),
        "tenant_id": task.tenant_id or "default",
        "plan": task.plan or "default",
        "created_at": task.created_at,
    }


def build_dashboard_summary(
    queue: Any,
    storage: Any,
    artifact_store: Any = None,
) -> Dict[str, Any]:
    """Aggregate queue/storage/artifact state into a versioned summary.

    Args:
        queue: A :class:`~movie_narrator.cloud.queue.LocalTaskQueue` (or a
            duck-typed replacement exposing ``active_count`` and
            ``_max_workers``). Read-only access.
        storage: A :class:`~movie_narrator.cloud.storage.TaskStorage` (or
            duck-typed: ``count(status=None)``, ``list_tasks(limit=...)``).
        artifact_store: Optional
            :class:`~movie_narrator.cloud.artifact_store.StorageBackend`;
            when None (or it fails) the artifact totals are zeros.

    Returns:
        A JSON-serializable dict with ``schema_version`` :data:`SCHEMA_VERSION`.
    """
    by_status: Dict[str, int] = {}
    for status in TaskStatus:
        try:
            by_status[status.value] = _int_or(storage.count(status), 0)
        except Exception:  # noqa: BLE001 — summary must never raise
            logger.debug("dashboard: failed to count %s", status.value, exc_info=True)
            by_status[status.value] = 0
    try:
        total = _int_or(storage.count(), 0)
    except Exception:  # noqa: BLE001 — summary must never raise
        logger.debug("dashboard: failed to count tasks", exc_info=True)
        total = 0
    try:
        recent_tasks = list(storage.list_tasks(limit=RECENT_TASKS_LIMIT))
    except Exception:  # noqa: BLE001 — summary must never raise
        logger.debug("dashboard: failed to list recent tasks", exc_info=True)
        recent_tasks = []

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _utc_now_iso(),
        "tasks": {
            "total": total,
            "by_status": by_status,
            "recent": [_recent_task_view(t) for t in recent_tasks],
        },
        "queue": {
            "depth": _queue_depth(queue, storage),
            "active": _queue_active(queue, by_status),
            "max_workers": _queue_max_workers(queue),
        },
        "artifacts": _artifact_totals(artifact_store),
        "plans": {
            "default": default_plan_name(),
            "configured": list(available_plan_names()),
        },
    }


__all__ = ["SCHEMA_VERSION", "RECENT_TASKS_LIMIT", "build_dashboard_summary"]
