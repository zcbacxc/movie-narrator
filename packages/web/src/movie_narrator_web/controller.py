"""Cooperative cancel controller for the Web API.

Identical pattern to GradioController: threading.Event for cross-thread
cancel signaling. Pipeline thread polls is_cancelled(), UI thread calls cancel().
"""

from __future__ import annotations

import threading


class TaskController:
    """Thread-safe cooperative cancel flag."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation (called by REST DELETE or WS cancel)."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """Check if cancellation was requested (called by pipeline thread)."""
        return self._event.is_set()

    def reset(self) -> None:
        """Clear the cancel flag for a new run."""
        self._event.clear()
