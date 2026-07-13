"""AppLogger — 纯文件日志层，不负责 UI，不接触控制台。"""

import logging
from pathlib import Path


class AppLogger:
    """Pure file logging layer. Does not handle console output."""

    def __init__(self, log_file: Path, level: int = logging.DEBUG):
        self._logger = logging.getLogger("movie_narrator")
        self._logger.setLevel(level)
        self._logger.handlers.clear()
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s [%(levelname)s] %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
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

    def error(self, msg: str, exc_info: bool = False) -> None:
        self._logger.error(msg, exc_info=exc_info)
