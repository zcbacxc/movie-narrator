# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.5.1 Feature 2 — per-tenant token-bucket rate limiting.

Covers:
- ``cloud/ratelimit.py``: bucket math (fake clock), thread-safety,
  LRU eviction, env parsing.
- ``cloud/api.py``: 429 + ``Retry-After`` + ``{"error": "rate_limited"}``
  on task-submission routes only; disabled-by-default unchanged
  behaviour; per-tenant isolation.
"""

from __future__ import annotations

import io
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, Optional

import pytest

from movie_narrator.cloud import TaskAPIServer
from movie_narrator.cloud.api import _APIHandler
from movie_narrator.cloud.ratelimit import (
    DEFAULT_CAPACITY,
    DEFAULT_REFILL_PER_MINUTE,
    ENV_RATE_LIMIT_CAPACITY,
    ENV_RATE_LIMIT_ENABLED,
    ENV_RATE_LIMIT_REFILL_PER_MINUTE,
    RateLimiter,
    TokenBucket,
)


# ── Test doubles ───────────────────────────────────────────


class FakeClock:
    """Controllable monotonic clock for deterministic bucket math."""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeServer:
    """Minimal server stub for socket-free handler unit tests."""

    def __init__(self, api_key: Optional[str] = None, limiter: Optional[RateLimiter] = None):
        self.host = "127.0.0.1"
        self.api_key = api_key
        self.queue = None
        self.shutting_down = None
        if limiter is not None:
            self.rate_limiter = limiter


def _make_handler(server: object, headers: Optional[dict] = None) -> _APIHandler:
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


def _fast_pipeline(ctx, **kwargs):
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    return ctx


def _http(method: str, url: str, body=None, headers=None, timeout: float = 10.0):
    """Perform an HTTP request and return ``(status, parsed_json, resp_headers)``."""
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8")), dict(resp.headers)
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        return e.code, payload, dict(e.headers)


def _submit_body(movie: str = "RateLimit Movie") -> Dict[str, object]:
    return {"movie_name": movie, "style": "热血搞笑", "duration": 10}


@pytest.fixture
def api_server(tmp_path, monkeypatch):
    """Loopback API server (no key) with a fast mock pipeline."""
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


# ── TokenBucket math ───────────────────────────────────────


class TestTokenBucket:
    def test_grants_until_capacity_exhausted(self):
        clock = FakeClock()
        bucket = TokenBucket(2, 60, clock=clock)
        assert bucket.try_acquire() == 0.0
        assert bucket.try_acquire() == 0.0
        # Empty: deficit of 1 token at 1 token/sec → retry in 1s.
        assert bucket.try_acquire() == pytest.approx(1.0)

    def test_refills_over_time(self):
        clock = FakeClock()
        bucket = TokenBucket(2, 60, clock=clock)
        bucket.try_acquire()
        bucket.try_acquire()
        clock.advance(1.0)
        assert bucket.try_acquire() == 0.0

    def test_refill_is_capped_at_capacity(self):
        clock = FakeClock()
        bucket = TokenBucket(2, 60, clock=clock)
        bucket.try_acquire()
        bucket.try_acquire()
        clock.advance(1000.0)  # far more than enough to refill to cap
        assert bucket.available_tokens == pytest.approx(2.0)
        assert bucket.try_acquire() == 0.0
        assert bucket.try_acquire() == 0.0
        assert bucket.try_acquire() == pytest.approx(1.0)

    def test_retry_after_is_proportional_to_refill_rate(self):
        clock = FakeClock()
        bucket = TokenBucket(1, 30, clock=clock)  # 0.5 tokens/sec
        bucket.try_acquire()
        assert bucket.try_acquire() == pytest.approx(2.0)

    def test_invalid_arguments(self):
        with pytest.raises(ValueError):
            TokenBucket(0, 60)
        with pytest.raises(ValueError):
            TokenBucket(2, 0)


# ── Thread safety ──────────────────────────────────────────


class TestThreadSafety:
    def test_no_over_admission_under_concurrency(self):
        capacity = 100
        bucket = TokenBucket(capacity, 0.6)  # ~0.01 tokens/sec: no meaningful refill
        successes = []
        failures = []
        lock = threading.Lock()

        def worker():
            for _ in range(50):
                outcome = bucket.try_acquire()
                with lock:
                    (successes if outcome == 0.0 else failures).append(outcome)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(successes) == capacity
        assert len(failures) == 200 - capacity


# ── RateLimiter — eviction and env parsing ─────────────────


class TestRateLimiter:
    def test_disabled_by_default_grants_everything(self):
        limiter = RateLimiter()
        assert limiter.enabled is False
        for _ in range(1000):
            assert limiter.try_acquire("t") == 0.0

    def test_lru_eviction_bounds_buckets(self):
        clock = FakeClock()
        limiter = RateLimiter(
            enabled=True, capacity=1, refill_per_minute=60, max_buckets=2, clock=clock
        )
        assert limiter.try_acquire("a") == 0.0
        assert limiter.try_acquire("b") == 0.0
        # Third tenant evicts the least recently used ("a").
        assert limiter.try_acquire("c") == 0.0
        assert set(limiter.snapshot()) == {"b", "c"}
        # "a" starts from a fresh full bucket after eviction.
        assert limiter.try_acquire("a") == 0.0
        assert set(limiter.snapshot()) == {"c", "a"}

    def test_per_key_isolation(self):
        clock = FakeClock()
        limiter = RateLimiter(enabled=True, capacity=1, refill_per_minute=0.6, clock=clock)
        assert limiter.try_acquire("t1") == 0.0
        assert limiter.try_acquire("t1") > 0.0  # t1 exhausted
        assert limiter.try_acquire("t2") == 0.0  # t2 unaffected

    def test_from_env_defaults_disabled(self):
        limiter = RateLimiter.from_env({})
        assert limiter.enabled is False
        assert limiter.capacity == DEFAULT_CAPACITY
        assert limiter.refill_per_minute == DEFAULT_REFILL_PER_MINUTE

    def test_from_env_enables_on_truthy(self):
        for raw in ("1", "true", "yes", "on", "TRUE"):
            limiter = RateLimiter.from_env({ENV_RATE_LIMIT_ENABLED: raw})
            assert limiter.enabled is True

    def test_from_env_ignores_falsy_and_invalid(self):
        for raw in ("0", "false", "", "no"):
            limiter = RateLimiter.from_env({ENV_RATE_LIMIT_ENABLED: raw})
            assert limiter.enabled is False

    def test_from_env_numeric_parsing(self):
        limiter = RateLimiter.from_env(
            {
                ENV_RATE_LIMIT_ENABLED: "1",
                ENV_RATE_LIMIT_CAPACITY: "10",
                ENV_RATE_LIMIT_REFILL_PER_MINUTE: "5",
            }
        )
        assert limiter.capacity == 10.0
        assert limiter.refill_per_minute == 5.0

    def test_from_env_invalid_numbers_fall_back(self):
        limiter = RateLimiter.from_env(
            {
                ENV_RATE_LIMIT_ENABLED: "1",
                ENV_RATE_LIMIT_CAPACITY: "abc",
                ENV_RATE_LIMIT_REFILL_PER_MINUTE: "-3",
            }
        )
        assert limiter.capacity == DEFAULT_CAPACITY
        assert limiter.refill_per_minute == DEFAULT_REFILL_PER_MINUTE


# ── Handler-level gating (socket-free) ─────────────────────


class TestHandlerRejection:
    def test_missing_limiter_never_rejects(self):
        handler = _make_handler(_FakeServer())  # no rate_limiter attribute
        assert handler._rate_limit_rejection() is None

    def test_disabled_limiter_never_rejects(self):
        limiter = RateLimiter(enabled=True).__class__(enabled=False)
        handler = _make_handler(_FakeServer(limiter=limiter))
        assert handler._rate_limit_rejection() is None

    def test_rejection_shape_when_exhausted(self):
        clock = FakeClock()
        limiter = RateLimiter(enabled=True, capacity=1, refill_per_minute=0.6, clock=clock)
        handler = _make_handler(_FakeServer(limiter=limiter))
        assert handler._rate_limit_rejection() is None  # token granted
        status, body, retry_after = handler._rate_limit_rejection()
        assert status == 429
        assert body == {"error": "rate_limited", "retry_after_s": round(retry_after, 2)}
        assert retry_after > 0.0

    def test_tenant_key_from_header(self):
        clock = FakeClock()
        limiter = RateLimiter(enabled=True, capacity=1, refill_per_minute=0.6, clock=clock)
        server = _FakeServer(api_key="secret", limiter=limiter)
        h1 = _make_handler(server, {"X-API-Key": "secret", "X-MN-Tenant": "t1"})
        assert h1._rate_limit_rejection() is None
        assert h1._rate_limit_rejection() is not None  # t1 exhausted
        h2 = _make_handler(server, {"X-API-Key": "secret", "X-MN-Tenant": "t2"})
        assert h2._rate_limit_rejection() is None  # t2 has its own bucket

    def test_retry_after_header_format(self):
        assert _APIHandler._retry_after_header(1000.0) == "1000"
        assert _APIHandler._retry_after_header(1000.4) == "1001"
        assert _APIHandler._retry_after_header(0.2) == "1"


# ── Live API behaviour ─────────────────────────────────────


class TestApiIntegration:
    def test_disabled_default_unchanged(self, api_server):
        """With the limiter disabled (default), submissions behave as before."""
        assert api_server.rate_limiter.enabled is False
        for _ in range(3):
            status, body, _ = _http(
                "POST", f"{api_server.base_url}/tasks", body=_submit_body()
            )
            assert status == 201, body

    def test_submission_throttled_with_retry_after(self, api_server):
        api_server.rate_limiter = RateLimiter(
            enabled=True, capacity=1, refill_per_minute=0.06
        )
        status, body, headers = _http(
            "POST", f"{api_server.base_url}/tasks", body=_submit_body()
        )
        assert status == 201, body
        status, body, headers = _http(
            "POST", f"{api_server.base_url}/tasks", body=_submit_body("Second Movie")
        )
        assert status == 429
        assert body["error"] == "rate_limited"
        assert body["retry_after_s"] > 0
        assert int(headers["Retry-After"]) >= 1

    def test_reads_stay_open_when_throttled(self, api_server):
        api_server.rate_limiter = RateLimiter(
            enabled=True, capacity=1, refill_per_minute=0.06
        )
        _http("POST", f"{api_server.base_url}/tasks", body=_submit_body())
        status, _, _ = _http(
            "POST", f"{api_server.base_url}/tasks", body=_submit_body("Second Movie")
        )
        assert status == 429
        for path in ("/tasks", "/health", "/info", "/ready"):
            status, _, _ = _http("GET", f"{api_server.base_url}{path}")
            assert status == 200, path

    def test_per_tenant_isolation(self, tmp_path, monkeypatch):
        monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "tasks",
            max_workers=1,
            api_key="secret",
        )
        server.rate_limiter = RateLimiter(
            enabled=True, capacity=1, refill_per_minute=0.06
        )
        server.start(blocking=False)
        time.sleep(0.1)
        try:
            auth = {"X-API-Key": "secret"}
            t1 = dict(auth, **{"X-MN-Tenant": "t1"})
            t2 = dict(auth, **{"X-MN-Tenant": "t2"})
            status, _, _ = _http(
                "POST", f"{server.base_url}/tasks", body=_submit_body(), headers=t1
            )
            assert status == 201
            status, _, _ = _http(
                "POST", f"{server.base_url}/tasks", body=_submit_body("B"), headers=t1
            )
            assert status == 429  # t1 exhausted
            status, _, _ = _http(
                "POST", f"{server.base_url}/tasks", body=_submit_body("C"), headers=t2
            )
            assert status == 201  # t2 isolated from t1
        finally:
            server.stop()
