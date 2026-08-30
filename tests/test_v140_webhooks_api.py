# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.4.0 Feature 3: webhook delivery records + redelivery API.

Covers (all via ``httpx.MockTransport`` — no network in unit tests):
- JSONL delivery-log reader: newest first, filters, limit, tolerance
- raw-event log retention + ``load_webhook_event``
- ``WebhookDispatcher.redeliver``: same event id (dedup key), re-signed
  with the same secret, identical body, recorded like any delivery
- ``GET /api/v1/webhooks/deliveries``: filters, limit, 400 bad limit
- ``POST /api/v1/webhooks/redeliver/{event_id}``: 202, 404 unknown event,
  404 webhooks disabled, tenant scoping, API-key auth
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import List, Optional

import httpx
import pytest

from movie_narrator.cloud import TaskAPIServer
from movie_narrator.cloud.models import TaskRequest
from movie_narrator.cloud.webhooks import (
    DELIVERY_LOG_FILENAME,
    EVENT_LOG_FILENAME,
    WebhookDispatcher,
    WebhookEvent,
    load_webhook_event,
    read_delivery_records,
    sign_payload,
)


SECRET = "shhh"


# ── Helpers ────────────────────────────────────────────────


def _fast_pipeline(ctx, **kwargs):
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    return ctx


def _make_dispatcher(handler, tmp_path: Path, **kwargs) -> WebhookDispatcher:
    defaults = dict(
        secret=SECRET,
        timeout=5.0,
        max_retries=1,
        base_delay=0.01,
        delivery_log=tmp_path / DELIVERY_LOG_FILENAME,
        event_log=tmp_path / EVENT_LOG_FILENAME,
        transport=httpx.MockTransport(handler),
    )
    defaults.update(kwargs)
    return WebhookDispatcher(["http://hook.invalid/cb"], **defaults)


def _write_events(path: Path, payloads: List[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for payload in payloads:
            f.write(json.dumps(payload) + "\n")


def _sample_event(event_id: str, tenant: str = "default") -> dict:
    return {
        "id": event_id,
        "type": "task.completed",
        "task_id": "0" * 32,
        "tenant_id": tenant,
        "created_at": "2026-01-01T00:00:00+00:00",
        "data": {"status": "completed", "error": None, "artifacts": ["final.mp4"]},
    }


# ════════════════════════════════════════════════════════════
#  JSONL readers
# ════════════════════════════════════════════════════════════


class TestReadDeliveryRecords:
    def test_newest_first_and_limit(self, tmp_path):
        log = tmp_path / DELIVERY_LOG_FILENAME
        lines = [
            {"event_id": "e1", "attempt": 1, "ok": False, "task_id": "t1"},
            {"event_id": "e1", "attempt": 2, "ok": True, "task_id": "t1"},
            {"event_id": "e2", "attempt": 1, "ok": True, "task_id": "t2"},
        ]
        _write_events(log, lines)
        records = read_delivery_records(log)
        assert [r["attempt"] for r in records] == [1, 2, 1]  # reversed
        capped = read_delivery_records(log, limit=2)
        assert len(capped) == 2
        assert capped[0]["attempt"] == 1  # newest first

    def test_filter_event_and_task(self, tmp_path):
        log = tmp_path / DELIVERY_LOG_FILENAME
        _write_events(
            log,
            [
                {"event_id": "e1", "task_id": "t1"},
                {"event_id": "e2", "task_id": "t2"},
                {"event_id": "e1", "task_id": "t2"},
            ],
        )
        assert [r["task_id"] for r in read_delivery_records(log, event_id="e1")] == [
            "t2",
            "t1",
        ]
        assert [r["event_id"] for r in read_delivery_records(log, task_id="t2")] == [
            "e1",
            "e2",
        ]

    def test_malformed_lines_skipped_and_missing_file_empty(self, tmp_path):
        log = tmp_path / DELIVERY_LOG_FILENAME
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            '{"event_id": "e1"}\nnot-json\n\n{"event_id": "e2"}\n', encoding="utf-8"
        )
        assert len(read_delivery_records(log)) == 2
        assert read_delivery_records(tmp_path / "nope.jsonl") == []

    def test_task_id_filter_excludes_legacy_records(self, tmp_path):
        log = tmp_path / DELIVERY_LOG_FILENAME
        _write_events(log, [{"event_id": "e1"}])  # pre-v1.4.0 record
        assert read_delivery_records(log, task_id="t1") == []


class TestLoadWebhookEvent:
    def test_loads_newest_match(self, tmp_path):
        log = tmp_path / EVENT_LOG_FILENAME
        _write_events(
            log,
            [_sample_event("e1"), _sample_event("e2"), _sample_event("e1")],
        )
        payload = load_webhook_event(log, "e1")
        assert payload is not None
        assert payload["id"] == "e1"
        assert payload["created_at"] == "2026-01-01T00:00:00+00:00"
        assert load_webhook_event(log, "missing") is None

    def test_empty_event_id_is_none(self, tmp_path):
        assert load_webhook_event(tmp_path / EVENT_LOG_FILENAME, "") is None


# ════════════════════════════════════════════════════════════
#  Dispatcher redelivery
# ════════════════════════════════════════════════════════════


class TestRedeliver:
    def test_redeliver_preserves_id_and_re_signs(self, tmp_path):
        captured: List[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            return httpx.Response(200)

        dispatcher = _make_dispatcher(handler, tmp_path)
        event = WebhookEvent(type="task.completed", task_id="0" * 32)
        dispatcher.dispatch(event)
        assert dispatcher.flush(timeout=10)

        # Redeliver the payload exactly as stored in the event log —
        # the same path the API endpoint uses.
        stored = load_webhook_event(tmp_path / EVENT_LOG_FILENAME, event.id)
        assert stored is not None
        assert dispatcher.redeliver(stored)
        assert dispatcher.flush(timeout=10)

        assert len(captured) == 2
        first, second = captured
        assert second.headers["X-MN-Event-Id"] == first.headers["X-MN-Event-Id"]
        assert second.read() == first.read()  # identical payload body
        expected = sign_payload(SECRET, second.read())
        assert second.headers["X-MN-Signature"] == expected

    def test_redeliver_does_not_duplicate_event_log(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        dispatcher = _make_dispatcher(handler, tmp_path)
        event = WebhookEvent(type="task.completed", task_id="0" * 32)
        dispatcher.dispatch(event)
        assert dispatcher.flush(timeout=10)
        events_log = tmp_path / EVENT_LOG_FILENAME
        assert len(events_log.read_text(encoding="utf-8").splitlines()) == 1
        assert dispatcher.redeliver(_sample_event(event.id))
        assert dispatcher.flush(timeout=10)
        assert len(events_log.read_text(encoding="utf-8").splitlines()) == 1

    def test_redelivered_attempt_is_recorded(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        dispatcher = _make_dispatcher(handler, tmp_path)
        payload = _sample_event("a" * 32)
        dispatcher.redeliver(payload)
        assert dispatcher.flush(timeout=10)
        records = read_delivery_records(tmp_path / DELIVERY_LOG_FILENAME)
        assert len(records) == 1
        assert records[0]["event_id"] == "a" * 32
        assert records[0]["task_id"] == "0" * 32
        assert records[0]["ok"] is True

    def test_invalid_payload_rejected(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        dispatcher = _make_dispatcher(handler, tmp_path)
        assert dispatcher.redeliver({"no_id": True}) is False
        assert dispatcher.redeliver("junk") is False  # type: ignore[arg-type]

    def test_disabled_dispatcher_rejects(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        dispatcher = WebhookDispatcher(
            [],
            secret=SECRET,
            transport=httpx.MockTransport(handler),
            delivery_log=tmp_path / DELIVERY_LOG_FILENAME,
            event_log=tmp_path / EVENT_LOG_FILENAME,
        )
        assert dispatcher.redeliver(_sample_event("b" * 32)) is False

    def test_delivery_records_carry_task_id(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200)

        dispatcher = _make_dispatcher(handler, tmp_path)
        dispatcher.dispatch(WebhookEvent(type="task.failed", task_id="c" * 32))
        assert dispatcher.flush(timeout=10)
        records = read_delivery_records(tmp_path / DELIVERY_LOG_FILENAME)
        assert records[0]["task_id"] == "c" * 32


# ════════════════════════════════════════════════════════════
#  HTTP API
# ════════════════════════════════════════════════════════════


def _http(method: str, url: str, headers: Optional[dict] = None, timeout: float = 10.0):
    req = urllib.request.Request(url, data=None, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        return e.code, payload


class _CapturingHandler:
    """MockTransport handler capturing every request (200 OK)."""

    def __init__(self) -> None:
        self.requests: List[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return httpx.Response(200)


@pytest.fixture
def api_server(tmp_path, monkeypatch, fast_pipeline):
    """Loopback server whose queue has an injecting webhook dispatcher."""
    monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=1,
    )
    server.start(blocking=False)
    time.sleep(0.1)
    capture = _CapturingHandler()
    storage_dir = server.queue.storage.storage_dir
    dispatcher = _make_dispatcher(capture, storage_dir)
    server.queue._webhooks = dispatcher
    server._capture = capture  # type: ignore[attr-defined]
    yield server
    server.stop()
    server.queue.webhooks.close() if server.queue.webhooks else None


@pytest.fixture
def fast_pipeline(monkeypatch):
    monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)


class TestDeliveriesEndpoint:
    def test_lists_records_after_task_completion(self, api_server):
        server = api_server
        task_id = server.queue.submit(TaskRequest(movie_name="Deliver"))
        assert server.queue.wait(task_id, timeout=30) is not None
        assert server.queue.webhooks.flush(timeout=10)

        status, body = _http(
            "GET", f"{server.base_url}/api/v1/webhooks/deliveries"
        )
        assert status == 200
        assert body["count"] >= 1
        record = body["deliveries"][0]
        assert record["task_id"] == task_id
        assert record["ok"] is True
        assert record["event_id"]

    def test_filters_by_task_and_event(self, api_server):
        server = api_server
        task_id = server.queue.submit(TaskRequest(movie_name="Filter"))
        assert server.queue.wait(task_id, timeout=30) is not None
        assert server.queue.webhooks.flush(timeout=10)

        status, body = _http(
            "GET",
            f"{server.base_url}/api/v1/webhooks/deliveries?task_id={task_id}",
        )
        assert status == 200
        assert body["count"] == 1
        event_id = body["deliveries"][0]["event_id"]

        status, body = _http(
            "GET",
            f"{server.base_url}/api/v1/webhooks/deliveries?event_id={event_id}",
        )
        assert status == 200
        assert body["count"] >= 1
        assert all(r["event_id"] == event_id for r in body["deliveries"])

        status, body = _http(
            "GET",
            f"{server.base_url}/api/v1/webhooks/deliveries"
            f"?task_id={'f' * 32}",
        )
        assert status == 200
        assert body["count"] == 0

    def test_limit_and_bad_limit(self, api_server):
        server = api_server
        status, body = _http(
            "GET", f"{server.base_url}/api/v1/webhooks/deliveries?limit=1"
        )
        assert status == 200
        assert body["count"] <= 1
        status, _ = _http(
            "GET", f"{server.base_url}/api/v1/webhooks/deliveries?limit=-1"
        )
        assert status == 400


class TestRedeliverEndpoint:
    def test_redeliver_reposts_original_event(self, api_server):
        server = api_server
        capture = server._capture
        task_id = server.queue.submit(TaskRequest(movie_name="Redeliver"))
        assert server.queue.wait(task_id, timeout=30) is not None
        assert server.queue.webhooks.flush(timeout=10)
        assert len(capture.requests) == 1

        event_id = capture.requests[0].headers["X-MN-Event-Id"]
        status, body = _http(
            "POST", f"{server.base_url}/api/v1/webhooks/redeliver/{event_id}"
        )
        assert status == 202
        assert body == {"event_id": event_id, "redelivered": True}
        assert server.queue.webhooks.flush(timeout=10)

        assert len(capture.requests) == 2
        first, second = capture.requests
        assert second.headers["X-MN-Event-Id"] == event_id
        assert second.read() == first.read()
        assert second.headers["X-MN-Signature"] == sign_payload(SECRET, second.read())

    def test_unknown_event_404(self, api_server):
        status, body = _http(
            "POST",
            f"{api_server.base_url}/api/v1/webhooks/redeliver/{'9' * 32}",
        )
        assert status == 404
        assert "not found" in body["error"]

    def test_redeliver_without_webhooks_configured_404(self, tmp_path, monkeypatch, fast_pipeline):
        monkeypatch.delenv("MN_WEBHOOK_URLS", raising=False)
        server = TaskAPIServer(
            host="127.0.0.1", port=0, storage_dir=tmp_path / "t", max_workers=1
        )
        server.start(blocking=False)
        time.sleep(0.1)
        try:
            status, body = _http(
                "POST", f"{server.base_url}/api/v1/webhooks/redeliver/{'a' * 32}"
            )
            assert status == 404
            assert "MN_WEBHOOK_URLS" in body["error"]
        finally:
            server.stop()

    def test_api_key_required_on_non_loopback_style_bind(self, tmp_path, monkeypatch, fast_pipeline):
        """With an api_key configured, redelivery requires X-API-Key."""
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "t",
            max_workers=1,
            api_key="secret-key",
        )
        server.start(blocking=False)
        time.sleep(0.1)
        try:
            status, _ = _http(
                "POST",
                f"{server.base_url}/api/v1/webhooks/redeliver/{'a' * 32}",
            )
            assert status == 401
            status, _ = _http(
                "POST",
                f"{server.base_url}/api/v1/webhooks/redeliver/{'a' * 32}",
                headers={"X-API-Key": "secret-key"},
            )
            assert status == 404  # auth passes; event genuinely unknown
        finally:
            server.stop()

    def test_tenant_scoping(self, tmp_path, monkeypatch, fast_pipeline):
        """A non-default tenant can only redeliver its own events."""
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "t",
            max_workers=1,
            api_key="k",
        )
        server.start(blocking=False)
        time.sleep(0.1)
        capture = _CapturingHandler()
        storage_dir = server.queue.storage.storage_dir
        server.queue._webhooks = _make_dispatcher(capture, storage_dir)
        try:
            _write_events(
                storage_dir / EVENT_LOG_FILENAME,
                [_sample_event("c" * 32, tenant="acme")],
            )
            headers = {"X-API-Key": "k"}
            # default-tenant caller (exempt) may redeliver any event
            status, body = _http(
                "POST",
                f"{server.base_url}/api/v1/webhooks/redeliver/{'c' * 32}",
                headers=headers,
            )
            assert status == 202
            # tenant "other" may not touch acme's event
            status, body = _http(
                "POST",
                f"{server.base_url}/api/v1/webhooks/redeliver/{'c' * 32}",
                headers={**headers, "X-MN-Tenant": "other"},
            )
            assert status == 403
            assert "another tenant" in body["error"]
            # tenant "acme" may redeliver its own event
            status, body = _http(
                "POST",
                f"{server.base_url}/api/v1/webhooks/redeliver/{'c' * 32}",
                headers={**headers, "X-MN-Tenant": "acme"},
            )
            assert status == 202
        finally:
            server.stop()
            if server.queue.webhooks:
                server.queue.webhooks.close()
