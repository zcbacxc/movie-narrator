# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Console — output abstraction: UI rendering + log dispatch.

Improvements (v0.5.4+):
- ``build_console`` supports ``log_level`` / ``verbose`` / ``json_format`` / ``run_id``.
- ``PlainConsole.debug`` also writes to the console in verbose mode.
- Adds the ``step_timing`` context manager for sub-step performance timing.
- ``Services.logger`` auto-wires into ``AppLogger``.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator, Optional, Protocol, runtime_checkable

from .log import AppLogger, _JsonFormatter, _TextFormatter, generate_run_id
from .retention import cleanup_logs


# ── ANSI helpers ──────────────────────────────────────────────

_BLUE = "\033[94m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
_DIM = "\033[2m"
_RESET = "\033[0m"
_BOLD = "\033[1m"


def _fmt_time(seconds: float) -> str:
    if seconds < 1:
        return f"{seconds * 1000:.0f}ms"
    if seconds < 60:
        return f"{seconds:.1f}s"
    return f"{seconds / 60:.1f}m"


# ── Protocol ────────────────────────────────────────────────


@runtime_checkable
class Console(Protocol):
    """Output abstraction — console rendering + log dispatch."""

    def step(self, name: str) -> None:
        """Mark the start of a pipeline step.

        Args:
            name: The step's display name.
        """
        ...

    def step_ok(self, name: str, elapsed: float) -> None:
        """Report a step that completed successfully.

        Args:
            name: The step's display name.
            elapsed: Seconds the step took.
        """
        ...

    def step_skip(self, name: str, reason: str) -> None:
        """Report a step that was skipped.

        Args:
            name: The step's display name.
            reason: Why the step was skipped.
        """
        ...

    def step_warn(self, name: str, reason: str) -> None:
        """Report a step that completed with a warning.

        Args:
            name: The step's display name.
            reason: The warning message.
        """
        ...

    def step_err(self, name: str, exc: Exception, elapsed: float) -> None:
        """Report a step that failed.

        Args:
            name: The step's display name.
            exc: The exception that was raised.
            elapsed: Seconds the step took before failing.
        """
        ...

    def warn(self, msg: str) -> None:
        """Emit a warning message.

        Args:
            msg: The warning text.
        """
        ...

    def info(self, msg: str) -> None:
        """Emit an informational message.

        Args:
            msg: The message text.
        """
        ...

    def debug(self, msg: str) -> None:
        """Emit a debug message (silenced unless verbose).

        Args:
            msg: The debug text.
        """
        ...

    def inline_warn(self, msg: str) -> None:
        """Emit a warning that overrides the current line.

        Args:
            msg: The warning text.
        """
        ...

    def final(self, msg: str) -> None:
        """Emit the final summary message.

        Args:
            msg: The summary text.
        """
        ...

    def done(self, elapsed: float) -> None:
        """Report overall success.

        Args:
            elapsed: Total seconds for the run.
        """
        ...

    def cancelled(self, msg: str) -> None:
        """Report that the run was cancelled.

        Args:
            msg: The cancellation reason.
        """
        ...

    def progress(self, *args, **kwargs):
        """Return a progress indicator (e.g. a tqdm-like context manager)."""
        ...


# ── BaseConsole ─────────────────────────────────────────────


class BaseConsole:
    """Shared formatting helpers for Console implementations.

    Inherited by ``PlainConsole`` and ``GradioConsole`` to centralize
    common formatting logic.  Subclasses still implement the full
    ``Console`` Protocol via their own methods.
    """

    _fmt_time = staticmethod(_fmt_time)


# ── SilentConsole ──────────────────────────────────────────


class SilentConsole(BaseConsole):
    """No-op Console implementation.

    Used as the default ``services.console`` when a Context is built without
    an explicit Console. It satisfies the Console Protocol structurally
    (Python's runtime_checkable Protocol allows duck-typed instances),
    so step-internal ``ctx.services.console.debug(...)`` etc. never raise
    AttributeError. The progress() method returns None so callers that
    use it as a context manager (with pbar: ...) simply do nothing.
    """

    def step(self, name: str) -> None:
        """Record the start of a pipeline step.

        Args:
            name: The step's display name.
        """
        ...

    def step_ok(self, name: str, elapsed: float) -> None:
        """Mark the current step as successfully completed.

        Args:
            name: The step's display name.
            elapsed: Seconds the step took.
        """
        ...

    def step_skip(self, name: str, reason: str) -> None:
        """Mark the current step as skipped.

        Args:
            name: The step's display name.
            reason: Why the step was skipped.
        """
        ...

    def step_warn(self, name: str, reason: str) -> None:
        """Mark the current step as completed with warnings.

        Args:
            name: The step's display name.
            reason: The warning message.
        """
        ...

    def step_err(self, name: str, exc: Exception, elapsed: float) -> None:
        """Mark the current step as failed.

        Args:
            name: The step's display name.
            exc: The exception that was raised.
            elapsed: Seconds the step took before failing.
        """
        ...

    def warn(self, msg: str) -> None:
        """Emit a warning message.

        Args:
            msg: The warning text.
        """
        ...

    def info(self, msg: str) -> None:
        """Emit an informational message.

        Args:
            msg: The message text.
        """
        ...

    def debug(self, msg: str) -> None:
        """Emit a debug message.

        Args:
            msg: The debug text.
        """
        ...

    def inline_warn(self, msg: str) -> None:
        """Emit a warning message.

        Args:
            msg: The warning text.
        """
        ...

    def final(self, msg: str) -> None:
        """Print the final pipeline summary.

        Args:
            msg: The summary text.
        """
        ...

    def done(self, elapsed: float) -> None:
        """Mark the pipeline as complete.

        Args:
            elapsed: Total seconds for the run.
        """
        ...

    def cancelled(self, msg: str) -> None:
        """Mark the pipeline as cancelled.

        Args:
            msg: The cancellation reason.
        """
        ...

    def progress(self, *args, **kwargs):
        """Update progress display."""
        return None


# ── PlainConsole ────────────────────────────────────────────


class PlainConsole(BaseConsole):
    """Standard Console implementation: ANSI + print + tqdm + typer.echo.

    Args:
        logger: AppLogger instance for file logging.
        verbose: If True, debug messages are also printed to console.
    """

    def __init__(self, logger: AppLogger, verbose: bool = False) -> None:
        self._log = logger
        self._verbose = verbose

    # ── lifecycle events (called by runner) ──────────────────

    def step(self, name: str) -> None:
        """Record the start of a pipeline step."""
        print(f"{_BLUE}▶{_RESET} {name}", end="", flush=True)
        self._log.info(f"STEP_START {name}")

    def step_ok(self, name: str, elapsed: float) -> None:
        """Mark the current step as successfully completed."""
        t = _fmt_time(elapsed)
        print(f"\r{_GREEN}✓{_RESET} {name}  {_BOLD}{t}{_RESET}")
        self._log.info(f"STEP_OK {name} elapsed={elapsed:.3f}s")

    def step_skip(self, name: str, reason: str) -> None:
        """Mark the current step as skipped."""
        print(f"\r{_YELLOW}⏭{_RESET} {name}: {reason}")
        self._log.info(f"STEP_SKIP {name} reason={reason}")

    def step_warn(self, name: str, reason: str) -> None:
        """Mark the current step as completed with warnings."""
        print(f"\r{_YELLOW}⚠{_RESET} {name}: {reason}")
        self._log.warning(f"STEP_WARN {name} reason={reason}")

    def step_err(self, name: str, exc: Exception, elapsed: float) -> None:
        """Mark the current step as failed."""
        t = _fmt_time(elapsed)
        print(f"\r{_RED}✗{_RESET} {name}: {exc} {_YELLOW}({t}){_RESET}")
        self._log.error(f"STEP_ERR {name}", exc_info=True)

    def done(self, elapsed: float) -> None:
        """Final 'Done in ...' banner at end of pipeline."""
        print(f"\n{_BOLD}Done in {_fmt_time(elapsed)}{_RESET}")
        self._log.info(f"PIPELINE_DONE elapsed={elapsed:.3f}s")

    def cancelled(self, msg: str) -> None:
        """Cancel banner — distinct terminal path (not warn, not error)."""
        print(f"\n{_YELLOW}⊘ Cancelled{_RESET} {msg}")
        self._log.info(f"PIPELINE_CANCELLED {msg}")

    # ── in-process messages (called by steps directly) ─────

    def debug(self, msg: str) -> None:
        """Emit a debug message."""
        self._log.debug(msg)
        if self._verbose:
            print(f"{_DIM}  {msg}{_RESET}")

    def info(self, msg: str) -> None:
        """Emit an informational message."""
        self._log.info(msg)
        print(f"  {msg}")

    def inline_warn(self, msg: str) -> None:
        """Non-fatal in-process warning (e.g. partial metadata missing)."""
        print(f"{_YELLOW}⚠{_RESET} {msg}")
        self._log.warning(msg)

    def warn(self, msg: str) -> None:
        """CLI-level error notification → stderr + log WARNING.
        Used in cli.py exception handlers, paired with typer.Exit(1)."""
        import typer

        typer.echo(msg, err=True)
        self._log.warning(msg)

    # ── final result (called by cli layer) ─────────────────

    def final(self, msg: str) -> None:
        """Final result output — stdout + log INFO."""
        import typer

        typer.echo(msg)
        self._log.info(msg)

    # ── progress bar (passthrough to tqdm) ──────────────────

    def progress(self, *args, **kwargs):
        """Update progress display."""
        from tqdm import tqdm

        return tqdm(*args, **kwargs)


# ── Sub-step timing helper ─────────────────────────────────


@contextmanager
def step_timing(console: Any, label: str) -> Generator[None, None, None]:
    """Context manager for sub-step performance timing.

    Usage::

        with step_timing(ctx.services.console, "llm_api"):
            response = call_llm(...)

    Logs elapsed time at DEBUG level. Does not affect control flow.
    """
    start = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - start
        console.debug(f"TIMING {label} elapsed={elapsed:.3f}s")


# ── Factory ────────────────────────────────────────────────


def build_console(
    output_dir: Path,
    *,
    log_level: int = logging.DEBUG,
    verbose: bool = False,
    json_format: bool = False,
    run_id: Optional[str] = None,
) -> PlainConsole:
    """Build a Console wired to timestamped + latest.log dual-write.

    Args:
        output_dir: Pipeline output directory (logs/ created inside).
        log_level: Logging level (default DEBUG).
        verbose: If True, debug messages also print to console.
        json_format: If True, emit JSON lines for machine parsing.
        run_id: Optional run identifier for log correlation. Auto-generated if None.
    """
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    if run_id is None:
        run_id = generate_run_id()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"{timestamp}_{run_id}.log"

    # latest.log — truncate-write (Windows-safe, no symlink)
    latest = logs_dir / "latest.log"
    latest.write_text("", encoding="utf-8")

    logger = AppLogger(
        log_file,
        level=log_level,
        json_format=json_format,
        run_id=run_id,
    )

    # Dual-write: attach latest handler with matching format
    existing = [
        h
        for h in logger._logger.handlers
        if isinstance(h, logging.FileHandler)
        and Path(h.baseFilename).name == "latest.log"
    ]
    for h in existing:
        logger._logger.removeHandler(h)

    latest_handler = logging.FileHandler(latest, encoding="utf-8")
    if json_format:
        latest_handler.setFormatter(_JsonFormatter())
    else:
        latest_handler.setFormatter(_TextFormatter(run_id=run_id))
    logger._logger.addHandler(latest_handler)

    cleanup_logs(logs_dir, keep=3)

    console = PlainConsole(logger, verbose=verbose)
    # Store run_id on the console for metadata export
    console._run_id = run_id  # type: ignore[attr-defined]
    return console
