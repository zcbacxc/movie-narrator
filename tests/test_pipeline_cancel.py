"""Tests for cooperative pipeline cancellation.

Verifies that ``check_cancelled`` raises ``PipelineCancelled`` when
the controller flag is set, and that ``controller=None`` (CLI mode)
never fires the check.
"""

from __future__ import annotations

import threading
import time

import pytest

from movie_narrator.pipeline.errors import (
    PipelineCancelled,
    RunController,
    check_cancelled,
)
from movie_narrator.web_api.controller import TaskController


class _StubController:
    """Minimal controller for testing the Protocol."""

    def __init__(self, cancelled: bool = False) -> None:
        self._cancelled = cancelled

    def is_cancelled(self) -> bool:
        return self._cancelled


class TestCheckCancelled:
    def test_none_controller_never_raises(self):
        """CLI mode: controller=None → no check fires."""
        check_cancelled(None)

    def test_not_cancelled_does_not_raise(self):
        check_cancelled(_StubController(cancelled=False))

    def test_cancelled_raises(self):
        with pytest.raises(PipelineCancelled):
            check_cancelled(_StubController(cancelled=True))

    def test_task_controller_cancel(self):
        """TaskController: cancel() → is_cancelled() True → raises."""
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
