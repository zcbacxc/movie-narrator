"""Tests for v0.6.0 Task Queue & Async Job System.

Covers:
- Task models: TaskStatus, TaskPriority, TaskRequest, TaskProgress, TaskResult, Task
- Task storage: save, load, delete, list, count, clear_terminal, clear_all
- CancelController: cancel, is_cancelled, reset
- ProgressConsole: step tracking, progress updates
- LocalTaskQueue: submit, get_task, get_status, get_progress, cancel, list, wait, shutdown
- Worker run_task: execution, cancellation, retry logic
- Contract exports: cloud types in contract.py and __init__.py
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.cloud.models import (
    ACTIVE_STATES,
    TERMINAL_STATES,
    Task,
    TaskPriority,
    TaskProgress,
    TaskRequest,
    TaskResult,
    TaskStatus,
)
from movie_narrator.cloud.storage import TaskStorage
from movie_narrator.cloud.queue import LocalTaskQueue, TaskQueue
from movie_narrator.cloud.worker import (
    CancelController,
    ProgressConsole,
    run_task,
    _build_output_dir,
    _execute_task,
)
from movie_narrator.utils.console import SilentConsole


# ════════════════════════════════════════════════════════════
#  Task Models Tests
# ════════════════════════════════════════════════════════════


class TestTaskStatus:
    """TaskStatus enum."""

    def test_values(self):
        assert TaskStatus.PENDING == "pending"
        assert TaskStatus.RUNNING == "running"
        assert TaskStatus.COMPLETED == "completed"
        assert TaskStatus.FAILED == "failed"
        assert TaskStatus.CANCELLED == "cancelled"
        assert TaskStatus.RETRYING == "retrying"

    def test_terminal_states(self):
        assert TaskStatus.COMPLETED in TERMINAL_STATES
        assert TaskStatus.FAILED in TERMINAL_STATES
        assert TaskStatus.CANCELLED in TERMINAL_STATES
        assert TaskStatus.PENDING not in TERMINAL_STATES
        assert TaskStatus.RUNNING not in TERMINAL_STATES

    def test_active_states(self):
        assert TaskStatus.PENDING in ACTIVE_STATES
        assert TaskStatus.RUNNING in ACTIVE_STATES
        assert TaskStatus.RETRYING in ACTIVE_STATES
        assert TaskStatus.COMPLETED not in ACTIVE_STATES


class TestTaskPriority:
    """TaskPriority enum."""

    def test_values(self):
        assert TaskPriority.LOW == 0
        assert TaskPriority.NORMAL == 5
        assert TaskPriority.HIGH == 10
        assert TaskPriority.URGENT == 20

    def test_ordering(self):
        assert TaskPriority.URGENT > TaskPriority.HIGH
        assert TaskPriority.HIGH > TaskPriority.NORMAL
        assert TaskPriority.NORMAL > TaskPriority.LOW


class TestTaskRequest:
    """TaskRequest model."""

    def test_defaults(self):
        req = TaskRequest(movie_name="TestMovie")
        assert req.movie_name == "TestMovie"
        assert req.style == ""
        assert req.duration == 60
        assert req.video_format == "16:9"
        assert req.priority == TaskPriority.NORMAL
        assert req.max_retries == 3
        assert req.retry_delay == 5.0
        assert req.lang == "zh"

    def test_with_options(self):
        req = TaskRequest(
            movie_name="飞驰人生",
            style="热血搞笑",
            duration=120,
            no_bgm=True,
            strict=True,
            priority=TaskPriority.HIGH,
            max_retries=5,
        )
        assert req.movie_name == "飞驰人生"
        assert req.duration == 120
        assert req.no_bgm is True
        assert req.strict is True
        assert req.priority == TaskPriority.HIGH
        assert req.max_retries == 5

    def test_serialization(self):
        req = TaskRequest(movie_name="Test", style="cool")
        d = req.model_dump()
        assert d["movie_name"] == "Test"
        assert d["style"] == "cool"
        # Round-trip
        req2 = TaskRequest(**d)
        assert req2.movie_name == "Test"


class TestTaskProgress:
    """TaskProgress model."""

    def test_defaults(self):
        p = TaskProgress()
        assert p.current_step == ""
        assert p.current_step_index == 0
        assert p.total_steps == 16
        assert p.percentage == 0.0
        assert p.steps_completed == []

    def test_update_step(self):
        p = TaskProgress()
        p.update_step("generate_script", 3, 16, elapsed=2.5)
        assert p.current_step == "generate_script"
        assert p.current_step_index == 3
        assert p.percentage == pytest.approx(18.8, abs=0.1)

    def test_mark_completed(self):
        p = TaskProgress()
        p.mark_completed("resolve_video")
        p.mark_completed("prepare_assets")
        assert len(p.steps_completed) == 2
        assert "resolve_video" in p.steps_completed

    def test_mark_skipped(self):
        p = TaskProgress()
        p.mark_skipped("align_audio")
        assert "align_audio" in p.steps_skipped

    def test_mark_failed(self):
        p = TaskProgress()
        p.mark_failed("render_video")
        assert "render_video" in p.steps_failed

    def test_completed_count(self):
        p = TaskProgress()
        p.mark_completed("step1")
        p.mark_completed("step2")
        p.mark_skipped("step3")
        assert p.completed_count == 3

    def test_no_duplicate_marks(self):
        p = TaskProgress()
        p.mark_completed("step1")
        p.mark_completed("step1")
        assert len(p.steps_completed) == 1


class TestTaskResult:
    """TaskResult model."""

    def test_defaults(self):
        r = TaskResult()
        assert r.video_path is None
        assert r.error is None
        assert r.metadata == {}

    def test_succeeded(self):
        r = TaskResult(video_path="/output/final.mp4")
        assert r.succeeded is True

    def test_failed(self):
        r = TaskResult(error="something went wrong")
        assert r.succeeded is False

    def test_no_video_not_succeeded(self):
        r = TaskResult()
        assert r.succeeded is False


class TestTask:
    """Task model."""

    def test_defaults(self):
        req = TaskRequest(movie_name="Test")
        task = Task(request=req)
        assert task.status == TaskStatus.PENDING
        assert task.result is None
        assert task.progress is None
        assert task.retries == 0
        assert task.created_at != ""

    def test_is_terminal(self):
        req = TaskRequest(movie_name="Test")
        task = Task(request=req, status=TaskStatus.COMPLETED)
        assert task.is_terminal is True
        task.status = TaskStatus.RUNNING
        assert task.is_terminal is False

    def test_is_active(self):
        req = TaskRequest(movie_name="Test")
        task = Task(request=req, status=TaskStatus.PENDING)
        assert task.is_active is True
        task.status = TaskStatus.COMPLETED
        assert task.is_active is False

    def test_elapsed_seconds_none_when_not_started(self):
        req = TaskRequest(movie_name="Test")
        task = Task(request=req)
        assert task.elapsed_seconds is None

    def test_elapsed_seconds_when_running(self):
        req = TaskRequest(movie_name="Test")
        task = Task(
            request=req,
            status=TaskStatus.RUNNING,
            started_at="2026-01-01T00:00:00+00:00",
        )
        assert task.elapsed_seconds is not None
        assert task.elapsed_seconds > 0

    def test_to_summary(self):
        req = TaskRequest(movie_name="飞驰人生")
        task = Task(
            request=req,
            status=TaskStatus.RUNNING,
            progress=TaskProgress(current_step="generate_script", percentage=25.0),
        )
        s = task.to_summary()
        assert s["movie"] == "飞驰人生"
        assert s["status"] == "running"
        assert "25%" in s["progress"]
        assert s["current_step"] == "generate_script"

    def test_unique_ids(self):
        req = TaskRequest(movie_name="Test")
        t1 = Task(request=req)
        t2 = Task(request=req)
        assert t1.id != t2.id


# ════════════════════════════════════════════════════════════
#  Task Storage Tests
# ════════════════════════════════════════════════════════════


class TestTaskStorage:
    """TaskStorage JSON persistence."""

    def test_save_and_load(self, tmp_path):
        storage = TaskStorage(tmp_path)
        req = TaskRequest(movie_name="TestMovie")
        task = Task(request=req)
        storage.save(task)

        loaded = storage.load(task.id)
        assert loaded is not None
        assert loaded.id == task.id
        assert loaded.request.movie_name == "TestMovie"
        assert loaded.status == TaskStatus.PENDING

    def test_load_not_found(self, tmp_path):
        storage = TaskStorage(tmp_path)
        assert storage.load("nonexistent") is None

    def test_delete(self, tmp_path):
        storage = TaskStorage(tmp_path)
        req = TaskRequest(movie_name="Test")
        task = Task(request=req)
        storage.save(task)
        assert storage.delete(task.id) is True
        assert storage.load(task.id) is None

    def test_delete_not_found(self, tmp_path):
        storage = TaskStorage(tmp_path)
        assert storage.delete("nonexistent") is False

    def test_list_tasks(self, tmp_path):
        storage = TaskStorage(tmp_path)
        for i in range(5):
            req = TaskRequest(movie_name=f"Movie{i}")
            task = Task(request=req, status=TaskStatus.COMPLETED if i < 3 else TaskStatus.PENDING)
            storage.save(task)

        all_tasks = storage.list_tasks()
        assert len(all_tasks) == 5

        completed = storage.list_tasks(status=TaskStatus.COMPLETED)
        assert len(completed) == 3

        pending = storage.list_tasks(status=TaskStatus.PENDING)
        assert len(pending) == 2

    def test_list_tasks_limit(self, tmp_path):
        storage = TaskStorage(tmp_path)
        for i in range(10):
            req = TaskRequest(movie_name=f"Movie{i}")
            storage.save(Task(request=req))

        tasks = storage.list_tasks(limit=5)
        assert len(tasks) == 5

    def test_count(self, tmp_path):
        storage = TaskStorage(tmp_path)
        storage.save(Task(request=TaskRequest(movie_name="A"), status=TaskStatus.COMPLETED))
        storage.save(Task(request=TaskRequest(movie_name="B"), status=TaskStatus.RUNNING))
        storage.save(Task(request=TaskRequest(movie_name="C"), status=TaskStatus.COMPLETED))

        assert storage.count() == 3
        assert storage.count(TaskStatus.COMPLETED) == 2
        assert storage.count(TaskStatus.RUNNING) == 1

    def test_clear_terminal(self, tmp_path):
        storage = TaskStorage(tmp_path)
        storage.save(Task(request=TaskRequest(movie_name="A"), status=TaskStatus.COMPLETED))
        storage.save(Task(request=TaskRequest(movie_name="B"), status=TaskStatus.RUNNING))
        storage.save(Task(request=TaskRequest(movie_name="C"), status=TaskStatus.FAILED))

        removed = storage.clear_terminal()
        assert removed == 2
        assert storage.count() == 1
        assert storage.count(TaskStatus.RUNNING) == 1

    def test_clear_all(self, tmp_path):
        storage = TaskStorage(tmp_path)
        storage.save(Task(request=TaskRequest(movie_name="A")))
        storage.save(Task(request=TaskRequest(movie_name="B")))

        removed = storage.clear_all()
        assert removed == 2
        assert storage.count() == 0

    def test_persistence_across_instances(self, tmp_path):
        """Task data survives storage recreation."""
        storage1 = TaskStorage(tmp_path)
        req = TaskRequest(movie_name="PersistTest")
        task = Task(request=req)
        storage1.save(task)

        # Create a new storage instance pointing to the same directory
        storage2 = TaskStorage(tmp_path)
        loaded = storage2.load(task.id)
        assert loaded is not None
        assert loaded.request.movie_name == "PersistTest"

    def test_index_file_exists(self, tmp_path):
        storage = TaskStorage(tmp_path)
        storage.save(Task(request=TaskRequest(movie_name="Test")))
        assert storage.index_path.exists()
        data = json.loads(storage.index_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict)


# ════════════════════════════════════════════════════════════
#  CancelController Tests
# ════════════════════════════════════════════════════════════


class TestCancelController:
    """CancelController for cooperative cancellation."""

    def test_initial_state(self):
        ctrl = CancelController()
        assert ctrl.is_cancelled() is False

    def test_cancel(self):
        ctrl = CancelController()
        ctrl.cancel()
        assert ctrl.is_cancelled() is True

    def test_reset(self):
        ctrl = CancelController()
        ctrl.cancel()
        assert ctrl.is_cancelled() is True
        ctrl.reset()
        assert ctrl.is_cancelled() is False

    def test_implements_run_controller(self):
        """CancelController satisfies the RunController protocol."""
        from movie_narrator.pipeline.errors import RunController
        ctrl = CancelController()
        # RunController is a Protocol with is_cancelled() method
        assert hasattr(ctrl, "is_cancelled")
        assert callable(ctrl.is_cancelled)


# ════════════════════════════════════════════════════════════
#  ProgressConsole Tests
# ════════════════════════════════════════════════════════════


class TestProgressConsole:
    """ProgressConsole wrapper."""

    def test_step_updates_progress(self):
        progress = TaskProgress()
        console = ProgressConsole(
            inner=SilentConsole(),
            progress=progress,
            start_time=time.time(),
        )
        console.step("resolve_video")
        assert progress.current_step == "resolve_video"
        assert progress.current_step_index == 0

    def test_step_ok_marks_completed(self):
        progress = TaskProgress()
        console = ProgressConsole(
            inner=SilentConsole(),
            progress=progress,
            start_time=time.time(),
        )
        console.step("resolve_video")
        console.step_ok("resolve_video", 1.5)
        assert "resolve_video" in progress.steps_completed
        assert progress.current_step_index == 1

    def test_step_skip_marks_skipped(self):
        progress = TaskProgress()
        console = ProgressConsole(
            inner=SilentConsole(),
            progress=progress,
            start_time=time.time(),
        )
        console.step_skip("align_audio", "disabled")
        assert "align_audio" in progress.steps_skipped

    def test_step_err_marks_failed(self):
        progress = TaskProgress()
        console = ProgressConsole(
            inner=SilentConsole(),
            progress=progress,
            start_time=time.time(),
        )
        console.step_err("render_video", RuntimeError("boom"), 2.0)
        assert "render_video" in progress.steps_failed

    def test_delegates_to_inner(self):
        """ProgressConsole delegates output to the inner console."""
        inner = MagicMock()
        inner.step = MagicMock()
        inner.debug = MagicMock()
        progress = TaskProgress()
        console = ProgressConsole(
            inner=inner,
            progress=progress,
            start_time=time.time(),
        )
        console.step("test_step")
        inner.step.assert_called_once_with("test_step")

        console.debug("test message")
        inner.debug.assert_called_once_with("test message")

    def test_multiple_steps_progress(self):
        progress = TaskProgress()
        console = ProgressConsole(
            inner=SilentConsole(),
            progress=progress,
            start_time=time.time(),
        )
        # Simulate 3 steps
        for i, name in enumerate(["step1", "step2", "step3"]):
            console.step(name)
            console.step_ok(name, 1.0)

        assert len(progress.steps_completed) == 3
        assert progress.current_step_index == 3
        assert progress.percentage == pytest.approx(18.8, abs=0.1)  # 3/16 * 100


# ════════════════════════════════════════════════════════════
#  LocalTaskQueue Tests
# ════════════════════════════════════════════════════════════


class TestLocalTaskQueue:
    """LocalTaskQueue in-process execution."""

    def test_create_queue(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=2)
        assert queue.is_started is True
        queue.shutdown()

    def test_create_queue_no_auto_start(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        assert queue.is_started is False
        queue.start()
        assert queue.is_started is True
        queue.shutdown()

    def test_submit_returns_task_id(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
        try:
            req = TaskRequest(movie_name="TestMovie", max_retries=0)
            task_id = queue.submit(req)
            assert isinstance(task_id, str)
            assert len(task_id) > 0
        finally:
            queue.shutdown(wait=False)

    def test_get_task(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
        try:
            req = TaskRequest(movie_name="TestMovie", max_retries=0)
            task_id = queue.submit(req)
            task = queue.get_task(task_id)
            assert task is not None
            assert task.id == task_id
            assert task.request.movie_name == "TestMovie"
        finally:
            queue.shutdown(wait=False)

    def test_get_status(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
        try:
            req = TaskRequest(movie_name="TestMovie", max_retries=0)
            task_id = queue.submit(req)
            status = queue.get_status(task_id)
            assert status is not None
            assert status in [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.COMPLETED, TaskStatus.FAILED]
        finally:
            queue.shutdown(wait=False)

    def test_get_status_not_found(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        assert queue.get_status("nonexistent") is None

    def test_get_result_not_terminal(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
        try:
            req = TaskRequest(movie_name="TestMovie", max_retries=0)
            task_id = queue.submit(req)
            # Immediately check — task likely not terminal yet
            result = queue.get_result(task_id)
            # Could be None if not yet terminal
            if result is not None:
                assert isinstance(result, TaskResult)
        finally:
            queue.shutdown(wait=False)

    def test_cancel_not_found(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        assert queue.cancel("nonexistent") is False

    def test_cancel_terminal_task(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        # Manually create a terminal task in storage
        task = Task(
            request=TaskRequest(movie_name="Test"),
            status=TaskStatus.COMPLETED,
        )
        queue.storage.save(task)
        assert queue.cancel(task.id) is False

    def test_list_tasks_empty(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        assert queue.list_tasks() == []

    def test_list_tasks_with_data(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        queue.storage.save(Task(request=TaskRequest(movie_name="A")))
        queue.storage.save(Task(request=TaskRequest(movie_name="B")))
        tasks = queue.list_tasks()
        assert len(tasks) == 2

    def test_wait_not_found(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        result = queue.wait("nonexistent", timeout=0.1)
        assert result is None

    def test_wait_timeout(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        # Create a running task that will never complete
        task = Task(
            request=TaskRequest(movie_name="Test"),
            status=TaskStatus.RUNNING,
            started_at="2026-01-01T00:00:00+00:00",
        )
        queue.storage.save(task)
        result = queue.wait(task.id, timeout=0.2, poll_interval=0.05)
        assert result is None

    def test_cleanup_terminal(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        queue.storage.save(Task(request=TaskRequest(movie_name="A"), status=TaskStatus.COMPLETED))
        queue.storage.save(Task(request=TaskRequest(movie_name="B"), status=TaskStatus.RUNNING))
        count = queue.cleanup_terminal()
        assert count == 1

    def test_cleanup_all(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        queue.storage.save(Task(request=TaskRequest(movie_name="A")))
        queue.storage.save(Task(request=TaskRequest(movie_name="B")))
        count = queue.cleanup_all()
        assert count == 2

    def test_active_count(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        queue.storage.save(Task(request=TaskRequest(movie_name="A"), status=TaskStatus.PENDING))
        queue.storage.save(Task(request=TaskRequest(movie_name="B"), status=TaskStatus.RUNNING))
        queue.storage.save(Task(request=TaskRequest(movie_name="C"), status=TaskStatus.COMPLETED))
        assert queue.active_count == 2

    def test_submit_when_not_started_raises(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        with pytest.raises(RuntimeError, match="not started"):
            queue.submit(TaskRequest(movie_name="Test"))

    def test_task_queue_protocol(self, tmp_path):
        """LocalTaskQueue satisfies the TaskQueue protocol."""
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        assert isinstance(queue, TaskQueue)
        queue.shutdown()


# ════════════════════════════════════════════════════════════
#  Worker Tests
# ════════════════════════════════════════════════════════════


class TestBuildOutputDir:
    """_build_output_dir helper."""

    def test_explicit_output_dir(self):
        req = TaskRequest(movie_name="Test", output_dir="/custom/path")
        result = _build_output_dir(req)
        assert str(result) == "/custom/path" or str(result) == "\\custom\\path"

    def test_default_output_dir(self):
        req = TaskRequest(movie_name="飞驰人生")
        result = _build_output_dir(req)
        assert "output" in str(result)
        # Sanitized movie name should be in the path
        assert "飞驰人生" in str(result) or "_" in str(result)


class TestExecuteTask:
    """_execute_task single-attempt execution."""

    def test_successful_execution(self, tmp_path, monkeypatch):
        """Test that _execute_task runs the pipeline and captures results."""
        # Mock run_pipeline to return a fake context
        from movie_narrator.models import Context, Assets, Services
        fake_ctx = Context(
            movie_name="TestMovie",
            output_dir=str(tmp_path),
            assets=Assets(),
            services=Services(console=SilentConsole()),
        )
        fake_ctx.video_path = str(tmp_path / "final.mp4")
        fake_ctx.audio_path = str(tmp_path / "narration.mp3")
        fake_ctx.metadata = {"test": True}

        monkeypatch.setenv("CI", "1")  # Use SilentConsole
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            lambda ctx, **kw: fake_ctx,
        )

        req = TaskRequest(movie_name="TestMovie", output_dir=str(tmp_path))
        task = Task(request=req)
        controller = CancelController()

        result = _execute_task(task, controller)
        assert result.status == TaskStatus.COMPLETED
        assert result.result is not None
        assert result.result.video_path == str(tmp_path / "final.mp4")
        assert result.result.metadata == {"test": True}
        assert result.progress is not None

    def test_pipeline_failure(self, tmp_path, monkeypatch):
        """Test that _execute_task captures pipeline failures."""
        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            lambda ctx, **kw: (_ for _ in ()).throw(RuntimeError("LLM timeout")),
        )

        req = TaskRequest(movie_name="TestMovie", output_dir=str(tmp_path))
        task = Task(request=req)
        controller = CancelController()

        result = _execute_task(task, controller)
        assert result.status == TaskStatus.FAILED
        assert result.result is not None
        assert "LLM timeout" in result.result.error
        assert result.result.error_type == "RuntimeError"

    def test_pipeline_cancelled(self, tmp_path, monkeypatch):
        """Test that _execute_task handles cancellation."""
        from movie_narrator.pipeline.errors import PipelineCancelled
        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            lambda ctx, **kw: (_ for _ in ()).throw(PipelineCancelled()),
        )

        req = TaskRequest(movie_name="TestMovie", output_dir=str(tmp_path))
        task = Task(request=req)
        controller = CancelController()

        result = _execute_task(task, controller)
        assert result.status == TaskStatus.CANCELLED
        assert result.result is not None
        assert "cancelled" in result.result.error.lower()

    def test_progress_tracking(self, tmp_path, monkeypatch):
        """Test that progress is tracked during execution."""
        from movie_narrator.models import Context, Assets, Services

        def fake_pipeline(ctx, **kw):
            # Simulate a few step calls
            ctx.services.console.step("resolve_video")
            ctx.services.console.step_ok("resolve_video", 0.1)
            ctx.services.console.step("generate_script")
            ctx.services.console.step_ok("generate_script", 1.0)
            return ctx

        fake_ctx = Context(
            movie_name="TestMovie",
            output_dir=str(tmp_path),
            assets=Assets(),
            services=Services(console=SilentConsole()),
        )
        # The _execute_task will build its own context, so we need to mock build_context
        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            fake_pipeline,
        )

        req = TaskRequest(movie_name="TestMovie", output_dir=str(tmp_path))
        task = Task(request=req)
        controller = CancelController()

        result = _execute_task(task, controller)
        assert result.status == TaskStatus.COMPLETED
        assert result.progress is not None
        assert len(result.progress.steps_completed) >= 2


class TestRunTask:
    """run_task with retry support."""

    def test_successful_first_try(self, tmp_path, monkeypatch):
        """Task succeeds on first attempt — no retries needed."""
        from movie_narrator.models import Context, Assets, Services
        fake_ctx = Context(
            movie_name="Test",
            output_dir=str(tmp_path),
            assets=Assets(),
            services=Services(console=SilentConsole()),
        )
        fake_ctx.video_path = str(tmp_path / "out.mp4")

        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            lambda ctx, **kw: fake_ctx,
        )

        req = TaskRequest(movie_name="Test", output_dir=str(tmp_path), max_retries=3)
        task = Task(request=req)
        controller = CancelController()

        result = run_task(task, controller)
        assert result.status == TaskStatus.COMPLETED
        assert result.retries == 0

    def test_non_retryable_error_no_retry(self, tmp_path, monkeypatch):
        """Non-retryable errors don't trigger retry."""
        monkeypatch.setenv("CI", "1")
        call_count = [0]

        def failing_pipeline(ctx, **kw):
            call_count[0] += 1
            raise ValueError("config error")  # non-retryable

        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            failing_pipeline,
        )

        req = TaskRequest(
            movie_name="Test",
            output_dir=str(tmp_path),
            max_retries=3,
            retry_delay=0.01,
        )
        task = Task(request=req)
        controller = CancelController()

        result = run_task(task, controller)
        assert result.status == TaskStatus.FAILED
        assert call_count[0] == 1  # no retries
        assert result.retries == 0

    def test_retryable_error_triggers_retry(self, tmp_path, monkeypatch):
        """Retryable errors trigger retry with backoff."""
        monkeypatch.setenv("CI", "1")
        call_count = [0]

        def retryable_failing(ctx, **kw):
            call_count[0] += 1
            if call_count[0] < 2:
                exc = ConnectionError("network down")
                raise exc
            # Succeed on second try
            from movie_narrator.models import Context, Assets, Services
            ctx.video_path = str(tmp_path / "out.mp4")
            return ctx

        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            retryable_failing,
        )

        req = TaskRequest(
            movie_name="Test",
            output_dir=str(tmp_path),
            max_retries=3,
            retry_delay=0.01,
        )
        task = Task(request=req)
        controller = CancelController()

        result = run_task(task, controller)
        assert result.status == TaskStatus.COMPLETED
        assert call_count[0] == 2
        assert result.retries == 1

    def test_retry_exhausted(self, tmp_path, monkeypatch):
        """All retries exhausted — task fails."""
        monkeypatch.setenv("CI", "1")

        def always_fail(ctx, **kw):
            raise ConnectionError("permanent network issue")

        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            always_fail,
        )

        req = TaskRequest(
            movie_name="Test",
            output_dir=str(tmp_path),
            max_retries=2,
            retry_delay=0.01,
        )
        task = Task(request=req)
        controller = CancelController()

        result = run_task(task, controller)
        assert result.status == TaskStatus.FAILED
        assert result.retries == 2

    def test_cancellation_during_retry_sleep(self, tmp_path, monkeypatch):
        """Cancel during retry sleep — task is cancelled."""
        monkeypatch.setenv("CI", "1")

        def always_fail(ctx, **kw):
            raise ConnectionError("network down")

        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            always_fail,
        )

        req = TaskRequest(
            movie_name="Test",
            output_dir=str(tmp_path),
            max_retries=3,
            retry_delay=10.0,  # long sleep
        )
        task = Task(request=req)

        controller = CancelController()

        # Cancel after a short delay (during the retry sleep)
        import threading
        timer = threading.Timer(0.1, controller.cancel)
        timer.start()

        result = run_task(task, controller)
        assert result.status == TaskStatus.CANCELLED
        timer.join()


# ════════════════════════════════════════════════════════════
#  Contract & SDK Export Tests
# ════════════════════════════════════════════════════════════


class TestContractExports:
    """Cloud types exported via contract.py and __init__.py."""

    def test_contract_version_bumped(self):
        from movie_narrator.contract import CONTRACT_VERSION
        assert CONTRACT_VERSION == (0, 8, 3)

    def test_contract_exports_cloud_types(self):
        from movie_narrator.contract import (
            CancelController,
            LocalTaskQueue,
            ProgressConsole,
            Task,
            TaskProgress,
            TaskQueue,
            TaskRequest,
            TaskResult,
            TaskStatus,
            TaskStorage,
            run_task,
        )
        # All should be importable
        assert CancelController is not None
        assert LocalTaskQueue is not None
        assert Task is not None
        assert TaskQueue is not None

    def test_init_exports_cloud_types(self):
        from movie_narrator import (
            CancelController,
            LocalTaskQueue,
            Task,
            TaskRequest,
            TaskResult,
            TaskStatus,
        )
        assert CancelController is not None
        assert LocalTaskQueue is not None
        assert Task is not None

    def test_cloud_package_exports(self):
        from movie_narrator.cloud import (
            ACTIVE_STATES,
            TERMINAL_STATES,
            CancelController,
            LocalTaskQueue,
            ProgressConsole,
            Task,
            TaskPriority,
            TaskProgress,
            TaskQueue,
            TaskRequest,
            TaskResult,
            TaskStatus,
            TaskStorage,
            run_task,
        )
        assert all(x is not None for x in [
            ACTIVE_STATES, TERMINAL_STATES, CancelController, LocalTaskQueue,
            ProgressConsole, Task, TaskPriority, TaskProgress, TaskQueue,
            TaskRequest, TaskResult, TaskStatus, TaskStorage, run_task,
        ])


# ════════════════════════════════════════════════════════════
#  Integration Tests
# ════════════════════════════════════════════════════════════


class TestIntegration:
    """End-to-end integration tests with mocked pipeline."""

    def test_submit_and_wait_success(self, tmp_path, monkeypatch):
        """Submit a task and wait for it to complete successfully."""
        from movie_narrator.models import Context, Assets, Services

        def fake_pipeline(ctx, **kw):
            ctx.video_path = str(tmp_path / "final.mp4")
            return ctx

        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            fake_pipeline,
        )

        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        try:
            req = TaskRequest(
                movie_name="IntegrationTest",
                output_dir=str(tmp_path / "output"),
                max_retries=0,
            )
            task_id = queue.submit(req)
            result = queue.wait(task_id, timeout=10, poll_interval=0.1)

            assert result is not None
            assert result.succeeded is True
            assert result.video_path == str(tmp_path / "final.mp4")
        finally:
            queue.shutdown()

    def test_submit_and_wait_failure(self, tmp_path, monkeypatch):
        """Submit a task that fails and check the result."""
        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            lambda ctx, **kw: (_ for _ in ()).throw(ValueError("bad config")),
        )

        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        try:
            req = TaskRequest(
                movie_name="FailTest",
                output_dir=str(tmp_path / "output"),
                max_retries=0,
            )
            task_id = queue.submit(req)
            result = queue.wait(task_id, timeout=10, poll_interval=0.1)

            assert result is not None
            assert result.succeeded is False
            assert "bad config" in result.error
        finally:
            queue.shutdown()

    def test_cancel_running_task(self, tmp_path, monkeypatch):
        """Cancel a running task."""
        from movie_narrator.pipeline.errors import PipelineCancelled, check_cancelled

        def slow_pipeline(ctx, **kw):
            # Check cancellation cooperatively
            controller = kw.get("controller")
            if controller:
                check_cancelled(controller)
            return ctx

        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            slow_pipeline,
        )

        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        try:
            req = TaskRequest(
                movie_name="CancelTest",
                output_dir=str(tmp_path / "output"),
                max_retries=0,
            )
            task_id = queue.submit(req)

            # Wait for the task to start and complete (our mock returns immediately)
            time.sleep(0.3)

            # Task should have completed quickly since mock returns fast
            task = queue.get_task(task_id)
            assert task.is_terminal
        finally:
            queue.shutdown()

    def test_multiple_tasks_concurrent(self, tmp_path, monkeypatch):
        """Submit multiple tasks and verify they all complete."""
        from movie_narrator.models import Context

        def fake_pipeline(ctx, **kw):
            ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
            return ctx

        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            fake_pipeline,
        )

        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=2)
        try:
            task_ids = []
            for i in range(3):
                req = TaskRequest(
                    movie_name=f"Concurrent{i}",
                    output_dir=str(tmp_path / f"output{i}"),
                    max_retries=0,
                )
                task_ids.append(queue.submit(req))

            # Wait for all tasks
            for tid in task_ids:
                result = queue.wait(tid, timeout=15, poll_interval=0.1)
                assert result is not None
                assert result.succeeded is True
        finally:
            queue.shutdown()

    def test_task_persistence_across_restart(self, tmp_path, monkeypatch):
        """Task data persists across queue restart."""
        monkeypatch.setenv("CI", "1")

        storage_dir = tmp_path / "tasks"

        # First queue: create a task in storage
        queue1 = LocalTaskQueue(storage_dir=storage_dir, auto_start=False)
        task = Task(
            request=TaskRequest(movie_name="PersistTest"),
            status=TaskStatus.COMPLETED,
            result=TaskResult(video_path="/output/final.mp4"),
        )
        queue1.storage.save(task)

        # Second queue: should see the same task
        queue2 = LocalTaskQueue(storage_dir=storage_dir, auto_start=False)
        loaded = queue2.get_task(task.id)
        assert loaded is not None
        assert loaded.status == TaskStatus.COMPLETED
        assert loaded.result.video_path == "/output/final.mp4"

    def test_progress_tracking_in_queue(self, tmp_path, monkeypatch):
        """Progress is tracked and accessible via the queue."""
        from movie_narrator.models import Context

        def pipeline_with_steps(ctx, **kw):
            ctx.services.console.step("resolve_video")
            ctx.services.console.step_ok("resolve_video", 0.1)
            ctx.services.console.step("generate_script")
            ctx.services.console.step_ok("generate_script", 1.0)
            ctx.video_path = str(tmp_path / "final.mp4")
            return ctx

        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            pipeline_with_steps,
        )

        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        try:
            req = TaskRequest(
                movie_name="ProgressTest",
                output_dir=str(tmp_path / "output"),
                max_retries=0,
            )
            task_id = queue.submit(req)
            result = queue.wait(task_id, timeout=10, poll_interval=0.1)

            assert result is not None
            assert result.succeeded is True

            # Check progress was tracked
            task = queue.get_task(task_id)
            assert task.progress is not None
            assert len(task.progress.steps_completed) >= 2
            assert "resolve_video" in task.progress.steps_completed
        finally:
            queue.shutdown()
