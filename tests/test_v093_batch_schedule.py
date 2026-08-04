# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.9.3: Batch job submission + batch progress + scheduled jobs.

Covers:
- ``BatchRequest`` validation (empty / oversized batches)
- ``LocalTaskQueue.submit_batch``: success, partial failure, cancellation
- Aggregate ``BatchProgress`` across member tasks
- The lightweight cron parser (``*``, ``*/n``, ranges, lists, invalid input)
- ``JobScheduler`` due-triggering (mocked submission)
- The new API routes (``POST /tasks/batch``, ``GET /batches/{id}``,
  ``POST/DELETE /schedules``)
- OpenAPI document contains the new paths and component schemas
- Contract exports for the new batch & schedule symbols
"""

from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from movie_narrator.cloud import (
    Batch,
    BatchProgress,
    BatchRequest,
    BatchStatus,
    JobScheduler,
    TaskAPIServer,
    TaskRequest,
    TaskStatus,
)
from movie_narrator.cloud.models import Task
from movie_narrator.cloud.queue import LocalTaskQueue
from movie_narrator.cloud.scheduler import CronExpression, ScheduleError


# ── Helpers ────────────────────────────────────────────────


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


@pytest.fixture
def fast_pipeline(monkeypatch):
    """Patch run_pipeline so tasks complete without real work."""
    monkeypatch.setattr(
        "movie_narrator.cloud.worker.run_pipeline", _fast_pipeline
    )


@pytest.fixture
def api_server(tmp_path, fast_pipeline):
    """Start an API server on a random port for testing."""
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=2,
    )
    server.start(blocking=False)
    time.sleep(0.1)
    yield server
    server.stop()


def _http(method: str, url: str, body=None, timeout: float = 10.0):
    """Perform an HTTP request and return ``(status, parsed_json)``."""
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json; charset=utf-8"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        return e.code, payload


# ════════════════════════════════════════════════════════════
#  BatchRequest validation
# ════════════════════════════════════════════════════════════


class TestBatchRequestValidation:
    def test_empty_batch_rejected(self):
        """A batch with no requests is invalid."""
        with pytest.raises(ValidationError):
            BatchRequest(requests=[])

    def test_oversized_batch_rejected(self):
        """More than 50 requests is invalid."""
        reqs = [TaskRequest(movie_name=f"m{i}") for i in range(51)]
        with pytest.raises(ValidationError):
            BatchRequest(requests=reqs)

    def test_max_size_allowed(self):
        """Exactly 50 requests is accepted."""
        reqs = [TaskRequest(movie_name=f"m{i}") for i in range(50)]
        batch = BatchRequest(requests=reqs, name="big")
        assert len(batch.requests) == 50
        assert batch.name == "big"

    def test_format_alias_supported(self):
        """The legacy ``format`` alias works inside batch requests (v0.9.5)."""
        batch = BatchRequest(
            requests=[{"movie_name": "x", "format": "9:16"}]
        )
        assert batch.requests[0].video_format == "9:16"


# ════════════════════════════════════════════════════════════
#  submit_batch
# ════════════════════════════════════════════════════════════


class TestSubmitBatch:
    def test_submit_success(self, tmp_path, fast_pipeline):
        """A batch submits every task and aggregates to completed."""
        queue = LocalTaskQueue(storage_dir=tmp_path / "q")
        batch = queue.submit_batch(
            BatchRequest(
                name="ok",
                requests=[
                    TaskRequest(movie_name=_uniq("a"), max_retries=0),
                    TaskRequest(movie_name=_uniq("b"), max_retries=0),
                ],
            )
        )
        assert batch.batch_id
        assert len(batch.task_ids) == 2
        # The batch may already be terminal when the mock pipeline runs fast
        # (e.g. on CI); only a *completed* state is asserted after the wait
        # loop below, so the initial state check must accept any state.
        assert batch.status in (
            BatchStatus.PENDING,
            BatchStatus.RUNNING,
            BatchStatus.COMPLETED,
        )

        deadline = time.time() + 10
        while time.time() < deadline:
            refreshed = queue.get_batch(batch.batch_id)
            if refreshed.status == BatchStatus.COMPLETED:
                break
            time.sleep(0.05)
        assert refreshed.status == BatchStatus.COMPLETED
        assert refreshed.progress.total == 2
        assert refreshed.progress.completed == 2
        assert refreshed.progress.percentage == 100.0
        assert refreshed.success_count == 2
        assert refreshed.failure_ids == []
        assert refreshed.completed_at is not None
        queue.shutdown()

    def test_submit_persists_batch(self, tmp_path, fast_pipeline):
        """The batch record survives a queue restart (separate JSON file)."""
        storage = tmp_path / "persist"
        queue = LocalTaskQueue(storage_dir=storage)
        batch = queue.submit_batch(
            BatchRequest(requests=[TaskRequest(movie_name=_uniq("p"))])
        )
        queue.shutdown()

        queue2 = LocalTaskQueue(storage_dir=storage)
        assert queue2.get_batch(batch.batch_id) is not None
        assert (storage / "batches.json").is_file()
        queue2.shutdown()

    def test_partial_failure(self, tmp_path, fast_pipeline):
        """A failing submission marks the batch partial_failed."""
        queue = LocalTaskQueue(storage_dir=tmp_path / "q2")
        real_submit = queue.submit

        def flaky_submit(request: TaskRequest) -> str:
            if request.movie_name == "boom":
                raise RuntimeError("cannot submit")
            return real_submit(request)

        queue.submit = flaky_submit  # type: ignore[method-assign]
        batch = queue.submit_batch(
            BatchRequest(
                requests=[
                    TaskRequest(movie_name="boom", max_retries=0),
                    TaskRequest(movie_name=_uniq("good"), max_retries=0),
                ]
            )
        )
        assert batch.status == BatchStatus.PARTIAL_FAILED
        assert len(batch.task_ids) == 1
        assert batch.progress.failed == 1
        assert batch.progress.total == 2
        queue.shutdown()

    def test_all_submissions_fail(self, tmp_path, fast_pipeline):
        """A batch where every submission fails is marked failed."""
        queue = LocalTaskQueue(storage_dir=tmp_path / "q3")

        def failing_submit(request: TaskRequest) -> str:
            raise RuntimeError("queue down")

        queue.submit = failing_submit  # type: ignore[method-assign]
        batch = queue.submit_batch(
            BatchRequest(
                requests=[TaskRequest(movie_name=_uniq("x"), max_retries=0)]
            )
        )
        assert batch.status == BatchStatus.FAILED
        assert batch.task_ids == []
        queue.shutdown()

    def test_cancel_batch(self, tmp_path, monkeypatch):
        """cancel_batch cancels active members and refreshes the aggregate."""
        gate = threading.Event()
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline", _slow_pipeline(gate)
        )
        queue = LocalTaskQueue(storage_dir=tmp_path / "q4", max_workers=1)
        batch = queue.submit_batch(
            BatchRequest(
                requests=[
                    TaskRequest(movie_name=_uniq("r1"), max_retries=0),
                    TaskRequest(movie_name=_uniq("r2"), max_retries=0),
                ]
            )
        )
        # Wait until the first task is actually running.
        deadline = time.time() + 10
        while time.time() < deadline:
            task = queue.get_task(batch.task_ids[0])
            if task and task.status == TaskStatus.RUNNING:
                break
            time.sleep(0.05)
        assert queue.cancel_batch(batch.batch_id) is True
        gate.set()

        deadline = time.time() + 10
        refreshed = queue.get_batch(batch.batch_id)
        # Wait until BOTH the cancelled member is counted AND the reset
        # (second) member reaches a terminal state. The cancelled member is
        # reported first, so breaking on cancelled alone races the second
        # task's completion (v0.9.5 test-robustness fix).
        while time.time() < deadline and (
            refreshed.progress.cancelled == 0 or refreshed.progress.completed == 0
        ):
            time.sleep(0.05)
            refreshed = queue.get_batch(batch.batch_id)
        # One member was cancelled, the second (reset) member completed.
        assert refreshed.progress.cancelled == 1
        assert refreshed.progress.completed == 1
        assert refreshed.status == BatchStatus.PARTIAL_FAILED
        queue.shutdown()

    def test_cancel_batch_unknown(self, tmp_path, fast_pipeline):
        """cancel_batch returns False for an unknown batch."""
        queue = LocalTaskQueue(storage_dir=tmp_path / "q5")
        assert queue.cancel_batch("nonexistent") is False
        queue.shutdown()


# ════════════════════════════════════════════════════════════
#  BatchProgress aggregation
# ════════════════════════════════════════════════════════════


class TestBatchProgressAggregation:
    def test_mixed_statuses_percentage(self, tmp_path, fast_pipeline):
        """Terminal tasks count 100%, running tasks their own progress."""
        queue = LocalTaskQueue(storage_dir=tmp_path / "q6")
        batch = queue.submit_batch(
            BatchRequest(
                requests=[
                    TaskRequest(movie_name=_uniq("x1"), max_retries=0),
                    TaskRequest(movie_name=_uniq("x2"), max_retries=0),
                    TaskRequest(movie_name=_uniq("x3"), max_retries=0),
                    TaskRequest(movie_name=_uniq("x4"), max_retries=0),
                ]
            )
        )
        # Build a deterministic mixed state directly in storage: two
        # completed, one failed, one pending (never submitted).
        completed = Task(
            id="c1",
            request=TaskRequest(movie_name=_uniq("c1")),
            status=TaskStatus.COMPLETED,
        )
        completed2 = Task(
            id="c2",
            request=TaskRequest(movie_name=_uniq("c2")),
            status=TaskStatus.COMPLETED,
        )
        failed = Task(
            id="f1",
            request=TaskRequest(movie_name=_uniq("f")),
            status=TaskStatus.FAILED,
        )
        queue._storage.save(completed)
        queue._storage.save(completed2)
        queue._storage.save(failed)
        batch.task_ids = ["c1", "c2", "f1", "ghost"]
        batch.progress.total = 4
        queue._batch_storage.save(batch)

        refreshed = queue.get_batch(batch.batch_id)
        assert refreshed.progress.completed == 2
        assert refreshed.progress.failed == 1 + 1  # "ghost" never submitted
        assert refreshed.progress.percentage == 75.0  # 3/4 tasks at 100%
        assert refreshed.status == BatchStatus.PARTIAL_FAILED
        assert refreshed.success_count == 2
        assert refreshed.failure_ids == ["f1"]
        queue.shutdown()

    def test_zero_total_guarded(self, tmp_path, fast_pipeline):
        """A batch with total 0 does not divide by zero."""
        queue = LocalTaskQueue(storage_dir=tmp_path / "q7")
        batch = Batch(batch_id="empty", progress=BatchProgress(total=0))
        queue._batch_storage.save(batch)
        refreshed = queue.get_batch("empty")
        assert refreshed is not None
        assert refreshed.progress.percentage == 0.0
        queue.shutdown()


# ════════════════════════════════════════════════════════════
#  Cron parser
# ════════════════════════════════════════════════════════════


class TestCronParser:
    def test_every_minute(self):
        expr = CronExpression.parse("* * * * *")
        base = datetime(2026, 8, 3, 12, 30, 5, tzinfo=timezone.utc)
        nxt = expr.next_after(base)
        assert nxt == datetime(2026, 8, 3, 12, 31, tzinfo=timezone.utc)

    def test_step_minutes(self):
        expr = CronExpression.parse("*/5 * * * *")
        base = datetime(2026, 8, 3, 12, 30, 5, tzinfo=timezone.utc)
        nxt = expr.next_after(base)
        assert nxt.minute == 35

    def test_range(self):
        expr = CronExpression.parse("30 9 1 1 *")
        base = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)
        nxt = expr.next_after(base)
        assert (nxt.month, nxt.day, nxt.hour, nxt.minute) == (1, 1, 9, 30)
        assert nxt.year == 2027

    def test_list(self):
        expr = CronExpression.parse("0,30 * * * *")
        base = datetime(2026, 8, 3, 12, 0, 0, tzinfo=timezone.utc)
        assert expr.next_after(base).minute == 30

    def test_weekday_range(self):
        expr = CronExpression.parse("0 12 * * 1-5")
        # Friday 13:00 → next weekday noon is Monday.
        base = datetime(2026, 8, 7, 13, 0, tzinfo=timezone.utc)
        nxt = expr.next_after(base)
        assert nxt.weekday() == 0  # Monday
        assert (nxt.hour, nxt.minute) == (12, 0)

    def test_dom_dow_or_semantics(self):
        # 13th of the month, OR any Friday.
        expr = CronExpression.parse("0 0 13 * 5")
        base = datetime(2026, 8, 1, 0, 0, tzinfo=timezone.utc)
        nxt = expr.next_after(base)
        assert nxt.day == 7 or nxt.day == 13
        assert nxt.day == 7  # 2026-08-07 is a Friday

    @pytest.mark.parametrize(
        "expr",
        [
            "",               # empty
            "* * *",          # too few fields
            "60 * * * *",     # minute out of range
            "* 24 * * *",     # hour out of range
            "* * 32 * *",     # day-of-month out of range
            "* * * 13 *",     # month out of range
            "* * * * 7",      # day-of-week out of range
            "a * * * *",      # non-numeric
            "* */0 * * *",    # zero step
            "*/x * * * *",    # non-numeric step
            "10-1 * * * *",   # reversed range
        ],
    )
    def test_invalid_expressions(self, expr):
        with pytest.raises(ScheduleError):
            CronExpression.parse(expr)

    def test_matches(self):
        expr = CronExpression.parse("30 12 * * *")
        assert expr.matches(datetime(2026, 8, 3, 12, 30, tzinfo=timezone.utc))
        assert not expr.matches(datetime(2026, 8, 3, 12, 31, tzinfo=timezone.utc))


# ════════════════════════════════════════════════════════════
#  JobScheduler
# ════════════════════════════════════════════════════════════


class TestJobScheduler:
    @pytest.fixture
    def mock_queue(self):
        class _MockQueue:
            def __init__(self):
                self.submitted = []

            def submit(self, request: TaskRequest) -> str:
                self.submitted.append(request)
                return f"tid_{len(self.submitted)}"

        return _MockQueue()

    def test_register_schedule_validates_cron(self, mock_queue, tmp_path):
        scheduler = JobScheduler(queue=mock_queue, storage_dir=tmp_path)
        with pytest.raises(ScheduleError):
            scheduler.register_schedule("not-a-cron", TaskRequest(movie_name="m"))
        assert scheduler.list_schedules() == []

    def test_due_trigger_submits_job(self, mock_queue, tmp_path):
        scheduler = JobScheduler(queue=mock_queue, storage_dir=tmp_path)
        schedule = scheduler.register_schedule(
            "* * * * *", TaskRequest(movie_name="due_movie")
        )
        # Force the next run into the past so it is due immediately.
        schedule.next_run_at = "2000-01-01T00:00:00+00:00"
        scheduler._store.save(schedule)

        runs = scheduler.check_due()
        assert len(runs) == 1
        assert runs[0].status == "submitted"
        assert runs[0].task_id == "tid_1"
        assert mock_queue.submitted[0].movie_name == "due_movie"

        # next_run_at advanced past the forced date.
        updated = scheduler.get_schedule(schedule.schedule_id)
        assert updated.next_run_at is not None
        assert datetime.fromisoformat(updated.next_run_at) > datetime(
            2000, 1, 1, tzinfo=timezone.utc
        )

    def test_disabled_schedule_not_triggered(self, mock_queue, tmp_path):
        scheduler = JobScheduler(queue=mock_queue, storage_dir=tmp_path)
        schedule = scheduler.register_schedule(
            "* * * * *", TaskRequest(movie_name="off"), enabled=False
        )
        schedule.next_run_at = "2000-01-01T00:00:00+00:00"
        scheduler._store.save(schedule)

        assert scheduler.check_due() == []
        assert mock_queue.submitted == []

    def test_failed_submission_records_run(self, tmp_path):
        class _BoomQueue:
            def submit(self, request: TaskRequest) -> str:
                raise RuntimeError("boom")

        scheduler = JobScheduler(queue=_BoomQueue(), storage_dir=tmp_path)
        schedule = scheduler.register_schedule(
            "* * * * *", TaskRequest(movie_name="fails")
        )
        schedule.next_run_at = "2000-01-01T00:00:00+00:00"
        scheduler._store.save(schedule)

        runs = scheduler.check_due()
        assert len(runs) == 1
        assert runs[0].status == "failed"
        assert "boom" in (runs[0].error or "")

    def test_runs_recorded_per_schedule(self, mock_queue, tmp_path):
        scheduler = JobScheduler(queue=mock_queue, storage_dir=tmp_path)
        s1 = scheduler.register_schedule(
            "* * * * *", TaskRequest(movie_name="a")
        )
        s2 = scheduler.register_schedule(
            "* * * * *", TaskRequest(movie_name="b")
        )
        for s in (s1, s2):
            s.next_run_at = "2000-01-01T00:00:00+00:00"
            scheduler._store.save(s)
        scheduler.check_due()

        runs1 = scheduler.get_runs(s1.schedule_id)
        runs2 = scheduler.get_runs(s2.schedule_id)
        assert len(runs1) == 1 and len(runs2) == 1
        # check_due walks newest-first, so either schedule may fire first.
        assert {runs1[0].task_id, runs2[0].task_id} == {"tid_1", "tid_2"}

    def test_start_stop_thread(self, mock_queue, tmp_path):
        """The loop thread starts and stops cleanly."""
        scheduler = JobScheduler(
            queue=mock_queue, storage_dir=tmp_path, poll_interval=0.05
        )
        assert not scheduler.is_running
        scheduler.start()
        assert scheduler.is_running
        scheduler.stop()
        assert not scheduler.is_running
        # Stop is idempotent.
        scheduler.stop()

    def test_cancel_schedule(self, mock_queue, tmp_path):
        scheduler = JobScheduler(queue=mock_queue, storage_dir=tmp_path)
        schedule = scheduler.register_schedule(
            "* * * * *", TaskRequest(movie_name="gone")
        )
        assert scheduler.cancel_schedule(schedule.schedule_id) is True
        assert scheduler.cancel_schedule(schedule.schedule_id) is False
        assert scheduler.get_schedule(schedule.schedule_id) is None


# ════════════════════════════════════════════════════════════
#  API routes
# ════════════════════════════════════════════════════════════


class TestBatchApi:
    def test_post_tasks_batch(self, api_server):
        """POST /tasks/batch returns 201 with a batch_id."""
        status, body = _http(
            "POST",
            f"{api_server.base_url}/tasks/batch",
            body={
                "name": "api-batch",
                "requests": [
                    {"movie_name": _uniq("api1"), "max_retries": 0},
                    {"movie_name": _uniq("api2"), "max_retries": 0},
                ],
            },
        )
        assert status == 201
        assert body["batch_id"]
        assert len(body["task_ids"]) == 2

        deadline = time.time() + 10
        batch = api_server.queue.get_batch(body["batch_id"])
        while time.time() < deadline and batch.status != BatchStatus.COMPLETED:
            time.sleep(0.05)
            batch = api_server.queue.get_batch(body["batch_id"])
        assert batch.status == BatchStatus.COMPLETED

    def test_get_batch(self, api_server):
        """GET /batches/{id} returns the aggregated batch."""
        _, created = _http(
            "POST",
            f"{api_server.base_url}/tasks/batch",
            body={"requests": [{"movie_name": _uniq("get"), "max_retries": 0}]},
        )
        status, body = _http(
            "GET", f"{api_server.base_url}/batches/{created['batch_id']}"
        )
        assert status == 200
        assert body["batch_id"] == created["batch_id"]
        assert "progress" in body
        assert body["progress"]["total"] == 1

    def test_get_batch_not_found(self, api_server):
        """GET /batches/{id} returns 404 for an unknown batch."""
        status, body = _http("GET", f"{api_server.base_url}/batches/deadbeef")
        assert status == 404
        assert "error" in body

    def test_list_batches(self, api_server):
        """GET /batches lists batches newest first."""
        _http(
            "POST",
            f"{api_server.base_url}/tasks/batch",
            body={"requests": [{"movie_name": _uniq("l1"), "max_retries": 0}]},
        )
        status, body = _http("GET", f"{api_server.base_url}/batches")
        assert status == 200
        assert body["count"] >= 1
        assert "batch_id" in body["batches"][0]

    def test_post_batch_invalid_body(self, api_server):
        """POST /tasks/batch with an empty request list returns 400."""
        status, _ = _http(
            "POST",
            f"{api_server.base_url}/tasks/batch",
            body={"requests": []},
        )
        assert status == 400


class TestScheduleApi:
    def test_post_get_delete_schedule(self, api_server):
        """POST /schedules, GET /schedules and DELETE /schedules/{id}."""
        status, created = _http(
            "POST",
            f"{api_server.base_url}/schedules",
            body={
                "cron": "*/10 * * * *",
                "task_request": {"movie_name": _uniq("sched")},
                "enabled": True,
            },
        )
        assert status == 201
        assert created["schedule_id"]
        assert created["cron"] == "*/10 * * * *"
        assert created["next_run_at"] is not None

        status, listing = _http("GET", f"{api_server.base_url}/schedules")
        assert status == 200
        assert listing["count"] >= 1
        assert created["schedule_id"] in {
            s["schedule_id"] for s in listing["schedules"]
        }

        status, runs = _http(
            "GET",
            f"{api_server.base_url}/schedules/{created['schedule_id']}/runs",
        )
        assert status == 200
        assert runs["runs"] == []  # no scheduler loop is running in the test

        status, _ = _http(
            "DELETE",
            f"{api_server.base_url}/schedules/{created['schedule_id']}",
        )
        assert status == 200

        status, _ = _http(
            "DELETE",
            f"{api_server.base_url}/schedules/{created['schedule_id']}",
        )
        assert status == 404

    def test_post_schedule_invalid_cron(self, api_server):
        """POST /schedules with a bad cron expression returns 400."""
        status, body = _http(
            "POST",
            f"{api_server.base_url}/schedules",
            body={
                "cron": "not-a-cron",
                "task_request": {"movie_name": _uniq("bad")},
            },
        )
        assert status == 400
        assert "error" in body


# ════════════════════════════════════════════════════════════
#  OpenAPI
# ════════════════════════════════════════════════════════════


class TestOpenApi:
    def test_new_paths_present(self):
        """The OpenAPI document declares the v0.9.3 routes."""
        from movie_narrator.cloud.openapi import build_openapi_spec

        spec = build_openapi_spec()
        paths = set(spec["paths"])
        for path in (
            "/tasks/batch",
            "/batches",
            "/batches/{batch_id}",
            "/schedules",
            "/schedules/{schedule_id}",
            "/schedules/{schedule_id}/runs",
        ):
            assert path in paths, path

    def test_new_component_schemas_present(self):
        from movie_narrator.cloud.openapi import build_openapi_spec

        spec = build_openapi_spec()
        schemas = set(spec["components"]["schemas"])
        for name in (
            "Batch",
            "BatchRequest",
            "BatchProgress",
            "BatchStatus",
            "ScheduleRequest",
            "ScheduleRun",
        ):
            assert name in schemas, name


# ════════════════════════════════════════════════════════════
#  Contract exports
# ════════════════════════════════════════════════════════════


class TestContractExports:
    def test_batch_schedule_in_contract_all(self):
        from movie_narrator import contract

        for name in (
            "BatchRequest",
            "Batch",
            "BatchProgress",
            "ScheduleRequest",
            "JobScheduler",
            "ScheduleError",
        ):
            assert name in contract.__all__, f"{name} not in contract.__all__"
            assert hasattr(contract, name), f"{name} not accessible"

    def test_importable_from_package(self):
        from movie_narrator import Batch, BatchProgress, BatchRequest
        from movie_narrator import JobScheduler, ScheduleError, ScheduleRequest

        assert all(
            obj is not None
            for obj in (
                Batch,
                BatchProgress,
                BatchRequest,
                JobScheduler,
                ScheduleError,
                ScheduleRequest,
            )
        )
