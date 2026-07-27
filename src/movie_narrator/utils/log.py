"""AppLogger — 纯文件日志层，不负责 UI，不接触控制台。

改进点 (v0.5.4+):
- RotatingFileHandler 防止单文件过大 (10MB, 5 backups)
- JSON 格式日志选项 (json_format=True)
- error() 默认记录堆栈 (exc_info=True)
- run_id 前缀关联多次运行
- 可配置日志级别
"""

import json
import logging
import uuid
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


class _JsonFormatter(logging.Formatter):
    """JSON line formatter for structured log aggregation (ELK/Loki)."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry = {
            "ts": datetime.fromtimestamp(record.created).strftime(
                "%Y-%m-%d %H:%M:%S.%f"
            )[:-3],
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        if record.exc_info and record.exc_info[1] is not None:
            log_entry["traceback"] = self.formatException(record.exc_info)
        return json.dumps(log_entry, ensure_ascii=False)


class _TextFormatter(logging.Formatter):
    """Text formatter with optional run_id prefix."""

    def __init__(self, run_id: str = ""):
        super().__init__(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        self._run_id = run_id

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        if self._run_id:
            return f"[{self._run_id}] {base}"
        return base


class AppLogger:
    """Pure file logging layer. Does not handle console output.

    Args:
        log_file: Path to the primary log file.
        level: Logging level (default DEBUG for maximum detail).
        json_format: If True, emit JSON lines for machine parsing.
        run_id: Optional run identifier prepended to log messages.
    """

    def __init__(
        self,
        log_file: Path,
        level: int = logging.DEBUG,
        json_format: bool = False,
        run_id: str = "",
    ):
        self._run_id = run_id
        self._logger = logging.getLogger("movie_narrator")
        self._logger.setLevel(level)
        self._logger.handlers.clear()
        self._json_format = json_format

        # RotatingFileHandler: 10MB per file, keep 5 backups
        handler = RotatingFileHandler(
            log_file,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        if json_format:
            handler.setFormatter(_JsonFormatter())
        else:
            handler.setFormatter(_TextFormatter(run_id=run_id))
        self._logger.addHandler(handler)

    def add_handler(self, handler: logging.Handler) -> None:
        """Attach an additional handler (e.g. for dual-write to latest.log)."""
        self._logger.addHandler(handler)

    def remove_handler(self, handler: logging.Handler) -> None:
        """Remove a previously attached handler."""
        self._logger.removeHandler(handler)

    def debug(self, msg: str) -> None:
        self._logger.debug(msg)

    def info(self, msg: str) -> None:
        self._logger.info(msg)

    def warning(self, msg: str) -> None:
        self._logger.warning(msg)

    def error(self, msg: str, exc_info: bool = True) -> None:
        """Log error. exc_info defaults to True so tracebacks are always captured."""
        self._logger.error(msg, exc_info=exc_info)


def generate_run_id() -> str:
    """Generate a short unique run ID for log correlation."""
    return uuid.uuid4().hex[:8]
