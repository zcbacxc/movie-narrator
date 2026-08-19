# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.2 Wave 2B: non-loopback mandatory auth + task admission.

Covers:
- ``api._is_loopback_host`` addressing rules
- ``_APIHandler._check_auth`` loopback vs non-loopback semantics
- opt-in concurrency and estimated-artifact-size admission limits
  (``MN_MAX_CONCURRENT_TASKS`` / ``MN_MAX_ESTIMATED_ARTIFACT_BYTES``)
"""

from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
import urllib.request
from http import HTTPStatus
from pathlib import Path
from typing import Optional

import pytest

from movie_narrator.cloud import TaskAPIServer
from movie_narrator.cloud.api import (
    _APIHandler,
    _artifact_size_limit,
    _concurrency_limit,
    _estimate_artifact_bytes,
    _is_loopback_host,
)
from movie_narrator.cloud.models import TaskRequest


# ── Test doubles ───────────────────────────────────────────


class _FakeQueue:
    """Minimal queue stub exposing ``active_count`` for admission tests."""

    def __init__(self, active_count: int = 0) -> None:
        self.active_count = active_count


class _FakeServer:
    """Minimal server stub injecting host/api_key/queue into the handler."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        api_key: Optional[str] = None,
        queue: Optional[_FakeQueue] = None,
    ) -> None:
        self.host = host
        self.api_key = api_key
        self.queue = queue
        self.shutting_down = None


def _make_handler(server: object, headers: Optional[dict] = None) -> _APIHandler:
    """Build a bare ``_APIHandler`` without running the HTTP serve loop.

    ``BaseHTTPRequestHandler.__init__`` immediately drives ``handle()``, so
    constructing one directly would start reading from a socket. Instead we
    allocate the object raw and set only the attributes the code under test
    needs, which keeps the unit tests deterministic and socket-free.
    """
    handler = object.__new__(_APIHandler)
    handler.server = server
    handler.headers = headers if headers is not None else {}
    handler.wfile = io.BytesIO()
    handler.rfile = io.BytesIO()
    handler.command = ""
    handler.path = "/"
    handler.requestline = ""
    handler.request_version = "HTTP/1.1"
    handler.protocol_version = "HTTP/1.1"
    handler.client_address = ("127.0.0.1", 0)
    handler._headers_buffer = []
    handler.log_message = lambda *args, **kwargs: None
    return handler


def _uniq(prefix: str) -> str:
    """A unique movie name per call (avoids shared ./output collisions)."""
    return f"{prefix}_{int(time.time() * 1000)}_{id(object())}"


def _fast_pipeline(ctx, **kwargs):
    """Mock pipeline that finishes instantly."""
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    return ctx


def _slow_pipeline(gate: threading.Event):
    """Mock pipeline that blocks until ``gate`` is set, honouring cancel."""

    def _run(ctx, **kwargs):
        from movie_narrator.pipeline.errors import PipelineCancelled

        gate.wait(timeout=15)
        controller = kwargs.get("controller")
        if controller is not None and controller.is_cancelled():
            raise PipelineCancelled("cancelled by test")
        Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
        ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
        return ctx

    return _run


def _http(method: str, url: str, body=None, timeout: float = 10.0):
    """Perform an HTTP request and return ``(status, parsed_json)``."""
    data = None
    headers = {}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        return e.code, payload


@pytest.fixture
def api_server(tmp_path, monkeypatch):
    """Start a loopback API server (no key) with a fast mock pipeline."""
    monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=1,
    )
    server.start(blocking=False)
    time.sleep(0.1)
    yield server
    server.stop()


# ════════════════════════════════════════════════════════════
#  _is_loopback_host
# ════════════════════════════════════════════════════════════


class TestLoopbackHost:
    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
    def test_loopback_is_true(self, host):
        assert _is_loopback_host(host) is True

    @pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.1", "", "::"])
    def test_non_loopback_is_false(self, host):
        assert _is_loopback_host(host) is False

    def test_expanded_ipv6_loopback(self):
        assert _is_loopback_host("0:0:0:0:0:0:0:1") is True

    def test_localhost_is_case_insensitive(self):
        assert _is_loopback_host("LOCALHOST") is True


# ════════════════════════════════════════════════════════════
#  _check_auth
# ════════════════════════════════════════════════════════════


class TestCheckAuth:
    def test_loopback_no_key_allows(self):
        handler = _make_handler(_FakeServer(host="127.0.0.1", api_key=None))
        assert handler._check_auth() is True

    def test_non_loopback_no_key_rejects(self):
        handler = _make_handler(_FakeServer(host="0.0.0.0", api_key=None))
        assert handler._check_auth() is False
        assert b"MN_API_KEY" in handler.wfile.getvalue()

    def test_loopback_with_key_correct_header(self):
        handler = _make_handler(
            _FakeServer(host="127.0.0.1", api_key="secret"),
            headers={"X-API-Key": "secret"},
        )
        assert handler._check_auth() is True

    def test_non_loopback_with_key_correct_header(self):
        handler = _make_handler(
            _FakeServer(host="0.0.0.0", api_key="secret"),
            headers={"X-API-Key": "secret"},
        )
        assert handler._check_auth() is True

    def test_non_loopback_with_key_wrong_header(self):
        handler = _make_handler(
            _FakeServer(host="0.0.0.0", api_key="secret"),
            headers={"X-API-Key": "wrong"},
        )
        assert handler._check_auth() is False

    def test_server_without_host_attr_defaults_to_loopback(self):
        class _UnadornedServer:
            api_key = None

        handler = _make_handler(_UnadornedServer())
        assert handler._check_auth() is True


# ════════════════════════════════════════════════════════════
#  Env parsing helpers
# ════════════════════════════════════════════════════════════


class TestEnvParsing:
    def test_unset_disables_limits(self, monkeypatch):
        monkeypatch.delenv("MN_MAX_CONCURRENT_TASKS", raising=False)
        monkeypatch.delenv("MN_MAX_ESTIMATED_ARTIFACT_BYTES", raising=False)
        assert _concurrency_limit() is None
        assert _artifact_size_limit() is None

    def test_invalid_values_disable_limits(self, monkeypatch):
        monkeypatch.setenv("MN_MAX_CONCURRENT_TASKS", "not-a-number")
        monkeypatch.setenv("MN_MAX_ESTIMATED_ARTIFACT_BYTES", "0")
        assert _concurrency_limit() is None
        assert _artifact_size_limit() is None

    def test_positive_values_parsed(self, monkeypatch):
        monkeypatch.setenv("MN_MAX_CONCURRENT_TASKS", "4")
        monkeypatch.setenv("MN_MAX_ESTIMATED_ARTIFACT_BYTES", "123456")
        assert _concurrency_limit() == 4
        assert _artifact_size_limit() == 123456


# ════════════════════════════════════════════════════════════
#  _estimate_artifact_bytes
# ════════════════════════════════════════════════════════════


class TestEstimateArtifactBytes:
    def test_estimate_scales_with_duration(self):
        short = TaskRequest(movie_name="x", duration=10)
        long = TaskRequest(movie_name="x", duration=100)
        assert _estimate_artifact_bytes(long) > _estimate_artifact_bytes(short)

    def test_portrait_has_lower_bitrate(self):
        horizontal = TaskRequest(movie_name="x", duration=60, video_format="16:9")
        vertical = TaskRequest(movie_name="x", duration=60, video_format="9:16")
        assert _estimate_artifact_bytes(vertical) < _estimate_artifact_bytes(horizontal)


# ════════════════════════════════════════════════════════════
#  _admission_rejection (unit)
# ════════════════════════════════════════════════════════════


class TestAdmissionRejection:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv("MN_MAX_CONCURRENT_TASKS", raising=False)
        monkeypatch.delenv("MN_MAX_ESTIMATED_ARTIFACT_BYTES", raising=False)
        handler = _make_handler(_FakeServer(queue=_FakeQueue(active_count=99)))
        assert handler._admission_rejection([TaskRequest(movie_name="x")]) is None

    def test_concurrency_rejects_when_over(self, monkeypatch):
        monkeypatch.setenv("MN_MAX_CONCURRENT_TASKS", "2")
        handler = _make_handler(_FakeServer(queue=_FakeQueue(active_count=2)))
        status, message = handler._admission_rejection([TaskRequest(movie_name="x")])
        assert status == HTTPStatus.TOO_MANY_REQUESTS
        assert "MN_MAX_CONCURRENT_TASKS" in message

    def test_concurrency_allows_when_under(self, monkeypatch):
        monkeypatch.setenv("MN_MAX_CONCURRENT_TASKS", "2")
        handler = _make_handler(_FakeServer(queue=_FakeQueue(active_count=1)))
        assert handler._admission_rejection([TaskRequest(movie_name="x")]) is None

    def test_concurrency_counts_batch_size(self, monkeypatch):
        monkeypatch.setenv("MN_MAX_CONCURRENT_TASKS", "2")
        handler = _make_handler(_FakeServer(queue=_FakeQueue(active_count=0)))
        requests = [TaskRequest(movie_name=f"m{i}") for i in range(3)]
        status, _ = handler._admission_rejection(requests)
        assert status == HTTPStatus.TOO_MANY_REQUESTS

    def test_size_rejects_when_over(self, monkeypatch):
        monkeypatch.setenv("MN_MAX_ESTIMATED_ARTIFACT_BYTES", "1000")
        handler = _make_handler(_FakeServer(queue=_FakeQueue(active_count=0)))
        status, message = handler._admission_rejection(
            [TaskRequest(movie_name="x", duration=60)]
        )
        assert status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        assert "MN_MAX_ESTIMATED_ARTIFACT_BYTES" in message

    def test_size_allows_when_under(self, monkeypatch):
        monkeypatch.setenv("MN_MAX_ESTIMATED_ARTIFACT_BYTES", str(20 * 1024 * 1024))
        handler = _make_handler(_FakeServer(queue=_FakeQueue(active_count=0)))
        assert handler._admission_rejection([TaskRequest(movie_name="x", duration=1)]) is None


# ════════════════════════════════════════════════════════════
#  Admission at the HTTP boundary
# ════════════════════════════════════════════════════════════


class TestHttpAdmission:
    def test_normal_post_unaffected(self, api_server, monkeypatch):
        monkeypatch.delenv("MN_MAX_CONCURRENT_TASKS", raising=False)
        monkeypatch.delenv("MN_MAX_ESTIMATED_ARTIFACT_BYTES", raising=False)
        status, body = _http(
            "POST",
            f"{api_server.base_url}/tasks",
            body={"movie_name": _uniq("ok"), "max_retries": 0},
        )
        assert status == HTTPStatus.CREATED
        assert body["task_id"]

    def test_concurrency_limit_rejects(self, api_server, monkeypatch):
        monkeypatch.setenv("MN_MAX_CONCURRENT_TASKS", "1")
        gate = threading.Event()
        monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _slow_pipeline(gate))
        try:
            status, first = _http(
                "POST",
                f"{api_server.base_url}/tasks",
                body={"movie_name": _uniq("a"), "max_retries": 0},
            )
            assert status == HTTPStatus.CREATED
            assert first["task_id"]

            deadline = time.time() + 10
            while time.time() < deadline and api_server.queue.active_count < 1:
                time.sleep(0.05)

            status, body = _http(
                "POST",
                f"{api_server.base_url}/tasks",
                body={"movie_name": _uniq("b"), "max_retries": 0},
            )
            assert status == HTTPStatus.TOO_MANY_REQUESTS
            assert "MN_MAX_CONCURRENT_TASKS" in body["error"]
        finally:
            gate.set()
            deadline = time.time() + 10
            while time.time() < deadline and api_server.queue.active_count > 0:
                time.sleep(0.05)

    def test_batch_concurrency_rejects(self, api_server, monkeypatch):
        monkeypatch.setenv("MN_MAX_CONCURRENT_TASKS", "2")
        status, body = _http(
            "POST",
            f"{api_server.base_url}/tasks/batch",
            body={"requests": [{"movie_name": _uniq(f"m{i}")} for i in range(3)]},
        )
        assert status == HTTPStatus.TOO_MANY_REQUESTS
        assert "MN_MAX_CONCURRENT_TASKS" in body["error"]

    def test_size_limit_rejects(self, api_server, monkeypatch):
        monkeypatch.setenv("MN_MAX_ESTIMATED_ARTIFACT_BYTES", "1000")
        status, body = _http(
            "POST",
            f"{api_server.base_url}/tasks",
            body={"movie_name": _uniq("big"), "duration": 3600, "max_retries": 0},
        )
        assert status == HTTPStatus.REQUEST_ENTITY_TOO_LARGE
        assert "MN_MAX_ESTIMATED_ARTIFACT_BYTES" in body["error"]
