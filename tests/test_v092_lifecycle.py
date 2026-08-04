# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.9.2 Task Lifecycle — checkpointing + graceful shutdown.

Covers:
- TaskCheckpoint / CheckpointStore: save/load/delete, atomic writes,
  resume-plan resolution (start_step / done)
- Worker checkpointing: per-step writes, crash recovery passes
  ``start_step`` to ``run_pipeline``, context restoration, checkpoint
  deletion on success
- Queue graceful shutdown: wait joins in-flight tasks, timeout
  force-cancels the remainder, submissions after shutdown raise
  ``QueueShutdownError``
- API / daemon drain: new submissions rejected while draining, probes
  report the draining state, ``drain_inflight`` ordering
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from movie_narrator.cloud import (
    CheckpointStore,
    QueueShutdownError,
    TaskCheckpoint,
)
from movie_narrator.cloud.api import TaskAPIServer
from movie_narrator.cloud.models import (
    Task,
    TaskRequest,
    TaskStatus,
)
from movie_narrator.cloud.queue import LocalTaskQueue
from movie_narrator.cloud.worker import (
    CancelController,
    _execute_task,
    run_task,
)
from movie_narrator.models import Assets, Context, Services
from movie_narrator.pipeline.runner import STEPS, _next_step_after
from movie_narrator.pipeline.errors import PipelineCancelled
from movie_narrator.utils.console import SilentConsole


# ── Shared helpers ─────────────────────────────────────────


def _mock_pipeline(ctx, **kwargs):
    """Mock pipeline that does no real work."""
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    return ctx


def _cancel_aware_slow_pipeline(duration: float):
    """Pipeline that runs for *duration* s and honours cooperative cancel."""

    def _run(ctx, **kwargs):
        controller = kwargs.get("controller")
        deadline = time.time() + duration
        while time.time() < deadline:
            if controller is not None and controller.is_cancelled():
                raise PipelineCancelled()
            time.sleep(0.02)
        ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
        return ctx

    return _run


def _context_dump(tmp_path: Path, movie_name: str = "Test") -> dict:
    """Serialize a minimal Context the same way the worker's checkpoint does."""
    ctx = Context(
        movie_name=movie_name,
        output_dir=str(tmp_path / "out"),
        assets=Assets(),
        services=Services(console=SilentConsole()),
    )
    ctx.metadata["flag"] = "kept"
    return ctx.model_dump(mode="json", exclude={"services", "cost_tracker"})


def _patch_pipeline(monkeypatch, fake) -> None:
    """Route ``cloud.worker.run_pipeline`` to *fake*."""
    monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", fake)


# ════════════════════════════════════════════════════════════
#  TaskCheckpoint / CheckpointStore
# ════════════════════════════════════════════════════════════


class TestCheckpointStore:
    """Checkpoint persistence and resume-plan resolution."""

    def test_save_and_load_roundtrip(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        cp = TaskCheckpoint(
            task_id="abc",
            completed_step="generate_script",
            context_dump={"movie_name": "X"},
            attempt=2,
        )
        store.save(cp)
        loaded = store.load("abc")
        assert loaded is not None
        assert loaded.task_id == "abc"
        assert loaded.completed_step == "generate_script"
        assert loaded.context_dump == {"movie_name": "X"}
        assert loaded.attempt == 2
        assert loaded.saved_at

    def test_load_missing_returns_none(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        assert store.load("nonexistent") is None

    def test_atomic_write_no_tmp_leftover(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        store.save(TaskCheckpoint(task_id="abc", completed_step="resolve_video"))
        # Only <task_id>.json remains — no .tmp sibling.
        files = [p.name for p in store.dir.iterdir()]
        assert files == ["abc.json"]
        data = json.loads(store.path_for("abc").read_text(encoding="utf-8"))
        assert data["completed_step"] == "resolve_video"

    def test_corrupt_file_loads_as_none(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        store.path_for("bad").write_text("{not json", encoding="utf-8")
        assert store.load("bad") is None

    def test_delete_returns_existence(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        store.save(TaskCheckpoint(task_id="abc", completed_step="resolve_video"))
        assert store.delete("abc") is True
        assert store.delete("abc") is False
        assert store.load("abc") is None

    def test_resolve_resume_returns_next_step(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        store.save(
            TaskCheckpoint(
                task_id="abc",
                completed_step="generate_script",
                context_dump={"movie_name": "X"},
            )
        )
        plan = store.resolve_resume("abc")
        assert plan is not None
        assert plan.done is False
        assert plan.start_step == _next_step_after("generate_script")
        assert plan.context_dump == {"movie_name": "X"}

    def test_resolve_resume_last_step_marks_done(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        last = STEPS[-1].__name__
        store.save(TaskCheckpoint(task_id="abc", completed_step=last))
        plan = store.resolve_resume("abc")
        assert plan is not None
        assert plan.done is True
        assert plan.start_step is None

    def test_resolve_resume_missing_returns_none(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        assert store.resolve_resume("missing") is None

    def test_unknown_step_falls_back_to_scratch(self, tmp_path):
        store = CheckpointStore(tmp_path / "tasks")
        store.save(TaskCheckpoint(task_id="abc", completed_step="not_a_step"))
        plan = store.resolve_resume("abc")
        assert plan is not None
        assert plan.done is False
        assert plan.start_step is None  # run the whole pipeline


# ════════════════════════════════════════════════════════════
#  Worker checkpointing
# ════════════════════════════════════════════════════════════


class TestWorkerCheckpointing:
    """Per-step checkpoint writes and crash recovery in the worker."""

    def test_checkpoint_written_after_each_step(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        store = CheckpointStore(tmp_path / "tasks")

        def fake_pipeline(ctx, **kwargs):
            ctx.services.console.step("resolve_video")
            ctx.services.console.step_ok("resolve_video", 0.1)
            ctx.services.console.step("prepare_assets")
            ctx.services.console.step_ok("prepare_assets", 0.1)
            ctx.services.console.step("generate_script")
            ctx.services.console.step_ok("generate_script", 0.1)
            ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
            return ctx

        _patch_pipeline(monkeypatch, fake_pipeline)
        req = TaskRequest(
            movie_name="CheckpointTest",
            output_dir=str(tmp_path / "out"),
            max_retries=0,
        )
        task = Task(request=req)
        result = _execute_task(task, CancelController(), checkpoint_store=store, attempt=2)
        assert result.status == TaskStatus.COMPLETED

        cp = store.load(task.id)
        assert cp is not None
        assert cp.completed_step == "generate_script"
        assert cp.attempt == 2
        assert cp.context_dump["movie_name"] == "CheckpointTest"

    def test_checkpoint_metadata_on_progress(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        store = CheckpointStore(tmp_path / "tasks")

        def fake_pipeline(ctx, **kwargs):
            ctx.services.console.step("resolve_video")
            ctx.services.console.step_ok("resolve_video", 0.1)
            ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
            return ctx

        _patch_pipeline(monkeypatch, fake_pipeline)
        req = TaskRequest(movie_name="Meta", output_dir=str(tmp_path / "out"), max_retries=0)
        task = Task(request=req)
        result = _execute_task(task, CancelController(), checkpoint_store=store)
        assert result.status == TaskStatus.COMPLETED
        assert result.progress is not None
        assert result.progress.latest_checkpoint_step == "resolve_video"
        assert result.progress.checkpoint_updated_at

    def test_resume_passes_start_step_and_restores_context(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        store = CheckpointStore(tmp_path / "tasks")
        captured: dict = {}

        def fake_pipeline(ctx, **kwargs):
            captured["start_step"] = kwargs.get("start_step")
            captured["movie_name"] = ctx.movie_name
            captured["flag"] = ctx.metadata.get("flag")
            ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
            return ctx

        _patch_pipeline(monkeypatch, fake_pipeline)
        req = TaskRequest(
            movie_name="ResumeTest",
            output_dir=str(tmp_path / "out"),
            max_retries=0,
        )
        task = Task(request=req)
        store.save(
            TaskCheckpoint(
                task_id=task.id,
                completed_step="generate_script",
                context_dump=_context_dump(tmp_path, "ResumeTest"),
                attempt=0,
            )
        )

        result = run_task(task, CancelController(), checkpoint_store=store)
        assert result.status == TaskStatus.COMPLETED
        # The pipeline resumes at the step AFTER the completed one.
        assert captured["start_step"] == "export_script_md"
        # The context was rebuilt from the checkpoint, not from scratch.
        assert captured["movie_name"] == "ResumeTest"
        assert captured["flag"] == "kept"

    def test_done_resume_skips_pipeline(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        store = CheckpointStore(tmp_path / "tasks")
        called: list = []

        def fake_pipeline(ctx, **kwargs):
            called.append(True)
            return ctx

        _patch_pipeline(monkeypatch, fake_pipeline)
        req = TaskRequest(
            movie_name="DoneTest",
            output_dir=str(tmp_path / "out"),
            max_retries=0,
        )
        task = Task(request=req)
        dump = _context_dump(tmp_path, "DoneTest")
        dump["video_path"] = str(tmp_path / "out" / "final.mp4")
        store.save(
            TaskCheckpoint(
                task_id=task.id,
                completed_step=STEPS[-1].__name__,
                context_dump=dump,
                attempt=0,
            )
        )

        result = run_task(task, CancelController(), checkpoint_store=store)
        assert result.status == TaskStatus.COMPLETED
        assert called == []  # run_pipeline never invoked
        assert result.result is not None
        assert result.result.video_path == str(tmp_path / "out" / "final.mp4")

    def test_checkpoint_deleted_on_success(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        store = CheckpointStore(tmp_path / "tasks")
        _patch_pipeline(monkeypatch, _mock_pipeline)
        req = TaskRequest(movie_name="Delete", output_dir=str(tmp_path / "out"), max_retries=0)
        task = Task(request=req)
        result = run_task(task, CancelController(), checkpoint_store=store)
        assert result.status == TaskStatus.COMPLETED
        assert store.load(task.id) is None

    def test_fresh_task_runs_from_scratch(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        store = CheckpointStore(tmp_path / "tasks")
        captured: dict = {}

        def fake_pipeline(ctx, **kwargs):
            captured["start_step"] = kwargs.get("start_step")
            ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
            return ctx

        _patch_pipeline(monkeypatch, fake_pipeline)
        req = TaskRequest(movie_name="Fresh", output_dir=str(tmp_path / "out"), max_retries=0)
        task = Task(request=req)
        result = run_task(task, CancelController(), checkpoint_store=store)
        assert result.status == TaskStatus.COMPLETED
        assert captured["start_step"] is None


# ════════════════════════════════════════════════════════════
#  Queue graceful shutdown
# ════════════════════════════════════════════════════════════


class TestQueueGracefulShutdown:
    """LocalTaskQueue.shutdown drain semantics."""

    def test_shutdown_wait_joins_inflight(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        _patch_pipeline(monkeypatch, _cancel_aware_slow_pipeline(0.6))
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        task_id = queue.submit(
            TaskRequest(movie_name="Join", output_dir=str(tmp_path / "out"), max_retries=0)
        )
        start = time.monotonic()
        queue.shutdown(wait=True, timeout=10.0)
        elapsed = time.monotonic() - start
        task = queue.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.COMPLETED  # drained, not cancelled
        assert elapsed >= 0.4  # actually waited for the in-flight task

    def test_shutdown_timeout_cancels_inflight(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        _patch_pipeline(monkeypatch, _cancel_aware_slow_pipeline(10.0))
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        task_id = queue.submit(
            TaskRequest(movie_name="Cancel", output_dir=str(tmp_path / "out"), max_retries=0)
        )
        start = time.monotonic()
        queue.shutdown(wait=True, timeout=0.5)
        assert time.monotonic() - start < 5.0  # returned despite long task
        # The task is (eventually) terminal and cancelled.
        deadline = time.monotonic() + 5.0
        task = None
        while time.monotonic() < deadline:
            task = queue.get_task(task_id)
            if task is not None and task.is_terminal:
                break
            time.sleep(0.05)
        assert task is not None
        assert task.status == TaskStatus.CANCELLED

    def test_shutdown_wait_false_returns_fast(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        _patch_pipeline(monkeypatch, _cancel_aware_slow_pipeline(5.0))
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        queue.submit(
            TaskRequest(movie_name="Abandon", output_dir=str(tmp_path / "out"), max_retries=0)
        )
        time.sleep(0.2)  # let the worker start
        start = time.monotonic()
        queue.shutdown(wait=False)
        assert time.monotonic() - start < 2.0

    def test_submit_after_shutdown_raises(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", auto_start=False)
        queue.start()
        queue.shutdown()
        with pytest.raises(QueueShutdownError):
            queue.submit(TaskRequest(movie_name="Late"))

    def test_shutdown_wait_drains_queued_tasks(self, tmp_path, monkeypatch):
        """Queued tasks are drained (not orphaned) by a waiting shutdown."""
        monkeypatch.setenv("CI", "1")
        _patch_pipeline(monkeypatch, _cancel_aware_slow_pipeline(0.3))
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        first_id = queue.submit(
            TaskRequest(movie_name="Occupier", output_dir=str(tmp_path / "out"), max_retries=0)
        )
        queued_id = queue.submit(
            TaskRequest(movie_name="Queued", output_dir=str(tmp_path / "out2"), max_retries=0)
        )
        time.sleep(0.1)  # occupier running, the other still queued

        start = time.monotonic()
        queue.shutdown(wait=True)  # timeout=None must still return once drained
        elapsed = time.monotonic() - start
        assert elapsed < 5.0

        # The executor's workers drain already-submitted work: both tasks
        # ran to completion, matching pre-v0.9.2 ``shutdown(wait=True)``.
        first = queue.get_task(first_id)
        queued = queue.get_task(queued_id)
        assert first is not None and first.status == TaskStatus.COMPLETED
        assert queued is not None and queued.status == TaskStatus.COMPLETED

    def test_is_shutting_down_flag(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", auto_start=False)
        assert queue.is_shutting_down is False
        queue.start()
        queue.shutdown(wait=False)
        assert queue.is_shutting_down is True


# ════════════════════════════════════════════════════════════
#  API / daemon graceful shutdown
# ════════════════════════════════════════════════════════════


def _get_json(url: str, timeout: float = 5.0):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post_json(url: str, body: dict, timeout: float = 5.0):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class TestApiGracefulShutdown:
    """TaskAPIServer drain path: reject new work and report draining."""

    def test_drain_rejects_new_tasks_and_reports_state(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        _patch_pipeline(monkeypatch, _cancel_aware_slow_pipeline(2.0))
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "tasks",
            max_workers=1,
        )
        server.start(blocking=False)
        try:
            server.queue.submit(
                TaskRequest(
                    movie_name="Drain",
                    output_dir=str(tmp_path / "out"),
                    max_retries=0,
                )
            )
            time.sleep(0.2)  # let the worker start

            # stop() drains from another thread; the HTTP loop stays up
            # during the drain so probes can report the draining state.
            stop_thread = threading.Thread(target=server.stop, kwargs={"drain_timeout": 5.0})
            stop_thread.start()
            time.sleep(0.3)  # drain in progress

            # New submissions are rejected while draining.
            code, body = _post_json(f"{server.base_url}/tasks", {"movie_name": "Late"})
            assert code == 503
            assert "shutting down" in body["error"]

            # /ready reports not-ready (queue + shutdown checks fail).
            code, body = _get_json(f"{server.base_url}/ready")
            assert code == 503
            assert body["ready"] is False

            # /info exposes the draining flag.
            code, body = _get_json(f"{server.base_url}/info")
            assert code == 200
            assert body["shutting_down"] is True

            stop_thread.join(timeout=10.0)
        finally:
            server.stop(drain_timeout=0.1)

    def test_info_shows_shutting_down_after_stop(self, tmp_path):
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "tasks",
            max_workers=1,
        )
        server.start(blocking=False)
        try:
            server.stop()
            assert server.is_shutting_down is True
        finally:
            server.stop()


class TestDaemonDrain:
    """daemon.py graceful-shutdown helpers."""

    def test_drain_inflight_orders_begin_drain_then_queue_shutdown(self):
        from movie_narrator.cloud.daemon import drain_inflight

        server = MagicMock()
        queue = MagicMock()
        drain_inflight(server, queue, 12.0)
        server.begin_drain.assert_called_once_with(12.0)
        queue.shutdown.assert_called_once_with(wait=True, timeout=12.0)

    def test_graceful_shutdown_timeout_default(self, monkeypatch):
        monkeypatch.setenv("CI", "1")
        monkeypatch.delenv("MN_GRACEFUL_SHUTDOWN_TIMEOUT", raising=False)
        from movie_narrator import config as config_mod

        config_mod.get_settings.cache_clear()
        from movie_narrator.cloud.daemon import graceful_shutdown_timeout

        assert graceful_shutdown_timeout() == 30.0

    def test_graceful_shutdown_timeout_from_env(self, monkeypatch):
        monkeypatch.setenv("CI", "1")
        monkeypatch.setenv("MN_GRACEFUL_SHUTDOWN_TIMEOUT", "7.5")
        from movie_narrator import config as config_mod

        config_mod.get_settings.cache_clear()
        from movie_narrator.cloud.daemon import graceful_shutdown_timeout

        assert graceful_shutdown_timeout() == 7.5


# ════════════════════════════════════════════════════════════
#  End-to-end checkpoint wiring through the queue
# ════════════════════════════════════════════════════════════


class TestQueueCheckpointWiring:
    """The queue threads CheckpointStore through to the worker."""

    def test_checkpoint_persisted_during_run_then_cleaned(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")

        def fake_pipeline(ctx, **kwargs):
            ctx.services.console.step("resolve_video")
            ctx.services.console.step_ok("resolve_video", 0.1)
            # Pause so the test can observe the in-flight checkpoint.
            controller = kwargs.get("controller")
            deadline = time.time() + 3.0
            while time.time() < deadline:
                if controller is not None and controller.is_cancelled():
                    raise PipelineCancelled()
                time.sleep(0.02)
            ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
            return ctx

        _patch_pipeline(monkeypatch, fake_pipeline)
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        try:
            task_id = queue.submit(
                TaskRequest(
                    movie_name="Wired",
                    output_dir=str(tmp_path / "out"),
                    max_retries=0,
                )
            )
            # The checkpoint for the completed step must appear while the
            # worker is paused inside the pipeline.
            deadline = time.monotonic() + 5.0
            cp = None
            while time.monotonic() < deadline:
                cp = queue.checkpoint_store.load(task_id)
                if cp is not None:
                    break
                time.sleep(0.05)
            assert cp is not None
            assert cp.completed_step == "resolve_video"

            # After completion the checkpoint is removed.
            result = queue.wait(task_id, timeout=10, poll_interval=0.1)
            assert result is not None and result.succeeded is True
            assert queue.checkpoint_store.load(task_id) is None
        finally:
            queue.shutdown(wait=False)
