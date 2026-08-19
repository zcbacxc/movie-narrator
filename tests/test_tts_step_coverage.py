# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""High-coverage unit tests for ``pipeline/tts.py``.

Covers the branch matrix of ``generate_voice`` / ``_build_audio`` that was
previously untested:

- CI mode (synthesize to temp file, probe, delete, never writes cache)
- non-CI cache hit / miss / corrupt-cache rebuild
- per-segment retry (transient failure then success, and retry exhaustion)
- cost tracking (``record_tts_call`` with cached vs. missed segments)
- emotion prosody (``EmotionTrack`` + ``apply_speed``)
- v1 pause reduction (ratio > 1.15) and no-duration-feedback path
- v2 speedup ratio computation
- audio-QA warnings and LRU cache eviction

All external I/O (TTS provider, mp3 decode, audio export, QA analysis) is
mocked so the tests run without ffmpeg or a TTS endpoint.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from movie_narrator.config import Settings
from movie_narrator.models import Context, ScriptSegment
from movie_narrator.pipeline import tts as tts_module
from movie_narrator.utils.cost_tracker import CostTracker
from movie_narrator.workflow.errors import ProviderError


class FakeAudio:
    """Stand-in for pydub.AudioSegment supporting the ops tts.py uses.

    Supports ``__add__`` (concatenation), ``len()`` (milliseconds),
    ``export``, and the frame-rate trick used by ``apply_speed``
    (``_spawn`` + ``set_frame_rate``).
    """

    def __init__(self, duration_ms: int = 1000, frame_rate: int = 44100):
        self._dur = duration_ms
        self.frame_rate = frame_rate
        self.raw_data = b""
        self.max_dBFS = -6.0
        self.dBFS = -20.0

    def __add__(self, other: "FakeAudio") -> "FakeAudio":
        return FakeAudio(self._dur + getattr(other, "_dur", 0), self.frame_rate)

    def __len__(self) -> int:
        return self._dur

    def export(self, path, format=None, bitrate=None):  # noqa: A002
        return None

    def _spawn(self, raw_data, overrides=None) -> "FakeAudio":
        overrides = overrides or {}
        return FakeAudio(self._dur, overrides.get("frame_rate", self.frame_rate))

    def set_frame_rate(self, fr: int) -> "FakeAudio":
        return FakeAudio(self._dur, fr)


class _Metric:
    """Minimal stand-in for ``SegmentAudioMetrics``."""

    def __init__(self, index: int):
        self.index = index
        self.issues: list[str] = []

    def to_dict(self) -> dict:
        return {"index": self.index}


def _make_ctx(tmp_path, *, duration=60, metadata=None):
    ctx = Context(movie_name="T", output_dir=str(tmp_path), duration=duration)
    ctx.segments = [ScriptSegment(text=f"Segment #{i}", index=i) for i in range(2)]
    if metadata:
        ctx.metadata.update(metadata)
    return ctx


def _write_audio(text, voice, path) -> None:
    """AsyncMock side-effect: write a fake mp3 file at ``path``."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(b"fake-mp3")
    return None


def _install_patches(
    monkeypatch,
    *,
    from_mp3_return=None,
):
    """Patch the external deps of ``tts.py`` and return handles.

    Returns ``(provider, settings, audio_mock)`` so tests can reconfigure
    them per-scenario.
    """
    audio = MagicMock()
    audio.empty.return_value = FakeAudio(0)
    audio.silent.side_effect = lambda duration, *a, **k: FakeAudio(duration)
    audio.from_mp3.return_value = from_mp3_return if from_mp3_return is not None else FakeAudio(1000)
    monkeypatch.setattr(tts_module, "AudioSegment", audio)

    settings = Settings(_env_file=None)
    monkeypatch.setattr(tts_module, "get_settings", lambda: settings)

    monkeypatch.setattr(tts_module, "is_ci", lambda: False)

    provider = MagicMock()
    provider.synthesize = AsyncMock(side_effect=_write_audio)
    monkeypatch.setattr(tts_module, "get_tts_provider", lambda s: provider)

    monkeypatch.setattr(tts_module, "analyze_segment", lambda a, i: _Metric(i))
    monkeypatch.setattr(tts_module, "aggregate_metrics", lambda ms: {"segment_count": len(ms)})

    return provider, settings, audio


# ── CI mode ──────────────────────────────────────────────────────────


def test_ci_synthesizes_temp_and_cleans_up(monkeypatch, tmp_path):
    provider, _, _ = _install_patches(monkeypatch)
    monkeypatch.setattr(tts_module, "is_ci", lambda: True)

    ctx = _make_ctx(tmp_path)
    tts_module.generate_voice(ctx)

    # two segments synthesized to temp paths
    assert provider.synthesize.call_count == 2
    # audio path + timed segments produced
    assert ctx.audio_path == str(Path(tmp_path) / "narration.mp3")
    assert len(ctx.timed_segments) == 2
    assert ctx.timed_segments[1].start > ctx.timed_segments[0].end  # pause gap
    # temp files cleaned up, no cache written in CI
    assert list(Path(tmp_path).glob(".ci_*.mp3")) == []
    assert list((Path(tmp_path) / "cache" / "tts").rglob("*.mp3")) == []
    assert ctx.metadata["tts_provider"] == "edge"
    assert "voice_used" in ctx.metadata


# ── non-CI: cache miss / hit / corrupt ───────────────────────────────


def test_cache_miss_synthesizes_to_partial_then_rename(monkeypatch, tmp_path):
    provider, _, _ = _install_patches(monkeypatch)
    ctx = _make_ctx(tmp_path)
    tts_module.generate_voice(ctx)

    assert provider.synthesize.call_count == 2
    # cache populated (os.replace moved .partial -> final)
    cache_files = list((Path(tmp_path) / "cache" / "tts").rglob("*.mp3"))
    assert len(cache_files) == 2
    # no leftover .partial files
    assert list((Path(tmp_path) / "cache" / "tts").rglob("*.partial")) == []
    assert ctx.metadata["audio_quality"]["summary"]["segment_count"] == 2


def test_cache_hit_skips_synthesize_and_records_cached_cost(monkeypatch, tmp_path):
    provider, _, _ = _install_patches(monkeypatch)

    # First run populates the cache (miss) and records uncached cost.
    ctx1 = _make_ctx(tmp_path)
    ctx1.cost_tracker = CostTracker()
    tts_module.generate_voice(ctx1)
    assert provider.synthesize.call_count == 2
    assert [r.cached for r in ctx1.cost_tracker.tts_calls] == [False, False]

    # Second run hits the cache: no synthesize, records cached cost.
    provider.synthesize = AsyncMock()  # reset; must NOT be called
    ctx2 = _make_ctx(tmp_path)
    ctx2.cost_tracker = CostTracker()
    tts_module.generate_voice(ctx2)
    assert provider.synthesize.call_count == 0
    assert [r.cached for r in ctx2.cost_tracker.tts_calls] == [True, True]
    assert ctx2.metadata["audio_quality"]["summary"]["segment_count"] == 2


def test_corrupt_cache_rebuilds(monkeypatch, tmp_path):
    provider, _, audio = _install_patches(monkeypatch)

    # Populate cache with a valid first run.
    tts_module.generate_voice(_make_ctx(tmp_path))
    assert provider.synthesize.call_count == 2

    # Second run: from_mp3 raises once per cache file (corrupt), then
    # succeeds after the file is re-synthesized.
    seen = set()

    def _mp3(path):
        p = str(path)
        if p not in seen:
            seen.add(p)
            raise Exception("corrupt")
        return FakeAudio(1000)

    audio.from_mp3.side_effect = _mp3
    provider.synthesize = AsyncMock(side_effect=_write_audio)
    ctx = _make_ctx(tmp_path)
    tts_module.generate_voice(ctx)
    # rebuild re-synthesized every segment once
    assert provider.synthesize.call_count == 2
    assert ctx.metadata["audio_quality"]["summary"]["segment_count"] == 2


# ── retry logic ─────────────────────────────────────────────────────


def test_retry_succeeds_after_transient_failure(monkeypatch, tmp_path):
    provider, _, _ = _install_patches(monkeypatch)
    monkeypatch.setattr(tts_module, "_TTS_RETRY_DELAY", 0)

    # First synthesize call (whichever segment) fails once, then succeeds;
    # the other segment succeeds immediately.
    state = {"calls": 0}

    def _synth(text, voice, path):
        state["calls"] += 1
        if state["calls"] == 1:
            raise ProviderError("network", retryable=True)
        return _write_audio(text, voice, path)

    provider.synthesize = AsyncMock(side_effect=_synth)
    ctx = _make_ctx(tmp_path)
    tts_module.generate_voice(ctx)
    # 3 synthesize calls total (2 successes + 1 initial failure)
    assert provider.synthesize.call_count == 3
    assert len(ctx.timed_segments) == 2


def test_retry_exhausted_raises(monkeypatch, tmp_path):
    provider, _, _ = _install_patches(monkeypatch)
    monkeypatch.setattr(tts_module, "_TTS_RETRY_DELAY", 0)

    provider.synthesize = AsyncMock(side_effect=ProviderError("always fails", retryable=True))
    ctx = _make_ctx(tmp_path)
    with pytest.raises(ProviderError, match="always fails"):
        tts_module.generate_voice(ctx)
    # both segments retried _TTS_SEGMENT_RETRIES times each
    assert provider.synthesize.call_count == tts_module._TTS_SEGMENT_RETRIES * len(ctx.segments)


# ── emotion prosody ─────────────────────────────────────────────────


def test_emotion_speed_applied(monkeypatch, tmp_path):
    _install_patches(monkeypatch)
    track = MagicMock()
    track.from_metadata.return_value.segment_emotions.return_value = ["intense", "suspense"]
    monkeypatch.setattr(tts_module, "EmotionTrack", track)

    ctx = _make_ctx(tmp_path)
    tts_module.generate_voice(ctx)

    prosody = ctx.metadata["audio_quality"]["prosody"]
    assert prosody[0]["emotion"] == "intense"
    assert prosody[0]["speed"] == pytest.approx(1.12)
    assert prosody[1]["emotion"] == "suspense"
    assert prosody[1]["speed"] == pytest.approx(0.88)


def test_emotion_neutral_applies_no_speed(monkeypatch, tmp_path):
    _install_patches(monkeypatch)
    track = MagicMock()
    track.from_metadata.return_value.segment_emotions.return_value = [None, None]
    monkeypatch.setattr(tts_module, "EmotionTrack", track)

    ctx = _make_ctx(tmp_path)
    tts_module.generate_voice(ctx)

    prosody = ctx.metadata["audio_quality"]["prosody"]
    assert prosody[0]["speed"] == 1.0
    assert prosody[1]["speed"] == 1.0


# ── duration feedback (v1 pause / v2 speed) ─────────────────────────


def test_v1_pause_reduction_applied(monkeypatch, tmp_path):
    _install_patches(monkeypatch, from_mp3_return=FakeAudio(2000))
    ctx = _make_ctx(tmp_path, duration=2, metadata={"duration": 2})
    tts_module.generate_voice(ctx)

    dm = ctx.metadata["duration_metrics"]
    assert dm["adjusted"] is True
    assert dm["pause_ms_original"] == 300
    assert dm["pause_ms_applied"] == 50
    assert dm["ratio_vs_target"] > 1.0


def test_no_duration_feedback_when_target_zero(monkeypatch, tmp_path):
    _install_patches(monkeypatch)
    ctx = _make_ctx(tmp_path, duration=0, metadata={"duration": 0})
    tts_module.generate_voice(ctx)
    # target_duration is falsy -> neither v1 nor v2 runs
    assert "duration_metrics" not in ctx.metadata


def test_v2_overflow_ratio_computed(monkeypatch, tmp_path):
    _install_patches(monkeypatch, from_mp3_return=FakeAudio(2000))
    ctx = _make_ctx(tmp_path, duration=2, metadata={"duration": 2})
    tts_module.generate_voice(ctx)

    aq = ctx.metadata["audio_quality"]
    assert aq is not None
    # v1 reduced pause, but audio still over target (2x) -> v2 speedup kicks in.
    # actual/target = 2.0 snapped to _MAX_SPEEDUP (1.15), recorded as the
    # applied speedup.
    assert aq["duration_v2_speed"] == 1.15
    dm = ctx.metadata["duration_metrics"]
    assert dm.get("v2_speed_applied") == 1.15
    assert dm.get("ratio_v2") is not None


# ── audio QA warnings ───────────────────────────────────────────────


def test_audio_qa_issues_are_warned(monkeypatch, tmp_path):
    _install_patches(monkeypatch)
    issue_metric = MagicMock()
    issue_metric.issues = ["high silence ratio"]
    issue_metric.to_dict.return_value = {"index": 0, "issues": ["high silence ratio"]}
    monkeypatch.setattr(tts_module, "analyze_segment", lambda a, i: issue_metric)

    ctx = _make_ctx(tmp_path)
    tts_module.generate_voice(ctx)
    assert ctx.metadata["audio_quality"]["segments"] == [
        {"index": 0, "issues": ["high silence ratio"]}
    ] * 2


# ── LRU cache eviction ──────────────────────────────────────────────


def test_lru_cache_eviction(monkeypatch, tmp_path):
    _, settings, _ = _install_patches(monkeypatch)
    settings.tts_cache_max_mb = 0  # any cache file exceeds the budget

    ctx = _make_ctx(tmp_path)
    tts_module.generate_voice(ctx)

    # after eviction the cache is drained back to (or below) the budget
    remaining = list((Path(tmp_path) / "cache" / "tts").rglob("*.mp3"))
    assert remaining == []
    assert ctx.metadata["audio_quality"]["summary"]["segment_count"] == 2


def test_lru_eviction_breaks_once_budget_met(monkeypatch, tmp_path):
    provider, settings, _ = _install_patches(monkeypatch)
    # 1 MiB budget; two ~600 KB cache files exceed it, but removing one
    # brings the total back under budget -> the eviction loop early-exits.
    settings.tts_cache_max_mb = 1
    provider.synthesize = AsyncMock(
        side_effect=lambda text, voice, path: (
            Path(path).parent.mkdir(parents=True, exist_ok=True)
            or Path(path).write_bytes(b"\x00" * 600_000) or None
        )
    )

    ctx = _make_ctx(tmp_path)
    tts_module.generate_voice(ctx)

    remaining = list((Path(tmp_path) / "cache" / "tts").rglob("*.mp3"))
    assert len(remaining) == 1


# ── _build_audio unit ───────────────────────────────────────────────


def test_build_audio_inserts_pause_and_timestamps(monkeypatch):
    _install_patches(monkeypatch)
    segs = [ScriptSegment(text="a", index=0), ScriptSegment(text="b", index=1)]
    results = [(FakeAudio(1000), 1.0), (FakeAudio(500), 0.5)]
    combined, timed = tts_module._build_audio(results, segs, pause_ms=300)

    assert len(timed) == 2
    assert timed[0].start == 0.0
    assert timed[0].end == 1.0
    assert timed[1].start == pytest.approx(1.3)  # 1.0 + 0.3s pause
    assert timed[1].end == pytest.approx(1.8)
    assert len(combined) == 1000 + 300 + 500  # audio + pause + audio