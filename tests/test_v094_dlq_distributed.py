# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.9.4: dead-letter queue + conditional distributed rendering.

Covers:
- DLQ write/read/replay (retry exhaustion -> DEAD -> replay produces a
  fresh task)
- ``enable_dlq=False`` keeps the pre-v0.9.4 ``FAILED`` behaviour
- API routes: ``GET /deadletters``, ``GET /deadletters/{id}``,
  ``POST /deadletters/{id}/replay``, ``DELETE /deadletters/{id}``
- ``NodeRegistry`` parsing and health probing (mocked)
- ``DistributedRenderPlanner`` decision matrix
- Distributed dispatch failure falling back to local rendering
"""

from __future__ import annotations

import json
import time
import urllib.request
from pathlib import Path

import pytest

from movie_narrator.cloud import (
    DeadLetterRecord,
    DeadLetterStore,
    DistributedRenderError,
    DistributedRenderPlanner,
    NodeRegistry,
    TaskAPIServer,
    TaskRequest,
    TaskStatus,
    render_task_dispatcher,
    replay_dead_letter,
)
from movie_narrator.cloud.distributed import estimate_render_seconds
from movie_narrator.cloud.models import Task, TaskResult, TERMINAL_STATES
from movie_narrator.cloud.queue import LocalTaskQueue
from movie_narrator.cloud.worker import CancelController, run_task


# ── Mock pipeline (reused from test_v061_remote pattern) ───


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


@pytest.fixture
def dlq_store(tmp_path, monkeypatch):
    """Isolate the process-wide dead-letter store to a temp directory."""
    store = DeadLetterStore(tmp_path / "dlq")
    monkeypatch.setattr(
        "movie_narrator.cloud.dlq.get_default_store",
        lambda: store,
    )
    return store


def _run_with_error(monkeypatch, exc, max_retries=1, retry_delay=0.01, **kwargs):
    """Run a task whose pipeline always raises *exc*; return the task."""
    monkeypatch.setenv("CI", "1")

    def failing_pipeline(ctx, **kw):
        raise exc

    monkeypatch.setattr(
        "movie_narrator.cloud.worker.run_pipeline",
        failing_pipeline,
    )
    req = TaskRequest(
        movie_name="DLQTest",
        output_dir=str(Path("output") / "dlqtest"),
        max_retries=max_retries,
        retry_delay=retry_delay,
        **kwargs,
    )
    task = Task(request=req)
    return run_task(task, CancelController())


def _failing_pipeline_factory(exc):
    """Return a pipeline callable that always raises *exc*."""

    def failing_pipeline(ctx, **kw):
        raise exc

    return failing_pipeline


# ── HTTP helpers ───────────────────────────────────────────


def _get(server: TaskAPIServer, path: str) -> dict:
    with urllib.request.urlopen(f"{server.base_url}{path}", timeout=5) as resp:
        return json.loads(resp.read())


def _post(server: TaskAPIServer, path: str) -> dict:
    req = urllib.request.Request(
        f"{server.base_url}{path}",
        data=None,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


def _delete(server: TaskAPIServer, path: str) -> dict:
    req = urllib.request.Request(f"{server.base_url}{path}", method="DELETE")
    with urllib.request.urlopen(req, timeout=5) as resp:
        return json.loads(resp.read())


# ── Dead-letter queue ──────────────────────────────────────


class TestDeadLetterStore:
    """Unit tests for record persistence and replay."""

    def test_record_roundtrip(self, tmp_path):
        store = DeadLetterStore(tmp_path / "dlq")
        record = DeadLetterRecord(
            task_id="abc123",
            original_request=TaskRequest(movie_name="M", max_retries=0),
            reason="boom",
            failed_at="2026-08-03T00:00:00+00:00",
            attempts=1,
        )
        store.save(record)

        loaded = store.get("abc123")
        assert loaded is not None
        assert loaded.reason == "boom"
        assert loaded.original_request.movie_name == "M"
        assert loaded.attempts == 1

        listed = store.list()
        assert [r.task_id for r in listed] == ["abc123"]

        assert store.remove("abc123") is True
        assert store.get("abc123") is None
        assert store.remove("abc123") is False

    def test_missing_record_is_none(self, tmp_path):
        store = DeadLetterStore(tmp_path / "dlq")
        assert store.get("missing") is None
        assert store.list() == []


class TestDeadLetterRouting:
    """Worker integration: retry exhaustion routes to the DLQ."""

    def test_retry_exhausted_routes_to_dead(self, monkeypatch, dlq_store, tmp_path):
        task = _run_with_error(monkeypatch, ConnectionError("network down"), max_retries=1)
        assert task.status == TaskStatus.DEAD
        assert task.retries == 1

        record = dlq_store.get(task.id)
        assert record is not None
        assert record.attempts == 2  # initial attempt + 1 retry
        assert record.replay_count == 0
        assert record.original_request.movie_name == "DLQTest"
        assert "network down" in record.reason

    def test_dead_is_terminal(self, monkeypatch, dlq_store, tmp_path):
        assert TaskStatus.DEAD in TERMINAL_STATES
        task = _run_with_error(monkeypatch, ConnectionError("down"), max_retries=1)
        assert task.is_terminal is True

    def test_non_retryable_error_stays_failed(self, monkeypatch, dlq_store, tmp_path):
        task = _run_with_error(monkeypatch, ValueError("config error"), max_retries=3)
        assert task.status == TaskStatus.FAILED
        assert dlq_store.get(task.id) is None

    def test_enable_dlq_false_keeps_failed(self, monkeypatch, dlq_store, tmp_path):
        task = _run_with_error(
            monkeypatch, ConnectionError("down"), max_retries=1, enable_dlq=False
        )
        assert task.status == TaskStatus.FAILED
        assert dlq_store.get(task.id) is None

    def test_replay_creates_new_task(self, monkeypatch, dlq_store, tmp_path):
        task = _run_with_error(monkeypatch, ConnectionError("down"), max_retries=1)
        assert task.status == TaskStatus.DEAD

        # Restore the success mock so the replayed task completes.
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            _mock_pipeline,
        )
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        try:
            new_id = replay_dead_letter(task.id, queue=queue)
            assert new_id != task.id

            result = queue.wait(new_id, timeout=10, poll_interval=0.1)
            assert result is not None
            assert result.succeeded is True

            record = dlq_store.get(task.id)
            assert record is not None
            assert record.replay_count == 1  # record kept, history preserved
        finally:
            queue.shutdown()

    def test_dead_counts_error_metric(self, monkeypatch, dlq_store, tmp_path):
        """DLQ'd tasks increment the error metric (v0.9.4 review fix)."""
        monkeypatch.setenv("CI", "1")
        recorded = []

        def fake_record_error(error_type: str) -> None:
            recorded.append(error_type)

        monkeypatch.setattr(
            "movie_narrator.cloud.queue.record_error",
            fake_record_error,
        )
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline",
            _failing_pipeline_factory(ConnectionError("down")),
        )

        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        try:
            req = TaskRequest(
                movie_name="DLQTest",
                output_dir=str(Path("output") / "dlqtest"),
                max_retries=1,
                retry_delay=0.01,
            )
            task_id = queue.submit(req)
            result = queue.wait(task_id, timeout=10, poll_interval=0.05)
            assert result is not None
            task = queue.get_task(task_id)
            assert task is not None
            assert task.status == TaskStatus.DEAD
            assert "dead_letter" in recorded
        finally:
            queue.shutdown()


# ── API routes ─────────────────────────────────────────────


@pytest.fixture
def api_server(tmp_path, dlq_store):
    """Start an API server whose worker and DLQ share one temp store."""
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=1,
        dead_letter_store=dlq_store,
    )
    server.start(blocking=False)
    time.sleep(0.1)
    yield server
    server.stop()


class TestDeadLetterApi:
    """REST endpoints for the dead-letter queue."""

    def test_deadletter_flow(self, api_server, dlq_store, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")

        def failing(ctx, **kw):
            raise ConnectionError("api network failure")

        monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", failing)

        req = TaskRequest(
            movie_name="APIDLQ",
            max_retries=1,
            retry_delay=0.01,
            output_dir=str(tmp_path / "out"),
        )
        task_id = api_server.queue.submit(req)

        # Wait for the task to die.
        task = None
        for _ in range(100):
            task = api_server.queue.get_task(task_id)
            if task is not None and task.status == TaskStatus.DEAD:
                break
            time.sleep(0.05)
        assert task is not None
        assert task.status == TaskStatus.DEAD

        # GET /deadletters lists the record.
        listing = _get(api_server, "/deadletters")
        assert listing["count"] >= 1
        assert any(r["task_id"] == task_id for r in listing["deadletters"])

        # GET /deadletters/{id} returns the record.
        detail = _get(api_server, f"/deadletters/{task_id}")
        assert detail["task_id"] == task_id
        assert detail["original_request"]["movie_name"] == "APIDLQ"

        # POST /deadletters/{id}/replay queues a fresh task.
        replayed = _post(api_server, f"/deadletters/{task_id}/replay")
        assert replayed["original_task_id"] == task_id
        assert replayed["task_id"] != task_id

        record = dlq_store.get(task_id)
        assert record is not None
        assert record.replay_count == 1

        # DELETE /deadletters/{id} removes the record.
        removed = _delete(api_server, f"/deadletters/{task_id}")
        assert removed["removed"] is True
        assert dlq_store.get(task_id) is None

    def test_deadletter_not_found(self, api_server):
        detail_resp = _not_found_get(api_server, "/deadletters/nonexistent")
        assert detail_resp == 404


def _not_found_get(server: TaskAPIServer, path: str) -> int:
    import urllib.error

    req = urllib.request.Request(f"{server.base_url}{path}")
    try:
        urllib.request.urlopen(req, timeout=5)
        return 200
    except urllib.error.HTTPError as e:
        return e.code


# ── Node registry ──────────────────────────────────────────


class TestNodeRegistry:
    """Node parsing and readiness probing."""

    def test_parse_comma_separated_nodes(self, monkeypatch):
        monkeypatch.setattr(NodeRegistry, "_probe", lambda self, url: True)
        reg = NodeRegistry(nodes="http://a:1, http://b:2 ,", health_timeout=1.0)
        assert reg.configured_nodes == ["http://a:1", "http://b:2"]
        assert reg.available_nodes() == ["http://a:1", "http://b:2"]

    def test_unhealthy_nodes_excluded(self, monkeypatch):
        monkeypatch.setattr(
            NodeRegistry,
            "_probe",
            lambda self, url: url == "http://ok:1",
        )
        reg = NodeRegistry(nodes="http://ok:1,http://bad:2", health_timeout=1.0)
        assert reg.available_nodes() == ["http://ok:1"]

    def test_empty_nodes(self):
        reg = NodeRegistry(nodes="", health_timeout=1.0)
        assert reg.configured_nodes == []
        assert reg.available_nodes() == []

    def test_probe_checks_ready_endpoint(self, monkeypatch):
        seen: dict = {}

        class _Resp:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self):
                return json.dumps({"ready": True}).encode("utf-8")

        def fake_urlopen(url, timeout=5):
            seen["url"] = url
            seen["timeout"] = timeout
            return _Resp()

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        reg = NodeRegistry(nodes="http://node:8765", health_timeout=2.0)
        assert reg._probe("http://node:8765") is True
        assert seen["url"] == "http://node:8765/ready"
        assert seen["timeout"] == 2.0


# ── Planner ────────────────────────────────────────────────


class TestDistributedRenderPlanner:
    """Decision matrix for whether a render should be dispatched."""

    def test_disabled_never_distributes(self):
        planner = DistributedRenderPlanner(
            enabled=False,
            min_render_seconds=600,
            available_nodes=["http://a:1"],
        )
        assert planner.should_distribute(100000) is False

    def test_no_nodes_never_distributes(self):
        planner = DistributedRenderPlanner(
            enabled=True,
            min_render_seconds=600,
            available_nodes=[],
        )
        assert planner.should_distribute(100000) is False

    def test_short_render_never_distributes(self):
        planner = DistributedRenderPlanner(
            enabled=True,
            min_render_seconds=600,
            available_nodes=["http://a:1"],
        )
        assert planner.should_distribute(100) is False

    def test_distributes_when_all_conditions_met(self):
        planner = DistributedRenderPlanner(
            enabled=True,
            min_render_seconds=600,
            available_nodes=["http://a:1"],
        )
        assert planner.should_distribute(601) is True

    def test_defaults_disable_distribution(self):
        planner = DistributedRenderPlanner(
            enabled=False,
            min_render_seconds=600,
            available_nodes=["http://a:1"],
        )
        assert planner.enabled is False

    def test_estimate_render_seconds(self):
        req = TaskRequest(movie_name="M", duration=90)
        assert estimate_render_seconds(req) == 90.0
        assert estimate_render_seconds(req, history_seconds=1200) == 1200.0
        from movie_narrator.cloud.models import TaskProgress

        progress = TaskProgress(step_elapsed_seconds=42.0)
        assert estimate_render_seconds(req, progress=progress) == 42.0


# ── Dispatcher ─────────────────────────────────────────────


class TestRenderTaskDispatcher:
    """Subtask submission, polling and artifact download."""

    def test_success_dispatches_and_downloads(self, monkeypatch, tmp_path):
        submitted: dict = {}
        remote_result = TaskResult(
            video_path="/remote/out/final.mp4",
            output_dir="/remote/out",
        )

        class FakeRemoteQueue:
            def __init__(self, base_url, **kw):
                submitted["base_url"] = base_url

            def submit(self, request):
                submitted["workflow_steps"] = request.workflow_steps
                assert request.workflow_steps.get("render_video") is True
                return "remote-task-1"

            def wait(self, task_id, **kw):
                return remote_result

        monkeypatch.setattr(
            "movie_narrator.cloud.distributed.RemoteTaskQueue",
            FakeRemoteQueue,
        )

        def fake_download(base_url, task_id, filename, **kw):
            dest = Path(kw["dest_dir"]) / filename
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(b"x")
            return dest

        monkeypatch.setattr(
            "movie_narrator.cloud.remote_provider.download_artifact",
            fake_download,
        )

        out = render_task_dispatcher(
            request=TaskRequest(movie_name="M", duration=60),
            node="http://node:8765",
            download_dir=str(tmp_path / "out"),
        )
        assert submitted["base_url"] == "http://node:8765"
        assert out.video_path == str(tmp_path / "out" / "final.mp4")

    def test_failed_subtask_raises_distributed_error(self, monkeypatch, tmp_path):
        class FakeRemoteQueue:
            def __init__(self, base_url, **kw):
                pass

            def submit(self, request):
                return "rt"

            def wait(self, task_id, **kw):
                return TaskResult(error="remote boom", error_type="RenderError")

        monkeypatch.setattr(
            "movie_narrator.cloud.distributed.RemoteTaskQueue",
            FakeRemoteQueue,
        )
        with pytest.raises(DistributedRenderError):
            render_task_dispatcher(
                request=TaskRequest(movie_name="M"),
                node="http://node:8765",
            )

    def test_submit_failure_raises_distributed_error(self, monkeypatch, tmp_path):
        from movie_narrator.cloud.remote_queue import RemoteQueueError

        class FakeRemoteQueue:
            def __init__(self, base_url, **kw):
                pass

            def submit(self, request):
                raise RemoteQueueError("connection refused")

        monkeypatch.setattr(
            "movie_narrator.cloud.distributed.RemoteTaskQueue",
            FakeRemoteQueue,
        )
        with pytest.raises(DistributedRenderError):
            render_task_dispatcher(
                request=TaskRequest(movie_name="M"),
                node="http://node:8765",
            )


# ── Worker soft hook fallback ──────────────────────────────


class TestDistributedFallback:
    """Dispatch failure must fall back to the local pipeline."""

    def test_dispatch_failure_falls_back_to_local(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CI", "1")

        class FakePlanner:
            enabled = True
            min_render_seconds = 0.0

            @property
            def available_nodes(self):
                return ["http://node:8765"]

            def should_distribute(self, estimated_seconds):
                return True

        monkeypatch.setattr(
            "movie_narrator.cloud.distributed.DistributedRenderPlanner",
            lambda **kw: FakePlanner(),
        )

        def boom(*args, **kwargs):
            raise DistributedRenderError("node unreachable")

        monkeypatch.setattr(
            "movie_narrator.cloud.distributed.render_task_dispatcher",
            boom,
        )

        req = TaskRequest(
            movie_name="Fallback",
            output_dir=str(tmp_path / "out"),
            max_retries=0,
        )
        task = Task(request=req)
        result = run_task(task, CancelController())

        # Local pipeline (mock) ran after the dispatch failure.
        assert result.status == TaskStatus.COMPLETED
        assert result.result is not None
        assert result.result.video_path is not None

    def test_disabled_distribution_runs_locally(self, monkeypatch, tmp_path):
        """Default (disabled) path never attempts distribution."""
        req = TaskRequest(
            movie_name="Local",
            output_dir=str(tmp_path / "out"),
            max_retries=0,
        )
        task = Task(request=req)
        result = run_task(task, CancelController())
        assert result.status == TaskStatus.COMPLETED


# ── Contract / OpenAPI ─────────────────────────────────────


class TestContractExports:
    """v0.9.4 symbols exported from the stable contract."""

    def test_dlq_symbols_in_contract_all(self):
        import movie_narrator.contract as contract

        for name in [
            "DeadLetterRecord",
            "DeadLetterStore",
            "replay_dead_letter",
        ]:
            assert name in contract.__all__, f"{name} not in contract.__all__"

    def test_distributed_symbols_in_contract_all(self):
        import movie_narrator.contract as contract

        for name in [
            "NodeRegistry",
            "DistributedRenderPlanner",
            "DistributedRenderError",
            "render_task_dispatcher",
        ]:
            assert name in contract.__all__, f"{name} not in contract.__all__"

    def test_task_status_still_exported(self):
        import movie_narrator.contract as contract

        assert "TaskStatus" in contract.__all__
        assert "DEAD" in TaskStatus.__members__

    def test_openapi_includes_deadletters(self):
        from movie_narrator.cloud.openapi import build_openapi_spec

        spec = build_openapi_spec()
        assert "/deadletters" in spec["paths"]
        assert "/deadletters/{task_id}" in spec["paths"]
        assert "/deadletters/{task_id}/replay" in spec["paths"]
        assert "DeadLetterRecord" in spec["components"]["schemas"]
        assert "DeadLetterReplayed" in spec["components"]["schemas"]
