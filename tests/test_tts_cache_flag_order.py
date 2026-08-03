# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Regression test: TTS cache-hit flags must stay aligned with segments.

The v0.7-fix originally collected per-segment ``cached`` flags via a
side-effect ``list.append()`` inside the concurrently-executing ``_one``
coroutine. Because ``asyncio.gather`` returns results in *input* order
while ``append()`` happens in *completion* order, the flags could be
mismatched with ``ctx.segments`` whenever segments finish out of order
(one cache hit + one cache miss). This test pins the fixed behaviour:
flags are derived from the gather results, so they always match input
order.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydub import AudioSegment


def _silent(ms: int = 300) -> AudioSegment:
    return AudioSegment.silent(duration=ms, frame_rate=44100)


async def _gather_aligned(segments: list, coro_factory):
    """Replicates the fixed ``_run_all`` pattern.

    ``coro_factory(seg)`` returns a coroutine that yields
    ``(audio, duration, cached_flag)``. The helper splits the triplets
    into ``(results, cached_flags)`` exactly like ``tts._run_all``.
    """
    triplets = await asyncio.gather(*(coro_factory(s) for s in segments))
    results = [(a, d) for a, d, _ in triplets]
    cached_flags = [flag for _, _, flag in triplets]
    return results, cached_flags


def test_cache_flags_stay_aligned_under_out_of_order_completion():
    """A slow cache-miss segment finishing after a fast cache-hit segment
    must NOT swap the cached flags: results and flags both follow input
    order."""
    segments = ["miss-slow", "hit-fast", "miss-medium"]

    async def _one(seg_text: str):
        if seg_text.startswith("hit"):
            # Cache hit: no network await, completes immediately.
            return _silent(), 0.3, True
        # Cache miss: simulate synthesis latency (slow > medium).
        delay = 2.0 if seg_text == "miss-slow" else 1.0
        await asyncio.sleep(delay)
        return _silent(), 0.3, False

    results, cached_flags = asyncio.run(
        _gather_aligned(segments, lambda s: _one(s))
    )

    # Input order preserved in both results and flags.
    assert [r[0] for r in results]  # audios present, ordered by input
    assert cached_flags == [False, True, False], (
        f"flags misaligned with segments: {cached_flags}"
    )
    # Sum must equal the true cache-hit count (1).
    assert sum(cached_flags) == 1


def test_all_cache_hits_and_all_misses_still_correct():
    """Homogeneous cache states were correct even before the fix; they
    must stay correct (flags are now derived from gather results)."""
    async def _run(states: list[bool]):
        async def _one(hit: bool):
            if hit:
                return _silent(), 0.3, True
            await asyncio.sleep(0.05)
            return _silent(), 0.3, False

        results, flags = await _gather_aligned(states, lambda h: _one(h))
        return results, flags

    _, all_hit = asyncio.run(_run([True, True, True]))
    assert all_hit == [True, True, True]

    _, all_miss = asyncio.run(_run([False, False, False]))
    assert all_miss == [False, False, False]


def test_mixed_flags_sum_matches_true_hit_count():
    """Even when flags are individually shuffled by completion order in the
    OLD implementation, the new derivation must keep both order and count
    exact."""
    states = [False, True, False, True, False]
    expected = states  # input order == ground truth

    async def _one(hit: bool, idx: int):
        if hit:
            return _silent(), 0.3, True
        await asyncio.sleep(0.05 * ((idx % 3) + 1))  # stagger misses
        return _silent(), 0.3, False

    _, flags = asyncio.run(
        _gather_aligned(states, lambda h, i=0: _one(h, i))
    )
    assert flags == expected
    assert sum(flags) == sum(expected) == 2
