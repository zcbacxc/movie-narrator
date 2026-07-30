# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the v0.7.0 per-run cost tracker.

Covers: initial state, LLM/TTS call recording, summary structure
(by_step / by_provider grouping), estimated cost calculation,
thread safety under concurrent writes, and the empty-tracker edge case.
"""

from __future__ import annotations

import threading

from movie_narrator.utils.cost_tracker import (
    CostTracker,
    LLMCallRecord,
    TTSCallRecord,
)


# ── 1. Initial state ────────────────────────────────────────


def test_cost_tracker_initial_state():
    """A fresh CostTracker has empty call lists."""
    tracker = CostTracker()
    assert tracker.llm_calls == []
    assert tracker.tts_calls == []


def test_cost_tracker_has_lock():
    """The tracker carries an internal Lock (not repr'd)."""
    tracker = CostTracker()
    assert tracker._lock is not None
    # repr=False means the lock does not appear in repr()
    assert "_lock" not in repr(tracker)


# ── 2. record_llm_call ─────────────────────────────────────


def test_record_llm_call_basic():
    """A single LLM call is appended to llm_calls."""
    tracker = CostTracker()
    tracker.record_llm_call("script", "gpt-4o-mini", {
        "prompt_tokens": 100,
        "completion_tokens": 50,
        "total_tokens": 150,
    })
    assert len(tracker.llm_calls) == 1
    rec = tracker.llm_calls[0]
    assert rec.step == "script"
    assert rec.model == "gpt-4o-mini"
    assert rec.prompt_tokens == 100
    assert rec.completion_tokens == 50
    assert rec.total_tokens == 150


def test_record_llm_call_missing_keys_default_zero():
    """Missing usage keys default to 0, not KeyError."""
    tracker = CostTracker()
    tracker.record_llm_call("research", "test-model", {})
    rec = tracker.llm_calls[0]
    assert rec.prompt_tokens == 0
    assert rec.completion_tokens == 0
    assert rec.total_tokens == 0


def test_record_llm_call_none_values_default_zero():
    """None values in the usage dict are coerced to 0."""
    tracker = CostTracker()
    tracker.record_llm_call("translate", "m", {
        "prompt_tokens": None,
        "completion_tokens": None,
        "total_tokens": None,
    })
    rec = tracker.llm_calls[0]
    assert rec.prompt_tokens == 0
    assert rec.completion_tokens == 0
    assert rec.total_tokens == 0


def test_record_llm_call_non_dict_ignored():
    """Non-dict usage (e.g. a MagicMock) is silently ignored."""
    tracker = CostTracker()
    tracker.record_llm_call("script", "m", "not-a-dict")
    tracker.record_llm_call("script", "m", None)
    assert len(tracker.llm_calls) == 0


def test_record_llm_call_multiple_accumulate():
    """Multiple calls all accumulate in the list."""
    tracker = CostTracker()
    for i in range(5):
        tracker.record_llm_call("script", "m", {
            "prompt_tokens": i, "completion_tokens": i, "total_tokens": 2 * i,
        })
    assert len(tracker.llm_calls) == 5


# ── 3. record_tts_call ─────────────────────────────────────


def test_record_tts_call_basic():
    """A single TTS call is appended to tts_calls."""
    tracker = CostTracker()
    tracker.record_tts_call("edge", characters=120)
    assert len(tracker.tts_calls) == 1
    rec = tracker.tts_calls[0]
    assert rec.provider == "edge"
    assert rec.model == ""
    assert rec.characters == 120
    assert rec.segments == 1
    assert rec.cached is False


def test_record_tts_call_cached():
    """cached=True is recorded correctly."""
    tracker = CostTracker()
    tracker.record_tts_call("openai", model="tts-1",
                            characters=500, segments=3, cached=True)
    rec = tracker.tts_calls[0]
    assert rec.cached is True
    assert rec.segments == 3
    assert rec.provider == "openai"
    assert rec.model == "tts-1"


def test_record_tts_call_multiple_providers():
    """Calls to different providers are all recorded."""
    tracker = CostTracker()
    tracker.record_tts_call("edge", characters=10)
    tracker.record_tts_call("openai", characters=20)
    tracker.record_tts_call("mimo", characters=30)
    assert len(tracker.tts_calls) == 3
    providers = [r.provider for r in tracker.tts_calls]
    assert providers == ["edge", "openai", "mimo"]


# ── 4. summary() structure ─────────────────────────────────


def test_summary_returns_correct_top_level_keys():
    """summary() returns dict with 'llm' and 'tts' keys."""
    tracker = CostTracker()
    s = tracker.summary()
    assert set(s.keys()) == {"llm", "tts"}


def test_summary_llm_structure():
    """The 'llm' sub-dict has all required fields."""
    tracker = CostTracker()
    tracker.record_llm_call("script", "m", {
        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
    })
    llm = tracker.summary()["llm"]
    assert llm["total_calls"] == 1
    assert llm["total_prompt_tokens"] == 100
    assert llm["total_completion_tokens"] == 50
    assert llm["total_tokens"] == 150
    assert "by_step" in llm
    assert "estimated_cost_usd" in llm


def test_summary_tts_structure():
    """The 'tts' sub-dict has all required fields."""
    tracker = CostTracker()
    tracker.record_tts_call("edge", characters=100, segments=2)
    tts = tracker.summary()["tts"]
    assert tts["total_calls"] == 1
    assert tts["total_segments"] == 2
    assert tts["total_characters"] == 100
    assert tts["cached_segments"] == 0
    assert "by_provider" in tts
    assert "estimated_cost_usd" in tts


def test_summary_by_step_grouping():
    """LLM calls are grouped by step name."""
    tracker = CostTracker()
    tracker.record_llm_call("script", "m", {
        "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150,
    })
    tracker.record_llm_call("script", "m", {
        "prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300,
    })
    tracker.record_llm_call("research", "m", {
        "prompt_tokens": 50, "completion_tokens": 25, "total_tokens": 75,
    })

    by_step = tracker.summary()["llm"]["by_step"]
    assert "script" in by_step
    assert "research" in by_step
    # script: 2 calls, 300 prompt, 150 completion, 450 total
    assert by_step["script"]["calls"] == 2
    assert by_step["script"]["prompt_tokens"] == 300
    assert by_step["script"]["completion_tokens"] == 150
    assert by_step["script"]["total_tokens"] == 450
    # research: 1 call
    assert by_step["research"]["calls"] == 1
    assert by_step["research"]["prompt_tokens"] == 50


def test_summary_by_provider_grouping():
    """TTS calls are grouped by provider name."""
    tracker = CostTracker()
    tracker.record_tts_call("edge", characters=100, segments=2)
    tracker.record_tts_call("edge", characters=50, segments=1, cached=True)
    tracker.record_tts_call("openai", characters=2000, segments=5)

    by_provider = tracker.summary()["tts"]["by_provider"]
    assert "edge" in by_provider
    assert "openai" in by_provider
    # edge: 2 calls, 3 segments, 150 chars, 1 cached
    assert by_provider["edge"]["calls"] == 2
    assert by_provider["edge"]["segments"] == 3
    assert by_provider["edge"]["characters"] == 150
    assert by_provider["edge"]["cached_segments"] == 1
    # openai: 1 call, 5 segments, 2000 chars, 0 cached
    assert by_provider["openai"]["calls"] == 1
    assert by_provider["openai"]["segments"] == 5
    assert by_provider["openai"]["characters"] == 2000
    assert by_provider["openai"]["cached_segments"] == 0


# ── 5. Estimated cost calculation ──────────────────────────


def test_summary_llm_estimated_cost():
    """LLM estimated cost = prompt*0.002/1K + completion*0.006/1K."""
    tracker = CostTracker()
    tracker.record_llm_call("script", "m", {
        "prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500,
    })
    cost = tracker.summary()["llm"]["estimated_cost_usd"]
    # 1000/1000 * 0.002 + 500/1000 * 0.006 = 0.002 + 0.003 = 0.005
    assert abs(cost - 0.005) < 1e-9


def test_summary_tts_estimated_cost_openai():
    """OpenAI TTS cost = chars * 0.015 / 1K (non-cached only)."""
    tracker = CostTracker()
    tracker.record_tts_call("openai", characters=2000, segments=1)
    cost = tracker.summary()["tts"]["estimated_cost_usd"]
    # 2000/1000 * 0.015 = 0.03
    assert abs(cost - 0.03) < 1e-9


def test_summary_tts_cached_excluded_from_cost():
    """Cached TTS segments incur no cost."""
    tracker = CostTracker()
    tracker.record_tts_call("openai", characters=2000, segments=1, cached=True)
    cost = tracker.summary()["tts"]["estimated_cost_usd"]
    assert cost == 0.0


def test_summary_tts_edge_and_mimo_free():
    """Edge and Mimo TTS providers are free."""
    tracker = CostTracker()
    tracker.record_tts_call("edge", characters=10000, segments=5)
    tracker.record_tts_call("mimo", characters=5000, segments=3)
    cost = tracker.summary()["tts"]["estimated_cost_usd"]
    assert cost == 0.0


def test_summary_tts_mixed_cached_and_fresh():
    """Mixed cached/fresh OpenAI calls: only fresh chars are billed."""
    tracker = CostTracker()
    tracker.record_tts_call("openai", characters=1000, cached=True)
    tracker.record_tts_call("openai", characters=2000, cached=False)
    cost = tracker.summary()["tts"]["estimated_cost_usd"]
    # Only 2000 non-cached chars: 2000/1000 * 0.015 = 0.03
    assert abs(cost - 0.03) < 1e-9


def test_summary_cached_segments_counted():
    """Cached segments are counted in totals even though free."""
    tracker = CostTracker()
    tracker.record_tts_call("edge", characters=100, segments=2, cached=True)
    tracker.record_tts_call("edge", characters=50, segments=1, cached=False)
    tts = tracker.summary()["tts"]
    assert tts["total_segments"] == 3
    assert tts["cached_segments"] == 2
    assert tts["total_characters"] == 150


# ── 6. Thread safety ───────────────────────────────────────


def test_thread_safe_concurrent_llm_calls():
    """Concurrent record_llm_call from many threads must not lose records."""
    tracker = CostTracker()
    n_threads = 20
    calls_per_thread = 50

    def worker():
        for _ in range(calls_per_thread):
            tracker.record_llm_call("script", "m", {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
            })

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = n_threads * calls_per_thread
    assert len(tracker.llm_calls) == expected


def test_thread_safe_concurrent_tts_calls():
    """Concurrent record_tts_call from many threads must not lose records."""
    tracker = CostTracker()
    n_threads = 20
    calls_per_thread = 50

    def worker():
        for _ in range(calls_per_thread):
            tracker.record_tts_call("edge", characters=10, segments=1)

    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected = n_threads * calls_per_thread
    assert len(tracker.tts_calls) == expected


def test_thread_safe_mixed_calls():
    """Concurrent LLM + TTS calls do not corrupt each other."""
    tracker = CostTracker()
    n_threads = 10
    calls_per_thread = 100

    def llm_worker():
        for _ in range(calls_per_thread):
            tracker.record_llm_call("script", "m", {
                "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2,
            })

    def tts_worker():
        for _ in range(calls_per_thread):
            tracker.record_tts_call("edge", characters=5, segments=1)

    threads = []
    for _ in range(n_threads):
        threads.append(threading.Thread(target=llm_worker))
        threads.append(threading.Thread(target=tts_worker))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(tracker.llm_calls) == n_threads * calls_per_thread
    assert len(tracker.tts_calls) == n_threads * calls_per_thread

    # Summary must be consistent
    s = tracker.summary()
    assert s["llm"]["total_calls"] == n_threads * calls_per_thread
    assert s["tts"]["total_calls"] == n_threads * calls_per_thread


def test_thread_safe_summary_during_writes():
    """summary() called while writes are in progress must not crash."""
    tracker = CostTracker()
    n_threads = 10
    errors = []

    def writer():
        try:
            for i in range(100):
                tracker.record_llm_call("script", "m", {
                    "prompt_tokens": i, "completion_tokens": i, "total_tokens": 2 * i,
                })
                tracker.record_tts_call("edge", characters=i, segments=1)
        except Exception as e:
            errors.append(e)

    def reader():
        try:
            for _ in range(50):
                tracker.summary()
        except Exception as e:
            errors.append(e)

    threads = []
    for _ in range(n_threads):
        threads.append(threading.Thread(target=writer))
        threads.append(threading.Thread(target=reader))
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == [], f"Concurrent errors: {errors}"


# ── 7. Empty tracker summary ───────────────────────────────


def test_summary_empty_tracker_llm():
    """An empty tracker's LLM summary is all zeros."""
    tracker = CostTracker()
    llm = tracker.summary()["llm"]
    assert llm["total_calls"] == 0
    assert llm["total_prompt_tokens"] == 0
    assert llm["total_completion_tokens"] == 0
    assert llm["total_tokens"] == 0
    assert llm["by_step"] == {}
    assert llm["estimated_cost_usd"] == 0.0


def test_summary_empty_tracker_tts():
    """An empty tracker's TTS summary is all zeros."""
    tracker = CostTracker()
    tts = tracker.summary()["tts"]
    assert tts["total_calls"] == 0
    assert tts["total_segments"] == 0
    assert tts["total_characters"] == 0
    assert tts["cached_segments"] == 0
    assert tts["by_provider"] == {}
    assert tts["estimated_cost_usd"] == 0.0


def test_summary_empty_tracker_both():
    """An empty tracker returns a valid summary with zero values."""
    tracker = CostTracker()
    s = tracker.summary()
    assert s["llm"]["total_calls"] == 0
    assert s["tts"]["total_calls"] == 0
    assert s["llm"]["estimated_cost_usd"] == 0.0
    assert s["tts"]["estimated_cost_usd"] == 0.0


# ── 8. Dataclass shape ─────────────────────────────────────


def test_llm_call_record_defaults():
    """LLMCallRecord has correct default values for token fields."""
    rec = LLMCallRecord(step="script", model="m")
    assert rec.prompt_tokens == 0
    assert rec.completion_tokens == 0
    assert rec.total_tokens == 0


def test_tts_call_record_defaults():
    """TTSCallRecord has correct default values."""
    rec = TTSCallRecord(provider="edge")
    assert rec.model == ""
    assert rec.characters == 0
    assert rec.segments == 1
    assert rec.cached is False
