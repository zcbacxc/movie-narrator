# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.2 Wave 2A — orphan crash recovery + checkpoint fingerprints.

Covers:
- ``compute_request_fingerprint``: stability, sensitivity to semantic input,
  insensitivity to scheduling fields, and dictionary-ordering independence.
- ``TaskCheckpoint`` ``schema_version`` / ``input_fingerprint`` persistence,
  including backward-compatible loading of pre-v1.2 checkpoints.
- ``CheckpointStore.resolve_resume`` fingerprint validation: match → plan,
  mismatch → None, legacy missing fingerprint → conservative None.
- ``LocalTaskQueue.start()`` orphan recovery: resumable orphans are
  re-enqueued, non-resumable orphans are marked FAILED, and PENDING tasks
  are left untouched. No real pipeline runs.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from movie_narrator.cloud import queue as queue_module
from movie_narrator.cloud.checkpoint import (
    CheckpointStore,
    TaskCheckpoint,
    compute_request_fingerprint,
)
from movie_narrator.cloud.models import (
    Task,
    TaskPriority,
    TaskRequest,
    TaskResult,
    TaskStatus,
)
from movie_narrator.cloud.queue import LocalTaskQueue
from movie_narrator.pipeline.runner import _next_step_after


# ════════════════════════════════════════════════════════════
#  Request fingerprint
# ════════════════════════════════════════════════════════════


class TestRequestFingerprint:
    """Stability / sensitivity of ``compute_request_fingerprint``."""

    def test_same_input_same_fingerprint(self):
        a = TaskRequest(movie_name="飞驰人生", style="热血搞笑", duration=60)
        b = TaskRequest(movie_name="飞驰人生", style="热血搞笑", duration=60)
        assert compute_request_fingerprint(a) == compute_request_fingerprint(b)
        # Deterministic across repeated calls.
        assert compute_request_fingerprint(a) == compute_request_fingerprint(a)

    def test_semantic_changes_change_fingerprint(self):
        base = TaskRequest(movie_name="A", style="funny", duration=60)
        assert compute_request_fingerprint(base) != compute_request_fingerprint(
            TaskRequest(movie_name="B", style="funny", duration=60)
        )
        assert compute_request_fingerprint(base) != compute_request_fingerprint(
            TaskRequest(movie_name="A", style="serious", duration=60)
        )
        assert compute_request_fingerprint(base) != compute_request_fingerprint(
            TaskRequest(movie_name="A", style="funny", duration=90)
        )
        assert compute_request_fingerprint(base) != compute_request_fingerprint(
            TaskRequest(movie_name="A", style="funny", duration=60, params={"tone": "hot"})
        )

    def test_scheduling_fields_do_not_change_fingerprint(self):
        base = TaskRequest(movie_name="A", style="funny")
        altered = TaskRequest(
            movie_name="A",
            style="funny",
            output_dir=str(Path("/somewhere/else")),
            priority=TaskPriority.HIGH,
            max_retries=9,
            retry_delay=123.0,
        )
        assert compute_request_fingerprint(base) == compute_request_fingerprint(altered)

    def test_nested_dict_order_independent(self):
        a = TaskRequest(movie_name="A", workflow_steps={"x": True, "y": False})
        b = TaskRequest(movie_name="A", workflow_steps={"y": False, "x": True})
        assert compute_request_fingerprint(a) == compute_request_fingerprint(b)


# ════════════════════════════════════════════════════════════
#  Checkpoint fingerprint persistence
# ════════════════════════════════════════════════════════════


class TestCheckpointFingerprintPersistence:
    """``schema_version`` / ``input_fingerprint`` round-trip and back-compat."""

    def test_save_load_roundtrips_fingerprint(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        req = TaskRequest(movie_name="X", style="funny")
        fp = compute_request_fingerprint(req)
        store.save(
            TaskCheckpoint(
                task_id="t1",
                completed_step="resolve_video",
                input_fingerprint=fp,
            )
        )
        loaded = store.load("t1")
        assert loaded is not None
        assert loaded.schema_version == 1
        assert loaded.input_fingerprint == fp
        assert loaded.artifact_manifest is None

    def test_legacy_checkpoint_loads_with_defaults(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        store.path_for("old").write_text(
            json.dumps({"task_id": "old", "completed_step": "resolve_video", "attempt": 2}),
            encoding="utf-8",
        )
        loaded = store.load("old")
        assert loaded is not None
        assert loaded.schema_version == 1
        assert loaded.input_fingerprint is None
        assert loaded.artifact_manifest is None


# ════════════════════════════════════════════════════════════
#  resolve_resume fingerprint validation
# ════════════════════════════════════════════════════════════


class TestResolveResumeFingerprint:
    """``resolve_resume`` validates the request fingerprint on resume."""

    def test_matching_fingerprint_returns_plan(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        req = TaskRequest(movie_name="X", style="funny")
        store.save(
            TaskCheckpoint(
                task_id="t",
                completed_step="generate_script",
                input_fingerprint=compute_request_fingerprint(req),
            )
        )
        plan = store.resolve_resume("t", req)
        assert plan is not None
        assert plan.done is False
        assert plan.start_step == _next_step_after("generate_script")

    def test_mismatched_fingerprint_returns_none(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        store.save(
            TaskCheckpoint(
                task_id="t",
                completed_step="generate_script",
                input_fingerprint="0" * 32,
            )
        )
        assert store.resolve_resume("t", TaskRequest(movie_name="X")) is None

    def test_legacy_missing_fingerprint_treated_as_stale(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        store.save(TaskCheckpoint(task_id="t", completed_step="generate_script"))
        # Pre-v1.2 checkpoints carry no fingerprint → conservatively stale.
        assert store.resolve_resume("t", TaskRequest(movie_name="X")) is None

    def test_without_request_skips_fingerprint_check(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        store.save(TaskCheckpoint(task_id="t", completed_step="generate_script"))
        plan = store.resolve_resume("t")
        assert plan is not None
        assert plan.start_step == _next_step_after("generate_script")


# ════════════════════════════════════════════════════════════
#  Orphan recovery at queue startup
# ════════════════════════════════════════════════════════════


class TestOrphanRecoveryOnStart:
    """``LocalTaskQueue.start()`` recovers / fails tasks orphaned by a crash."""

    def test_recover_resumable_and_fail_unrecoverable(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("CI", "1")
        storage_dir = tmp_path / "tasks"
        queue = LocalTaskQueue(storage_dir=storage_dir, max_workers=2, auto_start=False)

        resumable_req = TaskRequest(movie_name="Resumable", style="funny", max_retries=0)
        unrecoverable_req = TaskRequest(movie_name="NoCp", style="funny", max_retries=0)
        pending_req = TaskRequest(movie_name="Pending", style="funny", max_retries=0)

        resumable = Task(id="resumable", status=TaskStatus.RUNNING, request=resumable_req)
        unrecoverable = Task(id="unrecoverable", status=TaskStatus.RUNNING, request=unrecoverable_req)
        pending = Task(id="pending", status=TaskStatus.PENDING, request=pending_req)
        for task in (resumable, unrecoverable, pending):
            queue.storage.save(task)

        # The resumable orphan owns a fingerprint-matching checkpoint.
        queue.checkpoint_store.save(
            TaskCheckpoint(
                task_id="resumable",
                completed_step="generate_script",
                context_dump={"movie_name": "Resumable"},
                input_fingerprint=compute_request_fingerprint(resumable_req),
            )
        )

        recovered: list = []

        def fake_run_task(
            task, controller, on_progress=None, on_status_change=None, checkpoint_store=None
        ):
            del controller, on_progress, on_status_change, checkpoint_store
            recovered.append(task.id)
            task.status = TaskStatus.COMPLETED
            task.completed_at = datetime.now(timezone.utc).isoformat()
            task.result = TaskResult(video_path=str(tmp_path / "out" / "final.mp4"))
            return task

        monkeypatch.setattr(queue_module, "run_task", fake_run_task)

        queue.start()
        try:
            # The unrecoverable orphan is failed synchronously during start().
            failed = queue.get_task("unrecoverable")
            assert failed is not None
            assert failed.status == TaskStatus.FAILED
            assert "no resumable checkpoint" in (failed.last_error or "")

            # The PENDING task is neither failed nor re-enqueued.
            assert queue.get_task("pending").status == TaskStatus.PENDING

            # The resumable orphan is re-enqueued (worker calls run_task).
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline and "resumable" not in recovered:
                time.sleep(0.02)
            assert "resumable" in recovered

            # Wait for the re-enqueued task to settle and the counter to
            # converge to just the PENDING task.
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                if (
                    queue.get_task("resumable").status == TaskStatus.COMPLETED
                    and queue.active_count == 1
                ):
                    break
                time.sleep(0.02)
            assert queue.get_task("resumable").status == TaskStatus.COMPLETED
            assert queue.active_count == 1
        finally:
            queue.shutdown(wait=False)
