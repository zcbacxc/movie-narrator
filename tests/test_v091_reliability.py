# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the v0.9.1 Reliability features.

Covers:
- Circuit breaker state machine (CLOSED → OPEN → HALF_OPEN → CLOSED),
  recovery probing, and thread safety.
- CircuitOpenError propagation (retryable=True, no network on rejection).
- RetryPolicy framework: exponential backoff, exhaustion, non-retryable
  errors, custom ``should_retry``, async backoff, idempotent
  ``compute_delay``.
- CircuitBreakerRegistry service isolation.
- Integration wiring (TMDB / VLM / LLM / TTS) via mocks — no real
  network requests.
"""

from __future__ import annotations

import asyncio
import threading
import time
from unittest import mock

import pytest

from movie_narrator.reliability import (
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitOpenError,
    CircuitState,
    RetryPolicy,
    circuit_guard,
    compute_delay,
    with_async_retry,
    with_retry,
)


# ── Circuit breaker state machine ─────────────────────────


class TestCircuitBreakerStateMachine:
    def _breaker(self, **kwargs):
        kwargs.setdefault("failure_threshold", 2)
        kwargs.setdefault("recovery_timeout", 30.0)
        kwargs.setdefault("half_open_max_calls", 1)
        return CircuitBreaker("svc", **kwargs)

    def test_starts_closed(self):
        b = self._breaker()
        assert b.state is CircuitState.CLOSED
        assert b.failure_count == 0
        assert not b.is_open

    def test_closed_to_open_after_failure_threshold(self):
        b = self._breaker(failure_threshold=3)
        for _ in range(3):
            with pytest.raises(ConnectionError):
                with b.guard():
                    raise ConnectionError("boom")
        assert b.state is CircuitState.OPEN
        assert b.failure_count == 3
        assert b.is_open

    def test_success_resets_failure_count(self):
        b = self._breaker(failure_threshold=3)
        for _ in range(2):
            with pytest.raises(ConnectionError):
                with b.guard():
                    raise ConnectionError("boom")
        assert b.failure_count == 2
        with b.guard():
            pass  # success
        assert b.failure_count == 0
        assert b.state is CircuitState.CLOSED

    def test_open_rejects_without_network(self):
        b = self._breaker(failure_threshold=1)
        with pytest.raises(ConnectionError):
            with b.guard():
                raise ConnectionError("boom")
        assert b.state is CircuitState.OPEN

        called = []
        with pytest.raises(CircuitOpenError):
            with b.guard():
                called.append("body ran")
        # The guard body must never run while the circuit is open.
        assert called == []

    def test_circuit_open_error_has_retryable_flag(self):
        b = self._breaker(failure_threshold=1)
        with pytest.raises(ConnectionError):
            with b.guard():
                raise ConnectionError("boom")
        with pytest.raises(CircuitOpenError) as excinfo:
            with b.guard():
                pass
        assert excinfo.value.retryable is True
        assert excinfo.value.service == "svc"

    def test_recovery_opens_to_half_open_after_timeout(self):
        b = CircuitBreaker(
            "svc", failure_threshold=1, recovery_timeout=0.05, half_open_max_calls=1
        )
        with pytest.raises(ConnectionError):
            with b.guard():
                raise ConnectionError("boom")
        assert b.state is CircuitState.OPEN

        # Before timeout → still rejected.
        with pytest.raises(CircuitOpenError):
            with b.guard():
                pass

        time.sleep(0.06)
        # After timeout → probe allowed (HALF_OPEN), body runs.
        with b.guard():
            pass
        assert b.state is CircuitState.CLOSED

    def test_half_open_probe_failure_reopens(self):
        b = CircuitBreaker(
            "svc", failure_threshold=1, recovery_timeout=0.05, half_open_max_calls=1
        )
        with pytest.raises(ConnectionError):
            with b.guard():
                raise ConnectionError("boom")
        time.sleep(0.06)

        # Probe fails → circuit re-opens.
        with pytest.raises(ConnectionError):
            with b.guard():
                raise ConnectionError("still down")
        assert b.state is CircuitState.OPEN

        # Still open → rejected again immediately (no new probe).
        with pytest.raises(CircuitOpenError):
            with b.guard():
                pass

    def test_guard_rejects_when_probe_slot_busy(self):
        b = CircuitBreaker(
            "svc", failure_threshold=1, recovery_timeout=0.05, half_open_max_calls=1
        )
        with pytest.raises(ConnectionError):
            with b.guard():
                raise ConnectionError("boom")
        time.sleep(0.06)

        entered = threading.Event()
        release = threading.Event()
        outcomes = []

        def probe():
            try:
                with b.guard():
                    entered.set()
                    release.wait(2.0)
                    outcomes.append("probe-ok")
            except Exception as e:  # noqa: BLE001
                outcomes.append(type(e).__name__)

        t = threading.Thread(target=probe)
        t.start()
        assert entered.wait(1.0)

        # Second caller must be rejected while the probe slot is busy.
        with pytest.raises(CircuitOpenError):
            with b.guard():
                outcomes.append("second-ran")
        release.set()
        t.join(2.0)

        assert "probe-ok" in outcomes
        assert "second-ran" not in outcomes
        # Probe success closed the circuit.
        assert b.state is CircuitState.CLOSED

    def test_decorator_usage(self):
        @circuit_guard("svc-deco")
        def work():
            return "done"

        registry = CircuitBreakerRegistry()
        breaker = registry.get(
            "svc-deco", failure_threshold=1, recovery_timeout=30.0, half_open_max_calls=1
        )
        decorated = circuit_guard("svc-deco", registry=registry)(work)
        assert decorated() == "done"

        with pytest.raises(ConnectionError):
            with breaker.guard():
                raise ConnectionError("boom")
        with pytest.raises(CircuitOpenError):
            decorated()

    def test_manual_force_helpers(self):
        b = self._breaker()
        b.force_open()
        assert b.state is CircuitState.OPEN
        b.force_half_open()
        assert b.state is CircuitState.HALF_OPEN
        b.reset()
        assert b.state is CircuitState.CLOSED
        assert b.failure_count == 0


# ── Circuit breaker concurrency ───────────────────────────


class TestCircuitBreakerConcurrency:
    def test_concurrent_successful_calls_stay_closed(self):
        b = CircuitBreaker(
            "svc", failure_threshold=10, recovery_timeout=30.0, half_open_max_calls=1
        )
        errors = []

        def worker():
            try:
                for _ in range(20):
                    with b.guard():
                        pass
            except Exception as e:  # noqa: BLE001
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert b.state is CircuitState.CLOSED
        assert b.failure_count == 0

    def test_concurrent_failures_open_at_threshold(self):
        b = CircuitBreaker(
            "svc", failure_threshold=5, recovery_timeout=30.0, half_open_max_calls=1
        )

        def worker():
            for _ in range(5):
                try:
                    with b.guard():
                        raise ConnectionError("x")
                except ConnectionError:
                    pass
                except CircuitOpenError:
                    break  # circuit already open — stop probing

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert b.state is CircuitState.OPEN
        assert b.failure_count >= 5


# ── CircuitBreakerRegistry isolation ──────────────────────


class TestCircuitBreakerRegistry:
    def test_services_are_isolated(self):
        registry = CircuitBreakerRegistry()
        a = registry.get(
            "service-a", failure_threshold=1, recovery_timeout=30.0, half_open_max_calls=1
        )
        b = registry.get(
            "service-b", failure_threshold=5, recovery_timeout=30.0, half_open_max_calls=1
        )
        assert a is not b
        assert a.failure_threshold == 1
        assert b.failure_threshold == 5

        # Failing service-a opens only its own circuit.
        with pytest.raises(ConnectionError):
            with a.guard():
                raise ConnectionError("boom")
        assert a.state is CircuitState.OPEN
        assert b.state is CircuitState.CLOSED

    def test_getitem_and_membership(self):
        registry = CircuitBreakerRegistry()
        breaker = registry["svc"]
        assert registry.get("svc") is breaker
        assert "svc" in registry
        assert "missing" not in registry

    def test_reset_and_clear(self):
        registry = CircuitBreakerRegistry()
        breaker = registry.get(
            "svc", failure_threshold=1, recovery_timeout=30.0, half_open_max_calls=1
        )
        with pytest.raises(ConnectionError):
            with breaker.guard():
                raise ConnectionError("boom")
        assert breaker.state is CircuitState.OPEN
        registry.reset()
        assert breaker.state is CircuitState.CLOSED
        registry.clear()
        assert "svc" not in registry


# ── RetryPolicy framework ─────────────────────────────────


class TestRetryPolicy:
    def test_exponential_backoff_sleeps(self):
        calls = {"n": 0}
        sleeps = []

        @with_retry(RetryPolicy(max_attempts=3, base_delay=0.5, multiplier=2.0, jitter=0.0))
        def flaky():
            calls["n"] += 1
            raise ConnectionError("x")

        with mock.patch("movie_narrator.reliability.retry.time.sleep", side_effect=lambda d: sleeps.append(d)):
            with pytest.raises(ConnectionError):
                flaky()

        assert calls["n"] == 3  # initial + 2 retries
        # Delays: attempt 1 → base 0.5, attempt 2 → base*2 = 1.0.
        assert sleeps == [0.5, 1.0]

    def test_success_on_later_attempt(self):
        calls = {"n": 0}

        @with_retry(RetryPolicy(max_attempts=4, base_delay=0.001, jitter=0.0))
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("x")
            return "ok"

        assert flaky() == "ok"
        assert calls["n"] == 3

    def test_exhaustion_reraises_original(self):
        calls = {"n": 0}

        @with_retry(RetryPolicy(max_attempts=2, base_delay=0.001, jitter=0.0))
        def always_fails():
            calls["n"] += 1
            raise TimeoutError("original")

        with pytest.raises(TimeoutError) as excinfo:
            always_fails()
        assert "original" in str(excinfo.value)
        assert calls["n"] == 2

    def test_non_retryable_error_no_retry(self):
        calls = {"n": 0}

        @with_retry(RetryPolicy(max_attempts=3, base_delay=0.001, jitter=0.0))
        def bad():
            calls["n"] += 1
            raise ValueError("bad request")

        with pytest.raises(ValueError):
            bad()
        assert calls["n"] == 1

    def test_retryable_exceptions_whitelist(self):
        calls = {"n": 0}

        @with_retry(
            RetryPolicy(
                max_attempts=3,
                base_delay=0.001,
                jitter=0.0,
                retryable_exceptions=(ConnectionError,),
            )
        )
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("x")
            return "ok"

        assert flaky() == "ok"
        assert calls["n"] == 3

        # A non-whitelisted exception is never retried.
        calls["n"] = 0

        @with_retry(
            RetryPolicy(
                max_attempts=3,
                base_delay=0.001,
                jitter=0.0,
                retryable_exceptions=(ConnectionError,),
            )
        )
        def other():
            calls["n"] += 1
            raise OSError("not whitelisted")

        with pytest.raises(OSError):
            other()
        assert calls["n"] == 1

    def test_custom_should_retry(self):
        calls = {"n": 0}

        @with_retry(
            RetryPolicy(
                max_attempts=4,
                base_delay=0.001,
                jitter=0.0,
                should_retry=lambda exc: isinstance(exc, RuntimeError),
            )
        )
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("transient")
            return "ok"

        assert flaky() == "ok"
        assert calls["n"] == 3

    def test_should_retry_overrides_default(self):
        # ConnectionError is normally retryable — a should_retry that
        # always says False must suppress retries.
        calls = {"n": 0}

        @with_retry(
            RetryPolicy(max_attempts=3, base_delay=0.001, jitter=0.0, should_retry=lambda exc: False)
        )
        def flaky():
            calls["n"] += 1
            raise ConnectionError("x")

        with pytest.raises(ConnectionError):
            flaky()
        assert calls["n"] == 1

    def test_provider_error_retryable_attribute(self):
        from movie_narrator.workflow.errors import ProviderError

        calls = {"n": 0}

        @with_retry(RetryPolicy(max_attempts=3, base_delay=0.001, jitter=0.0))
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise ProviderError("rate limited", retryable=True)
            return "ok"

        assert flaky() == "ok"
        assert calls["n"] == 3

    def test_async_backoff(self):
        calls = {"n": 0}
        sleeps = []

        async def fake_sleep(delay):
            sleeps.append(delay)

        @with_async_retry(RetryPolicy(max_attempts=3, base_delay=0.5, multiplier=2.0, jitter=0.0))
        async def flaky():
            calls["n"] += 1
            raise ConnectionError("x")

        with mock.patch(
            "movie_narrator.reliability.retry.asyncio.sleep", side_effect=fake_sleep
        ):
            with pytest.raises(ConnectionError):
                asyncio.run(flaky())

        assert calls["n"] == 3
        assert sleeps == [0.5, 1.0]

    def test_async_success(self):
        calls = {"n": 0}

        @with_async_retry(RetryPolicy(max_attempts=3, base_delay=0.001, jitter=0.0))
        async def flaky():
            calls["n"] += 1
            if calls["n"] < 2:
                raise TimeoutError("x")
            return "ok"

        assert asyncio.run(flaky()) == "ok"
        assert calls["n"] == 2

    def test_compute_delay_is_idempotent(self):
        policy = RetryPolicy(base_delay=1.0, max_delay=30.0, multiplier=2.0)
        assert compute_delay(0, policy) == 0.0
        assert compute_delay(1, policy) == 1.0
        assert compute_delay(2, policy) == 2.0
        assert compute_delay(3, policy) == 4.0
        # Same inputs → same outputs (deterministic).
        assert compute_delay(2, policy) == compute_delay(2, policy)
        # Capped at max_delay.
        cap = RetryPolicy(base_delay=1.0, max_delay=10.0, multiplier=10.0)
        assert compute_delay(3, cap) == 10.0

    def test_invalid_policy_rejected(self):
        with pytest.raises(ValueError):
            with_retry(RetryPolicy(max_attempts=0))
        with pytest.raises(ValueError):
            with_async_retry(RetryPolicy(max_attempts=0))
        with pytest.raises(ValueError):
            with_retry(RetryPolicy(jitter=2.0))


# ── Integration wiring (mocks, no real network) ───────────


class TestTmdbIntegration:
    def _fresh_registry(self):
        registry = CircuitBreakerRegistry()
        registry.get(
            "tmdb", failure_threshold=2, recovery_timeout=0.05, half_open_max_calls=1
        )
        return registry

    def test_tmdb_network_error_counts_toward_breaker(self):
        from movie_narrator.providers import tmdb as tmdb_module

        tmdb_module._TMDB_CACHE.clear()
        registry = self._fresh_registry()
        breaker = registry["tmdb"]

        err = OSError("connection refused")
        with mock.patch.object(tmdb_module, "CIRCUIT_REGISTRY", registry), mock.patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen", side_effect=err
        ):
            with pytest.raises(OSError):
                tmdb_module._tmdb_get(
                    "https://api.themoviedb.org/3", "/search/movie", "key", {"query": "t"}
                )
            assert breaker.failure_count == 1

    def test_tmdb_open_circuit_fails_fast_without_network(self):
        from movie_narrator.providers import tmdb as tmdb_module

        tmdb_module._TMDB_CACHE.clear()
        registry = self._fresh_registry()
        breaker = registry["tmdb"]
        breaker.force_open()

        with mock.patch.object(tmdb_module, "CIRCUIT_REGISTRY", registry), mock.patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen"
        ) as mock_open:
            with pytest.raises(CircuitOpenError):
                tmdb_module._tmdb_get(
                    "https://api.themoviedb.org/3", "/search/movie", "key", {"query": "t"}
                )
            mock_open.assert_not_called()


class TestVlmIntegration:
    def _fresh_registry(self):
        registry = CircuitBreakerRegistry()
        registry.get(
            "vlm", failure_threshold=2, recovery_timeout=0.05, half_open_max_calls=1
        )
        return registry

    def _captioner(self):
        from movie_narrator.vision.vlm import VLMCaptioner

        return VLMCaptioner(
            api_key="test-key",
            model="gpt-4o",
            base_url="https://example.invalid/v1",
            timeout=1,
        )

    def test_vlm_open_circuit_raises_without_network(self):
        from movie_narrator import models
        from movie_narrator.vision import vlm as vlm_module

        registry = self._fresh_registry()
        breaker = registry["vlm"]
        breaker.force_open()
        captioner = self._captioner()
        scene = models.Scene(index=0, start=0.0, end=10.0)

        with mock.patch.object(vlm_module, "CIRCUIT_REGISTRY", registry), mock.patch(
            "movie_narrator.vision.vlm.urllib.request.urlopen"
        ) as mock_open:
            with pytest.raises(CircuitOpenError):
                captioner._caption_frame("ZmFrZQ==", scene)
            mock_open.assert_not_called()

    def test_vlm_caption_scenes_degrades_when_circuit_open(self, tmp_path):
        from movie_narrator import models
        from movie_narrator.vision import vlm as vlm_module

        video = tmp_path / "video.mp4"
        video.write_bytes(b"fake")

        registry = self._fresh_registry()
        breaker = registry["vlm"]
        breaker.force_open()
        captioner = self._captioner()
        scene = models.Scene(index=0, start=0.0, end=10.0)

        with mock.patch.object(vlm_module, "CIRCUIT_REGISTRY", registry), mock.patch.object(
            captioner, "_extract_keyframe_b64", return_value="ZmFrZQ=="
        ), mock.patch(
            "movie_narrator.vision.vlm.urllib.request.urlopen"
        ) as mock_open:
            result = captioner.caption_scenes([scene], video_path=str(video))
            mock_open.assert_not_called()
        # Circuit open → per-scene failure → fallback label, not a crash.
        assert result and "seconds" in result[0]


class TestLlmIntegration:
    def test_llm_client_open_circuit_fails_fast(self):
        from movie_narrator.utils import llm as llm_module

        registry = CircuitBreakerRegistry()
        breaker = registry.get(
            "llm", failure_threshold=1, recovery_timeout=30.0, half_open_max_calls=1
        )
        breaker.force_open()

        with mock.patch.object(llm_module, "CIRCUIT_REGISTRY", registry), mock.patch(
            "movie_narrator.utils.llm.llm_registry"
        ) as mock_registry:
            with pytest.raises(CircuitOpenError):
                with llm_module.get_llm_client():
                    pass  # pragma: no cover — must not be reached
            # The provider factory must never be invoked while open.
            mock_registry.create.assert_not_called()


class TestTtsIntegration:
    def _fresh_registry(self):
        registry = CircuitBreakerRegistry()
        registry.get(
            "tts", failure_threshold=2, recovery_timeout=0.05, half_open_max_calls=1
        )
        return registry

    def test_tts_open_circuit_raises_retryable_provider_error(self):
        from pathlib import Path

        from movie_narrator.tts import base as tts_base
        from movie_narrator.workflow.errors import ProviderError

        class StubProvider(tts_base.BaseTTSProvider):
            async def _real_synthesize(self, text, voice, output_path):  # noqa: A002
                raise AssertionError("must not run")  # pragma: no cover

        registry = self._fresh_registry()
        registry["tts"].force_open()
        provider = StubProvider()

        with mock.patch.object(tts_base, "CIRCUIT_REGISTRY", registry), mock.patch.object(
            tts_base, "is_ci", return_value=False
        ):
            with pytest.raises(ProviderError) as excinfo:
                asyncio.run(provider.synthesize("hi", "voice", Path("out.mp3")))
            assert excinfo.value.retryable is True
            assert "circuit" in str(excinfo.value).lower()

    def test_tts_network_error_becomes_retryable_and_counts(self):
        from pathlib import Path

        from movie_narrator.tts import base as tts_base
        from movie_narrator.workflow.errors import ProviderError

        class FlakyProvider(tts_base.BaseTTSProvider):
            async def _real_synthesize(self, text, voice, output_path):  # noqa: A002
                raise ConnectionError("conn reset")

        registry = self._fresh_registry()
        breaker = registry["tts"]
        provider = FlakyProvider()

        with mock.patch.object(tts_base, "CIRCUIT_REGISTRY", registry), mock.patch.object(
            tts_base, "is_ci", return_value=False
        ):
            with pytest.raises(ProviderError) as excinfo:
                asyncio.run(provider.synthesize("hi", "voice", Path("out.mp3")))
            assert excinfo.value.retryable is True
        assert breaker.failure_count == 1

    def test_tts_non_network_error_passthrough(self):
        from pathlib import Path

        from movie_narrator.tts import base as tts_base

        class BadProvider(tts_base.BaseTTSProvider):
            async def _real_synthesize(self, text, voice, output_path):  # noqa: A002
                raise ValueError("invalid voice")

        registry = self._fresh_registry()
        breaker = registry["tts"]
        provider = BadProvider()

        with mock.patch.object(tts_base, "CIRCUIT_REGISTRY", registry), mock.patch.object(
            tts_base, "is_ci", return_value=False
        ):
            with pytest.raises(ValueError):
                asyncio.run(provider.synthesize("hi", "voice", Path("out.mp3")))
        # Non-retryable errors still count as breaker failures (the call
        # failed), matching coarse-grained circuit breaker semantics.
        assert breaker.failure_count == 1


# ── Contract surface (v0.9.1) ─────────────────────────────


class TestReliabilityContractExports:
    def test_contract_reexports(self):
        from movie_narrator import contract

        expected = {
            "CircuitState",
            "CircuitBreaker",
            "CircuitBreakerRegistry",
            "CircuitOpenError",
            "RetryPolicy",
            "with_retry",
            "with_async_retry",
        }
        assert expected.issubset(set(contract.__all__))
        for name in expected:
            assert hasattr(contract, name)

    def test_contract_identity(self):
        from movie_narrator import contract

        assert contract.CircuitBreaker is CircuitBreaker
        assert contract.CircuitOpenError is CircuitOpenError
        assert contract.RetryPolicy is RetryPolicy
