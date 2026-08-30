# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.3.1 Feature 4: principal & tenant foundation.

Covers:
- ``Task`` / ``TaskRequest`` additive service-semantics fields with
  backward-compatible defaults (old serialized payloads still load)
- SQLite storage additive migration (tenant_id / principal columns)
- Request-time principal/tenant resolution in the API layer
- Stamping of created tasks + structured audit records
- Artifact listing scoping for non-default tenants
"""

from __future__ import annotations

import io
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Optional

import pytest

from movie_narrator.cloud.api import _APIHandler
from movie_narrator.cloud.models import Task, TaskRequest
from movie_narrator.cloud.queue import LocalTaskQueue
from movie_narrator.cloud.storage import TaskStorage


# ── Test doubles (same pattern as test_v120_auth_admission) ──


class _FakeServer:
    """Minimal server stub injecting host/api_key into the handler."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        api_key: Optional[str] = None,
    ) -> None:
        self.host = host
        self.api_key = api_key


def _make_handler(server: object, headers: Optional[dict] = None) -> _APIHandler:
    """Build a bare ``_APIHandler`` without running the HTTP serve loop."""
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


# ════════════════════════════════════════════════════════════
#  Models — additive fields, backward compatibility
# ════════════════════════════════════════════════════════════


class TestModelFields:
    def test_task_defaults(self):
        task = Task(request=TaskRequest(movie_name="T"))
        assert task.tenant_id == "default"
        assert task.principal == "local"

    def test_old_task_json_still_loads(self):
        """A v1.2-era serialized task (no tenant/principal keys) loads."""
        old = Task(request=TaskRequest(movie_name="Old"))
        data = old.model_dump(mode="json")
        data.pop("tenant_id")
        data.pop("principal")
        loaded = Task(**data)
        assert loaded.tenant_id == "default"
        assert loaded.principal == "local"

    def test_task_request_optional_fields(self):
        req = TaskRequest(movie_name="T")
        assert req.tenant_id is None
        assert req.principal is None
        req2 = TaskRequest(movie_name="T", tenant_id="acme", principal="svc")
        assert req2.tenant_id == "acme"
        assert req2.principal == "svc"

    def test_summary_includes_semantics(self):
        task = Task(
            request=TaskRequest(movie_name="T"),
            tenant_id="acme",
            principal="api-key",
        )
        summary = task.to_summary()
        assert summary["tenant_id"] == "acme"
        assert summary["principal"] == "api-key"


# ════════════════════════════════════════════════════════════
#  Storage — additive SQLite migration
# ════════════════════════════════════════════════════════════


class TestStorageMigration:
    def test_fresh_db_has_semantics_columns(self, tmp_path: Path):
        store = TaskStorage(tmp_path)
        cols = {
            str(row[1]) for row in store._conn.execute("PRAGMA table_info(tasks)")
        }
        assert {"tenant_id", "principal"}.issubset(cols)

    def test_legacy_db_migrates_on_open(self, tmp_path: Path):
        """A pre-v1.3.1 database (no semantics columns) migrates on open."""
        db_path = tmp_path / "tasks.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE tasks ("
            "id TEXT PRIMARY KEY, data TEXT NOT NULL, "
            "created_at TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'pending')"
        )
        legacy_task = Task(request=TaskRequest(movie_name="Legacy"))
        record = legacy_task.model_dump(mode="json")
        record.pop("tenant_id")
        record.pop("principal")
        conn.execute(
            "INSERT INTO tasks(id, data, created_at, status) VALUES (?, ?, ?, ?)",
            (
                legacy_task.id,
                json.dumps(record),
                record.get("created_at", ""),
                record.get("status", "pending"),
            ),
        )
        conn.commit()
        conn.close()

        store = TaskStorage(tmp_path)
        loaded = store.load(legacy_task.id)
        assert loaded is not None
        assert loaded.request.movie_name == "Legacy"
        # Defaults supplied by the model, not the missing columns.
        assert loaded.tenant_id == "default"
        assert loaded.principal == "local"

    def test_save_mirrors_columns(self, tmp_path: Path):
        store = TaskStorage(tmp_path)
        task = Task(
            request=TaskRequest(movie_name="T"),
            tenant_id="acme",
            principal="api-key",
        )
        store.save(task)
        row = store._conn.execute(
            "SELECT tenant_id, principal FROM tasks WHERE id = ?", (task.id,)
        ).fetchone()
        assert row["tenant_id"] == "acme"
        assert row["principal"] == "api-key"

    def test_load_after_save_preserves_semantics(self, tmp_path: Path):
        store = TaskStorage(tmp_path)
        task = Task(
            request=TaskRequest(movie_name="T"),
            tenant_id="acme",
            principal="svc",
        )
        store.save(task)
        loaded = store.load(task.id)
        assert loaded is not None
        assert loaded.tenant_id == "acme"
        assert loaded.principal == "svc"


# ════════════════════════════════════════════════════════════
#  Queue stamping
# ════════════════════════════════════════════════════════════


class TestQueueStamping:
    def test_submit_stamps_from_request(self, tmp_path: Path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        import movie_narrator.cloud.worker as worker_mod

        def _fast(ctx, **kwargs):
            Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
            ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
            return ctx

        monkeypatch.setattr(worker_mod, "run_pipeline", _fast)
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1)
        try:
            task_id = queue.submit(
                TaskRequest(movie_name="T", tenant_id="acme", principal="svc")
            )
            task = queue.wait(task_id, timeout=10)
            stored = queue.get_task(task_id)
            assert stored is not None
            assert stored.tenant_id == "acme"
            assert stored.principal == "svc"
            assert task is not None
        finally:
            queue.shutdown(wait=False)

    def test_direct_submit_keeps_defaults(self, tmp_path: Path):
        """Direct queue use (CLI path) keeps the single-user defaults."""
        task = Task(request=TaskRequest(movie_name="T"))
        assert task.tenant_id == "default"
        assert task.principal == "local"


# ════════════════════════════════════════════════════════════
#  Identity resolution
# ════════════════════════════════════════════════════════════


class TestCurrentIdentity:
    def test_loopback_anonymous_is_local_default(self):
        handler = _make_handler(_FakeServer(host="127.0.0.1", api_key=None))
        assert handler._current_identity() == ("local", "default")

    def test_api_key_default_principal(self):
        handler = _make_handler(
            _FakeServer(host="0.0.0.0", api_key="secret"),
            headers={"X-API-Key": "secret"},
        )
        assert handler._current_identity() == ("api-key", "default")

    def test_api_key_with_tenant_header(self):
        handler = _make_handler(
            _FakeServer(host="0.0.0.0", api_key="secret"),
            headers={"X-API-Key": "secret", "X-MN-Tenant": "acme"},
        )
        assert handler._current_identity() == ("api-key", "acme")

    def test_tenant_header_whitespace_falls_back(self):
        handler = _make_handler(
            _FakeServer(host="0.0.0.0", api_key="secret"),
            headers={"X-API-Key": "secret", "X-MN-Tenant": "   "},
        )
        assert handler._current_identity() == ("api-key", "default")

    def test_env_principal_override(self, monkeypatch):
        monkeypatch.setenv("MN_API_PRINCIPAL", "svc-dashboard")
        handler = _make_handler(
            _FakeServer(host="0.0.0.0", api_key="secret"),
            headers={"X-API-Key": "secret"},
        )
        assert handler._current_identity() == ("svc-dashboard", "default")


# ════════════════════════════════════════════════════════════
#  Audit records
# ════════════════════════════════════════════════════════════


class TestAuditRecords:
    def test_audit_emits_structured_fields(self, caplog):
        handler = _make_handler(
            _FakeServer(host="0.0.0.0", api_key="secret"),
            headers={"X-API-Key": "secret", "X-MN-Tenant": "acme"},
        )
        with caplog.at_level(logging.INFO, logger="movie_narrator.cloud.api"):
            handler._audit("task_submit", "abc123")
        record = next(r for r in caplog.records if getattr(r, "event", "") == "audit")
        assert record.route == "task_submit"
        assert record.task_id == "abc123"
        assert record.tenant_id == "acme"
        assert record.principal == "api-key"

    def test_audit_never_raises(self):
        handler = _make_handler(_FakeServer(host="127.0.0.1", api_key=None))
        # A failing identity resolution must not propagate out of _audit.
        def _boom():
            raise RuntimeError("boom")

        handler._current_identity = _boom  # type: ignore[method-assign]
        handler._audit("task_status", "abc")  # must not raise


# ════════════════════════════════════════════════════════════
#  HTTP-level: stamping, audit, artifact scoping
# ════════════════════════════════════════════════════════════


def _fast_pipeline(ctx, **kwargs):
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    return ctx


def _http(method: str, url: str, body=None, headers=None, timeout: float = 10.0):
    """Perform an HTTP request and return ``(status, parsed_json)``."""
    import urllib.error
    import urllib.request

    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
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
def server_with_key(tmp_path, monkeypatch):
    """API server bound to loopback with an API key configured."""
    from movie_narrator.cloud import TaskAPIServer

    monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=1,
        api_key="secret",
    )
    server.start(blocking=False)
    time.sleep(0.1)
    yield server
    server.stop()


class TestHttpStampingAndScoping:
    def test_submit_stamps_key_principal_and_tenant(
        self, server_with_key, tmp_path, monkeypatch
    ):
        base = server_with_key.base_url
        headers = {"X-API-Key": "secret", "X-MN-Tenant": "acme"}
        status, body = _http(
            "POST",
            f"{base}/tasks",
            body={"movie_name": "TenantMovie"},
            headers=headers,
        )
        assert status == 201
        task_id = body["task_id"]
        status, detail = _http(
            "GET", f"{base}/tasks/{task_id}", headers=headers
        )
        assert status == 200
        assert detail["principal"] == "api-key"
        assert detail["tenant_id"] == "acme"

    def test_list_includes_semantics(self, server_with_key):
        base = server_with_key.base_url
        headers = {"X-API-Key": "secret"}
        status, body = _http(
            "POST", f"{base}/tasks", body={"movie_name": "ListMovie"}, headers=headers
        )
        assert status == 201
        status, listing = _http("GET", f"{base}/tasks", headers=headers)
        assert status == 200
        assert listing["count"] >= 1
        entry = next(
            t for t in listing["tasks"] if t["id"] == body["task_id"]
        )
        assert entry["principal"] == "api-key"
        assert entry["tenant_id"] == "default"

    def test_artifact_scoping_non_default_tenant(
        self, server_with_key, caplog
    ):
        base = server_with_key.base_url
        acme = {"X-API-Key": "secret", "X-MN-Tenant": "acme"}
        other = {"X-API-Key": "secret", "X-MN-Tenant": "other"}
        # acme submits a task
        _, body = _http(
            "POST",
            f"{base}/tasks",
            body={"movie_name": "ScopedMovie"},
            headers=acme,
        )
        task_id = body["task_id"]
        # acme sees its own artifacts
        status, _ = _http("GET", f"{base}/tasks/{task_id}/artifacts", headers=acme)
        assert status == 200
        # another tenant does not
        status, payload = _http(
            "GET", f"{base}/tasks/{task_id}/artifacts", headers=other
        )
        assert status == 403
        # default tenant keeps the backward-compatible view of everything
        default = {"X-API-Key": "secret"}
        status, _ = _http("GET", f"{base}/tasks/{task_id}/artifacts", headers=default)
        assert status == 200
        # download is scoped the same way
        status, _ = _http(
            "GET", f"{base}/tasks/{task_id}/download/final.mp4", headers=other
        )
        assert status == 403

    def test_unauthenticated_non_loopback_still_rejected(self, monkeypatch, tmp_path):
        """v1.2 behaviour preserved: anonymous non-loopback bind → 401."""
        from movie_narrator.cloud import TaskAPIServer

        monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)
        server = TaskAPIServer(
            host="0.0.0.0",
            port=0,
            storage_dir=tmp_path / "tasks",
            max_workers=1,
            api_key=None,
        )
        server.start(blocking=False)
        try:
            url = f"http://127.0.0.1:{server.port}/tasks"
            status, _ = _http("GET", url)
            assert status == 401
        finally:
            server.stop()

    def test_audit_records_written(self, server_with_key, caplog):
        base = server_with_key.base_url
        headers = {"X-API-Key": "secret", "X-MN-Tenant": "acme"}
        _, body = _http(
            "POST", f"{base}/tasks", body={"movie_name": "AuditMovie"}, headers=headers
        )
        task_id = body["task_id"]
        with caplog.at_level(logging.INFO, logger="movie_narrator.cloud.api"):
            _http("GET", f"{base}/tasks/{task_id}", headers=headers)
            _http("GET", f"{base}/tasks/{task_id}/artifacts", headers=headers)
            _http("GET", f"{base}/tasks/{task_id}/download/final.mp4", headers=headers)
        audits = [r for r in caplog.records if getattr(r, "event", "") == "audit"]
        routes = {r.route for r in audits}
        assert {"task_status", "artifact_download"}.issubset(routes)
        download = next(
            r for r in audits if r.route == "artifact_download"
        )
        assert download.task_id == task_id
        assert download.tenant_id == "acme"
