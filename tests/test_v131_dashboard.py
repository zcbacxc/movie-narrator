# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.3.1 Feature 7: dashboard summary contract.

Covers:
- ``build_dashboard_summary`` schema keys, versioning and aggregation
- Recent-task minimal views (<= 10) and status breakdown
- Artifact totals (real store, failure → zeros)
- ``GET /api/v1/dashboard/summary`` route with auth gating consistent
  with the other read routes
- Contract exports for the v1.3.1 service-semantics surface
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path


from movie_narrator.cloud import TaskAPIServer
from movie_narrator.cloud.artifact_store import ArtifactInfo
from movie_narrator.cloud.dashboard import SCHEMA_VERSION, build_dashboard_summary
from movie_narrator.cloud.models import Task, TaskRequest, TaskStatus
from movie_narrator.cloud.queue import LocalTaskQueue
from movie_narrator.cloud.storage import TaskStorage


# ── Helpers ────────────────────────────────────────────────


class _FakeArtifactStore:
    """Minimal StorageBackend stand-in exposing only ``list()``."""

    def __init__(self, infos):
        self._infos = infos

    def list(self, prefix: str = ""):
        return list(self._infos)


def _seed_task(storage: TaskStorage, *, status: TaskStatus, movie: str, created_at: str, **kw) -> Task:
    task = Task(request=TaskRequest(movie_name=movie), status=status, created_at=created_at, **kw)
    storage.save(task)
    return task


def _http(method: str, url: str, headers=None, timeout: float = 10.0):
    req = urllib.request.Request(url, headers=dict(headers or {}), method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        return e.code, payload


EXPECTED_TOP_KEYS = {
    "schema_version",
    "generated_at",
    "tasks",
    "queue",
    "artifacts",
    "plans",
}


# ════════════════════════════════════════════════════════════
#  build_dashboard_summary
# ════════════════════════════════════════════════════════════


class TestDashboardSummary:
    def test_empty_storage_schema(self, tmp_path):
        storage = TaskStorage(tmp_path)
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=2, auto_start=False)
        summary = build_dashboard_summary(queue, storage)
        assert EXPECTED_TOP_KEYS.issubset(summary)
        assert summary["schema_version"] == SCHEMA_VERSION == 1
        assert summary["generated_at"]
        assert summary["tasks"]["total"] == 0
        assert summary["tasks"]["recent"] == []
        assert summary["tasks"]["by_status"]["completed"] == 0
        assert summary["queue"] == {"depth": 0, "active": 0, "max_workers": 2}
        assert summary["artifacts"] == {"count": 0, "total_bytes": 0}
        # plans surface
        assert summary["plans"]["default"] == "default"
        assert {"default", "free", "pro"}.issubset(summary["plans"]["configured"])

    def test_aggregation_on_seeded_storage(self, tmp_path):
        storage = TaskStorage(tmp_path)
        base = time.time()
        stamps = [datetime_iso(base - i) for i in range(6)]
        _seed_task(storage, status=TaskStatus.COMPLETED, movie="A", created_at=stamps[0])
        _seed_task(storage, status=TaskStatus.FAILED, movie="B", created_at=stamps[1])
        _seed_task(storage, status=TaskStatus.CANCELLED, movie="C", created_at=stamps[2])
        _seed_task(storage, status=TaskStatus.PENDING, movie="D", created_at=stamps[3])
        _seed_task(storage, status=TaskStatus.RUNNING, movie="E", created_at=stamps[4])
        _seed_task(storage, status=TaskStatus.DEAD, movie="F", created_at=stamps[5])
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=3, auto_start=False)
        summary = build_dashboard_summary(queue, storage)
        assert summary["tasks"]["total"] == 6
        by_status = summary["tasks"]["by_status"]
        assert by_status["completed"] == 1
        assert by_status["failed"] == 1
        assert by_status["cancelled"] == 1
        assert by_status["pending"] == 1
        assert by_status["running"] == 1
        assert by_status["dead"] == 1
        assert by_status["retrying"] == 0
        # queue block: depth = pending, active from the queue counter
        assert summary["queue"]["depth"] == 1
        assert summary["queue"]["active"] == 2  # pending + running (not started: scanned)
        assert summary["queue"]["max_workers"] == 3

    def test_recent_tasks_capped_at_ten(self, tmp_path):
        storage = TaskStorage(tmp_path)
        base = time.time()
        for i in range(15):
            _seed_task(
                storage,
                status=TaskStatus.COMPLETED,
                movie=f"M{i:02d}",
                created_at=datetime_iso(base - i),
            )
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1, auto_start=False)
        summary = build_dashboard_summary(queue, storage)
        recent = summary["tasks"]["recent"]
        assert len(recent) == 10
        # newest first
        assert recent[0]["movie"] == "M00"
        assert recent[-1]["movie"] == "M09"
        view = recent[0]
        assert set(view) == {
            "task_id",
            "movie",
            "status",
            "progress",
            "tenant_id",
            "plan",
            "created_at",
        }
        assert view["status"] == "completed"
        assert view["progress"] == 0.0
        assert view["tenant_id"] == "default"
        assert view["plan"] == "default"

    def test_artifact_totals_from_store(self, tmp_path):
        storage = TaskStorage(tmp_path)
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1, auto_start=False)
        store = _FakeArtifactStore(
            [
                ArtifactInfo(key="t1/final.mp4", size=1000, modified_at=0.0),
                ArtifactInfo(key="t1/sub.srt", size=250, modified_at=1.0),
            ]
        )
        summary = build_dashboard_summary(queue, storage, store)
        assert summary["artifacts"] == {"count": 2, "total_bytes": 1250}

    def test_artifact_store_failure_yields_zeros(self, tmp_path):
        storage = TaskStorage(tmp_path)
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1, auto_start=False)

        class _Broken:
            def list(self, prefix: str = ""):
                raise RuntimeError("store down")

        summary = build_dashboard_summary(queue, storage, _Broken())
        assert summary["artifacts"] == {"count": 0, "total_bytes": 0}

    def test_recent_view_carries_semantics(self, tmp_path):
        storage = TaskStorage(tmp_path)
        _seed_task(
            storage,
            status=TaskStatus.RUNNING,
            movie="Scoped",
            created_at=datetime_iso(time.time()),
            tenant_id="acme",
            principal="api-key",
            plan="free",
        )
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1, auto_start=False)
        summary = build_dashboard_summary(queue, storage)
        view = summary["tasks"]["recent"][0]
        assert view["tenant_id"] == "acme"
        assert view["plan"] == "free"

    def test_summary_is_json_serializable(self, tmp_path):
        storage = TaskStorage(tmp_path)
        _seed_task(
            storage,
            status=TaskStatus.PENDING,
            movie="J",
            created_at=datetime_iso(time.time()),
        )
        queue = LocalTaskQueue(storage_dir=tmp_path, max_workers=1, auto_start=False)
        summary = build_dashboard_summary(queue, storage)
        assert json.loads(json.dumps(summary)) == summary


def datetime_iso(ts: float) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


# ════════════════════════════════════════════════════════════
#  API route
# ════════════════════════════════════════════════════════════


def _fast_pipeline(ctx, **kwargs):
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    return ctx


class TestDashboardRoute:
    def _server(self, tmp_path, monkeypatch, host="127.0.0.1", api_key="secret"):
        monkeypatch.setenv("MN_STORAGE_ROOT", str(tmp_path / "artifacts"))
        monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)
        server = TaskAPIServer(
            host=host,
            port=0,
            storage_dir=tmp_path / "tasks",
            max_workers=1,
            api_key=api_key,
        )
        server.start(blocking=False)
        time.sleep(0.1)
        return server

    def test_route_200_with_schema_keys(self, tmp_path, monkeypatch):
        server = self._server(tmp_path, monkeypatch)
        try:
            base = server.base_url
            headers = {"X-API-Key": "secret"}
            # seed one task
            status, _ = _http_with_body(
                "POST", f"{base}/tasks", {"movie_name": "Dash"}, headers
            )
            assert status == 201
            status, summary = _http("GET", f"{base}/api/v1/dashboard/summary", headers)
            assert status == 200
            assert EXPECTED_TOP_KEYS.issubset(summary)
            assert summary["schema_version"] == 1
            assert summary["tasks"]["total"] >= 1
            assert any(t["movie"] == "Dash" for t in summary["tasks"]["recent"])
        finally:
            server.stop()

    def test_route_loopback_open_without_key(self, tmp_path, monkeypatch):
        server = self._server(tmp_path, monkeypatch, api_key=None)
        try:
            status, summary = _http(
                "GET", f"{server.base_url}/api/v1/dashboard/summary"
            )
            assert status == 200
            assert EXPECTED_TOP_KEYS.issubset(summary)
        finally:
            server.stop()

    def test_route_non_loopback_requires_key(self, tmp_path, monkeypatch):
        """Auth gating consistent with other read routes."""
        server = self._server(tmp_path, monkeypatch, host="0.0.0.0", api_key=None)
        try:
            url = f"http://127.0.0.1:{server.port}/api/v1/dashboard/summary"
            status, _ = _http("GET", url)
            assert status == 401
        finally:
            server.stop()


def _http_with_body(method: str, url: str, body, headers=None):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **(headers or {})},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, {}


# ════════════════════════════════════════════════════════════
#  Contract surface (v1.3.1)
# ════════════════════════════════════════════════════════════


class TestContractExports:
    def test_new_names_importable(self):
        from movie_narrator.contract import (
            EntitlementError,
            Plan,
            WebhookDispatcher,
            WebhookEvent,
            build_dashboard_summary,
        )

        assert Plan is not None
        assert issubclass(EntitlementError, Exception)
        assert WebhookEvent is not None
        assert WebhookDispatcher is not None
        assert callable(build_dashboard_summary)

    def test_new_names_in_all(self):
        from movie_narrator import contract

        for name in (
            "Plan",
            "EntitlementError",
            "WebhookEvent",
            "WebhookDispatcher",
            "build_dashboard_summary",
        ):
            assert name in contract.__all__
            assert hasattr(contract, name)
