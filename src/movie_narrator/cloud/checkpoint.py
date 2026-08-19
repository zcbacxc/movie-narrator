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

import hashlib
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
from .models import TaskRequest

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


#: ``TaskRequest`` fields hashed into a checkpoint's input fingerprint.
#: These are the *semantic* inputs that determine the rendered output.
#: Scheduling / operational fields (``output_dir``, ``priority``,
#: ``max_retries``, ``retry_delay``, ``keep_cache``, ``log_level``,
#: ``verbose``, ``enable_dlq``, ``config_path``) are deliberately excluded
#: so re-queuing the same creative request does not invalidate a valid
#: checkpoint, nor does bumping a retry budget force a full rebuild.
_FINGERPRINT_FIELDS: tuple[str, ...] = (
    "movie_name",
    "style",
    "duration",
    "voice",
    "video_format",
    "video",
    "library_dir",
    "research",
    "bgm",
    "no_bgm",
    "no_clips",
    "strict",
    "subtitle_lang",
    "subtitle_mode",
    "narration_preset",
    "lang",
    "workflow_steps",
    "params",
)

#: Number of leading SHA-256 hex characters kept in a fingerprint. 128 bits
#: makes accidental collisions practically impossible while keeping the
#: on-disk JSON and log lines short.
_FINGERPRINT_HEX_LEN = 32


def _stable_json(value: Any) -> str:
    """Serialize ``value`` to a canonical, ordering-independent JSON string.

    ``sort_keys`` collapses ``dict`` key-ordering differences, ``separators``
    strips irrelevant whitespace, and ``default=str`` keeps the function
    total even if a nested value is not natively JSON-serializable.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def compute_request_fingerprint(request: TaskRequest) -> str:
    """Return a stable SHA-256 fingerprint of a task's semantic inputs.

    Only :data:`_FINGERPRINT_FIELDS` are hashed. Scheduling fields such as
    ``output_dir``, ``priority`` or ``retry_delay`` are excluded, so moving
    a task between output locations or bumping its retry budget does not
    invalidate an otherwise-valid checkpoint. Nested mappings (for example
    ``workflow_steps`` and ``params``) are normalized by key before hashing.

    Args:
        request: The task request to fingerprint.

    Returns:
        The first :data:`_FINGERPRINT_HEX_LEN` hex characters of the
        SHA-256 digest, deterministic across dictionary key orderings.
    """
    payload = {field: getattr(request, field, None) for field in _FINGERPRINT_FIELDS}
    digest = hashlib.sha256(_stable_json(payload).encode("utf-8")).hexdigest()
    return digest[:_FINGERPRINT_HEX_LEN]


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
        schema_version: Checkpoint schema version (1 for all v1.2
            checkpoints). Kept for future migrations; pre-v1.2 files on
            disk lack the key and default to 1 when loaded.
        input_fingerprint: SHA-256 fingerprint of the task's semantic
            ``TaskRequest`` fields (see
            :func:`compute_request_fingerprint`). ``None`` for checkpoints
            written before v1.2; such checkpoints are treated as *not
            resumable* (conservative — never resume onto input we cannot
            verify matches the current request).
        artifact_manifest: Reserved placeholder for a future artifact
            manifest. Not populated yet.
    """

    task_id: str
    completed_step: str
    context_dump: Dict[str, Any] = Field(default_factory=dict)
    saved_at: str = Field(default_factory=_utc_now_iso)
    attempt: int = 0
    schema_version: int = 1
    input_fingerprint: Optional[str] = None
    artifact_manifest: Optional[Dict[str, Any]] = None


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

    def resolve_resume(
        self,
        task_id: str,
        request: Optional[TaskRequest] = None,
    ) -> Optional[ResumePlan]:
        """Turn the checkpoint for ``task_id`` into a :class:`ResumePlan`.

        Args:
            task_id: The task whose checkpoint should be resolved.
            request: The task's current ``TaskRequest``. When provided, the
                checkpoint's ``input_fingerprint`` must equal the
                fingerprint of ``request``; otherwise the checkpoint is
                treated as absent (``None``) so the task rebuilds from
                scratch. Omitted (``None``) for backward compatibility with
                callers that resolve without a live request — in that case
                no fingerprint check is performed.

        Returns:
            None when there is no checkpoint (a fresh task), when the
            checkpoint is corrupt, or — when ``request`` is supplied — when
            the stored fingerprint does not match (this also covers
            pre-v1.2 checkpoints whose fingerprint is ``None``: they are
            conservatively considered stale). The returned plan's ``done``
            flag is True only when the completed step was the final
            pipeline step — everything already ran, so the caller must not
            invoke the pipeline again.
        """
        checkpoint = self.load(task_id)
        if checkpoint is None:
            return None
        if request is not None:
            current = compute_request_fingerprint(request)
            if checkpoint.input_fingerprint != current:
                logger.warning(
                    "Ignoring checkpoint for task %s: input fingerprint "
                    "mismatch (stored=%r, current=%r)",
                    task_id,
                    checkpoint.input_fingerprint,
                    current,
                )
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
    "compute_request_fingerprint",
]
