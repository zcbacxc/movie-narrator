# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.8.1 Prometheus metrics (v0.8.1).

Covers the dependency-free metric primitives, the text exposition
renderer (version 0.0.4), label escaping, and the real instrumentation
helpers wired into the server. Also touches the route-template /
``/metrics``-auth helpers in :mod:`movie_narrator.cloud.api`.

These tests need no network and no API keys.
"""

from __future__ import annotations

import pytest

from movie_narrator.cloud import metrics
from movie_narrator.cloud.metrics import (
    ACTIVE_TASKS,
    BUILD_INFO,
    CONTENT_TYPE_LATEST,
    ERRORS_TOTAL,
    HTTP_REQUESTS_TOTAL,
    QUEUE_DEPTH,
    RENDER_DURATION,
    TASKS_TOTAL,
    TASK_DURATION,
    MetricsRegistry,
    get_registry,
    render_prometheus_text,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_registry()
    yield
    reset_registry()


# ── Constants / content type ────────────────────────────────


def test_metric_name_constants():
    assert BUILD_INFO == "mn_build_info"
    assert TASKS_TOTAL == "mn_tasks_total"
    assert QUEUE_DEPTH == "mn_queue_depth"
    assert ACTIVE_TASKS == "mn_active_tasks"
    assert TASK_DURATION == "mn_task_duration_seconds"
    assert RENDER_DURATION == "mn_render_duration_seconds"
    assert ERRORS_TOTAL == "mn_errors_total"
    assert HTTP_REQUESTS_TOTAL == "mn_http_requests_total"


def test_content_type_latest():
    assert CONTENT_TYPE_LATEST == "text/plain; version=0.0.4; charset=utf-8"


# ── Primitives ──────────────────────────────────────────────


def test_counter_render():
    reg = MetricsRegistry()
    reg.counter("my_counter", "A counter", ("code",)).inc(
        2, labels={"code": "200"}
    )
    out = reg.render()
    assert "# HELP my_counter A counter" in out
    assert "# TYPE my_counter counter" in out
    assert 'my_counter{code="200"} 2' in out


def test_gauge_render():
    reg = MetricsRegistry()
    reg.gauge("my_gauge").set(7)
    out = reg.render()
    assert "# TYPE my_gauge gauge" in out
    assert "my_gauge 7" in out


def test_histogram_render_has_buckets_sum_count():
    reg = MetricsRegistry()
    hist = reg.histogram("my_hist", "A hist", (), buckets=[1.0, 5.0])
    hist.observe(3.0)
    out = reg.render()
    assert "# TYPE my_hist histogram" in out
    # Cumulative buckets: 3.0 <= 5.0 but not <= 1.0.
    # Note: integer-valued bucket bounds render without a decimal (1 not 1.0).
    assert 'my_hist_bucket{le="1"} 0' in out
    assert 'my_hist_bucket{le="5"} 1' in out
    assert 'my_hist_bucket{le="+Inf"} 1' in out  # equals _count
    assert "my_hist_sum 3" in out
    assert "my_hist_count 1" in out


def test_label_value_escaping():
    reg = MetricsRegistry()
    reg.counter("esc", "esc", ("path",)).inc(1, labels={"path": 'a\\b"c\nd'})
    out = reg.render()
    # backslash, double-quote and newline must be escaped.
    assert 'path="a\\\\b\\"c\\nd"' in out


# ── Real instrumentation helpers ────────────────────────────


def test_render_prometheus_text_all_families():
    text = render_prometheus_text()
    for name in (
        BUILD_INFO,
        TASKS_TOTAL,
        QUEUE_DEPTH,
        ACTIVE_TASKS,
        TASK_DURATION,
        RENDER_DURATION,
        ERRORS_TOTAL,
        HTTP_REQUESTS_TOTAL,
    ):
        assert f"# TYPE {name} " in text
    # Build info carries the version label.
    assert 'mn_build_info{version=' in text


def test_record_task_submitted_and_terminal():
    metrics.record_task_submitted()
    metrics.record_task_terminal("completed")
    metrics.record_task_terminal("failed")
    c = get_registry().counter(TASKS_TOTAL, label_names=("status",))
    assert c.value({"status": "submitted"}) == 1
    assert c.value({"status": "completed"}) == 1
    assert c.value({"status": "failed"}) == 1


def test_set_queue_depth_and_active_tasks():
    metrics.set_queue_depth(4)
    metrics.set_active_tasks(2)
    g = get_registry()
    assert g.gauge(QUEUE_DEPTH).value() == 4
    assert g.gauge(ACTIVE_TASKS).value() == 2


def test_observe_durations():
    metrics.observe_task_duration(1.5)
    metrics.observe_render_duration(2.5)
    reg = get_registry()
    assert reg.histogram(TASK_DURATION).count() == 1
    assert reg.histogram(RENDER_DURATION).count() == 1
    assert reg.histogram(TASK_DURATION).sum() == 1.5
    assert reg.histogram(RENDER_DURATION).sum() == 2.5


def test_record_error_buckets_by_type():
    metrics.record_error("http_401")
    metrics.record_error("http_401")
    metrics.record_error("worker_thread")
    c = get_registry().counter(ERRORS_TOTAL, label_names=("type",))
    assert c.value({"type": "http_401"}) == 2
    assert c.value({"type": "worker_thread"}) == 1


def test_record_error_defaults_unknown():
    metrics.record_error("")  # empty type
    c = get_registry().counter(ERRORS_TOTAL, label_names=("type",))
    assert c.value({"type": "unknown"}) == 1


def test_record_http_request_uses_path_template():
    metrics.record_http_request("GET", "/tasks/{id}", 200)
    metrics.record_http_request("GET", "/tasks/{id}", 200)
    c = get_registry().counter(
        HTTP_REQUESTS_TOTAL, label_names=("method", "path", "code")
    )
    assert c.value({"method": "GET", "path": "/tasks/{id}", "code": "200"}) == 2


def test_helpers_never_raise_on_bad_input():
    # Telemetry must never break a request or worker thread.
    metrics.record_error(None)  # type: ignore[arg-type]
    metrics.observe_task_duration(float("nan"))
    metrics.record_http_request("", "", 0)
    metrics.set_queue_depth(-1)
    # No exception raised.
    assert True


# ── API observability wiring ────────────────────────────────


def test_route_template_static_and_unknown():
    from movie_narrator.cloud.api import _route_template

    assert _route_template("/health") == "/health"
    assert _route_template("/metrics") == "/metrics"
    assert _route_template("/tasks") == "/tasks"
    assert _route_template("/info") == "/info"
    assert _route_template("/totally/unknown/path") == "/other"


def test_route_template_folds_task_paths():
    from movie_narrator.cloud.api import _route_template

    assert _route_template("/tasks/abc123") == "/tasks/{id}"
    assert _route_template("/tasks/abc123/result") == "/tasks/{id}/result"
    assert (
        _route_template("/tasks/abc123/artifacts") == "/tasks/{id}/artifacts"
    )
    assert (
        _route_template("/tasks/abc123/download/video.mp4")
        == "/tasks/{id}/download/{filename}"
    )


def test_metrics_public_env(monkeypatch):
    from movie_narrator.cloud.api import _metrics_public

    monkeypatch.delenv("MN_METRICS_PUBLIC", raising=False)
    assert _metrics_public() is False
    monkeypatch.setenv("MN_METRICS_PUBLIC", "1")
    assert _metrics_public() is True
    monkeypatch.setenv("MN_METRICS_PUBLIC", "true")
    assert _metrics_public() is True
    monkeypatch.setenv("MN_METRICS_PUBLIC", "0")
    assert _metrics_public() is False
