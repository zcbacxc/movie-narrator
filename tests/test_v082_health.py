# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.8.2: health / readiness probes.

Covers:
- ``GET /health`` backward compatibility (v0.6.1 shape is frozen)
- ``GET /health?deep=1`` deep report + status policy
- ``GET /ready`` readiness probe (200 / 503) and its core checks
- auth exemption for both probes (orchestrators cannot send a key)
- probe robustness: the builders never raise, whatever the queue is

All tests run offline: outbound dependency probes are opt-in and are
always skipped when ``CI=1``.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional, Tuple

import pytest

from movie_narrator.cloud import TaskAPIServer
from movie_narrator.cloud.health import (
    CORE_CHECK_NAMES,
    DEEP_DEPS_ENV,
    DEPENDENCY_NAMES,
    REMOTE_STORAGE_ENV,
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_SKIPPED,
    _dependency_targets,
    _probe_url,
    build_health_payload,
    build_readiness_payload,
    dependency_report,
    parse_deep_flag,
    run_core_checks,
    run_dependency_checks,
)


# ── Helpers ───────────────────────────────────────────────


def _get(
    url: str,
    *,
    api_key: Optional[str] = None,
    timeout: float = 5.0,
) -> Tuple[int, Any]:
    """GET *url* and return ``(status_code, parsed_json_body)``.

    Never raises on 4xx/5xx — the probe status code is the payload here.
    """
    headers = {}
    if api_key is not None:
        headers["X-API-Key"] = api_key
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


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


class _BrokenQueue:
    """Queue stand-in whose every attribute access explodes."""

    @property
    def is_started(self) -> bool:
        raise RuntimeError("boom")

    @property
    def storage(self) -> Any:
        raise RuntimeError("boom")


# ════════════════════════════════════════════════════════════
#  /health backward compatibility (v0.6.1 contract)
# ════════════════════════════════════════════════════════════


class TestHealthBackwardCompatibility:
    """A plain GET /health must keep the exact v0.6.1 response shape."""

    def test_shallow_body_unchanged(self, open_server):
        """GET /health returns exactly {"status": "ok"} — no extra fields."""
        code, body = _get(f"{open_server.base_url}/health")
        assert code == 200
        assert body == {"status": "ok"}

    def test_shallow_body_unchanged_on_secure_server(self, secure_server):
        """The frozen shape also applies when auth is configured."""
        code, body = _get(f"{secure_server.base_url}/health")
        assert code == 200
        assert body == {"status": "ok"}

    def test_unrelated_query_stays_shallow(self, open_server):
        """An unrelated query string must not trigger the deep report."""
        code, body = _get(f"{open_server.base_url}/health?verbose=1")
        assert code == 200
        assert body == {"status": "ok"}

    def test_deep_zero_stays_shallow(self, open_server):
        """?deep=0 is an explicit opt-out and keeps the shallow payload."""
        code, body = _get(f"{open_server.base_url}/health?deep=0")
        assert code == 200
        assert body == {"status": "ok"}

    def test_builder_shallow_payload(self, open_server):
        """build_health_payload(deep=False) returns the frozen dict."""
        payload, code = build_health_payload(open_server.queue)
        assert payload == {"status": "ok"}
        assert code == 200


# ════════════════════════════════════════════════════════════
#  /health?deep=1
# ════════════════════════════════════════════════════════════


class TestDeepHealth:
    """Deep health check — superset of /ready plus dependency probes."""

    @pytest.mark.parametrize("query", ["deep=1", "deep=true", "deep=YES", "deep"])
    def test_deep_variants_accepted(self, open_server, query):
        """All documented spellings of the deep flag enable the report."""
        code, body = _get(f"{open_server.base_url}/health?{query}")
        assert code == 200
        assert body["deep"] is True

    def test_deep_payload_shape(self, open_server):
        """Deep payload carries status, version, checks and dependencies."""
        code, body = _get(f"{open_server.base_url}/health?deep=1")
        assert code == 200
        assert body["status"] == "ok"
        assert body["ready"] is True
        assert set(body) >= {
            "status",
            "version",
            "deep",
            "ready",
            "checks",
            "dependencies",
            "duration_ms",
        }
        assert set(body["checks"]) == set(CORE_CHECK_NAMES)
        assert set(body["dependencies"]) == set(DEPENDENCY_NAMES)

    def test_deep_reports_server_version(self, open_server):
        """The deep report advertises the running server version."""
        from movie_narrator import __version__

        _, body = _get(f"{open_server.base_url}/health?deep=1")
        assert body["version"] == __version__

    def test_deps_skipped_in_ci(self, open_server, monkeypatch):
        """CI=1 wins over MN_HEALTH_DEEP_DEPS=1 — no outbound traffic ever."""
        monkeypatch.setenv("CI", "1")
        monkeypatch.setenv(DEEP_DEPS_ENV, "1")
        payload, code = build_health_payload(open_server.queue, deep=True)
        assert code == 200
        assert payload["status"] == "ok"
        for name in DEPENDENCY_NAMES:
            assert payload["dependencies"][name]["status"] == STATUS_SKIPPED
            assert "CI=1" in payload["dependencies"][name]["detail"]

    def test_deps_skipped_when_not_opted_in(self, open_server, monkeypatch):
        """Without MN_HEALTH_DEEP_DEPS the probes are skipped by default."""
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.delenv(DEEP_DEPS_ENV, raising=False)
        payload, code = build_health_payload(open_server.queue, deep=True)
        assert code == 200
        for name in DEPENDENCY_NAMES:
            entry = payload["dependencies"][name]
            assert entry["status"] == STATUS_SKIPPED
            assert DEEP_DEPS_ENV in entry["detail"]

    def test_degraded_dependency_is_200(self, open_server, monkeypatch):
        """A failing dependency degrades the status but keeps HTTP 200."""
        monkeypatch.setattr(
            "movie_narrator.cloud.health.dependency_report",
            lambda: {"llm": {"status": STATUS_FAIL, "detail": "down", "duration_ms": 1.0}},
        )
        payload, code = build_health_payload(open_server.queue, deep=True)
        assert code == 200
        assert payload["status"] == "degraded"
        assert payload["ready"] is True

    def test_failed_core_check_is_503(self, open_server):
        """A failing core check makes the deep check report 503 / error."""
        payload, code = build_health_payload(open_server.queue, shutting_down=True, deep=True)
        assert code == 503
        assert payload["status"] == "error"
        assert payload["ready"] is False
        assert payload["checks"]["shutdown"]["status"] == STATUS_FAIL

    def test_core_failure_outranks_degraded(self, open_server, monkeypatch):
        """503 wins when both a core check and a dependency fail."""
        monkeypatch.setattr(
            "movie_narrator.cloud.health.dependency_report",
            lambda: {"llm": {"status": STATUS_FAIL, "detail": "down", "duration_ms": 1.0}},
        )
        payload, code = build_health_payload(open_server.queue, shutting_down=True, deep=True)
        assert code == 503
        assert payload["status"] == "error"


# ════════════════════════════════════════════════════════════
#  /ready
# ════════════════════════════════════════════════════════════


class TestReadinessEndpoint:
    """GET /ready — readiness probe for orchestrators."""

    def test_ready_when_healthy(self, open_server):
        """A freshly started server is ready (200)."""
        code, body = _get(f"{open_server.base_url}/ready")
        assert code == 200
        assert body["ready"] is True

    def test_reports_all_core_checks(self, open_server):
        """Every documented core check is present and passing."""
        _, body = _get(f"{open_server.base_url}/ready")
        assert set(body["checks"]) == set(CORE_CHECK_NAMES)
        for name, check in body["checks"].items():
            assert check["status"] == STATUS_PASS, f"{name}: {check['detail']}"

    def test_check_entry_shape(self, open_server):
        """Each check reports status, detail and a duration in ms."""
        _, body = _get(f"{open_server.base_url}/ready")
        for check in body["checks"].values():
            assert set(check) == {"status", "detail", "duration_ms"}
            assert isinstance(check["detail"], str) and check["detail"]
            assert isinstance(check["duration_ms"], (int, float))
            assert check["duration_ms"] >= 0

    def test_probe_is_fast(self, open_server):
        """The probe is cheap enough for a 1 s orchestrator timeout."""
        _, body = _get(f"{open_server.base_url}/ready")
        # Target is < 50 ms; assert a loose bound so a busy CI box that
        # merely hiccups on disk I/O does not produce a flaky failure.
        assert body["duration_ms"] < 1000

    def test_not_ready_when_queue_stopped(self, open_server):
        """503 once the queue executor is gone — the server cannot work."""
        open_server.queue.shutdown()
        code, body = _get(f"{open_server.base_url}/ready")
        assert code == 503
        assert body["ready"] is False
        assert body["checks"]["queue"]["status"] == STATUS_FAIL
        assert body["checks"]["workers"]["status"] == STATUS_FAIL

    def test_shutdown_flag_tracks_stop(self, tmp_path):
        """TaskAPIServer.is_shutting_down flips on stop()."""
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "tasks",
            max_workers=1,
        )
        server.start(blocking=False)
        assert server.is_shutting_down is False
        server.stop()
        assert server.is_shutting_down is True


# ════════════════════════════════════════════════════════════
#  Auth exemption
# ════════════════════════════════════════════════════════════


class TestProbeAuthExemption:
    """Probes follow the /health exemption — k8s cannot send an API key."""

    def test_ready_without_key(self, secure_server):
        """GET /ready succeeds with no X-API-Key header."""
        code, _ = _get(f"{secure_server.base_url}/ready")
        assert code == 200

    def test_ready_with_wrong_key(self, secure_server):
        """A wrong key is not rejected either — the route is exempt."""
        code, _ = _get(f"{secure_server.base_url}/ready", api_key="wrong")
        assert code == 200

    def test_deep_health_without_key(self, secure_server):
        """GET /health?deep=1 is exempt too."""
        code, body = _get(f"{secure_server.base_url}/health?deep=1")
        assert code == 200
        assert body["deep"] is True

    def test_protected_route_still_requires_key(self, secure_server):
        """The exemption did not leak to other routes."""
        code, _ = _get(f"{secure_server.base_url}/info")
        assert code == 401


# ════════════════════════════════════════════════════════════
#  Probe internals
# ════════════════════════════════════════════════════════════


class TestProbeInternals:
    """Unit-level behaviour of the health module."""

    @pytest.mark.parametrize(
        "query,expected",
        [
            ({}, False),
            ({"deep": "1"}, True),
            ({"deep": "true"}, True),
            ({"deep": "True"}, True),
            ({"deep": "yes"}, True),
            ({"deep": "on"}, True),
            ({"deep": ""}, True),
            ({"deep": "0"}, False),
            ({"deep": "false"}, False),
            ({"deep": "nope"}, False),
        ],
    )
    def test_parse_deep_flag(self, query, expected):
        """parse_deep_flag only opts in on documented truthy values."""
        assert parse_deep_flag(query) is expected

    def test_checks_never_raise_on_none_queue(self):
        """A missing queue yields failures, not an exception."""
        payload, code = build_readiness_payload(None)
        assert code == 503
        assert payload["ready"] is False
        assert payload["checks"]["queue"]["status"] == STATUS_FAIL
        assert payload["checks"]["storage"]["status"] == STATUS_FAIL
        assert payload["checks"]["workers"]["status"] == STATUS_FAIL
        # Nothing is shutting down, so that check still passes.
        assert payload["checks"]["shutdown"]["status"] == STATUS_PASS

    def test_checks_never_raise_on_broken_queue(self):
        """Exceptions raised inside a check become failed results."""
        checks = run_core_checks(_BrokenQueue())
        assert checks["queue"]["status"] == STATUS_FAIL
        assert "RuntimeError" in checks["queue"]["detail"]
        assert checks["storage"]["status"] == STATUS_FAIL

    def test_deep_health_never_raises_on_broken_queue(self):
        """The deep builder is equally defensive."""
        payload, code = build_health_payload(_BrokenQueue(), deep=True)
        assert code == 503
        assert payload["status"] == "error"

    def test_storage_check_cleans_up_probe_file(self, open_server):
        """The writability probe leaves no file behind."""
        storage_dir = open_server.queue.storage.storage_dir
        before = set(p.name for p in storage_dir.iterdir())
        build_readiness_payload(open_server.queue)
        after = set(p.name for p in storage_dir.iterdir())
        assert before == after

    def test_storage_check_fails_when_dir_missing(self, tmp_path):
        """A vanished storage directory is reported as a failure."""
        missing = tmp_path / "gone"

        class _Storage:
            storage_dir = missing

        class _Queue:
            is_started = True
            storage = _Storage()

        checks = run_core_checks(_Queue())
        assert checks["storage"]["status"] == STATUS_FAIL
        assert "does not exist" in checks["storage"]["detail"]


# ════════════════════════════════════════════════════════════
#  Optional dependency probes (no network — everything is faked)
# ════════════════════════════════════════════════════════════


class _FakeSettings:
    """Minimal Settings stand-in for dependency target resolution."""

    def __init__(self, tts_provider: str = "edge") -> None:
        self.llm_base_url = "http://llm.invalid/v1"
        self.tts_provider = tts_provider
        self.openai_tts_base_url = "http://openai-tts.invalid/v1"
        self.mimo_base_url = "http://mimo.invalid/v1"


class TestDependencyProbes:
    """Outbound probes are opt-in, concurrent and never fatal."""

    def test_edge_tts_has_no_http_target(self, monkeypatch):
        """The default offline TTS backend exposes no URL to probe."""
        monkeypatch.setattr("movie_narrator.config.get_settings", lambda: _FakeSettings("edge"))
        monkeypatch.delenv(REMOTE_STORAGE_ENV, raising=False)
        targets = _dependency_targets()
        assert set(targets) == {"llm"}

    @pytest.mark.parametrize(
        "provider,expected",
        [("openai", "http://openai-tts.invalid/v1"), ("mimo", "http://mimo.invalid/v1")],
    )
    def test_http_tts_providers_are_probed(self, monkeypatch, provider, expected):
        """HTTP-based TTS providers contribute their base URL."""
        monkeypatch.setattr("movie_narrator.config.get_settings", lambda: _FakeSettings(provider))
        targets = _dependency_targets()
        assert targets["tts"] == expected

    def test_remote_storage_from_env(self, monkeypatch):
        """MN_REMOTE_STORAGE_URL adds a remote storage target."""
        monkeypatch.setattr("movie_narrator.config.get_settings", lambda: _FakeSettings("edge"))
        monkeypatch.setenv(REMOTE_STORAGE_ENV, "http://storage.invalid")
        assert _dependency_targets()["remote_storage"] == "http://storage.invalid"

    def test_unconfigured_dependencies_are_skipped(self, monkeypatch):
        """Names without a target are reported as skipped, not failed."""
        monkeypatch.setattr(
            "movie_narrator.cloud.health._dependency_targets",
            lambda: {"llm": "http://llm.invalid/v1"},
        )
        monkeypatch.setattr(
            "movie_narrator.cloud.health._probe_url",
            lambda url: (True, "reachable (HTTP 200)"),
        )
        results = run_dependency_checks()
        assert set(results) == set(DEPENDENCY_NAMES)
        assert results["llm"]["status"] == STATUS_PASS
        assert results["tts"]["status"] == STATUS_SKIPPED
        assert results["remote_storage"]["status"] == STATUS_SKIPPED

    def test_unreachable_dependency_is_a_failure(self, monkeypatch):
        """A transport error marks the dependency as failed."""
        monkeypatch.setattr(
            "movie_narrator.cloud.health._dependency_targets",
            lambda: {"llm": "http://llm.invalid/v1"},
        )
        monkeypatch.setattr(
            "movie_narrator.cloud.health._probe_url",
            lambda url: (False, "unreachable: URLError: nope"),
        )
        results = run_dependency_checks()
        assert results["llm"]["status"] == STATUS_FAIL

    def test_probe_exception_does_not_propagate(self, monkeypatch):
        """A probe blowing up inside its thread is reported, not raised."""

        def _boom(url):
            raise RuntimeError("kaboom")

        monkeypatch.setattr(
            "movie_narrator.cloud.health._dependency_targets",
            lambda: {"llm": "http://llm.invalid/v1"},
        )
        monkeypatch.setattr("movie_narrator.cloud.health._probe_url", _boom)
        results = run_dependency_checks()
        assert results["llm"]["status"] == STATUS_FAIL
        assert "RuntimeError" in results["llm"]["detail"]

    def test_probe_url_treats_http_error_as_reachable(self, monkeypatch):
        """A 4xx answer still proves the endpoint is up."""

        def _raise(request, timeout=None):
            raise urllib.error.HTTPError(
                "http://llm.invalid/v1",
                404,
                "Not Found",
                None,
                None,  # type: ignore[arg-type]
            )

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        ok, detail = _probe_url("http://llm.invalid/v1")
        assert ok is True
        assert "404" in detail

    def test_probe_url_reports_transport_failure(self, monkeypatch):
        """A connection error is a failure, with the exception type in detail."""

        def _raise(request, timeout=None):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(urllib.request, "urlopen", _raise)
        ok, detail = _probe_url("http://llm.invalid/v1")
        assert ok is False
        assert "URLError" in detail

    def test_report_runs_probes_when_opted_in(self, monkeypatch):
        """MN_HEALTH_DEEP_DEPS=1 without CI actually runs the probes."""
        monkeypatch.delenv("CI", raising=False)
        monkeypatch.setenv(DEEP_DEPS_ENV, "1")
        called = {"n": 0}

        def _fake():
            called["n"] += 1
            return {}

        monkeypatch.setattr("movie_narrator.cloud.health.run_dependency_checks", _fake)
        assert dependency_report() == {}
        assert called["n"] == 1
