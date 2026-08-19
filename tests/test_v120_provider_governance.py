# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""v1.2 Wave 3B — provider call governance + TTS cache accounting.

Covers, with mocked network clients (no real requests):

1. LLM / VLM / TMDB retries routed through the shared
   :class:`~movie_narrator.reliability.retry.RetryPolicy` framework:
   transient failures are retried the configured number of times,
   non-retryable failures fail fast, and policy parameters are pinned.
2. The LLM idempotency decision: a short-circuit / response cache is
   deliberately NOT introduced (LLM output is non-deterministic), and the
   wrapped ``create`` forwards every call to the model.
3. TTS cache accounting: ``get_cache_stats()`` structure, hit/miss/rate
   correctness, on-disk entry/byte scanning, and the ``tts_cache_stats``
   field written into pipeline metadata.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from movie_narrator.config import Settings
from movie_narrator.models import Context, Scene, ScriptSegment
from movie_narrator.reliability import CircuitBreakerRegistry


# ── LLM fake client ────────────────────────────────────────


class _FakeCompletions:
    def __init__(self, create):
        self.create = create


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeLLMClient:
    """Minimal OpenAI-client stand-in exposing ``chat.completions.create``."""

    def __init__(self, create):
        self.chat = _FakeChat(_FakeCompletions(create))


# ── HTTP response helpers ──────────────────────────────────


def _http_resp(status: int, body: str):
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body.encode("utf-8")
    resp.headers = http.client.HTTPMessage()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _http_error(code: int):
    return urllib.error.HTTPError(
        "http://test", code, "Error", http.client.HTTPMessage(), BytesIO(b"")
    )


def _fresh_registry(service: str) -> CircuitBreakerRegistry:
    registry = CircuitBreakerRegistry()
    registry.get(service, failure_threshold=100, recovery_timeout=0.05, half_open_max_calls=1)
    return registry


# ── LLM retry governance ───────────────────────────────────


class TestLlmRetryGovernance:
    def test_retry_then_success(self):
        import movie_narrator.utils.llm as llm_module

        calls = {"n": 0}

        def flaky(**kwargs):
            calls["n"] += 1
            if calls["n"] < 3:
                raise ConnectionError("transient")
            return "ok"

        client = _FakeLLMClient(flaky)
        llm_module._wrap_llm_retry(client)
        with patch("movie_narrator.reliability.retry.time.sleep"):
            result = client.chat.completions.create(model="m", messages=[])

        assert result == "ok"
        assert calls["n"] == 3

    def test_non_retryable_error_not_retried(self):
        import movie_narrator.utils.llm as llm_module

        calls = {"n": 0}

        def bad(**kwargs):
            calls["n"] += 1
            raise ValueError("bad request")

        client = _FakeLLMClient(bad)
        llm_module._wrap_llm_retry(client)
        with pytest.raises(ValueError):
            client.chat.completions.create(model="m", messages=[])

        assert calls["n"] == 1

    def test_policy_params(self):
        import movie_narrator.utils.llm as llm_module

        assert llm_module.LLM_RETRY_POLICY.max_attempts == 3
        assert llm_module.LLM_RETRY_POLICY.base_delay == 1.0
        assert llm_module.LLM_RETRY_POLICY.should_retry is llm_module._llm_should_retry

    def test_no_idempotency_short_circuit(self):
        """The retry layer must NOT memoize LLM responses.

        Same normalized input → two independent calls to the model, because
        LLM output is non-deterministic and a response cache would risk
        stale/wrong reuse (documented decision in ``utils/llm.py``).
        """
        import movie_narrator.utils.llm as llm_module

        calls = {"n": 0}

        def make(**kwargs):
            calls["n"] += 1
            return "result"

        client = _FakeLLMClient(make)
        llm_module._wrap_llm_retry(client)
        kwargs = {"model": "m", "messages": [{"role": "user", "content": "hello"}]}
        client.chat.completions.create(**kwargs)
        client.chat.completions.create(**kwargs)

        assert calls["n"] == 2

    def test_openai_factory_wraps_create_and_disables_sdk_retries(self):
        import movie_narrator.utils.llm as llm_module

        settings = MagicMock()
        settings.llm_timeout = 60
        settings.llm_base_url = "http://x"
        settings.llm_api_key = "k"
        settings.llm_model = "m"
        fake_client = _FakeLLMClient(lambda **kw: "ok")

        with (
            patch.object(llm_module, "get_settings", return_value=settings),
            patch.object(llm_module, "httpx"),
            patch.object(llm_module, "OpenAI", return_value=fake_client) as mock_openai,
        ):
            cm = llm_module._make_openai_llm()
            with cm as llm:
                assert llm.client is fake_client
                assert llm.model == "m"

        assert mock_openai.call_args.kwargs["max_retries"] == 0
        assert hasattr(fake_client.chat.completions.create, "__wrapped__")


# ── VLM retry governance ───────────────────────────────────


def _vlm_ok(content: str):
    resp = MagicMock()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    payload = json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")
    resp.read.return_value = payload
    return resp


class TestVlmRetryGovernance:
    def _captioner(self):
        from movie_narrator.vision.vlm import VLMCaptioner

        return VLMCaptioner(api_key="k", base_url="https://example.invalid/v1", timeout=1)

    def test_retry_on_429_then_success(self):
        from movie_narrator.vision import vlm as vlm_module

        captioner = self._captioner()
        scene = Scene(index=0, start=0.0, end=10.0)
        sleeps = []

        with (
            patch.object(vlm_module, "CIRCUIT_REGISTRY", _fresh_registry("vlm")),
            patch(
                "movie_narrator.vision.vlm.urllib.request.urlopen",
                side_effect=[_http_error(429), _http_error(429), _vlm_ok("a man runs")],
            ) as mock_open,
            patch(
                "movie_narrator.reliability.retry.time.sleep", side_effect=sleeps.append
            ),
        ):
            result = captioner._caption_frame("ZmFrZQ==", scene)

        assert result == "a man runs"
        assert mock_open.call_count == 3
        # 429 backoff is 1s then 2s with the default max_retries=2.
        assert sleeps == [1.0, 2.0]

    def test_non_retryable_4xx_fails_fast(self):
        from movie_narrator.vision import vlm as vlm_module

        captioner = self._captioner()
        scene = Scene(index=0, start=0.0, end=10.0)
        sleeps = []

        with (
            patch.object(vlm_module, "CIRCUIT_REGISTRY", _fresh_registry("vlm")),
            patch(
                "movie_narrator.vision.vlm.urllib.request.urlopen",
                side_effect=_http_error(404),
            ) as mock_open,
            patch(
                "movie_narrator.reliability.retry.time.sleep", side_effect=sleeps.append
            ),
        ):
            with pytest.raises(RuntimeError, match="after 3 attempts"):
                captioner._caption_frame("ZmFrZQ==", scene)

        assert mock_open.call_count == 1
        assert sleeps == []

    def test_policy_params(self):
        captioner = self._captioner()
        # default max_retries=2 → 1 initial + 2 retries.
        assert captioner._retry_policy.max_attempts == 3
        assert captioner._retry_policy.jitter == 0.0


# ── TMDB retry governance ──────────────────────────────────


class TestTmdbRetryGovernance:
    def test_retry_delay_schedule_and_policy(self):
        from movie_narrator.providers import tmdb as tmdb_module

        err = tmdb_module._TMDBRateLimitError(None)
        assert tmdb_module._tmdb_retry_delay(err, 1) == 1
        assert tmdb_module._tmdb_retry_delay(err, 2) == 2
        assert tmdb_module._tmdb_retry_delay(err, 3) == 4
        assert tmdb_module._TMDB_RETRY_POLICY.max_attempts == 4  # 1 initial + 3 retries

    def test_retry_after_header_overrides_backoff(self):
        from movie_narrator.providers import tmdb as tmdb_module

        err = tmdb_module._TMDBRateLimitError(5.0)
        assert tmdb_module._tmdb_retry_delay(err, 1) == 5.0

    def test_429_retry_then_success_via_shared_policy(self):
        from movie_narrator.providers import tmdb as tmdb_module

        tmdb_module._TMDB_CACHE.clear()
        sleeps = []

        with (
            patch.object(tmdb_module, "CIRCUIT_REGISTRY", _fresh_registry("tmdb")),
            patch(
                "movie_narrator.providers.tmdb.urllib.request.urlopen",
                side_effect=[_http_error(429), _http_resp(200, '{"results": []}')],
            ) as mock_open,
            patch(
                "movie_narrator.reliability.retry.time.sleep", side_effect=sleeps.append
            ),
        ):
            result = tmdb_module._tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key", {"query": "t"}
            )

        assert result == {"results": []}
        assert mock_open.call_count == 2
        assert sleeps == [1.0]

    def test_non_429_http_error_not_retried(self):
        from movie_narrator.providers import tmdb as tmdb_module

        tmdb_module._TMDB_CACHE.clear()
        sleeps = []

        with (
            patch.object(tmdb_module, "CIRCUIT_REGISTRY", _fresh_registry("tmdb")),
            patch(
                "movie_narrator.providers.tmdb.urllib.request.urlopen",
                side_effect=_http_error(404),
            ) as mock_open,
            patch(
                "movie_narrator.reliability.retry.time.sleep", side_effect=sleeps.append
            ),
        ):
            result = tmdb_module._tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key", {"query": "t"}
            )

        assert result is None
        assert mock_open.call_count == 1
        assert sleeps == []


# ── TTS cache accounting ───────────────────────────────────


class _FakeAudio:
    def __init__(self, duration_ms: int = 1000, frame_rate: int = 44100):
        self._dur = duration_ms
        self.frame_rate = frame_rate
        self.raw_data = b""
        self.max_dBFS = -6.0
        self.dBFS = -20.0

    def __add__(self, other: "_FakeAudio") -> "_FakeAudio":
        return _FakeAudio(self._dur + getattr(other, "_dur", 0), self.frame_rate)

    def __len__(self) -> int:
        return self._dur

    def export(self, path, format=None, bitrate=None):  # noqa: A002
        return None

    def _spawn(self, raw_data, overrides=None) -> "_FakeAudio":
        overrides = overrides or {}
        return _FakeAudio(self._dur, overrides.get("frame_rate", self.frame_rate))

    def set_frame_rate(self, frame_rate: int) -> "_FakeAudio":
        return _FakeAudio(self._dur, frame_rate)


class _Metric:
    def __init__(self, index: int):
        self.index = index
        self.issues: list[str] = []

    def to_dict(self) -> dict:
        return {"index": self.index}


class TestTtsCacheAccounting:
    def test_get_cache_stats_structure_and_counts(self):
        from movie_narrator.tts.cache import (
            get_cache_stats,
            record_cache_hit,
            record_cache_miss,
            reset_cache_stats,
        )

        reset_cache_stats()
        assert get_cache_stats() == {
            "hits": 0,
            "misses": 0,
            "hit_rate": 0.0,
            "entry_count": 0,
            "total_bytes": 0,
        }

        record_cache_miss()
        record_cache_hit()
        record_cache_hit()
        stats = get_cache_stats()
        assert stats["hits"] == 2
        assert stats["misses"] == 1
        assert stats["hit_rate"] == pytest.approx(round(2 / 3, 4))
        reset_cache_stats()

    def test_get_cache_stats_scans_tracked_roots(self, tmp_path):
        from movie_narrator.tts.cache import (
            get_cache_stats,
            record_cache_hit,
            reset_cache_stats,
        )

        reset_cache_stats()
        root = tmp_path / "tts" / "edge"
        (root / "ab" / "cd").mkdir(parents=True)
        (root / "ab" / "cd" / "abc.mp3").write_bytes(b"\x00" * 100)

        record_cache_hit(root)
        stats = get_cache_stats()
        assert stats["entry_count"] == 1
        assert stats["total_bytes"] == 100
        reset_cache_stats()

    def test_generate_voice_writes_tts_cache_stats(self, monkeypatch, tmp_path):
        from movie_narrator.pipeline import tts as tts_module
        from movie_narrator.tts.cache import reset_cache_stats

        reset_cache_stats()

        audio = MagicMock()
        audio.empty.return_value = _FakeAudio(0)
        audio.silent.side_effect = lambda duration, *a, **k: _FakeAudio(duration)
        audio.from_mp3.return_value = _FakeAudio(1000)
        monkeypatch.setattr(tts_module, "AudioSegment", audio)

        settings = Settings(_env_file=None)
        monkeypatch.setattr(tts_module, "get_settings", lambda: settings)
        monkeypatch.setattr(tts_module, "is_ci", lambda: False)

        def _write(text, voice, path):  # noqa: A002
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            Path(path).write_bytes(b"fake-mp3")

        provider = MagicMock()
        provider.synthesize = AsyncMock(side_effect=_write)
        monkeypatch.setattr(tts_module, "get_tts_provider", lambda s: provider)
        monkeypatch.setattr(tts_module, "analyze_segment", lambda a, i: _Metric(i))
        monkeypatch.setattr(tts_module, "aggregate_metrics", lambda ms: {"segment_count": len(ms)})

        def _make_ctx():
            ctx = Context(movie_name="T", output_dir=str(tmp_path))
            ctx.segments = [
                ScriptSegment(text="seg one", index=0),
                ScriptSegment(text="seg two", index=1),
            ]
            return ctx

        # First run: both segments miss and populate the cache.
        first = _make_ctx()
        tts_module.generate_voice(first)

        stats = first.metadata["tts_cache_stats"]
        assert set(stats.keys()) == {"hits", "misses", "hit_rate", "entry_count", "total_bytes"}
        assert stats["hits"] == 0
        assert stats["misses"] == 2
        assert stats["hit_rate"] == 0.0
        assert stats["entry_count"] == 2
        assert stats["total_bytes"] == 2 * len(b"fake-mp3")

        # Second run: cache hits (cross-task accounting accumulates).
        provider.synthesize = AsyncMock()  # must not be called again
        second = _make_ctx()
        tts_module.generate_voice(second)
        stats2 = second.metadata["tts_cache_stats"]
        assert stats2["hits"] == 2
        assert stats2["misses"] == 2
        assert stats2["hit_rate"] == pytest.approx(0.5)
        assert stats2["entry_count"] == 2
        assert provider.synthesize.call_count == 0

    def test_reset_cache_stats(self):
        from movie_narrator.tts.cache import (
            get_cache_stats,
            record_cache_hit,
            reset_cache_stats,
        )

        reset_cache_stats()
        record_cache_hit()
        assert get_cache_stats()["hits"] == 1
        reset_cache_stats()
        assert get_cache_stats() == {
            "hits": 0,
            "misses": 0,
            "hit_rate": 0.0,
            "entry_count": 0,
            "total_bytes": 0,
        }
