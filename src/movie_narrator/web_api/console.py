"""WebSocketConsole — buffered console for WebSocket UI consumption.

Thread-safe: pipeline thread writes lines, WS endpoint polls snapshot().
Identical protocol to GradioConsole — drop-in replacement.
"""

from __future__ import annotations

import threading
from contextlib import nullcontext
from typing import Tuple

from ..contract import BaseConsole


class WebSocketConsole(BaseConsole):
    """Buffered console for WebSocket UI consumption."""

    def __init__(self) -> None:
        self._lines: list[str] = []
        self._version: int = 0
        self._current_step: str = ""
        self._lock = threading.Lock()

    def _append(self, msg: str) -> None:
        with self._lock:
            self._lines.append(msg)
            self._version += 1

    def snapshot(self) -> Tuple[int, str, str]:
        """Return ``(version, joined_text, current_step)`` atomically."""
        with self._lock:
            return self._version, "\n".join(self._lines), self._current_step

    def clear(self) -> None:
        with self._lock:
            self._lines.clear()
            self._version += 1
            self._current_step = ""

    # ── Console Protocol ──────────────────────────────────

    def step(self, name: str) -> None:
        with self._lock:
            self._lines.append(f"▶ {name}...")
            self._current_step = name
            self._version += 1

    def step_ok(self, name: str, elapsed: float) -> None:
        self._append(f"✓ {name} ({elapsed:.1f}s)")

    def step_skip(self, name: str, reason: str) -> None:
        self._append(f"⏭ {name}: {reason}")

    def step_warn(self, name: str, reason: str) -> None:
        self._append(f"⚠ {name}: {reason}")

    def step_err(self, name: str, exc: Exception, elapsed: float) -> None:
        self._append(f"✗ {name}: {exc}")

    def warn(self, msg: str) -> None:
        self._append(f"⚠ {msg}")

    def debug(self, msg: str) -> None:
        pass  # WebSocket UI doesn't show debug-level messages

    def inline_warn(self, msg: str) -> None:
        self._append(f"⚠ {msg}")

    def final(self, msg: str) -> None:
        self._append(msg)

    def done(self, elapsed: float) -> None:
        self._append(f"Done in {elapsed:.1f}s")

    def cancelled(self, msg: str) -> None:
        self._append(f"⊘ Cancelled — {msg}")

    def progress(self, *args, **kwargs):
        return nullcontext()
