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
import sqlite3
import threading
from pathlib import Path
from typing import Dict, List, Optional, Type

from pydantic import BaseModel

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
        model: Optional[Type[BaseModel]] = None,
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
            assert self._model is not None
            return self._model(**data)

    def list(self, limit: int = 100):
        """List all records, newest first by ``created_at`` when present."""
        with self._lock:
            self._ensure_loaded()
            assert self._model is not None
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
    """SQLite (WAL) task persistence (G8).

    Replaces the historical JSON index with a SQLite database running in
    WAL mode. The public interface is unchanged — ``save`` / ``load`` /
    ``delete`` / ``list_tasks`` / ``count`` / ``clear_terminal`` /
    ``clear_all`` and the ``storage_dir`` / ``index_path`` attributes — so
    callers in ``queue.py`` and downstream APIs are unaffected.

    Benefits over the JSON index:

    - **WAL** gives crash safety and concurrent readers/writers without a
      full-file rewrite on every save.
    - **Point updates** (INSERT OR REPLACE) are O(1) instead of O(n) JSON
      reserialization as the task count grows.
    - **Status index** accelerates ``count``/``list_tasks`` filters.

    A one-time, idempotent migration imports a legacy ``tasks.json`` into
    the database on first use (see :meth:`_migrate_from_json`).

    Args:
        storage_dir: Directory for the task database.
            Defaults to ``~/.mn_tasks``.
    """

    _TERMINAL_VALUES = frozenset(
        {
            TaskStatus.COMPLETED.value,
            TaskStatus.FAILED.value,
            TaskStatus.CANCELLED.value,
            # v0.9.4: DEAD is terminal (dead-letter queue), so it is swept
            # by ``clear_terminal`` like every other terminal state. The
            # DLQ record in ``~/.mn_tasks/deadletters/`` is independent and
            # survives this sweep.
            TaskStatus.DEAD.value,
        }
    )

    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        self.storage_dir = Path(storage_dir) if storage_dir else Path.home() / ".mn_tasks"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._index_path = self.storage_dir / "tasks.db"
        self._lock = threading.RLock()
        # ``check_same_thread=False`` so a single connection can be shared
        # across worker threads; the RLock serializes access within the
        # process, while WAL handles cross-process locking.
        self._conn = sqlite3.connect(str(self._index_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._init_schema()
            self._migrate_from_json()

    # ── Setup ────────────────────────────────────────────────

    def _init_schema(self) -> None:
        """Create the tasks table and status index (if absent)."""
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id         TEXT PRIMARY KEY,
                data       TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT '',
                status     TEXT NOT NULL DEFAULT 'pending'
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status)"
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.commit()
        # v1.3.1: service-semantics columns (additive migration on open).
        self._ensure_column("tenant_id", "default")
        self._ensure_column("principal", "local")

    def _ensure_column(self, name: str, default: str) -> None:
        """Add column *name* to the tasks table when it does not exist.

        Idempotent, additive ``ALTER TABLE`` guarded by an existence check
        against ``PRAGMA table_info`` — the smallest safe migration for the
        create-if-absent schema style used here. The authoritative task
        record stays the ``data`` JSON blob; the columns are a denormalized
        mirror kept in sync by :meth:`save` so tenant scoping can be
        (re)indexed without rewriting every row.
        """
        columns = {str(row[1]) for row in self._conn.execute("PRAGMA table_info(tasks)")}
        if name in columns:
            return
        try:
            # SQLite requires a *literal* DEFAULT in ALTER TABLE ADD COLUMN,
            # so the constant default is inlined. Both name and default are
            # code-owned constants (never user input).
            self._conn.execute(
                f"ALTER TABLE tasks ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'"  # nosec B608 — constants only
            )
            self._conn.commit()
        except sqlite3.Error as e:  # pragma: no cover - defensive
            logger.warning("Could not add column %r to tasks table: %s", name, e)

    def _migrate_from_json(self) -> None:
        """Import a legacy ``tasks.json`` into the DB (idempotent).

        Migration runs at most once:

        - If the database already has tasks, or no ``tasks.json`` exists,
          nothing happens.
        - Otherwise every record is inserted and the JSON file is renamed to
          ``tasks.json.migrated`` (kept for safety, never deleted).
        """
        json_path = self.storage_dir / "tasks.json"
        if not json_path.exists():
            return
        if self.count() > 0:
            return
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Skipping tasks.json migration (unreadable): %s", e)
            return
        if not isinstance(data, dict):
            return

        rows = []
        for tid, record in data.items():
            if not isinstance(record, dict):
                continue
            rows.append(
                (
                    str(tid),
                    json.dumps(record, ensure_ascii=False),
                    str(record.get("created_at", "")),
                    str(record.get("status", "pending")),
                )
            )
        if rows:
            self._conn.executemany(
                "INSERT OR IGNORE INTO tasks(id, data, created_at, status) "
                "VALUES (?, ?, ?, ?)",
                rows,
            )
            self._conn.commit()
            logger.info("Migrated %d legacy tasks from tasks.json", len(rows))
        try:
            json_path.replace(json_path.with_suffix(".json.migrated"))
        except OSError as e:
            logger.warning("Could not archive tasks.json after migration: %s", e)

    # ── Core CRUD ────────────────────────────────────────────

    def save(self, task: Task) -> None:
        """Save or update a task."""
        record = task.model_dump(mode="json")
        with self._lock:
            self._conn.execute(
                "INSERT INTO tasks(id, data, created_at, status, tenant_id, principal) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(id) DO UPDATE SET "
                "data=excluded.data, created_at=excluded.created_at, status=excluded.status, "
                "tenant_id=excluded.tenant_id, principal=excluded.principal",
                (
                    task.id,
                    json.dumps(record, ensure_ascii=False),
                    str(record.get("created_at", "")),
                    str(record.get("status", "pending")),
                    str(record.get("tenant_id", "default")),
                    str(record.get("principal", "local")),
                ),
            )
            self._conn.commit()

    def load(self, task_id: str) -> Optional[Task]:
        """Load a task by ID. Returns None if not found."""
        with self._lock:
            row = self._conn.execute(
                "SELECT data FROM tasks WHERE id = ?", (task_id,)
            ).fetchone()
        if row is None:
            return None
        return Task(**json.loads(row["data"]))

    def delete(self, task_id: str) -> bool:
        """Delete a task. Returns True if the task existed."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
            self._conn.commit()
            return cur.rowcount > 0

    def list_tasks(
        self,
        status: Optional[TaskStatus] = None,
        limit: int = 100,
    ) -> List[Task]:
        """List tasks, optionally filtered by status.

        Tasks are sorted by ``created_at`` descending (newest first).
        """
        with self._lock:
            if status is None:
                rows = self._conn.execute(
                    "SELECT data FROM tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT data FROM tasks WHERE status = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (status.value, limit),
                ).fetchall()
        return [Task(**json.loads(r["data"])) for r in rows]

    def count(self, status: Optional[TaskStatus] = None) -> int:
        """Count tasks, optionally filtered by status."""
        with self._lock:
            if status is None:
                row = self._conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()
            else:
                row = self._conn.execute(
                    "SELECT COUNT(*) AS n FROM tasks WHERE status = ?",
                    (status.value,),
                ).fetchone()
        return int(row["n"])

    # ── Bulk operations ──────────────────────────────────────

    def clear_terminal(self) -> int:
        """Remove all tasks in terminal states. Returns count removed."""
        placeholders = ",".join("?" for _ in self._TERMINAL_VALUES)
        with self._lock:
            cur = self._conn.execute(
                f"DELETE FROM tasks WHERE status IN ({placeholders})",  # nosec B608 — placeholders are "?", values parameterized
                tuple(self._TERMINAL_VALUES),
            )
            self._conn.commit()
            return cur.rowcount

    def clear_all(self) -> int:
        """Remove all tasks. Returns count removed."""
        with self._lock:
            cur = self._conn.execute("DELETE FROM tasks")
            self._conn.commit()
            return cur.rowcount

    @property
    def index_path(self) -> Path:
        """Path to the task database file."""
        return self._index_path

    def close(self) -> None:
        """Close the underlying SQLite connection (idempotent)."""
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:  # pragma: no cover - defensive
                pass
