# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.8.0 GAP-1: X-API-Key authentication middleware.

Covers:
- TaskAPIServer ``api_key`` parameter and ``_check_auth()`` middleware
- ``/health`` endpoint exemption from authentication
- daemon startup guard (refuses unauthenticated public binding)
- daemon ``allow_insecure`` opt-in
- Settings ``api_key`` field (``MN_API_KEY``)
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import pytest

from movie_narrator.cloud import TaskAPIServer
from movie_narrator.cloud.daemon import _is_loopback, run_daemon


# ── Mock pipeline ─────────────────────────────────────────


def _mock_pipeline(ctx, **kwargs):
    """Mock pipeline that doesn't do any actual work."""
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    ctx.audio_path = str(Path(ctx.output_dir) / "narration.mp3")
    ctx.output_dir = str(ctx.output_dir)
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    Path(ctx.video_path).write_bytes(b"mock video")
    Path(ctx.audio_path).write_bytes(b"mock audio")
    return ctx


@pytest.fixture(autouse=True)
def mock_pipeline(monkeypatch):
    """Mock run_pipeline to prevent actual pipeline execution in tests."""
    monkeypatch.setattr(
        "movie_narrator.cloud.worker.run_pipeline",
        _mock_pipeline,
    )


# ── Helpers ───────────────────────────────────────────────


def _request(
    url: str,
    *,
    method: str = "GET",
    api_key: Optional[str] = None,
    data: Optional[bytes] = None,
    timeout: float = 5.0,
):
    """Send an HTTP request; returns the response object (raises HTTPError on 4xx/5xx)."""
    headers = {}
    if api_key is not None:
        headers["X-API-Key"] = api_key
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def _request_code(url: str, **kwargs) -> int:
    """Send a request and return the HTTP status code (never raises on 4xx/5xx)."""
    try:
        with _request(url, **kwargs) as resp:
            return resp.getcode()
    except urllib.error.HTTPError as e:
        return e.code


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def open_server(tmp_path):
    """API server with no api_key (open, loopback default)."""
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=1,
        api_key=None,
    )
    server.start(blocking=False)
    time.sleep(0.1)
    yield server
    server.stop()


@pytest.fixture
def secure_server(tmp_path):
    """API server with api_key set."""
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=1,
        api_key="secret-key-123",
    )
    server.start(blocking=False)
    time.sleep(0.1)
    yield server
    server.stop()


# ════════════════════════════════════════════════════════════
#  API auth middleware tests
# ════════════════════════════════════════════════════════════


class TestApiAuthMiddleware:
    """Tests for the X-API-Key authentication middleware on TaskAPIServer."""

    def test_no_key_allows_all(self, open_server):
        """api_key=None: all endpoints accessible without a key (backwards compatible)."""
        assert _request_code(f"{open_server.base_url}/health") == 200
        assert _request_code(f"{open_server.base_url}/info") == 200
        assert _request_code(f"{open_server.base_url}/tasks") == 200

    def test_wrong_key_returns_401(self, secure_server):
        """api_key set: wrong X-API-Key returns 401 on protected endpoints."""
        assert _request_code(f"{secure_server.base_url}/info", api_key="wrong") == 401
        assert _request_code(f"{secure_server.base_url}/tasks", api_key="wrong") == 401

    def test_missing_key_returns_401(self, secure_server):
        """api_key set: missing X-API-Key header returns 401 on protected endpoints."""
        assert _request_code(f"{secure_server.base_url}/info") == 401
        assert _request_code(f"{secure_server.base_url}/tasks") == 401

    def test_correct_key_returns_200(self, secure_server):
        """api_key set: correct X-API-Key allows access to protected endpoints."""
        assert _request_code(f"{secure_server.base_url}/info", api_key="secret-key-123") == 200
        assert _request_code(f"{secure_server.base_url}/tasks", api_key="secret-key-123") == 200

    def test_correct_key_post_task(self, secure_server):
        """api_key set: correct key allows POST /tasks (201 created)."""
        data = json.dumps({"movie_name": "AuthTest", "max_retries": 0}).encode("utf-8")
        code = _request_code(
            f"{secure_server.base_url}/tasks",
            method="POST",
            api_key="secret-key-123",
            data=data,
        )
        assert code == 201

    def test_wrong_key_post_rejected(self, secure_server):
        """api_key set: wrong key blocks POST /tasks with 401."""
        data = json.dumps({"movie_name": "AuthTest", "max_retries": 0}).encode("utf-8")
        code = _request_code(
            f"{secure_server.base_url}/tasks",
            method="POST",
            api_key="wrong",
            data=data,
        )
        assert code == 401

    def test_health_exempt_with_key(self, secure_server):
        """api_key set: /health is exempt and accessible without a key."""
        # No key at all
        assert _request_code(f"{secure_server.base_url}/health") == 200
        # Even a wrong key is fine for /health (exempt from auth)
        assert _request_code(f"{secure_server.base_url}/health", api_key="wrong") == 200

    def test_unauthorized_body(self, secure_server):
        """401 response body is exactly {"error": "unauthorized"}."""
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            _request(f"{secure_server.base_url}/info", api_key="wrong")
        assert exc_info.value.code == 401
        body = json.loads(exc_info.value.read())
        assert body == {"error": "unauthorized"}

    def test_api_key_stored_on_server(self, tmp_path):
        """TaskAPIServer stores api_key on the instance."""
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "tasks",
            max_workers=1,
            api_key="stored-key",
        )
        assert server.api_key == "stored-key"


# ════════════════════════════════════════════════════════════
#  Daemon startup guard tests
# ════════════════════════════════════════════════════════════


class TestDaemonGuard:
    """Tests for the run_daemon startup guard."""

    def test_is_loopback(self):
        """_is_loopback identifies loopback addresses."""
        assert _is_loopback("127.0.0.1") is True
        assert _is_loopback("localhost") is True
        assert _is_loopback("::1") is True
        assert _is_loopback("0.0.0.0") is False
        assert _is_loopback("192.168.1.1") is False

    def test_guard_rejects_public_without_key(self, tmp_path):
        """run_daemon refuses a non-loopback host without api_key (SystemExit)."""
        with pytest.raises(SystemExit) as exc_info:
            run_daemon(
                host="0.0.0.0",
                port=0,
                storage_dir=tmp_path,
                blocking=False,
            )
        assert exc_info.value.code == 1

    def test_guard_rejects_external_host_without_key(self, tmp_path):
        """run_daemon refuses an external host without api_key."""
        with pytest.raises(SystemExit):
            run_daemon(
                host="10.0.0.5",
                port=0,
                storage_dir=tmp_path,
                blocking=False,
            )

    def test_guard_allows_loopback_without_key(self, tmp_path):
        """run_daemon allows loopback host without api_key (backwards compatible)."""
        server = run_daemon(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path,
            blocking=False,
        )
        assert server is not None
        assert server.api_key is None
        server.stop()

    def test_guard_allow_insecure(self, tmp_path, monkeypatch):
        """run_daemon allows non-loopback host with allow_insecure=True."""
        # Mock start to avoid actually binding a socket on 0.0.0.0.
        monkeypatch.setattr(
            "movie_narrator.cloud.api.TaskAPIServer.start",
            lambda self, blocking=False: None,
        )
        server = run_daemon(
            host="0.0.0.0",
            port=0,
            storage_dir=tmp_path,
            blocking=False,
            allow_insecure=True,
        )
        assert server is not None
        assert server.api_key is None
        server.queue.shutdown()

    def test_guard_allows_public_with_key(self, tmp_path, monkeypatch):
        """run_daemon allows non-loopback host when api_key is provided."""
        monkeypatch.setattr(
            "movie_narrator.cloud.api.TaskAPIServer.start",
            lambda self, blocking=False: None,
        )
        server = run_daemon(
            host="0.0.0.0",
            port=0,
            storage_dir=tmp_path,
            blocking=False,
            api_key="my-key",
        )
        assert server is not None
        assert server.api_key == "my-key"
        server.queue.shutdown()


# ════════════════════════════════════════════════════════════
#  Settings api_key field tests
# ════════════════════════════════════════════════════════════


class TestSettingsApiKey:
    """Tests for the Settings.api_key field (MN_API_KEY)."""

    def test_default_none(self, monkeypatch):
        """api_key defaults to None when MN_API_KEY is unset."""
        from movie_narrator.config import Settings

        monkeypatch.delenv("MN_API_KEY", raising=False)
        s = Settings()
        assert s.api_key is None

    def test_env_var(self, monkeypatch):
        """api_key reads from the MN_API_KEY environment variable."""
        from movie_narrator.config import Settings

        monkeypatch.setenv("MN_API_KEY", "env-secret")
        s = Settings()
        assert s.api_key == "env-secret"

    def test_env_example_documented(self):
        """MN_API_KEY is documented in .env.example."""
        from movie_narrator.config import _EXAMPLE_ENV

        content = _EXAMPLE_ENV.read_text(encoding="utf-8") if _EXAMPLE_ENV.is_file() else ""
        # Either the file documents it, or the fallback template omits it;
        # the example file is the source of truth, so assert it's there.
        assert "MN_API_KEY" in content
