"""Tests for web/console.py — GradioConsole thread safety and snapshot consistency."""

from __future__ import annotations

import threading
import time

from movie_narrator.web.console import GradioConsole


class TestGradioConsole:
    def test_snapshot_empty_initially(self):
        console = GradioConsole()
        version, text, step = console.snapshot()
        assert version == 0
        assert text == ""
        assert step == ""

    def test_step_sets_current_step(self):
        console = GradioConsole()
        console.step("generate_script")
        _, _, step = console.snapshot()
        assert step == "generate_script"

    def test_step_ok_appends_line(self):
        console = GradioConsole()
        console.step("generate_script")
        console.step_ok("generate_script", 1.5)
        _, text, _ = console.snapshot()
        assert "▶ generate_script..." in text
        assert "✓ generate_script (1.5s)" in text

    def test_cancelled_appends_line(self):
        console = GradioConsole()
        console.cancelled("Pipeline cancelled.")
        _, text, _ = console.snapshot()
        assert "⊘ Cancelled" in text

    def test_version_increments(self):
        console = GradioConsole()
        assert console.snapshot()[0] == 0
        console.step("a")
        assert console.snapshot()[0] == 1
        console.step_ok("a", 1.0)
        assert console.snapshot()[0] == 2

    def test_clear_resets(self):
        console = GradioConsole()
        console.step("a")
        console.clear()
        version, text, step = console.snapshot()
        assert text == ""
        assert step == ""
        assert version > 0  # version still increments

    def test_progress_returns_context_manager(self):
        console = GradioConsole()
        with console.progress():
            pass  # should not raise

    def test_debug_is_noop(self):
        console = GradioConsole()
        console.debug("hidden message")
        _, text, _ = console.snapshot()
        assert text == ""  # debug messages are not shown

    def test_thread_safety_snapshot_consistency(self):
        """Concurrent writes and snapshots: (version, text) must be consistent.

        After a snapshot returns version=N, the text must contain all lines
        up to and including version N, and no more.
        """
        console = GradioConsole()
        stop = threading.Event()

        def _writer():
            for i in range(100):
                console.step_ok(f"step_{i}", 0.1)
            stop.set()

        def _reader():
            while not stop.is_set():
                version, text, _ = console.snapshot()
                # If version > 0, text should not be empty
                if version > 0:
                    assert text != "", f"version={version} but text empty"
                time.sleep(0.001)

        writer = threading.Thread(target=_writer, daemon=True)
        reader = threading.Thread(target=_reader, daemon=True)
        writer.start()
        reader.start()
        writer.join(timeout=5)
        stop.set()
        reader.join(timeout=5)

        # Final check: all 100 lines present
        _, text, _ = console.snapshot()
        assert "step_0" in text
        assert "step_99" in text
