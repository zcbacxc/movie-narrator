# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Dead-letter queue — failed task inspection and replay (v0.9.4).

When a task exhausts its retry budget (or fails non-retryably) and the
request has ``enable_dlq`` set (the default), the worker routes it to
the dead-letter queue instead of marking it a plain ``FAILED``:

- ``TaskStatus.DEAD`` is a terminal state, so every consumer of
  ``TERMINAL_STATES`` (``wait()``, ``is_terminal``, API result polling)
  keeps working without changes.
- A :class:`DeadLetterRecord` is persisted under
  ``~/.mn_tasks/deadletters/<task_id>.json`` so the failure can be
  inspected, deleted, or replayed.

Typical usage::

    from movie_narrator.cloud.dlq import DeadLetterStore, replay_dead_letter

    store = DeadLetterStore()                     # ~/.mn_tasks/deadletters
    for record in store.list():
        print(record.task_id, record.reason)
    new_task_id = replay_dead_letter(record.task_id, queue=queue)
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from pydantic import BaseModel

from .models import TaskRequest

if TYPE_CHECKING:
    from .queue import TaskQueue

logger = logging.getLogger(__name__)

#: Default directory for dead-letter records — a sibling of the default
#: task index directory (``~/.mn_tasks/tasks.json``).
DEFAULT_DEADLETTER_DIR = Path.home() / ".mn_tasks" / "deadletters"


# ── Record ─────────────────────────────────────────────────


class DeadLetterRecord(BaseModel):
    """A persisted record of a task that exhausted its retries (v0.9.4).

    Attributes:
        task_id: ID of the original task that died.
        original_request: The ``TaskRequest`` the task was submitted
            with. Replayed tasks are rebuilt from this verbatim.
        reason: Final exception summary (``Task.last_error``).
        failed_at: ISO-8601 UTC timestamp of the routing.
        attempts: Total number of execution attempts before giving up.
        replay_count: How many times this record has been replayed.
    """

    task_id: str
    original_request: TaskRequest
    reason: str
    failed_at: str
    attempts: int
    replay_count: int = 0


# ── Store ──────────────────────────────────────────────────


class DeadLetterStore:
    """JSON-file persistence for dead-letter records (v0.9.4).

    Each record lives in its own file at ``<storage_dir>/<task_id>.json``
    so list/get/remove/replay are cheap file operations. Writes are
    atomic (temp file + rename) and guarded by a re-entrant lock so
    concurrent worker threads cannot corrupt the store.

    Args:
        storage_dir: Directory for dead-letter records. Defaults to
            ``~/.mn_tasks/deadletters``.
    """

    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        self.storage_dir = (
            Path(storage_dir) if storage_dir else DEFAULT_DEADLETTER_DIR
        )
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    def _path(self, task_id: str) -> Path:
        """
        Returns:
            The JSON path for *task_id*.
        """
        return self.storage_dir / f"{task_id}.json"

    def save(self, record: DeadLetterRecord) -> None:
        """Persist a dead-letter record (overwrites on same task_id)."""
        with self._lock:
            target = self._path(record.task_id)
            tmp = target.with_suffix(".json.tmp")
            tmp.write_text(record.model_dump_json(indent=2), encoding="utf-8")
            tmp.replace(target)

    def get(self, task_id: str) -> Optional[DeadLetterRecord]:
        """Load a record by original task ID; ``None`` if not found."""
        path = self._path(task_id)
        if not path.exists():
            return None
        try:
            return DeadLetterRecord.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.warning(
                "Failed to load dead-letter record %s: %s", task_id, e
            )
            return None

    def list(self) -> List[DeadLetterRecord]:
        """List all dead-letter records, newest ``failed_at`` first."""
        records: List[DeadLetterRecord] = []
        with self._lock:
            for path in self.storage_dir.glob("*.json"):
                if path.name.endswith(".tmp"):
                    continue
                record = self.get(path.stem)
                if record is not None:
                    records.append(record)
        records.sort(key=lambda r: r.failed_at, reverse=True)
        return records

    def remove(self, task_id: str) -> bool:
        """Delete a record. Returns True if it existed."""
        with self._lock:
            path = self._path(task_id)
            if not path.exists():
                return False
            path.unlink()
            return True

    def replay(self, record: DeadLetterRecord, queue: TaskQueue) -> str:
        """Replay a record: resubmit it and bump ``replay_count``.

        The original record is kept (with ``replay_count`` incremented)
        so the failure history is preserved; the replayed task receives a
        fresh task ID.

        Args:
            record: The record to replay.
            queue: Any ``TaskQueue`` (local or remote) to resubmit into.

        Returns:
            The new task ID.

        Raises:
            RuntimeError: if the queue rejects the rebuilt request.
        """
        request = record.original_request.model_copy(deep=True)
        new_task_id = queue.submit(request)
        if new_task_id:
            record.replay_count += 1
            self.save(record)
        return new_task_id


# ── Process-wide default store ─────────────────────────────

_default_store: Optional[DeadLetterStore] = None


def set_default_store(store: DeadLetterStore) -> None:
    """Set the process-wide default dead-letter store.

    The worker writes dead-letter records through this store, so tests
    and embedding applications can redirect DLQ persistence to an
    isolated directory by calling this once.
    """
    global _default_store
    _default_store = store


def get_default_store() -> DeadLetterStore:
    """
    Returns:
        The process-wide default dead-letter store.

        Lazily creates the default store at ``~/.mn_tasks/deadletters`` on
        first use.
    """
    global _default_store
    if _default_store is None:
        _default_store = DeadLetterStore()
    return _default_store


# ── Replay entry point ─────────────────────────────────────


def replay_dead_letter(
    task_id: str,
    *,
    queue: TaskQueue,
    store: Optional[DeadLetterStore] = None,
) -> str:
    """Replay a dead-letter record into a task queue.

    Rebuilds the original ``TaskRequest`` from the record and submits it
    to *queue* with a fresh task ID, then increments and persists the
    record's ``replay_count``.

    Args:
        task_id: The dead-letter record's task ID.
        queue: The queue to resubmit the task into.
        store: The :class:`DeadLetterStore` to read from. Defaults to
            the process-wide default store.

    Returns:
        The new task ID.

    Raises:
        KeyError: if no dead-letter record exists for *task_id*.
    """
    store = store or get_default_store()
    record = store.get(task_id)
    if record is None:
        raise KeyError(f"No dead-letter record for task {task_id}")
    return store.replay(record, queue)
