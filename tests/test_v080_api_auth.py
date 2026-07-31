# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for GAP-1: Server-side X-API-Key authentication (v0.8.0)."""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from pathlib import Path

import pytest

from movie_narrator.cloud.api import TaskAPIServer
from movie_narrator.cloud.queue import LocalTaskQueue


@pytest.fixture
def api_server(tmp_path):
    """Create and start an API server for testing."""
    queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
    server = TaskAPIServer(host="127.0.0.1", port=0, queue=queue)
    server.start(blocking=False)
    yield server
    server.stop()


@pytest.fixture
def auth_api_server(tmp_path):
    """Create and start an API server with API key auth."""
    queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
    server = TaskAPIServer(host="127.0.0.1", port=0, queue=queue, api_key="test-secret-key")
    server.start(blocking=False)
    yield server
    server.stop()


def _make_request(url, method="GET", headers=None, data=None):
    """Make an HTTP request and return (status_code, body_dict)."""
    req = urllib.request.Request(url, method=method, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


class TestNoAuthMode:
    """Tests for server without API key (backward compatible)."""

    def test_health_works_without_auth(self, api_server):
        """Health endpoint works without any auth."""
        status, body = _make_request(f"{api_server.base_url}/health")
        assert status == 200
        assert body["status"] == "ok"

    def test_info_works_without_auth(self, api_server):
        """Info endpoint works without auth when no key configured."""
        status, body = _make_request(f"{api_server.base_url}/info")
        assert status == 200

    def test_tasks_list_works_without_auth(self, api_server):
        """Tasks listing works without auth when no key configured."""
        status, body = _make_request(f"{api_server.base_url}/tasks")
        assert status == 200


class TestWithAuthMode:
    """Tests for server with API key auth."""

    def test_health_exempt_from_auth(self, auth_api_server):
        """Health endpoint is always accessible, even with auth."""
        status, body = _make_request(f"{auth_api_server.base_url}/health")
        assert status == 200
        assert body["status"] == "ok"

    def test_missing_api_key_returns_401(self, auth_api_server):
        """Requests without X-API-Key header get 401."""
        status, body = _make_request(f"{auth_api_server.base_url}/info")
        assert status == 401
        assert "unauthorized" in body["error"].lower()

    def test_wrong_api_key_returns_401(self, auth_api_server):
        """Wrong API key gets 401."""
        status, body = _make_request(
            f"{auth_api_server.base_url}/info",
            headers={"X-API-Key": "wrong-key"},
        )
        assert status == 401

    def test_correct_api_key_returns_200(self, auth_api_server):
        """Correct API key allows access."""
        status, body = _make_request(
            f"{auth_api_server.base_url}/info",
            headers={"X-API-Key": "test-secret-key"},
        )
        assert status == 200

    def test_tasks_list_requires_auth(self, auth_api_server):
        """Tasks listing requires valid API key."""
        # Without key
        status, _ = _make_request(f"{auth_api_server.base_url}/tasks")
        assert status == 401
        # With correct key
        status, _ = _make_request(
            f"{auth_api_server.base_url}/tasks",
            headers={"X-API-Key": "test-secret-key"},
        )
        assert status == 200

    def test_post_tasks_requires_auth(self, auth_api_server):
        """Task submission requires valid API key."""
        data = json.dumps({"movie_name": "Test"}).encode("utf-8")
        # Without key
        req = urllib.request.Request(
            f"{auth_api_server.base_url}/tasks",
            method="POST",
            data=data,
            headers={"Content-Type": "application/json"},
        )
        try:
            urllib.request.urlopen(req, timeout=5)
            assert False, "Should have raised HTTPError"
        except urllib.error.HTTPError as e:
            assert e.code == 401
        # With correct key
        req = urllib.request.Request(
            f"{auth_api_server.base_url}/tasks",
            method="POST",
            data=data,
            headers={"Content-Type": "application/json", "X-API-Key": "test-secret-key"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 201


class TestDaemonSecurityGuard:
    """Tests for daemon startup security guard."""

    def test_public_host_without_key_raises(self):
        """run_daemon raises ValueError on public host without API key."""
        from movie_narrator.cloud.daemon import run_daemon
        with pytest.raises(ValueError, match="public interface"):
            run_daemon(
                host="0.0.0.0",
                port=0,
                blocking=False,
            )

    def test_public_host_with_key_allowed(self, tmp_path):
        """run_daemon starts on public host when API key is provided."""
        from movie_narrator.cloud.daemon import run_daemon
        server = run_daemon(
            host="0.0.0.0",
            port=0,
            storage_dir=tmp_path,
            api_key="some-key",
            blocking=False,
        )
        server.stop()

    def test_public_host_with_insecure_flag(self, tmp_path):
        """run_daemon starts on public host with allow_insecure flag."""
        from movie_narrator.cloud.daemon import run_daemon
        server = run_daemon(
            host="0.0.0.0",
            port=0,
            storage_dir=tmp_path,
            allow_insecure=True,
            blocking=False,
        )
        server.stop()

    def test_loopback_without_key_allowed(self, tmp_path):
        """run_daemon starts on loopback without API key."""
        from movie_narrator.cloud.daemon import run_daemon
        server = run_daemon(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path,
            blocking=False,
        )
        server.stop()
