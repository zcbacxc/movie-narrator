"""Tests for web/controller.py — GradioController cancel flag."""

from __future__ import annotations

import threading
import time

from movie_narrator.web.controller import GradioController


class TestGradioController:
    def test_initial_state_not_cancelled(self):
        ctrl = GradioController()
        assert not ctrl.is_cancelled()

    def test_cancel_sets_flag(self):
        ctrl = GradioController()
        ctrl.cancel()
        assert ctrl.is_cancelled()

    def test_reset_clears_flag(self):
        ctrl = GradioController()
        ctrl.cancel()
        assert ctrl.is_cancelled()
        ctrl.reset()
        assert not ctrl.is_cancelled()

    def test_reset_on_fresh_controller(self):
        """reset() on a non-cancelled controller is a no-op."""
        ctrl = GradioController()
        ctrl.reset()
        assert not ctrl.is_cancelled()

    def test_cancel_from_another_thread(self):
        """cancel() called from a different thread is visible immediately."""
        ctrl = GradioController()
        assert not ctrl.is_cancelled()

        def _cancel():
            time.sleep(0.05)
            ctrl.cancel()

        t = threading.Thread(target=_cancel, daemon=True)
        t.start()

        # Poll until cancel is visible
        for _ in range(100):
            if ctrl.is_cancelled():
                break
            time.sleep(0.01)

        t.join()
        assert ctrl.is_cancelled()

    def test_reuse_after_reset(self):
        """Controller can be reused for a second run after reset."""
        ctrl = GradioController()
        ctrl.cancel()
        assert ctrl.is_cancelled()
        ctrl.reset()
        assert not ctrl.is_cancelled()
        # Second run
        ctrl.cancel()
        assert ctrl.is_cancelled()
        ctrl.reset()
        assert not ctrl.is_cancelled()
