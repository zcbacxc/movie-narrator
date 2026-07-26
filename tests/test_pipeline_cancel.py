"""Tests for cooperative pipeline cancellation (core engine).

Verifies that ``check_cancelled`` raises ``PipelineCancelled`` when
the controller flag is set, and that ``controller=None`` (CLI mode)
never fires the check.

TaskController-specific tests live in the movie-narrator-web repo
(``tests/test_controller.py``) since TaskController moved to
``movie_narrator_web`` during the WebUI split.
"""

from __future__ import annotations

import pytest

from movie_narrator.pipeline.errors import (
    PipelineCancelled,
    RunController,
    check_cancelled,
)


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
