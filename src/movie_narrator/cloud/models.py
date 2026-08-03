# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Task models for the async job system (v0.6.0).

Defines the data structures for pipeline task submission, tracking,
and result retrieval. These models are the foundation of the task
queue infrastructure that enables cloud deployment (v0.6.x).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


# ── Enums ──────────────────────────────────────────────────


class TaskStatus(str, Enum):
    """Lifecycle states for a pipeline task."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RETRYING = "retrying"


class TaskPriority(int, Enum):
    """Priority level for task scheduling (higher = more urgent)."""

    LOW = 0
    NORMAL = 5
    HIGH = 10
    URGENT = 20


# ── Terminal states ────────────────────────────────────────

TERMINAL_STATES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED}
)

ACTIVE_STATES: frozenset[TaskStatus] = frozenset(
    {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RETRYING}
)


# ── Request / Progress / Result ────────────────────────────


class TaskRequest(BaseModel):
    """Input parameters for a pipeline task.

    Mirrors the key arguments of ``build_context`` / ``mn create``
    so that any CLI invocation can be submitted as an async task.
    """

    movie_name: str
    style: str = ""
    duration: int = 60
    voice: Optional[str] = None
    # GAP-5 (v0.8.0): renamed from ``format`` to avoid shadowing the
    # Python builtin ``format()``. The ``format`` alias is kept so
    # existing API requests with {"format": "16:9"} still validate.
    video_format: str = Field(default="16:9", alias="format")
    video: Optional[str] = None
    library_dir: Optional[str] = None
    research: Optional[bool] = None
    bgm: Optional[str] = None
    no_bgm: bool = False
    no_clips: bool = False
    strict: bool = False
    subtitle_lang: Optional[str] = None
    subtitle_mode: Optional[str] = None
    narration_preset: Optional[str] = None
    lang: str = "zh"
    workflow_steps: Optional[Dict[str, bool]] = None
    params: Optional[Dict[str, Any]] = None
    config_path: Optional[str] = None

    # Task-specific fields
    output_dir: Optional[str] = None
    priority: TaskPriority = TaskPriority.NORMAL
    max_retries: int = 3
    retry_delay: float = 5.0
    keep_cache: bool = False
    log_level: str = "DEBUG"
    verbose: bool = False

    # GAP-5 (v0.8.0): allow population by the new field name
    # (``video_format``) while still accepting the legacy ``format``
    # alias defined above.
    model_config = {"populate_by_name": True}


class TaskProgress(BaseModel):
    """Progress tracking for a running task."""

    current_step: str = ""
    current_step_index: int = 0
    total_steps: int = 16
    percentage: float = 0.0
    elapsed_seconds: float = 0.0
    step_elapsed_seconds: float = 0.0
    steps_completed: List[str] = Field(default_factory=list)
    steps_skipped: List[str] = Field(default_factory=list)
    steps_failed: List[str] = Field(default_factory=list)
    # v0.9.2: last pipeline step safely persisted to a task checkpoint.
    # Populated while a task runs (checkpointing enabled), so API clients
    # can show "render survived up to step X" even after a crash.
    latest_checkpoint_step: Optional[str] = None
    checkpoint_updated_at: Optional[str] = None

    def update_step(
        self,
        step_name: str,
        index: int,
        total: int,
        elapsed: float = 0.0,
    ) -> None:
        """Update progress for the current step."""
        self.current_step = step_name
        self.current_step_index = index
        self.total_steps = total
        self.step_elapsed_seconds = elapsed
        if total > 0:
            self.percentage = round(index / total * 100, 1)

    def mark_completed(self, step_name: str) -> None:
        """Mark a step as completed."""
        if step_name not in self.steps_completed:
            self.steps_completed.append(step_name)

    def mark_skipped(self, step_name: str) -> None:
        """Mark a step as skipped."""
        if step_name not in self.steps_skipped:
            self.steps_skipped.append(step_name)

    def mark_failed(self, step_name: str) -> None:
        """Mark a step as failed."""
        if step_name not in self.steps_failed:
            self.steps_failed.append(step_name)

    @property
    def completed_count(self) -> int:
        """Number of completed steps (including skipped)."""
        return len(self.steps_completed) + len(self.steps_skipped)


class TaskResult(BaseModel):
    """Result of a completed (or failed) task."""

    video_path: Optional[str] = None
    audio_path: Optional[str] = None
    output_dir: Optional[str] = None
    subtitle_path: Optional[str] = None
    script_md_path: Optional[str] = None
    clips_dir: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    error: Optional[str] = None
    error_type: Optional[str] = None
    traceback: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        """True if the task completed without error."""
        return self.error is None and self.video_path is not None


# ── Task ───────────────────────────────────────────────────


def _utc_now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).isoformat()


class Task(BaseModel):
    """A pipeline task with full lifecycle tracking.

    This is the central data structure stored by the task queue.
    It tracks the request, progress, result, and lifecycle timestamps.
    """

    id: str = Field(default_factory=lambda: uuid4().hex[:12])
    status: TaskStatus = TaskStatus.PENDING
    request: TaskRequest
    result: Optional[TaskResult] = None
    progress: Optional[TaskProgress] = None
    created_at: str = Field(default_factory=_utc_now_iso)
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    retries: int = 0
    last_error: Optional[str] = None
    worker_id: Optional[str] = None
    # v0.8.1: correlation ID captured at submission time, so worker-side
    # logs can be joined with the API access log for the same request.
    # Optional because task JSON persisted by earlier versions has no
    # such key and must still load.
    correlation_id: Optional[str] = None

    # Allow arbitrary types for future extensibility
    model_config = {"arbitrary_types_allowed": True}

    @property
    def is_terminal(self) -> bool:
        """True if the task is in a terminal state."""
        return self.status in TERMINAL_STATES

    @property
    def is_active(self) -> bool:
        """True if the task is pending or running."""
        return self.status in ACTIVE_STATES

    @property
    def elapsed_seconds(self) -> Optional[float]:
        """Elapsed time from start to completion (or now if still running).

        Returns None if the task hasn't started yet.
        """
        if not self.started_at:
            return None
        start = datetime.fromisoformat(self.started_at)
        if self.completed_at:
            end = datetime.fromisoformat(self.completed_at)
        else:
            end = datetime.now(timezone.utc)
        return (end - start).total_seconds()

    def to_summary(self) -> Dict[str, Any]:
        """Return a compact summary dict for CLI display."""
        return {
            "id": self.id,
            "movie": self.request.movie_name,
            "status": self.status.value,
            "progress": (
                f"{self.progress.percentage:.0f}%"
                if self.progress and self.progress.current_step
                else "—"
            ),
            "current_step": (
                self.progress.current_step if self.progress else ""
            ),
            "retries": self.retries,
            "created_at": self.created_at,
            "completed_at": self.completed_at or "",
            "error": self.last_error or "",
        }
