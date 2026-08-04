# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Task-level checkpoints for long-running pipeline tasks (v0.9.2).

A pipeline task renders a full movie recap (16 steps, heavy TTS / render
work). When the process crashes or a step fails, re-running everything
from ``resolve_video`` is wasteful. This module persists a **checkpoint**
after every completed pipeline step so that :func:`~movie_narrator.cloud.
worker.run_task` can resume from the next step instead.

Design notes:

- A checkpoint is written by the worker after each step completes
  (``step_ok`` / ``step_skip`` / ``step_warn``), snapshoting the full
  :class:`~movie_narrator.models.Context` at that moment.
- ``CheckpointStore`` keeps one file per task at
  ``<storage_dir>/checkpoints/<task_id>.json`` — separate from
  ``tasks.json`` so a corrupt task index never destroys checkpoints.
- Writes are atomic (temp file + :func:`os.replace`), mirroring
  ``TaskStorage._flush``.
- The context dump reuses the runner's ``model_dump`` approach
  (``mode="json"``, excluding non-serializable fields); unlike the CLI
  ``pipeline_state.json`` we also exclude ``cost_tracker`` because it
  embeds a ``threading.Lock`` that pydantic cannot serialize.
- This is **automatic** — it does not replace the manual ``mn resume
  --state`` flow, which remains the human-in-the-loop path.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import datetime, timezone
from itertools import count
from pathlib import Path
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from ..pipeline.runner import STEPS, _next_step_after

logger = logging.getLogger(__name__)

#: Name of the subdirectory holding checkpoint files under the storage dir.
CHECKPOINT_DIR_NAME = "checkpoints"

#: Monotonic counter for unique temp-file names (see ``CheckpointStore.save``).
_tmp_counter = count(1)


def _utc_now_iso() -> str:
    """
    Returns:
        The current UTC time as an ISO-8601 string.
    """
    return datetime.now(timezone.utc).isoformat()


class TaskCheckpoint(BaseModel):
    """A snapshot of pipeline progress for a single task.

    Attributes:
        task_id: The task this checkpoint belongs to.
        completed_step: Name of the last pipeline step that finished.
            ``run_task`` resumes from ``_next_step_after(completed_step)``.
        context_dump: Serialized :class:`~movie_narrator.models.Context`
            (``model_dump(mode="json")``) captured after the step ran, so
            already-produced segments / clips / audio are not regenerated.
        saved_at: UTC timestamp of when the checkpoint was written.
        attempt: Retry attempt number that produced this checkpoint
            (0 for the first attempt).
    """

    task_id: str
    completed_step: str
    context_dump: Dict[str, Any] = Field(default_factory=dict)
    saved_at: str = Field(default_factory=_utc_now_iso)
    attempt: int = 0


class ResumePlan(BaseModel):
    """Resolved resume state for a task that owns a checkpoint.

    Returned by :meth:`CheckpointStore.resolve_resume`; consumed by
    :func:`~movie_narrator.cloud.worker.run_task`.

    Attributes:
        completed_step: The checkpoint's last completed pipeline step.
        start_step: First pipeline step to run on resume. ``None`` means
            the whole pipeline runs (only when ``done`` is False).
        context_dump: Serialized ``Context`` to restore instead of
            calling ``build_context``.
        done: True when the checkpoint's ``completed_step`` was the final
            pipeline step — every step already finished and only result
            extraction remains.
    """

    completed_step: str
    start_step: Optional[str] = None
    context_dump: Optional[Dict[str, Any]] = None
    done: bool = False


class CheckpointStore:
    """File-based persistence for :class:`TaskCheckpoint` objects.

    Thread-safe via a re-entrant lock. Writes are atomic so a crash
    mid-write can never leave a corrupt checkpoint behind.

    Args:
        storage_dir: Base directory, the same one ``TaskStorage`` uses.
            Checkpoints live in a ``checkpoints/`` subdirectory.
    """

    def __init__(self, storage_dir: Optional[Path] = None) -> None:
        base = Path(storage_dir) if storage_dir else Path.home() / ".mn_tasks"
        self._dir = base / CHECKPOINT_DIR_NAME
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()

    @property
    def dir(self) -> Path:
        """Directory holding the checkpoint files."""
        return self._dir

    def path_for(self, task_id: str) -> Path:
        """
        Returns:
            The checkpoint file path for ``task_id``.
        """
        return self._dir / f"{task_id}.json"

    def save(self, checkpoint: TaskCheckpoint) -> None:
        """Atomically persist ``checkpoint``.

        The payload is written to a temporary sibling file first and then
        moved into place with :func:`os.replace`, so a reader (or a crash)
        never observes a half-written checkpoint. The temp name is
        unique per call (pid + counter) so concurrent writers for the
        same task cannot clobber each other's temp file.
        """
        path = self.path_for(checkpoint.task_id)
        tmp = path.with_suffix(f".{os.getpid()}.{next(_tmp_counter)}.tmp")
        with self._lock:
            tmp.write_text(
                json.dumps(
                    checkpoint.model_dump(mode="json"),
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            tmp.replace(path)

    def load(self, task_id: str) -> Optional[TaskCheckpoint]:
        """Load the checkpoint for ``task_id``, or None when absent/corrupt.

        A corrupt file is logged and treated as "no checkpoint" — the
        task then re-runs from scratch, which is always safe.
        """
        path = self.path_for(task_id)
        with self._lock:
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                return TaskCheckpoint(**data)
            except (json.JSONDecodeError, ValueError, OSError) as exc:
                logger.warning("Failed to load checkpoint for task %s: %s", task_id, exc)
                return None

    def delete(self, task_id: str) -> bool:
        """Remove the checkpoint for ``task_id``.

        Returns:
            True when a checkpoint file existed and was removed.
        """
        path = self.path_for(task_id)
        with self._lock:
            if not path.exists():
                return False
            path.unlink(missing_ok=True)
            return True

    def resolve_resume(self, task_id: str) -> Optional[ResumePlan]:
        """Turn the checkpoint for ``task_id`` into a :class:`ResumePlan`.

        Returns:
            None when there is no checkpoint (a fresh task). The
            returned plan's ``done`` flag is True only when the completed
            step was the final pipeline step — everything already ran, so
            the caller must not invoke the pipeline again.
        """
        checkpoint = self.load(task_id)
        if checkpoint is None:
            return None
        if checkpoint.completed_step == STEPS[-1].__name__:
            return ResumePlan(
                completed_step=checkpoint.completed_step,
                context_dump=checkpoint.context_dump,
                done=True,
            )
        start_step = _next_step_after(checkpoint.completed_step)
        return ResumePlan(
            completed_step=checkpoint.completed_step,
            start_step=start_step,
            context_dump=checkpoint.context_dump,
            done=False,
        )


__all__ = [
    "CHECKPOINT_DIR_NAME",
    "CheckpointStore",
    "ResumePlan",
    "TaskCheckpoint",
]
