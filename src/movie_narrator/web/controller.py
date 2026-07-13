"""Cooperative cancel controller for the Web UI.

The pipeline thread calls ``is_cancelled()`` at step boundaries (via
``check_cancelled`` in ``pipeline/runner.py``). The UI thread calls
``cancel()`` when the user clicks the Cancel button.

``controller=None`` means CLI mode — no cancel checks fire.
"""

from __future__ import annotations

import threading


class GradioController:
    """Thread-safe cooperative cancel flag.

    Uses ``threading.Event`` — set by UI thread, polled by pipeline
    thread. ``reset()`` is called at the start of each run so the
    controller can be reused across sessions.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        """Request cancellation (called by UI thread)."""
        self._event.set()

    def is_cancelled(self) -> bool:
        """Check if cancellation was requested (called by pipeline thread)."""
        return self._event.is_set()

    def reset(self) -> None:
        """Clear the cancel flag for a new run."""
        self._event.clear()
