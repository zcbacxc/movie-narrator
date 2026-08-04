# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.8.1 structured logging and correlation IDs.

Covers :mod:`movie_narrator.utils.logging_config`: JSON rendering, the
correlation-ID context var (including cross-thread propagation), and the
idempotent :func:`configure_logging` / :func:`reset_logging` lifecycle.

These tests need no network and no API keys.
"""

from __future__ import annotations

import io
import json
import logging
import sys
import threading
from typing import Any, Dict, List

import pytest

from movie_narrator.utils.logging_config import (
    CORRELATION_FIELD,
    JsonFormatter,
    TextFormatter,
    configure_logging,
    correlation_scope,
    get_correlation_id,
    new_correlation_id,
    reset_logging,
)


@pytest.fixture(autouse=True)
def _managed_logging():
    """Ensure no managed handler leaks across tests."""
    reset_logging()
    yield
    reset_logging()


def _emit(handler: logging.Handler, record: logging.LogRecord) -> str:
    return handler.format(record)


def _make_record(
    msg: str,
    args: tuple = (),
    exc_info: Any = None,
    extra: Optional[Dict[str, Any]] = None,
) -> logging.LogRecord:
    if exc_info is True:
        exc_info = sys.exc_info()
    record = logging.LogRecord(
        "movie_narrator.test.logging",
        logging.INFO,
        "path",
        1,
        msg,
        args,
        exc_info,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


# ── JSON formatter ──────────────────────────────────────────


def test_json_formatter_basic_fields():
    handler = logging.StreamHandler(io.StringIO())
    handler.setFormatter(JsonFormatter())
    record = _make_record("hello %s", ("world",))
    out = _emit(handler, record)
    payload = json.loads(out)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "movie_narrator.test.logging"
    assert payload["msg"] == "hello world"
    assert "ts" in payload
    # No correlation id bound → key is omitted, not null.
    assert CORRELATION_FIELD not in payload


def test_json_formatter_correlation_id_present():
    handler = logging.StreamHandler(io.StringIO())
    handler.setFormatter(JsonFormatter())
    with correlation_scope("abc123deadbe"):
        record = _make_record("working")
        out = _emit(handler, record)
    payload = json.loads(out)
    assert payload[CORRELATION_FIELD] == "abc123deadbe"


def test_json_formatter_omits_empty_correlation():
    handler = logging.StreamHandler(io.StringIO())
    handler.setFormatter(JsonFormatter())
    record = _make_record("no id")
    # Explicitly set an empty correlation id.
    setattr(record, CORRELATION_FIELD, "")
    out = _emit(handler, record)
    payload = json.loads(out)
    assert CORRELATION_FIELD not in payload


def test_json_formatter_extra_fields():
    handler = logging.StreamHandler(io.StringIO())
    handler.setFormatter(JsonFormatter())
    record = _make_record("with extra", extra={"task_id": "abc", "n": 3})
    payload = json.loads(_emit(handler, record))
    assert payload["task_id"] == "abc"
    assert payload["n"] == 3


def test_json_formatter_exc_info():
    handler = logging.StreamHandler(io.StringIO())
    handler.setFormatter(JsonFormatter())
    try:
        raise ValueError("boom")
    except ValueError:
        record = _make_record("failed", exc_info=True)
    payload = json.loads(_emit(handler, record))
    assert payload["exc_type"] == "ValueError"
    assert payload["exc_message"] == "boom"
    assert "ValueError" in payload["traceback"]


def test_json_formatter_unserializable_degrades():
    handler = logging.StreamHandler(io.StringIO())
    handler.setFormatter(JsonFormatter())

    class Weird:
        def __repr__(self) -> str:
            return "<weird>"

    record = _make_record("odd", extra={"obj": Weird()})
    out = _emit(handler, record)
    payload = json.loads(out)  # must still be valid JSON
    assert payload["obj"] == "<weird>"


def test_text_formatter_surfaces_correlation():
    formatter = TextFormatter()
    with correlation_scope("id-xyz"):
        record = _make_record("doing work")
        out = formatter.format(record)
    assert "correlation_id=id-xyz" in out


# ── Correlation ID context var ─────────────────────────────


def test_new_correlation_id_format():
    cid = new_correlation_id()
    assert len(cid) == 12
    assert all(c in "0123456789abcdef" for c in cid)


def test_correlation_scope_restores_previous():
    assert get_correlation_id() is None
    with correlation_scope("outer") as outer:
        assert get_correlation_id() == "outer"
        with correlation_scope("inner") as inner:
            assert get_correlation_id() == "inner"
        assert get_correlation_id() == outer
    assert get_correlation_id() is None


def test_correlation_not_auto_propagated_to_thread():
    # A plain ``threading.Thread`` does NOT inherit the parent's ContextVar
    # value, so the ID must be re-bound explicitly inside the worker — which
    # is exactly what ``cloud/queue.py`` does via
    # ``correlation_scope(task.correlation_id)``.
    captured: Dict[str, Any] = {}

    with correlation_scope("parent-cid"):

        def worker() -> None:
            captured["auto"] = get_correlation_id()

        t = threading.Thread(target=worker)
        t.start()
        t.join()

    assert captured["auto"] is None


def test_correlation_explicitly_bound_in_thread():
    captured: Dict[str, Any] = {}

    def worker(cid: str) -> None:
        with correlation_scope(cid):
            captured["bound"] = get_correlation_id()

    t = threading.Thread(target=worker, args=("worker-cid",))
    t.start()
    t.join()

    assert captured["bound"] == "worker-cid"


# ── configure_logging lifecycle ─────────────────────────────


def test_configure_logging_idempotent():
    root = logging.getLogger()
    before = len(root.handlers)
    h1 = configure_logging(json_mode=False, level="INFO")
    after_one = len(root.handlers)
    assert after_one == before + 1
    h2 = configure_logging(json_mode=True, level="DEBUG")
    # Re-running must update the SAME handler, not stack a second one.
    assert len(root.handlers) == after_one
    assert h1 is h2
    assert isinstance(h2.formatter, JsonFormatter)


def test_configure_logging_json_env(monkeypatch):
    monkeypatch.setenv("MN_LOG_FORMAT", "json")
    monkeypatch.setenv("MN_LOG_LEVEL", "WARNING")
    handler = configure_logging()
    assert isinstance(handler.formatter, JsonFormatter)
    assert logging.getLogger().level == logging.WARNING


def test_configure_logging_explicit_overrides_env(monkeypatch):
    monkeypatch.setenv("MN_LOG_FORMAT", "json")
    # Explicit argument must win over the environment.
    handler = configure_logging(json_mode=False)
    assert isinstance(handler.formatter, TextFormatter)


def test_configure_logging_level_env(monkeypatch):
    monkeypatch.setenv("MN_LOG_LEVEL", "DEBUG")
    configure_logging(json_mode=False)
    assert logging.getLogger().level == logging.DEBUG


def test_reset_logging_removes_handler():
    root = logging.getLogger()
    before = len(root.handlers)
    configure_logging(json_mode=False)
    assert len(root.handlers) == before + 1
    reset_logging()
    assert len(root.handlers) == before


def test_configure_logging_emits_json_line(monkeypatch):
    monkeypatch.setenv("MN_LOG_FORMAT", "json")
    stream = io.StringIO()
    handler = configure_logging(json_mode=True)
    old = handler.stream
    handler.stream = stream
    try:
        logging.getLogger("movie_narrator.test.logging").info("structured")
    finally:
        handler.stream = old
    payload = json.loads(stream.getvalue().splitlines()[-1])
    assert payload["msg"] == "structured"
    assert payload["level"] == "INFO"
