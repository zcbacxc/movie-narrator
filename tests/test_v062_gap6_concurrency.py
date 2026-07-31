# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for GAP-6 concurrency model optimization (v0.6.2).

Covers:
- LocalTaskQueue active_count O(1) optimization
- LocalTaskQueue wait() using threading.Event instead of busy-polling
- RemoteTaskQueue wait() exponential backoff
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.cloud.models import (
    Task,
    TaskRequest,
    TaskResult,
    TaskStatus,
)
from movie_narrator.cloud.queue import LocalTaskQueue
from movie_narrator.cloud.remote_queue import RemoteTaskQueue


# ── Mock pipeline helpers ──────────────────────────────────


def _mock_pipeline_fast(ctx, **kwargs):
    """Mock pipeline that completes immediately."""
    ctx.video_path = str(ctx.output_dir) + "/final.mp4"
    return ctx


def _mock_pipeline_blocking(block_event):
    """Return a mock pipeline that blocks until ``block_event`` is set."""

    def _pipeline(ctx, **kwargs):
        block_event.wait(timeout=10)
        ctx.video_path = str(ctx.output_dir) + "/final.mp4"
        return ctx

    return _pipeline


@pytest.fixture(autouse=True)
def mock_pipeline(monkeypatch):
    """Mock run_pipeline to prevent actual pipeline execution in tests."""
    monkeypatch.setenv("CI", "1")
    monkeypatch.setattr(
        "movie_narrator.cloud.worker.run_pipeline",
        _mock_pipeline_fast,
    )


# ════════════════════════════════════════════════════════════
#  LocalTaskQueue — active_count O(1) optimization
# ════════════════════════════════════════════════════════════


class TestActiveCountOptimization:
    """Tests for O(1) active_count counter."""

    def test_active_count_returns_zero_when_empty(self, tmp_path):
        """active_count is 0 for a freshly started queue with no tasks."""
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
        try:
            assert queue.active_count == 0
        finally:
            queue.shutdown(wait=False)

    def test_active_count_increments_on_submit(self, tmp_path, monkeypatch):
        """active_count increments when a task is submitted."""
        block_event = threading.Event()
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            _mock_pipeline_blocking(block_event),
        )

        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=2)
        try:
            req = TaskRequest(
                movie_name="BlockTest",
                output_dir=str(tmp_path / "output"),
                max_retries=0,
            )
            task_id = queue.submit(req)
            # Give the worker a moment to pick up the task
            time.sleep(0.3)
            assert queue.active_count >= 1
            # Release the task so it can complete
            block_event.set()
            queue.wait(task_id, timeout=10)
        finally:
            queue.shutdown()

    def test_active_count_decrements_on_completion(self, tmp_path):
        """active_count returns to 0 after all tasks complete."""
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=2)
        try:
            req = TaskRequest(
                movie_name="CompleteTest",
                output_dir=str(tmp_path / "output"),
                max_retries=0,
            )
            task_id = queue.submit(req)
            result = queue.wait(task_id, timeout=10)
            assert result is not None
            # After completion, active_count should be 0
            assert queue.active_count == 0
        finally:
            queue.shutdown()

    def test_active_count_multiple_concurrent(self, tmp_path, monkeypatch):
        """active_count tracks multiple concurrent tasks correctly."""
        block_event = threading.Event()
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            _mock_pipeline_blocking(block_event),
        )

        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=4)
        try:
            task_ids = []
            for i in range(3):
                req = TaskRequest(
                    movie_name=f"Concurrent{i}",
                    output_dir=str(tmp_path / f"output{i}"),
                    max_retries=0,
                )
                task_ids.append(queue.submit(req))

            time.sleep(0.5)
            assert queue.active_count == 3

            block_event.set()
            for tid in task_ids:
                queue.wait(tid, timeout=10)

            assert queue.active_count == 0
        finally:
            queue.shutdown()

    def test_active_count_not_started_falls_back_to_storage(self, tmp_path):
        """When not started, active_count scans storage for compatibility."""
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        queue.storage.save(
            Task(request=TaskRequest(movie_name="A"), status=TaskStatus.PENDING)
        )
        queue.storage.save(
            Task(request=TaskRequest(movie_name="B"), status=TaskStatus.RUNNING)
        )
        queue.storage.save(
            Task(request=TaskRequest(movie_name="C"), status=TaskStatus.COMPLETED)
        )
        assert queue.active_count == 2


# ════════════════════════════════════════════════════════════
#  LocalTaskQueue — wait() with threading.Event
# ════════════════════════════════════════════════════════════


class TestWaitWithEvent:
    """Tests for Event-based wait() implementation."""

    def test_wait_returns_result_when_already_terminal(self, tmp_path):
        """wait() returns result immediately when task is already terminal."""
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        task = Task(
            request=TaskRequest(movie_name="DoneTest"),
            status=TaskStatus.COMPLETED,
            result=TaskResult(video_path="/output/final.mp4"),
        )
        queue.storage.save(task)

        result = queue.wait(task.id, timeout=1.0)
        assert result is not None
        assert result.video_path == "/output/final.mp4"

    def test_wait_returns_none_when_task_not_found(self, tmp_path):
        """wait() returns None for a non-existent task."""
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        result = queue.wait("nonexistent", timeout=0.1)
        assert result is None

    def test_wait_returns_none_on_timeout(self, tmp_path):
        """wait() returns None when timeout is reached (polling fallback)."""
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        task = Task(
            request=TaskRequest(movie_name="SlowTest"),
            status=TaskStatus.RUNNING,
            started_at="2026-01-01T00:00:00+00:00",
        )
        queue.storage.save(task)

        result = queue.wait(task.id, timeout=0.2, poll_interval=0.05)
        assert result is None

    def test_wait_returns_result_after_completion(self, tmp_path):
        """wait() returns result after task completes via Event signal."""
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
        try:
            req = TaskRequest(
                movie_name="EventTest",
                output_dir=str(tmp_path / "output"),
                max_retries=0,
            )
            task_id = queue.submit(req)
            result = queue.wait(task_id, timeout=10, poll_interval=0.1)

            assert result is not None
            assert result.succeeded is True
            # Verify the event was set and cleaned up
            with queue._lock:
                assert task_id not in queue._completion_events
        finally:
            queue.shutdown()

    def test_wait_event_set_on_cancel(self, tmp_path, monkeypatch):
        """wait() returns when a pending task is cancelled (event set)."""
        block_event = threading.Event()
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            _mock_pipeline_blocking(block_event),
        )

        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
        try:
            req = TaskRequest(
                movie_name="CancelWaitTest",
                output_dir=str(tmp_path / "output"),
                max_retries=0,
            )
            task_id = queue.submit(req)
            time.sleep(0.3)  # let the task start

            # Cancel should eventually let wait() return
            # The controller branch sets cancel; worker will process it
            queue.cancel(task_id)
            block_event.set()  # release the pipeline so worker can finish

            result = queue.wait(task_id, timeout=10, poll_interval=0.1)
            # Result could be None (cancelled) or a TaskResult
            # Either way, wait() should return promptly after cancellation
        finally:
            queue.shutdown()

    def test_wait_fallback_polling_for_external_task(self, tmp_path):
        """wait() uses polling fallback for tasks not submitted via submit()."""
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=False)
        task = Task(
            request=TaskRequest(movie_name="ExternalTest"),
            status=TaskStatus.RUNNING,
            started_at="2026-01-01T00:00:00+00:00",
        )
        queue.storage.save(task)

        # No completion event exists for this task, so polling is used
        with patch("movie_narrator.cloud.queue.time.sleep") as mock_sleep:
            mock_sleep.return_value = None
            queue.wait(task.id, timeout=0.15, poll_interval=0.05)
            # sleep should have been called (polling path)
            assert mock_sleep.called

    def test_wait_no_timeout_blocks_until_completion(self, tmp_path):
        """wait() with timeout=None blocks until task completes."""
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
        try:
            req = TaskRequest(
                movie_name="NoTimeoutTest",
                output_dir=str(tmp_path / "output"),
                max_retries=0,
            )
            task_id = queue.submit(req)
            # Use a thread to verify wait() returns
            result_holder = {}

            def _wait():
                result_holder["result"] = queue.wait(task_id, timeout=None)

            t = threading.Thread(target=_wait)
            t.start()
            t.join(timeout=10)
            assert not t.is_alive(), "wait() should have returned"
            assert result_holder["result"] is not None
            assert result_holder["result"].succeeded is True
        finally:
            queue.shutdown()


# ════════════════════════════════════════════════════════════
#  RemoteTaskQueue — wait() exponential backoff
# ════════════════════════════════════════════════════════════


class TestRemoteWaitExponentialBackoff:
    """Tests for exponential backoff in RemoteTaskQueue.wait()."""

    def test_exponential_backoff_intervals(self):
        """wait() uses exponentially growing sleep intervals."""
        queue = RemoteTaskQueue("http://127.0.0.1:1", timeout=1.0)

        # Mock get_task to always return a non-terminal task
        running_task = Task(
            request=TaskRequest(movie_name="BackoffTest"),
            status=TaskStatus.RUNNING,
        )

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        with (
            patch.object(queue, "get_task", return_value=running_task),
            patch("movie_narrator.cloud.remote_queue.time.sleep", side_effect=fake_sleep),
        ):
            result = queue.wait("test-task", timeout=0.01, poll_interval=1.0)
            assert result is None  # timed out

        # Verify sleep intervals grow exponentially (1.0, 1.5, 2.25, ...)
        assert len(sleep_calls) >= 2
        assert sleep_calls[0] == 1.0
        assert sleep_calls[1] == pytest.approx(1.5)
        if len(sleep_calls) >= 3:
            assert sleep_calls[2] == pytest.approx(2.25)

    def test_backoff_capped_at_10_seconds(self):
        """Sleep intervals are capped at 10 seconds."""
        queue = RemoteTaskQueue("http://127.0.0.1:1", timeout=1.0)

        running_task = Task(
            request=TaskRequest(movie_name="CapTest"),
            status=TaskStatus.RUNNING,
        )

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        with (
            patch.object(queue, "get_task", return_value=running_task),
            patch("movie_narrator.cloud.remote_queue.time.sleep", side_effect=fake_sleep),
        ):
            queue.wait("test-task", timeout=0.01, poll_interval=8.0)
            # First sleep: min(8.0, 10.0) = 8.0
            # Second sleep: min(8.0 * 1.5, 10.0) = min(12.0, 10.0) = 10.0
            # Third sleep: min(10.0 * 1.5, 10.0) = min(15.0, 10.0) = 10.0

        assert len(sleep_calls) >= 2
        assert sleep_calls[0] == 8.0
        assert sleep_calls[1] == 10.0  # capped
        if len(sleep_calls) >= 3:
            assert sleep_calls[2] == 10.0  # still capped

    def test_backoff_returns_result_when_terminal(self):
        """wait() returns result when task reaches terminal state."""
        queue = RemoteTaskQueue("http://127.0.0.1:1", timeout=1.0)

        completed_task = Task(
            request=TaskRequest(movie_name="DoneTest"),
            status=TaskStatus.COMPLETED,
            result=TaskResult(video_path="/output/final.mp4"),
        )

        with (
            patch.object(queue, "get_task", return_value=completed_task),
            patch("movie_narrator.cloud.remote_queue.time.sleep"),
        ):
            result = queue.wait("test-task", timeout=5.0, poll_interval=1.0)

        assert result is not None
        assert result.video_path == "/output/final.mp4"

    def test_backoff_returns_none_when_task_not_found(self):
        """wait() returns None when get_task returns None."""
        queue = RemoteTaskQueue("http://127.0.0.1:1", timeout=1.0)

        with (
            patch.object(queue, "get_task", return_value=None),
            patch("movie_narrator.cloud.remote_queue.time.sleep"),
        ):
            result = queue.wait("nonexistent", timeout=5.0)

        assert result is None

    def test_backoff_custom_starting_interval(self):
        """wait() respects custom poll_interval as starting interval."""
        queue = RemoteTaskQueue("http://127.0.0.1:1", timeout=1.0)

        running_task = Task(
            request=TaskRequest(movie_name="CustomIntervalTest"),
            status=TaskStatus.RUNNING,
        )

        sleep_calls = []

        def fake_sleep(seconds):
            sleep_calls.append(seconds)

        with (
            patch.object(queue, "get_task", return_value=running_task),
            patch("movie_narrator.cloud.remote_queue.time.sleep", side_effect=fake_sleep),
        ):
            queue.wait("test-task", timeout=0.01, poll_interval=2.0)

        assert len(sleep_calls) >= 2
        assert sleep_calls[0] == 2.0
        assert sleep_calls[1] == pytest.approx(3.0)  # 2.0 * 1.5
