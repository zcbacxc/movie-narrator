"""Console — output abstraction: UI rendering + log dispatch."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from .log import AppLogger
from .retention import cleanup_logs


# ── ANSI helpers ──────────────────────────────────────────────

_BLUE = "\033[94m"
_GREEN = "\033[92m"
_YELLOW = "\033[93m"
_RED = "\033[91m"
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

    def step(self, name: str) -> None: ...
    def step_ok(self, name: str, elapsed: float) -> None: ...
    def step_skip(self, name: str, reason: str) -> None: ...
    def step_warn(self, name: str, reason: str) -> None: ...
    def step_err(self, name: str, exc: Exception, elapsed: float) -> None: ...
    def warn(self, msg: str) -> None: ...
    def debug(self, msg: str) -> None: ...
    def inline_warn(self, msg: str) -> None: ...
    def final(self, msg: str) -> None: ...
    def done(self, elapsed: float) -> None: ...
    def progress(self, *args, **kwargs): ...


# ── PlainConsole ────────────────────────────────────────────


class PlainConsole:
    """Standard Console implementation: ANSI + print + tqdm + typer.echo."""

    def __init__(self, logger: AppLogger) -> None:
        self._log = logger

    # ── lifecycle events (called by runner) ──────────────────

    def step(self, name: str) -> None:
        print(f"{_BLUE}▶{_RESET} {name}", end="", flush=True)
        self._log.info(f"STEP_START {name}")

    def step_ok(self, name: str, elapsed: float) -> None:
        t = _fmt_time(elapsed)
        print(f"\r{_GREEN}✓{_RESET} {name}  {_BOLD}{t}{_RESET}")
        self._log.info(f"STEP_OK {name} elapsed={elapsed:.3f}s")

    def step_skip(self, name: str, reason: str) -> None:
        print(f"\r{_YELLOW}⏭{_RESET} {name}: {reason}")
        self._log.info(f"STEP_SKIP {name} reason={reason}")

    def step_warn(self, name: str, reason: str) -> None:
        print(f"\r{_YELLOW}⚠{_RESET} {name}: {reason}")
        self._log.warning(f"STEP_WARN {name} reason={reason}")

    def step_err(self, name: str, exc: Exception, elapsed: float) -> None:
        t = _fmt_time(elapsed)
        print(f"\r{_RED}✗{_RESET} {name}: {exc} {_YELLOW}({t}){_RESET}")
        self._log.error(f"STEP_ERR {name}", exc_info=True)

    def done(self, elapsed: float) -> None:
        """Final 'Done in ...' banner at end of pipeline."""
        print(f"\n{_BOLD}Done in {_fmt_time(elapsed)}{_RESET}")
        self._log.info(f"PIPELINE_DONE elapsed={elapsed:.3f}s")

    # ── in-process messages (called by steps directly) ─────

    def debug(self, msg: str) -> None:
        self._log.debug(msg)

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
        from tqdm import tqdm

        return tqdm(*args, **kwargs)


# ── Factory ────────────────────────────────────────────────


def build_console(output_dir: Path) -> PlainConsole:
    """Build a Console wired to timestamped + latest.log dual-write."""
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"{timestamp}.log"

    # latest.log — truncate-write (Windows-safe, no symlink)
    latest = logs_dir / "latest.log"
    latest.write_text("", encoding="utf-8")

    logger = AppLogger(log_file)

    # Dual-write: attach latest handler
    existing = [
        h
        for h in logger._logger.handlers
        if isinstance(h, logging.FileHandler) and Path(h.baseFilename).name == "latest.log"
    ]
    for h in existing:
        logger._logger.removeHandler(h)

    latest_handler = logging.FileHandler(latest, encoding="utf-8")
    latest_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger._logger.addHandler(latest_handler)

    cleanup_logs(logs_dir, keep=3)

    return PlainConsole(logger)
