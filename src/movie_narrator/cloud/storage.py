# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""JSON-based task persistence (v0.6.0).

Stores task state to a local JSON file so that tasks survive process
restarts. The storage layer is a simple key-value store keyed by task ID.

Future cloud backends (Redis, DynamoDB, etc.) can implement the same
interface by subclassing ``TaskStorage`` or providing a duck-typed
replacement.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Dict, List, Optional

from .models import Task, TaskStatus

logger = logging.getLogger(__name__)


class JsonModelStore:
    """Thread-safe JSON persistence for arbitrary pydantic records (v0.9.3).

    A generic key-value store for records that are not pipeline tasks —
    batch aggregates (``Batch``) and scheduled jobs (``ScheduleRequest``).
    Like :class:`TaskStorage` it writes atomically (temp file + rename)
    and serves reads from an in-memory cache.

    Args:
        storage_dir: Directory for the record file. Defaults to
            ``~/.mn_tasks``.
        filename: Name of the record file (e.g. ``batches.json``).
        model: The pydantic model class used to (de)serialize records.
        key_field: Attribute holding the record's unique key.
    """

    def __init__(
        self,
        storage_dir: Optional[Path] = None,
        filename: str = "records.json",
        model=None,
        key_field: str = "id",
    ) -> None:
        self.storage_dir = Path(storage_dir) if storage_dir else Path.home() / ".mn_tasks"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.storage_dir / filename
        self._model = model
        self._key_field = key_field
        self._lock = threading.RLock()
        self._cache: Dict[str, dict] = {}
        self._loaded = False

    @property
    def index_path(self) -> Path:
        """Path to the record file on disk."""
        return self._path

    def save(self, record) -> None:
        """Save or update a record."""
        key = getattr(record, self._key_field)
        with self._lock:
            self._ensure_loaded()
            self._cache[key] = record.model_dump(mode="json")
            self._flush()

    def load(self, key: str):
        """Load a record by key. Returns None if not found."""
        with self._lock:
            self._ensure_loaded()
            data = self._cache.get(key)
            if data is None:
                return None
            return self._model(**data)

    def list(self, limit: int = 100):
        """List all records, newest first by ``created_at`` when present."""
        with self._lock:
            self._ensure_loaded()
            records = [self._model(**v) for v in self._cache.values()]
        records.sort(
            key=lambda r: getattr(r, "created_at", "") or "",
            reverse=True,
        )
        return records[:limit]

    def delete(self, key: str) -> bool:
        """Delete a record. Returns True if the key existed."""
        with self._lock:
            self._ensure_loaded()
            if key not in self._cache:
                return False
            del self._cache[key]
            self._flush()
            return True

    def count(self) -> int:
        """Number of records currently stored."""
        with self._lock:
            self._ensure_loaded()
            return len(self._cache)

    def _ensure_loaded(self) -> None:
        """Load from disk if not yet loaded."""
        if self._loaded:
            return
        self._loaded = True
        if self._path.exists():
            try:
                data = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._cache = data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load %s: %s", self._path.name, e)
                self._cache = {}

    def _flush(self) -> None:
        """Write cache to disk atomically."""
        tmp_path = self._path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self._path)
        except OSError as e:
            logger.error("Failed to flush %s: %s", self._path.name, e)


class TaskStorage:
    """JSON file-based task persistence.

    Thread-safe via a re-entrant lock. The index file is written
    atomically (write to temp, then rename) to prevent corruption
    on crash.

    Args:
        storage_dir: Directory for the task index file.
            Defaults to ``~/.mn_tasks``.
    """

    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        self.storage_dir = Path(storage_dir) if storage_dir else Path.home() / ".mn_tasks"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.storage_dir / "tasks.json"
        self._lock = threading.RLock()
        # In-memory cache for fast reads
        self._cache: Dict[str, dict] = {}
        self._loaded = False

    # ── Core CRUD ────────────────────────────────────────────

    def save(self, task: Task) -> None:
        """Save or update a task."""
        with self._lock:
            self._ensure_loaded()
            self._cache[task.id] = task.model_dump(mode="json")
            self._flush()

    def load(self, task_id: str) -> Optional[Task]:
        """Load a task by ID. Returns None if not found."""
        with self._lock:
            self._ensure_loaded()
            data = self._cache.get(task_id)
            if data is None:
                return None
            return Task(**data)

    def delete(self, task_id: str) -> bool:
        """Delete a task. Returns True if the task existed."""
        with self._lock:
            self._ensure_loaded()
            if task_id not in self._cache:
                return False
            del self._cache[task_id]
            self._flush()
            return True

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 100,
    ) -> List[Task]:
        """List tasks, optionally filtered by status.

        Tasks are sorted by ``created_at`` descending (newest first).
        """
        with self._lock:
            self._ensure_loaded()
            tasks = [Task(**v) for v in self._cache.values()]
        if status:
            tasks = [t for t in tasks if t.status == status]
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        return tasks[:limit]

    def count(self, status: Optional[TaskStatus] = None) -> int:
        """Count tasks, optionally filtered by status."""
        with self._lock:
            self._ensure_loaded()
            if status is None:
                return len(self._cache)
            return sum(
                1
                for v in self._cache.values()
                if v.get("status") == status.value
            )

    # ── Bulk operations ──────────────────────────────────────

    def clear_terminal(self) -> int:
        """Remove all tasks in terminal states. Returns count removed."""
        with self._lock:
            self._ensure_loaded()
            to_remove = [
                tid
                for tid, data in self._cache.items()
                if data.get("status") in (
                    TaskStatus.COMPLETED.value,
                    TaskStatus.FAILED.value,
                    TaskStatus.CANCELLED.value,
                )
            ]
            for tid in to_remove:
                del self._cache[tid]
            if to_remove:
                self._flush()
            return len(to_remove)

    def clear_all(self) -> int:
        """Remove all tasks. Returns count removed."""
        with self._lock:
            self._ensure_loaded()
            count = len(self._cache)
            self._cache.clear()
            self._flush()
            return count

    # ── Internal ─────────────────────────────────────────────

    def _ensure_loaded(self) -> None:
        """Load from disk if not yet loaded."""
        if self._loaded:
            return
        self._loaded = True
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    self._cache = data
            except (json.JSONDecodeError, OSError) as e:
                logger.warning("Failed to load task index: %s", e)
                self._cache = {}

    def _flush(self) -> None:
        """Write cache to disk atomically."""
        tmp_path = self._index_path.with_suffix(".tmp")
        try:
            tmp_path.write_text(
                json.dumps(self._cache, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp_path.replace(self._index_path)
        except OSError as e:
            logger.error("Failed to flush task index: %s", e)

    @property
    def index_path(self) -> Path:
        """Path to the task index file."""
        return self._index_path
