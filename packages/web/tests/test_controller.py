"""Tests for TaskController — the web package's cooperative cancel controller.

Moved from ``tests/test_pipeline_cancel.py`` during the WebUI split.
These tests verify that ``TaskController`` (now in ``movie_narrator_web``)
works correctly with the core engine's ``check_cancelled`` mechanism.
"""

from __future__ import annotations

import threading
import time

import pytest

from movie_narrator.pipeline.errors import PipelineCancelled, check_cancelled
from movie_narrator_web.controller import TaskController


class TestTaskController:
    def test_task_controller_cancel(self):
        """TaskController: cancel() -> is_cancelled() True -> raises."""
        ctrl = TaskController()
        assert not ctrl.is_cancelled()
        check_cancelled(ctrl)  # should not raise
        ctrl.cancel()
        assert ctrl.is_cancelled()
        with pytest.raises(PipelineCancelled):
            check_cancelled(ctrl)

    def test_task_controller_reset(self):
        """reset() clears the cancel flag."""
        ctrl = TaskController()
        ctrl.cancel()
        assert ctrl.is_cancelled()
        ctrl.reset()
        assert not ctrl.is_cancelled()

    def test_task_controller_thread_safety(self):
        """cancel() from one thread is visible to is_cancelled() in another."""
        ctrl = TaskController()
        results: list[bool] = []

        def _poll():
            for _ in range(50):
                results.append(ctrl.is_cancelled())
                time.sleep(0.01)

        t = threading.Thread(target=_poll, daemon=True)
        t.start()
        time.sleep(0.1)
        ctrl.cancel()
        t.join()

        # Before cancel: all False; after cancel: at least one True
        assert not any(results[:5])  # early polls before cancel
        assert results[-1]  # last poll after cancel
