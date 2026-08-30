# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.5.1 Feature 3 — provider usage ledger.

Covers:
- ``utils/cost_ledger.py``: counter math (LLM kinds / TTS provider split /
  VLM), thread safety, summary deep-copy semantics.
- ``utils/llm.py`` hook: one record per attempt incl. retry outcome
  (retry 3x with a failing-then-succeeding fake completion), error path.
- ``pipeline/tts.py`` hook: per-segment records + cache-hit accounting
  across two runs; ``ctx.metadata["usage"]`` surface.
- metadata export: the ``usage`` section reaches the metadata JSON.
"""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from movie_narrator.models import Context, ScriptSegment
from movie_narrator.pipeline import tts as tts_module
from movie_narrator.utils import llm as llm_module
from movie_narrator.utils.cost_ledger import (
    UsageLedger,
    get_usage_ledger,
    reset_usage_ledger,
)
from movie_narrator.utils.metadata_export import build_metadata_json
from movie_narrator.utils.cost_tracker import CostTracker
from movie_narrator.reliability.retry import with_retry


@pytest.fixture(autouse=True)
def _fresh_ledger():
    """Isolate the process-global ledger for every test in this module."""
    reset_usage_ledger()
    yield
    reset_usage_ledger()


def _fake_response(content: str = "hello world"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


# ── Ledger counter math ────────────────────────────────────


class TestLedgerCounters:
    def test_llm_counter_math(self):
        ledger = UsageLedger()
        ledger.record_llm(kind="research", prompt_chars=10, resp_chars=20)
        ledger.record_llm(kind="script_beats", prompt_chars=5, resp_chars=7)
        ledger.record_llm(kind="research", error=True, prompt_chars=10)
        summary = ledger.summary()
        assert summary["llm"]["attempts"] == 3
        assert summary["llm"]["errors"] == 1
        assert summary["llm"]["prompt_chars"] == 25
        assert summary["llm"]["resp_chars"] == 27
        assert summary["llm"]["by_kind"]["research"]["attempts"] == 2
        # by_kind buckets track the fine-grained vocabulary; "errors" stays
        # a ledger-level counter (outcome is not part of the kind bucket).
        assert summary["llm"]["by_kind"]["research"]["prompt_chars"] == 20

    def test_llm_cache_hits(self):
        ledger = UsageLedger()
        ledger.record_llm(kind="research", cache_hit=True)
        ledger.record_llm(kind="research")
        summary = ledger.summary()
        assert summary["llm"]["cache_hits"] == 1
        assert summary["llm"]["by_kind"]["research"]["cache_hits"] == 1

    def test_tts_counter_math(self):
        ledger = UsageLedger()
        ledger.record_tts(provider="edge", chars=12)
        ledger.record_tts(provider="edge", chars=8, cache_hit=True)
        ledger.record_tts(provider="openai", chars=30, retries=2)
        summary = ledger.summary()
        assert summary["tts"]["synth_calls"] == 3
        assert summary["tts"]["chars"] == 50
        assert summary["tts"]["cache_hits"] == 1
        assert summary["tts"]["retries"] == 2
        assert summary["tts"]["by_provider"]["edge"] == {
            "calls": 2,
            "chars": 20,
            "cache_hits": 1,
        }
        assert summary["tts"]["by_provider"]["openai"]["chars"] == 30

    def test_vlm_counters(self):
        ledger = UsageLedger()
        ledger.record_vlm(calls=3)
        ledger.record_vlm(cache_hit=True)
        summary = ledger.summary()
        assert summary["vlm"] == {"calls": 4, "cache_hits": 1}

    def test_summary_is_a_deep_copy(self):
        ledger = UsageLedger()
        ledger.record_llm(kind="judge", attempts=2)
        snapshot = ledger.summary()
        snapshot["llm"]["attempts"] = 999
        snapshot["llm"]["by_kind"]["judge"]["attempts"] = 999
        assert ledger.summary()["llm"]["attempts"] == 2
        assert ledger.summary()["llm"]["by_kind"]["judge"]["attempts"] == 2

    def test_summary_is_json_serializable(self):
        import json

        ledger = UsageLedger()
        ledger.record_llm(kind="translate", resp_chars=5)
        ledger.record_tts(provider="edge", chars=6)
        ledger.record_vlm()
        assert json.loads(json.dumps(ledger.summary())) == ledger.summary()

    def test_thread_safety_smoke(self):
        ledger = UsageLedger()

        def worker():
            for _ in range(250):
                ledger.record_llm(kind="judge", attempts=1)

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert ledger.summary()["llm"]["attempts"] == 1000

    def test_shared_instance_and_reset(self):
        first = get_usage_ledger()
        assert get_usage_ledger() is first
        first.record_vlm()
        reset_usage_ledger()
        assert get_usage_ledger() is not first
        assert get_usage_ledger().summary()["vlm"]["calls"] == 0


# ── LLM hook (utils/llm.py) ────────────────────────────────


class _RetryableError(Exception):
    retryable = True


class TestLLMHook:
    def test_one_record_per_attempt_including_retries(self, monkeypatch):
        # Silence the exponential backoff — the counter math is what matters.
        monkeypatch.setattr("time.sleep", lambda _s: None)
        create = MagicMock(
            side_effect=[_RetryableError("boom"), _RetryableError("boom2"), _fake_response()]
        )
        wrapped = with_retry(llm_module.LLM_RETRY_POLICY)(
            llm_module._record_llm_usage(create)
        )
        response = wrapped(
            messages=[{"role": "user", "content": "abc"}], model="m", temperature=0.2
        )
        assert response.choices[0].message.content == "hello world"
        assert create.call_count == 3  # retry 3x
        summary = get_usage_ledger().summary()
        assert summary["llm"]["attempts"] == 3
        assert summary["llm"]["errors"] == 2
        assert summary["llm"]["prompt_chars"] == 9  # 3 attempts x len("abc")
        assert summary["llm"]["resp_chars"] == len("hello world")

    def test_non_retryable_error_records_single_attempt(self):
        create = MagicMock(side_effect=ValueError("bad request"))
        wrapped = with_retry(llm_module.LLM_RETRY_POLICY)(
            llm_module._record_llm_usage(create)
        )
        with pytest.raises(ValueError):
            wrapped(messages=[{"role": "user", "content": "xyz"}])
        summary = get_usage_ledger().summary()
        assert summary["llm"]["attempts"] == 1
        assert summary["llm"]["errors"] == 1
        assert summary["llm"]["resp_chars"] == 0

    def test_wrap_llm_retry_installs_recorder(self):
        client = MagicMock()
        llm_module._wrap_llm_retry(client)
        # Call through the installed wrapper (no retry needed: first try wins).
        client.chat.completions.create(messages=[{"role": "user", "content": "hi"}])
        summary = get_usage_ledger().summary()
        assert summary["llm"]["attempts"] == 1
        assert summary["llm"]["prompt_chars"] == 2


# ── TTS hook (pipeline/tts.py) ─────────────────────────────


def _write_audio(text, voice, path) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"fake-mp3")


class _Metric:
    def __init__(self, index: int):
        self.index = index
        self.issues: list[str] = []

    def to_dict(self) -> dict:
        return {"index": self.index}


class _FakeAudio:
    def __init__(self, duration_ms: int = 1000, frame_rate: int = 44100):
        self._dur = duration_ms
        self.frame_rate = frame_rate
        self.raw_data = b""
        self.max_dBFS = -6.0
        self.dBFS = -20.0

    def __add__(self, other):
        return _FakeAudio(self._dur + getattr(other, "_dur", 0), self.frame_rate)

    def __len__(self) -> int:
        return self._dur

    def export(self, path, format=None, bitrate=None):  # noqa: A002
        return None

    def _spawn(self, raw_data, overrides=None):
        return _FakeAudio(self._dur, (overrides or {}).get("frame_rate", self.frame_rate))

    def set_frame_rate(self, fr: int):
        return _FakeAudio(self._dur, fr)


def _install_tts_patches(monkeypatch):
    audio = MagicMock()
    audio.empty.return_value = _FakeAudio(0)
    audio.silent.side_effect = lambda duration, *a, **k: _FakeAudio(duration)
    audio.from_mp3.return_value = _FakeAudio(1000)
    monkeypatch.setattr(tts_module, "AudioSegment", audio)

    from movie_narrator.config import Settings

    settings = Settings(_env_file=None)
    monkeypatch.setattr(tts_module, "get_settings", lambda: settings)
    monkeypatch.setattr(tts_module, "is_ci", lambda: False)

    provider = MagicMock()
    provider.synthesize = AsyncMock(side_effect=_write_audio)
    monkeypatch.setattr(tts_module, "get_tts_provider", lambda s: provider)
    monkeypatch.setattr(tts_module, "analyze_segment", lambda a, i: _Metric(i))
    monkeypatch.setattr(tts_module, "aggregate_metrics", lambda ms: {"segment_count": len(ms)})
    return provider, settings


def _make_tts_ctx(tmp_path, duration=60):
    ctx = Context(movie_name="T", output_dir=str(tmp_path), duration=duration)
    ctx.segments = [
        ScriptSegment(text="Hello there", index=0),
        ScriptSegment(text="Second segment", index=1),
    ]
    return ctx


class TestTTSHook:
    def test_records_per_segment_and_surfaces_metadata(self, monkeypatch, tmp_path):
        _install_tts_patches(monkeypatch)
        ctx = _make_tts_ctx(tmp_path)
        ctx.cost_tracker = CostTracker()
        tts_module.generate_voice(ctx)

        summary = get_usage_ledger().summary()
        assert summary["tts"]["synth_calls"] == 2
        assert summary["tts"]["chars"] == len("Hello there") + len("Second segment")
        assert summary["tts"]["cache_hits"] == 0  # first run: both misses
        assert summary["tts"]["by_provider"]["edge"]["calls"] == 2
        # Surface: ctx.metadata["usage"] carries the same snapshot.
        assert ctx.metadata["usage"]["tts"]["synth_calls"] == 2
        assert ctx.metadata["usage"]["llm"]["attempts"] == 0

    def test_second_run_counts_cache_hits(self, monkeypatch, tmp_path):
        _install_tts_patches(monkeypatch)
        ctx = _make_tts_ctx(tmp_path)
        tts_module.generate_voice(ctx)
        tts_module.generate_voice(ctx)  # same output dir → cache hits

        summary = get_usage_ledger().summary()
        assert summary["tts"]["synth_calls"] == 4  # 2 runs x 2 segments
        assert summary["tts"]["cache_hits"] == 2  # second run fully cached


# ── Metadata export ────────────────────────────────────────


class TestMetadataExport:
    def test_usage_reaches_metadata_json(self, tmp_path):
        ctx = Context(movie_name="M", output_dir=str(tmp_path))
        ctx.metadata["usage"] = get_usage_ledger().summary()
        get_usage_ledger().record_llm(kind="research", resp_chars=42)
        ctx.metadata["usage"] = get_usage_ledger().summary()
        meta = build_metadata_json(ctx)
        assert meta["usage"]["llm"]["resp_chars"] == 42
        assert meta["usage"]["llm"]["by_kind"]["research"]["resp_chars"] == 42

    def test_usage_is_none_when_tts_never_ran(self, tmp_path):
        """A run without the TTS step exports a null usage section."""
        ctx = Context(movie_name="M", output_dir=str(tmp_path))
        meta = build_metadata_json(ctx)
        assert meta["usage"] is None
