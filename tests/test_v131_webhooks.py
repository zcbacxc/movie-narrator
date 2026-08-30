# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.3.1 Feature 6: webhook MVP.

Covers (all via ``httpx.MockTransport`` — no network in unit tests):
- HMAC-SHA256 signature correctness over the raw body
- Event id stability (dedup key) across attempts and URLs
- Retry with backoff then success; ``Retry-After`` honoured
- Permanent failure recorded without affecting the task outcome
- Disabled no-op when ``MN_WEBHOOK_URLS`` is unset
- Queue lifecycle emission on terminal transitions
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path

import httpx

from movie_narrator.cloud import LocalTaskQueue
from movie_narrator.cloud.models import Task, TaskRequest, TaskResult, TaskStatus
from movie_narrator.cloud.webhooks import (
    DELIVERY_LOG_FILENAME,
    WebhookDispatcher,
    WebhookEvent,
    event_for_task,
    sign_payload,
)


# ── Helpers ────────────────────────────────────────────────


def _fast_pipeline(ctx, **kwargs):
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    return ctx


def _dispatcher(handler, tmp_path, **kwargs):
    """A dispatcher backed by a MockTransport with a temp delivery log."""
    defaults = dict(
        secret="shhh",
        timeout=5.0,
        max_retries=3,
        base_delay=0.01,
        delivery_log=tmp_path / DELIVERY_LOG_FILENAME,
        transport=httpx.MockTransport(handler),
    )
    defaults.update(kwargs)
    return WebhookDispatcher(["http://hook.invalid/cb"], **defaults)


def _read_log(tmp_path: Path):
    log = tmp_path / DELIVERY_LOG_FILENAME
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines() if line]


def _completed_task(tmp_path: Path) -> Task:
    request = TaskRequest(movie_name="Hooked", output_dir=str(tmp_path / "out"))
    return Task(
        request=request,
        status=TaskStatus.COMPLETED,
        result=TaskResult(
            video_path=str(tmp_path / "out" / "final.mp4"),
            output_dir=str(tmp_path / "out"),
        ),
    )


# ════════════════════════════════════════════════════════════
#  Event model
# ════════════════════════════════════════════════════════════


class TestEventForTask:
    def test_status_mapping(self, tmp_path):
        request = TaskRequest(movie_name="M", output_dir=str(tmp_path / "o"))
        base = {"request": request}
        assert event_for_task(Task(status=TaskStatus.COMPLETED, **base)).type == "task.completed"
        assert event_for_task(Task(status=TaskStatus.FAILED, **base)).type == "task.failed"
        assert event_for_task(Task(status=TaskStatus.DEAD, **base)).type == "task.failed"
        assert event_for_task(Task(status=TaskStatus.CANCELLED, **base)).type == "task.cancelled"
        assert event_for_task(Task(status=TaskStatus.PENDING, **base)) is None
        assert event_for_task(Task(status=TaskStatus.RUNNING, **base)) is None

    def test_payload_is_small_and_named(self, tmp_path):
        task = _completed_task(tmp_path)
        task.last_error = None
        event = event_for_task(task)
        payload = event.to_payload()
        assert payload["id"] == event.id
        assert payload["type"] == "task.completed"
        assert payload["tenant_id"] == "default"
        assert payload["data"]["status"] == "completed"
        assert payload["data"]["artifacts"] == ["final.mp4"]
        assert payload["data"]["error"] is None

    def test_error_truncated(self, tmp_path):
        request = TaskRequest(movie_name="M", output_dir=str(tmp_path / "o"))
        task = Task(
            request=request,
            status=TaskStatus.FAILED,
            last_error="x" * 500,
        )
        event = event_for_task(task)
        assert len(event.data["error"]) == 200

    def test_event_id_unique_per_event(self):
        e1, e2 = WebhookEvent(type="task.completed"), WebhookEvent(type="task.completed")
        assert e1.id != e2.id
        assert len(e1.id) == 32  # uuid4 hex


# ════════════════════════════════════════════════════════════
#  Dispatcher behaviour
# ════════════════════════════════════════════════════════════


class TestSignatureAndHeaders:
    def test_signature_is_hmac_over_raw_body(self, tmp_path):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        dispatcher = _dispatcher(handler, tmp_path, secret="topsecret")
        event = WebhookEvent(type="task.completed", task_id="abc")
        dispatcher.dispatch(event)
        assert dispatcher.flush(timeout=5)
        request = seen[0]
        expected = hmac.new(
            b"topsecret", request.content, hashlib.sha256
        ).hexdigest()
        assert request.headers["X-MN-Signature"] == expected
        assert request.headers["X-MN-Event-Id"] == event.id
        assert request.headers["X-MN-Event-Type"] == "task.completed"
        assert request.headers["X-MN-Timestamp"] == event.created_at
        assert json.loads(request.content)["id"] == event.id

    def test_sign_payload_helper(self):
        assert sign_payload("k", b"body") == hmac.new(b"k", b"body", hashlib.sha256).hexdigest()

    def test_no_signature_header_without_secret(self, tmp_path):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        dispatcher = _dispatcher(handler, tmp_path, secret=None)
        dispatcher.dispatch(WebhookEvent(type="task.completed"))
        assert dispatcher.flush(timeout=5)
        assert "X-MN-Signature" not in seen[0].headers

    def test_event_id_stable_across_urls_and_attempts(self, tmp_path):
        """One logical event keeps one id across retries and target URLs."""
        seen: list[httpx.Request] = []
        state = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            state["count"] += 1
            if state["count"] <= 2:
                return httpx.Response(500)
            return httpx.Response(200)

        dispatcher = WebhookDispatcher(
            ["http://hook.invalid/a", "http://hook.invalid/b"],
            secret="s",
            max_retries=3,
            base_delay=0.01,
            transport=httpx.MockTransport(handler),
        )
        event = WebhookEvent(type="task.completed", task_id="abc")
        dispatcher.dispatch(event)
        assert dispatcher.flush(timeout=5)
        ids = {r.headers["X-MN-Event-Id"] for r in seen}
        assert ids == {event.id}
        assert len(seen) >= 3


class TestRetryBehaviour:
    def test_retry_then_success_records_all_attempts(self, tmp_path):
        state = {"count": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            state["count"] += 1
            if state["count"] <= 2:
                return httpx.Response(500)
            return httpx.Response(200)

        dispatcher = _dispatcher(handler, tmp_path, max_retries=3)
        event = WebhookEvent(type="task.completed", task_id="abc")
        dispatcher.dispatch(event)
        assert dispatcher.flush(timeout=10)
        assert state["count"] == 3
        records = _read_log(tmp_path)
        assert len(records) == 3
        assert [r["ok"] for r in records] == [False, False, True]
        assert [r["attempt"] for r in records] == [1, 2, 3]
        assert {r["event_id"] for r in records} == {event.id}
        assert records[0]["status_code"] == 500

    def test_permanent_failure_recorded(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503)

        dispatcher = _dispatcher(handler, tmp_path, max_retries=2)
        event = WebhookEvent(type="task.failed", task_id="abc")
        dispatcher.dispatch(event)
        assert dispatcher.flush(timeout=10)
        records = _read_log(tmp_path)
        assert len(records) == 3  # 1 + 2 retries
        assert all(not r["ok"] for r in records)
        assert all(r["status_code"] == 503 for r in records)

    def test_retry_after_header_overrides_backoff(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"Retry-After": "0.01"})

        dispatcher = _dispatcher(
            handler, tmp_path, max_retries=2, base_delay=30.0
        )
        start = time.monotonic()
        dispatcher.dispatch(WebhookEvent(type="task.completed", task_id="abc"))
        assert dispatcher.flush(timeout=10)
        elapsed = time.monotonic() - start
        # Without Retry-After mapping, base_delay=30 would blow the budget.
        assert elapsed < 5.0
        assert len(_read_log(tmp_path)) == 3

    def test_transport_error_retried(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        dispatcher = _dispatcher(handler, tmp_path, max_retries=1)
        dispatcher.dispatch(WebhookEvent(type="task.completed", task_id="abc"))
        assert dispatcher.flush(timeout=10)
        records = _read_log(tmp_path)
        assert len(records) == 2
        assert "ConnectError" in records[0]["error"]

    def test_disabled_when_no_urls(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
            raise AssertionError("must not be called")

        dispatcher = WebhookDispatcher(
            [], transport=httpx.MockTransport(handler),
            delivery_log=tmp_path / DELIVERY_LOG_FILENAME,
        )
        assert dispatcher.enabled is False
        dispatcher.dispatch(WebhookEvent(type="task.completed"))
        dispatcher.flush(timeout=1)
        assert _read_log(tmp_path) == []


class TestFromEnv:
    def test_unset_returns_none(self, monkeypatch):
        monkeypatch.delenv("MN_WEBHOOK_URLS", raising=False)
        assert WebhookDispatcher.from_env() is None

    def test_empty_urls_returns_none(self, monkeypatch):
        monkeypatch.setenv("MN_WEBHOOK_URLS", " , ")
        assert WebhookDispatcher.from_env() is None

    def test_full_configuration(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MN_WEBHOOK_URLS", "http://a/cb, http://b/cb")
        monkeypatch.setenv("MN_WEBHOOK_SECRET", "sec")
        monkeypatch.setenv("MN_WEBHOOK_TIMEOUT", "2.5")
        monkeypatch.setenv("MN_WEBHOOK_MAX_RETRIES", "5")
        dispatcher = WebhookDispatcher.from_env(storage_dir=tmp_path)
        assert dispatcher is not None
        assert dispatcher.urls == ["http://a/cb", "http://b/cb"]
        assert dispatcher.enabled
        # delivery log co-located with the task store
        assert dispatcher._delivery_log == tmp_path / DELIVERY_LOG_FILENAME

    def test_invalid_timeout_and_retries_use_defaults(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MN_WEBHOOK_URLS", "http://a/cb")
        monkeypatch.setenv("MN_WEBHOOK_TIMEOUT", "banana")
        monkeypatch.setenv("MN_WEBHOOK_MAX_RETRIES", "-x")
        dispatcher = WebhookDispatcher.from_env(storage_dir=tmp_path)
        assert dispatcher is not None
        assert dispatcher._timeout == 10.0
        assert dispatcher._max_retries == 3


# ════════════════════════════════════════════════════════════
#  Queue lifecycle emission
# ════════════════════════════════════════════════════════════


class TestQueueEmission:
    def _queue_with_hook(self, tmp_path, monkeypatch, handler, **dispatcher_kwargs):
        import movie_narrator.cloud.worker as worker_mod

        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(worker_mod, "run_pipeline", _fast_pipeline)
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        dispatcher = WebhookDispatcher(
            ["http://hook.invalid/cb"],
            secret="s",
            base_delay=0.01,
            delivery_log=tmp_path / "deliveries.jsonl",
            transport=httpx.MockTransport(handler),
            **dispatcher_kwargs,
        )
        # Inject the test dispatcher into the queue's lifecycle slot.
        queue._webhooks = dispatcher
        return queue, dispatcher

    def test_completed_task_notifies(self, tmp_path, monkeypatch):
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        queue, dispatcher = self._queue_with_hook(tmp_path, monkeypatch, handler)
        try:
            task_id = queue.submit(TaskRequest(movie_name="Notify"))
            result = queue.wait(task_id, timeout=15)
            assert result is not None
            assert dispatcher.flush(timeout=5)
        finally:
            queue.shutdown(wait=False)
        assert len(seen) == 1
        payload = json.loads(seen[0].content)
        assert payload["type"] == "task.completed"
        assert payload["task_id"] == task_id
        assert payload["data"]["status"] == "completed"
        records = [
            json.loads(line)
            for line in (tmp_path / "deliveries.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert records and records[0]["ok"] is True

    def test_failed_task_notifies_and_webhook_failure_does_not_affect_task(
        self, tmp_path, monkeypatch
    ):
        """A permanently failing webhook must never affect the task."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(500)

        import movie_narrator.cloud.worker as worker_mod

        def failing_pipeline(ctx, **kwargs):
            raise ConnectionError("network down")

        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(worker_mod, "run_pipeline", failing_pipeline)
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        dispatcher = WebhookDispatcher(
            ["http://hook.invalid/cb"],
            secret="s",
            max_retries=2,
            base_delay=0.01,
            delivery_log=tmp_path / "deliveries.jsonl",
            transport=httpx.MockTransport(handler),
        )
        queue._webhooks = dispatcher
        try:
            task_id = queue.submit(
                TaskRequest(movie_name="Failing", max_retries=0, enable_dlq=False)
            )
            result = queue.wait(task_id, timeout=15)
            assert result is not None
            assert result.error == "network down"
            stored = queue.get_task(task_id)
            assert stored is not None
            assert stored.status == TaskStatus.FAILED
            assert dispatcher.flush(timeout=10)
        finally:
            queue.shutdown(wait=False)
        # the emitted event is task.failed
        assert {json.loads(r.content)["type"] for r in seen} == {"task.failed"}
        # every retry was recorded, all failed
        records = [
            json.loads(line)
            for line in (tmp_path / "deliveries.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(records) == 3  # 1 + 2 retries, all failed
        assert all(not r["ok"] for r in records)
        assert all(r["status_code"] == 500 for r in records)

    def test_cancelled_pending_task_notifies(self, tmp_path, monkeypatch):
        """A never-started task flipped to CANCELLED emits task.cancelled."""
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        dispatcher = WebhookDispatcher(
            ["http://hook.invalid/cb"],
            secret="s",
            delivery_log=tmp_path / "deliveries.jsonl",
            transport=httpx.MockTransport(handler),
        )
        queue._webhooks = dispatcher
        try:
            # Seed a task that was persisted but never enqueued (no
            # CancelController) — the cancel() pending-flip path.
            task = Task(
                request=TaskRequest(movie_name="CancelMe"),
                status=TaskStatus.PENDING,
            )
            queue.storage.save(task)
            assert queue.cancel(task.id) is True
            assert dispatcher.flush(timeout=5)
        finally:
            queue.shutdown(wait=False)
        assert len(seen) == 1
        payload = json.loads(seen[0].content)
        assert payload["type"] == "task.cancelled"
        assert payload["task_id"] == task.id
        assert payload["data"]["status"] == "cancelled"

    def test_cancelled_running_task_notifies(self, tmp_path, monkeypatch):
        """Cancelling a running task emits task.cancelled after the worker."""
        seen: list[httpx.Request] = []
        gate = __import__("threading").Event()

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200)

        def gated_pipeline(ctx, **kwargs):
            gate.wait(timeout=10)
            controller = kwargs.get("controller")
            if controller is not None and controller.is_cancelled():
                from movie_narrator.pipeline.errors import PipelineCancelled

                raise PipelineCancelled("cancelled by test")
            return _fast_pipeline(ctx, **kwargs)

        import movie_narrator.cloud.worker as worker_mod

        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(worker_mod, "run_pipeline", gated_pipeline)
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        dispatcher = WebhookDispatcher(
            ["http://hook.invalid/cb"],
            secret="s",
            delivery_log=tmp_path / "deliveries.jsonl",
            transport=httpx.MockTransport(handler),
        )
        queue._webhooks = dispatcher
        try:
            task_id = queue.submit(TaskRequest(movie_name="CancelRunning"))
            time.sleep(0.3)  # let the worker claim the task
            assert queue.cancel(task_id) is True
            result = queue.wait(task_id, timeout=10)
            assert result is not None
            assert result.error_type == "PipelineCancelled"
            assert dispatcher.flush(timeout=5)
        finally:
            gate.set()
            queue.shutdown(wait=False)
        assert len(seen) == 1
        payload = json.loads(seen[0].content)
        assert payload["type"] == "task.cancelled"
        assert payload["task_id"] == task_id

    def test_disabled_queue_has_no_dispatcher(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MN_WEBHOOK_URLS", raising=False)
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        try:
            assert queue.webhooks is None
        finally:
            queue.shutdown(wait=False)

    def test_from_env_dispatcher_built_when_configured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MN_WEBHOOK_URLS", "http://hook.invalid/cb")
        queue = LocalTaskQueue(storage_dir=tmp_path / "tasks", max_workers=1)
        try:
            assert queue.webhooks is not None
            assert queue.webhooks.urls == ["http://hook.invalid/cb"]
            assert (
                queue.webhooks._delivery_log
                == tmp_path / "tasks" / DELIVERY_LOG_FILENAME
            )
        finally:
            queue.shutdown(wait=False)
