# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Structured logging configuration and correlation IDs (v0.8.1).

This module targets the *library / server* logging side — the
``logger = logging.getLogger(__name__)`` calls scattered across the
``cloud`` and ``workflow`` packages. It is deliberately separate from
:mod:`movie_narrator.utils.console` (the user-facing output façade) and
from :class:`movie_narrator.utils.log.AppLogger` (per-run file logging).

Two capabilities are provided:

**1. JSON log lines.** :class:`JsonFormatter` renders one JSON object
per record with a stable key set (``ts``, ``level``, ``logger``,
``msg``), so logs can be shipped to ELK / Loki / CloudWatch without a
grok pattern. Formatting is defensive: a log call must never crash the
code that emitted it, so unserializable values degrade to ``repr()``
rather than raising.

**2. Correlation IDs.** A :class:`contextvars.ContextVar` carries an ID
that ties together every record belonging to one logical unit of work —
an HTTP request, or the task it spawned. ``contextvars`` (rather than
``threading.local``) is used because each thread started by
``ThreadingHTTPServer`` / ``ThreadPoolExecutor`` gets its own copy of
the context, so IDs never leak between concurrently served requests,
and the same code keeps working if an async front end is ever added.

Typical usage::

    from movie_narrator.utils.logging_config import (
        configure_logging,
        correlation_scope,
    )

    configure_logging(json_mode=True, level="INFO")

    with correlation_scope() as cid:
        logger.info("handling request")   # record carries ``cid``

Configuration is driven by two environment variables, both optional:

``MN_LOG_FORMAT``
    ``json`` or ``text`` (default ``text``).
``MN_LOG_LEVEL``
    Standard level name (default ``INFO``).

Explicit arguments to :func:`configure_logging` win over the
environment.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional

__all__ = [
    "CORRELATION_FIELD",
    "CORRELATION_HEADER",
    "CorrelationIdFilter",
    "ENV_LOG_FORMAT",
    "ENV_LOG_LEVEL",
    "JsonFormatter",
    "REQUEST_ID_HEADER",
    "TextFormatter",
    "configure_logging",
    "correlation_scope",
    "get_correlation_id",
    "new_correlation_id",
    "reset_logging",
    "set_correlation_id",
]

#: Record attribute / JSON key holding the correlation ID.
CORRELATION_FIELD = "correlation_id"

#: Response header the server always echoes the correlation ID on.
CORRELATION_HEADER = "X-Correlation-ID"

#: Additional request header accepted as an inbound correlation ID.
REQUEST_ID_HEADER = "X-Request-ID"

#: Environment variable selecting ``json`` or ``text`` output.
ENV_LOG_FORMAT = "MN_LOG_FORMAT"

#: Environment variable selecting the root log level.
ENV_LOG_LEVEL = "MN_LOG_LEVEL"

_DEFAULT_LEVEL = logging.INFO

_correlation_id: ContextVar[Optional[str]] = ContextVar(
    "movie_narrator_correlation_id",
    default=None,
)


# ── Correlation ID ─────────────────────────────────────────


def new_correlation_id() -> str:
    """
    Returns:
        A fresh correlation ID (12 hex chars).

        Twelve hex characters carry 48 bits of entropy — ample to stay
        unique inside a log-retention window, while remaining short enough
        to eyeball in a terminal and to grep for.
    """
    return uuid.uuid4().hex[:12]


def get_correlation_id() -> Optional[str]:
    """
    Returns:
        The correlation ID bound to the current context, if any.
    """
    return _correlation_id.get()


def set_correlation_id(cid: Optional[str]) -> Token[Optional[str]]:
    """Bind ``cid`` as the current correlation ID.

    Args:
        cid: The ID to bind, or None to unbind.

    Returns:
        A token accepted by :meth:`contextvars.ContextVar.reset`, so
        callers that cannot use :func:`correlation_scope` (for example
        because bind and release happen in different functions) can
        still restore the previous value.
    """
    return _correlation_id.set(cid)


@contextmanager
def correlation_scope(cid: Optional[str] = None) -> Iterator[str]:
    """Bind a correlation ID for the duration of the ``with`` block.

    The previous value is restored on exit — including when the body
    raises — so nested scopes (a task running inside a request) do not
    clobber the caller's ID.

    Args:
        cid: ID to bind. When None or empty, a fresh one is generated.

    Yields:
        The correlation ID active inside the block.
    """
    resolved = cid or new_correlation_id()
    token = _correlation_id.set(resolved)
    try:
        yield resolved
    finally:
        _correlation_id.reset(token)


class CorrelationIdFilter(logging.Filter):
    """Inject the ambient correlation ID onto every record.

    Attached to the handler rather than to individual loggers so that
    the text formatter can reference ``%(correlation_id)s`` without
    risking a ``KeyError`` on records emitted by third-party libraries.

    An ID already present on the record wins: that lets a caller attach
    an explicit ID via ``logger.info(msg, extra={"correlation_id": ...})``
    from a thread that does not own the context.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Filter a log record.

        Returns:
            True if the record should be logged.
        """
        if not getattr(record, CORRELATION_FIELD, None):
            setattr(record, CORRELATION_FIELD, get_correlation_id() or "")
        return True


# ── Formatters ─────────────────────────────────────────────

#: Attributes present on every ``LogRecord``; anything else a caller
#: passed via ``extra=`` is emitted as a top-level JSON field.
_RESERVED_ATTRS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "taskName", "thread", "threadName",
})


def _isoformat_utc(timestamp: float) -> str:
    """Render an epoch timestamp as ISO-8601 in UTC with a ``Z`` suffix."""
    return (
        datetime.fromtimestamp(timestamp, tz=timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _dumps(payload: Dict[str, Any]) -> str:
    """Serialize ``payload``, degrading unserializable values to ``repr``.

    ``default=repr`` covers unserializable *values*, but not exotic
    *keys* (a dict keyed by tuples raises before ``default`` is
    consulted). The second pass therefore repr-s offending entries
    whole, and a final minimal payload guarantees this never raises —
    a logging call must not be able to crash its caller.
    """
    try:
        return json.dumps(payload, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        pass

    safe: Dict[str, Any] = {}
    for key, value in payload.items():
        try:
            json.dumps(value, ensure_ascii=False, default=repr)
            safe[str(key)] = value
        except (TypeError, ValueError):
            safe[str(key)] = repr(value)
    try:
        return json.dumps(safe, ensure_ascii=False, default=repr)
    except (TypeError, ValueError):
        return json.dumps({
            "ts": payload.get("ts", ""),
            "level": payload.get("level", "ERROR"),
            "logger": payload.get("logger", ""),
            "msg": "<unserializable log record>",
        })


def _safe_message(record: logging.LogRecord) -> str:
    """Interpolate the record message, tolerating bad format arguments."""
    try:
        return record.getMessage()
    except (TypeError, ValueError):
        return f"{record.msg!r} % {record.args!r}"


class JsonFormatter(logging.Formatter):
    """Render a record as a single-line JSON object.

    Key set::

        ts             ISO-8601 UTC, millisecond precision
        level          level name (INFO, ERROR, …)
        logger         logger name
        msg            interpolated message
        correlation_id present only when an ID is bound
        exc_type       \\
        exc_message     > present only when the record carries an exception
        traceback      /
        <extra>        any field passed via ``extra=``

    ``correlation_id`` is omitted rather than emitted as ``null`` when
    unset: a null would bloat every line and force every consumer to
    special-case it.
    """

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record.

        Returns:
            Formatted log string.
        """
        payload: Dict[str, Any] = {
            "ts": _isoformat_utc(record.created),
            "level": record.levelname,
            "logger": record.name,
            "msg": _safe_message(record),
        }

        correlation_id = getattr(record, CORRELATION_FIELD, None) or get_correlation_id()
        if correlation_id:
            payload[CORRELATION_FIELD] = correlation_id

        if record.exc_info and record.exc_info[1] is not None:
            exc = record.exc_info[1]
            payload["exc_type"] = type(exc).__name__
            payload["exc_message"] = str(exc)
            payload["traceback"] = self.formatException(record.exc_info)
        elif record.exc_text:
            payload["traceback"] = record.exc_text

        if record.stack_info:
            payload["stack_info"] = self.formatStack(record.stack_info)

        for key, value in record.__dict__.items():
            # ``correlation_id`` is handled by the block above so that an
            # empty value (the filter sets it to "" when unset) is omitted
            # rather than emitted as ``"correlation_id": ""`` on every line.
            if (
                key in _RESERVED_ATTRS
                or key == CORRELATION_FIELD
                or key in payload
                or key.startswith("_")
            ):
                continue
            payload[key] = value

        return _dumps(payload)


class TextFormatter(logging.Formatter):
    """Human-readable formatter that surfaces the correlation ID.

    The ID is appended in brackets only when present, so local runs
    without a bound ID keep the familiar terse output.
    """

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        """Format a log record.

        Returns:
            Formatted log string.
        """
        base = super().format(record)
        correlation_id = getattr(record, CORRELATION_FIELD, None) or get_correlation_id()
        if correlation_id:
            return f"{base} [{CORRELATION_FIELD}={correlation_id}]"
        return base


# ── Configuration ──────────────────────────────────────────

#: Marker attribute identifying the handler this module installed, so
#: repeated ``configure_logging()`` calls update it instead of stacking
#: duplicates, and ``reset_logging()`` leaves foreign handlers alone.
_MANAGED_FLAG = "_mn_observability_handler"

_config_lock = threading.Lock()

_LEVEL_NAMES: Dict[str, int] = {
    "CRITICAL": logging.CRITICAL,
    "FATAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARN": logging.WARNING,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}


def _resolve_level(level: Optional[str]) -> int:
    """Resolve a case-insensitive level name, falling back to INFO.

    Unlike :func:`movie_narrator.utils.log.resolve_log_level` (which
    defaults to DEBUG for pipeline debugging), server logs default to
    INFO — DEBUG on a long-running service is far too chatty.
    """
    if level is None:
        return _DEFAULT_LEVEL
    return _LEVEL_NAMES.get(level.strip().upper(), _DEFAULT_LEVEL)


def _resolve_json_mode(json_mode: Optional[bool]) -> bool:
    """Decide between JSON and text output (explicit arg wins over env)."""
    if json_mode is not None:
        return json_mode
    return os.environ.get(ENV_LOG_FORMAT, "text").strip().lower() == "json"


def _find_managed_handler(logger: logging.Logger) -> Optional[logging.Handler]:
    """
    Returns:
        The handler previously installed by this module, if any.
    """
    for handler in logger.handlers:
        if getattr(handler, _MANAGED_FLAG, False):
            return handler
    return None


def configure_logging(
    *,
    json_mode: Optional[bool] = None,
    level: Optional[str] = None,
) -> logging.Handler:
    """Install (or update) the process-wide structured log handler.

    Idempotent: calling this repeatedly reconfigures the single handler
    this module owns rather than stacking duplicates, so a library that
    calls it defensively cannot cause double-logging.

    The handler is attached to the **root** logger on purpose. Attaching
    it to the ``movie_narrator`` logger would be more polite, but
    :class:`movie_narrator.utils.log.AppLogger` calls ``handlers.clear()``
    on exactly that logger when a pipeline run starts, which would
    silently detach server logging mid-run.

    Args:
        json_mode: Force JSON (True) or text (False). When None, read
            ``MN_LOG_FORMAT`` (default text).
        level: Level name such as ``"DEBUG"``. When None, read
            ``MN_LOG_LEVEL`` (default ``INFO``).

    Returns:
        The managed handler, so callers can attach extra filters.
    """
    resolved_json = _resolve_json_mode(json_mode)
    resolved_level = _resolve_level(
        level if level is not None else os.environ.get(ENV_LOG_LEVEL)
    )

    with _config_lock:
        root = logging.getLogger()
        handler = _find_managed_handler(root)
        if handler is None:
            handler = logging.StreamHandler(sys.stderr)
            setattr(handler, _MANAGED_FLAG, True)
            handler.addFilter(CorrelationIdFilter())
            root.addHandler(handler)
        handler.setFormatter(JsonFormatter() if resolved_json else TextFormatter())
        handler.setLevel(resolved_level)
        root.setLevel(resolved_level)
        return handler


def reset_logging() -> None:
    """Remove the handler installed by :func:`configure_logging`.

    Only the managed handler is touched; handlers installed by the
    application or by pytest are left in place. Primarily useful in
    tests, and when embedding the package in a host process that owns
    its own logging setup.
    """
    with _config_lock:
        root = logging.getLogger()
        handler = _find_managed_handler(root)
        if handler is not None:
            root.removeHandler(handler)
            handler.close()
