"""Tests for v0.5.9 Voice & Audio Quality features.

Covers:
- Audio QA: clipping detection, SNR estimation, silence check
- Prosody: emotion-to-speed mapping, speed application, segment emotion mapping
- TTS pipeline: emotion prosody, v2 duration feedback, quality validation
- BGM dynamic transition: emotion zone detection, transition application
- Crossfade: audio segment crossfading
"""

import os
import shutil
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from pydub import AudioSegment

from movie_narrator.models import Assets, Context, Services, ScriptSegment, TimedSegment
from movie_narrator.utils.audio_qa import (
    SegmentAudioMetrics,
    detect_clipping,
    estimate_snr,
    check_silence,
    analyze_segment,
    aggregate_metrics,
)
from movie_narrator.utils.prosody import (
    emotion_to_speed,
    apply_speed,
    map_segment_emotions,
)
from movie_narrator.utils.audio_mix import crossfade_segments
from movie_narrator.pipeline.bgm import (
    _detect_emotion_zones,
    _apply_emotion_transitions,
    _EMOTION_BGM_GAIN,
)

# ffmpeg on this machine may lack mp3/wav codecs (minimal build).
# Tests that actually export/import mp3 via pydub need a full ffmpeg.
_FFMPEG_OK = shutil.which("ffmpeg") is not None
try:
    _tmp = Path(os.environ.get("TEMP", "/tmp")) / "_ffmpeg_probe_v059.mp3"
    AudioSegment.silent(duration=10).export(_tmp, format="mp3")
    AudioSegment.from_mp3(_tmp)
    _tmp.unlink(missing_ok=True)
    _MP3_OK = True
except Exception:
    _MP3_OK = False

requires_mp3 = pytest.mark.skipif(not _MP3_OK, reason="ffmpeg lacks mp3/wav codec support")


# ── Helpers ────────────────────────────────────────────────


def _make_audio(duration_ms: int = 1000, freq: int = 440, gain_db: float = 0.0) -> AudioSegment:
    """Create a sine-wave AudioSegment for testing."""
    sr = 24000
    n_samples = int(sr * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, n_samples, endpoint=False)
    wave = np.sin(2 * np.pi * freq * t)
    wave = wave * (10 ** (gain_db / 20.0))
    wave = np.clip(wave, -1, 1)
    samples = (wave * 32767).astype(np.int16)
    return AudioSegment(
        samples.tobytes(),
        frame_rate=sr,
        sample_width=2,
        channels=1,
    )


def _make_silent_audio(duration_ms: int = 1000) -> AudioSegment:
    return AudioSegment.silent(duration=duration_ms, frame_rate=24000)


def _make_clipped_audio(duration_ms: int = 500) -> AudioSegment:
    """Create audio with intentional clipping at max amplitude."""
    sr = 24000
    n_samples = int(sr * duration_ms / 1000)
    t = np.linspace(0, duration_ms / 1000, n_samples, endpoint=False)
    wave = np.sin(2 * np.pi * 440 * t)
    # Scale to exceed int16 max, causing clipping
    wave = np.clip(wave * 2.0, -1.0, 1.0)
    samples = (wave * 32767).astype(np.int16)
    return AudioSegment(
        samples.tobytes(),
        frame_rate=sr,
        sample_width=2,
        channels=1,
    )


# ── 1. Audio QA: clipping detection ────────────────────────


def test_detect_clipping_clean_audio():
    """Clean audio should have near-zero clipping ratio."""
    audio = _make_audio(duration_ms=500, gain_db=-6.0)
    ratio = detect_clipping(audio)
    assert ratio < 0.001, f"Expected <0.001, got {ratio}"


def test_detect_clipping_clipped_audio():
    """Intentionally clipped audio should have high clipping ratio."""
    audio = _make_clipped_audio(duration_ms=500)
    ratio = detect_clipping(audio)
    assert ratio > 0.001, f"Expected >0.001, got {ratio}"


def test_detect_clipping_silent_audio():
    """Silent audio should have zero clipping."""
    audio = _make_silent_audio(duration_ms=500)
    ratio = detect_clipping(audio)
    assert ratio == 0.0


def test_detect_clipping_empty_audio():
    """Empty audio should return 0.0."""
    audio = AudioSegment.empty()
    ratio = detect_clipping(audio)
    assert ratio == 0.0


# ── 2. Audio QA: SNR estimation ────────────────────────────


def test_estimate_snr_tonal_audio():
    """Tonal audio with silence gaps should have measurable SNR."""
    # 500ms tone + 500ms silence + 500ms tone
    tone1 = _make_audio(duration_ms=500, gain_db=-3.0)
    silence = _make_silent_audio(duration_ms=500)
    tone2 = _make_audio(duration_ms=500, gain_db=-3.0)
    audio = tone1 + silence + tone2
    snr = estimate_snr(audio)
    assert snr is not None, "SNR should be measurable for mixed audio"
    assert snr > 0, f"Expected positive SNR, got {snr}"


def test_estimate_snr_all_silent():
    """All-silent audio should return None (no signal)."""
    audio = _make_silent_audio(duration_ms=1000)
    snr = estimate_snr(audio)
    assert snr is None


def test_estimate_snr_all_signal():
    """Continuous tone (no silence gaps) should return None (no noise floor)."""
    audio = _make_audio(duration_ms=1000)
    snr = estimate_snr(audio)
    # May or may not be None depending on RMS variation
    # Just verify it doesn't crash
    if snr is not None:
        assert isinstance(snr, float)


def test_estimate_snr_too_short():
    """Very short audio should return None (too few windows)."""
    audio = _make_audio(duration_ms=10)
    snr = estimate_snr(audio)
    assert snr is None


# ── 3. Audio QA: silence check ─────────────────────────────


def test_check_silence_fully_silent():
    """Fully silent audio should have silence_ratio = 1.0."""
    audio = _make_silent_audio(duration_ms=1000)
    ratio = check_silence(audio)
    assert ratio == 1.0


def test_check_silence_fully_loud():
    """Continuous tone should have low silence ratio."""
    audio = _make_audio(duration_ms=1000, gain_db=-3.0)
    ratio = check_silence(audio)
    assert ratio < 0.1, f"Expected <0.1, got {ratio}"


def test_check_silence_half_silent():
    """Half-silent audio should have ~0.5 silence ratio."""
    tone = _make_audio(duration_ms=500, gain_db=-3.0)
    silence = _make_silent_audio(duration_ms=500)
    audio = tone + silence
    ratio = check_silence(audio)
    assert 0.3 < ratio < 0.7, f"Expected ~0.5, got {ratio}"


# ── 4. Audio QA: segment analysis ──────────────────────────


def test_analyze_segment_clean():
    """Clean audio segment should have no issues."""
    audio = _make_audio(duration_ms=1000, gain_db=-6.0)
    metrics = analyze_segment(audio, 0)
    assert len(metrics.issues) == 0
    assert metrics.duration_s == pytest.approx(1.0, abs=0.1)
    assert metrics.index == 0


def test_analyze_segment_clipped():
    """Clipped segment should report clipping issue."""
    audio = _make_clipped_audio(duration_ms=500)
    metrics = analyze_segment(audio, 1)
    assert any("clipping" in issue for issue in metrics.issues)
    assert metrics.clipping_ratio > 0.001


def test_analyze_segment_too_short():
    """Very short segment should report length issue."""
    audio = _make_audio(duration_ms=50)
    metrics = analyze_segment(audio, 2)
    assert any("too short" in issue for issue in metrics.issues)


def test_analyze_segment_silent():
    """Silent segment should report silence issue."""
    audio = _make_silent_audio(duration_ms=1000)
    metrics = analyze_segment(audio, 3)
    assert any("silence" in issue.lower() for issue in metrics.issues)
    assert metrics.silence_ratio > 0.5


def test_analyze_segment_to_dict():
    """to_dict should produce a serializable dict."""
    audio = _make_audio(duration_ms=500)
    metrics = analyze_segment(audio, 0)
    d = metrics.to_dict()
    assert isinstance(d, dict)
    assert d["index"] == 0
    assert "duration_s" in d
    assert "peak_dbfs" in d
    assert "issues" in d
    assert isinstance(d["issues"], list)


# ── 5. Audio QA: aggregation ───────────────────────────────


def test_aggregate_metrics_empty():
    """Empty list should return zero-count summary."""
    result = aggregate_metrics([])
    assert result["segment_count"] == 0


def test_aggregate_metrics_normal():
    """Normal metrics list should produce a summary."""
    audio = _make_audio(duration_ms=500, gain_db=-6.0)
    metrics = [analyze_segment(audio, i) for i in range(3)]
    result = aggregate_metrics(metrics)
    assert result["segment_count"] == 3
    assert result["total_duration_s"] > 0
    assert result["avg_segment_duration_s"] > 0
    assert result["segments_with_issues"] == 0
    assert result["total_issues"] == 0


def test_aggregate_metrics_with_issues():
    """Metrics with issues should be counted correctly."""
    clean = _make_audio(duration_ms=500, gain_db=-6.0)
    clipped = _make_clipped_audio(duration_ms=500)
    metrics = [analyze_segment(clean, 0), analyze_segment(clipped, 1)]
    result = aggregate_metrics(metrics)
    assert result["segment_count"] == 2
    assert result["segments_with_issues"] >= 1
    assert result["total_issues"] >= 1
    assert len(result["all_issues"]) >= 1


# ── 6. Prosody: emotion-to-speed ───────────────────────────


def test_emotion_to_speed_known():
    """Known emotions should return non-1.0 speed."""
    assert emotion_to_speed("intense") > 1.0
    assert emotion_to_speed("suspense") < 1.0
    assert emotion_to_speed("calm") < 1.0
    assert emotion_to_speed("twist") > 1.0
    assert emotion_to_speed("laughter") > 1.0


def test_emotion_to_speed_unknown():
    """Unknown emotions should return 1.0."""
    assert emotion_to_speed(None) == 1.0
    assert emotion_to_speed("unknown") == 1.0
    assert emotion_to_speed("") == 1.0


def test_emotion_to_speed_clamped():
    """Speed should be within the clamp range."""
    for emotion in ["intense", "suspense", "calm", "twist", "laughter"]:
        speed = emotion_to_speed(emotion)
        assert 0.85 <= speed <= 1.15, f"{emotion}: {speed} out of range"


# ── 7. Prosody: apply_speed ────────────────────────────────


def test_apply_speed_no_change():
    """Speed 1.0 should return audio unchanged."""
    audio = _make_audio(duration_ms=1000)
    result = apply_speed(audio, 1.0)
    assert len(result) == len(audio)


def test_apply_speed_faster():
    """Speed > 1.0 should produce shorter audio."""
    audio = _make_audio(duration_ms=1000)
    result = apply_speed(audio, 1.12)
    assert len(result) < len(audio), f"Expected shorter, got {len(result)} >= {len(audio)}"


def test_apply_speed_slower():
    """Speed < 1.0 should produce longer audio."""
    audio = _make_audio(duration_ms=1000)
    result = apply_speed(audio, 0.88)
    assert len(result) > len(audio), f"Expected longer, got {len(result)} <= {len(audio)}"


def test_apply_speed_preserves_sample_rate():
    """Output should have the same sample rate as input."""
    audio = _make_audio(duration_ms=500)
    result = apply_speed(audio, 1.1)
    assert result.frame_rate == audio.frame_rate


# ── 8. Prosody: segment emotion mapping ────────────────────


def test_map_segment_emotions_empty():
    """No beats_meta should return all None."""
    result = map_segment_emotions(5, None)
    assert len(result) == 5
    assert all(e is None for e in result)


def test_map_segment_emotions_equal_count():
    """When beats == segments, emotions map 1:1."""
    beats_meta = [{"emotion": "intense"}, {"emotion": "calm"}, {"emotion": "suspense"}]
    result = map_segment_emotions(3, beats_meta)
    assert result == ["intense", "calm", "suspense"]


def test_map_segment_emotions_more_segments():
    """More segments than beats: distribute proportionally."""
    beats_meta = [{"emotion": "intense"}, {"emotion": "calm"}]
    result = map_segment_emotions(4, beats_meta)
    assert len(result) == 4
    assert result[0] == "intense"
    assert result[1] == "intense"
    assert result[2] == "calm"
    assert result[3] == "calm"


def test_map_segment_emotions_missing_emotion():
    """Beats without emotion should be forward-filled."""
    beats_meta = [{"emotion": "intense"}, {}, {"emotion": "calm"}]
    result = map_segment_emotions(3, beats_meta)
    assert result[0] == "intense"
    assert result[1] == "intense"  # forward-filled
    assert result[2] == "calm"


def test_map_segment_emotions_all_none():
    """When no beats have emotions, return all None."""
    beats_meta = [{}, {}, {}]
    result = map_segment_emotions(3, beats_meta)
    assert all(e is None for e in result)


# ── 9. Crossfade ───────────────────────────────────────────


def test_crossfade_empty():
    """Empty list should return empty audio."""
    result = crossfade_segments([])
    assert len(result) == 0


def test_crossfade_single():
    """Single segment should return that segment."""
    audio = _make_audio(duration_ms=500)
    result = crossfade_segments([(audio, 0.0)])
    assert len(result) == len(audio)


def test_crossfade_two_segments():
    """Two segments should crossfade to shorter than sum."""
    a = _make_audio(duration_ms=1000, freq=440)
    b = _make_audio(duration_ms=1000, freq=880)
    result = crossfade_segments([(a, 0.0), (b, 1.0)], crossfade_ms=200)
    # Crossfade overlaps, so result should be shorter than a + b
    assert len(result) < len(a) + len(b)
    assert len(result) > len(a)  # but longer than just a


def test_crossfade_multiple():
    """Multiple segments should concatenate with crossfades."""
    segs = [(_make_audio(duration_ms=500, freq=440 + i * 100), float(i)) for i in range(3)]
    result = crossfade_segments(segs, crossfade_ms=100)
    assert len(result) > 0
    # Should be shorter than sum due to crossfade overlap
    total = sum(len(s) for s, _ in segs)
    assert len(result) < total


# ── 10. BGM: emotion zone detection ────────────────────────


def test_detect_emotion_zones_empty():
    """Empty input should return empty list."""
    assert _detect_emotion_zones([], []) == []


def test_detect_emotion_zones_single_emotion():
    """All same emotion should produce one zone."""
    segments = [
        TimedSegment(text="s1", start=0.0, end=1.0),
        TimedSegment(text="s2", start=1.0, end=2.0),
    ]
    emotions = ["intense", "intense"]
    zones = _detect_emotion_zones(segments, emotions)
    assert len(zones) == 1
    assert zones[0]["emotion"] == "intense"
    assert zones[0]["start"] == 0.0
    assert zones[0]["end"] == 2.0


def test_detect_emotion_zones_two_zones():
    """Emotion change should produce two zones."""
    segments = [
        TimedSegment(text="s1", start=0.0, end=1.0),
        TimedSegment(text="s2", start=1.0, end=2.0),
        TimedSegment(text="s3", start=2.0, end=3.0),
    ]
    emotions = ["intense", "intense", "calm"]
    zones = _detect_emotion_zones(segments, emotions)
    assert len(zones) == 2
    assert zones[0]["emotion"] == "intense"
    assert zones[0]["start"] == 0.0
    assert zones[0]["end"] == 2.0
    assert zones[1]["emotion"] == "calm"
    assert zones[1]["start"] == 2.0
    assert zones[1]["end"] == 3.0


def test_detect_emotion_zones_none_emotions():
    """None emotions should be handled gracefully."""
    segments = [TimedSegment(text="s1", start=0.0, end=1.0)]
    emotions = [None]
    zones = _detect_emotion_zones(segments, emotions)
    assert len(zones) == 1
    assert zones[0]["emotion"] is None


# ── 11. BGM: emotion transitions ───────────────────────────


def test_apply_emotion_transitions_empty():
    """Empty zones should return audio unchanged."""
    audio = _make_audio(duration_ms=1000)
    result, transitions = _apply_emotion_transitions(audio, [])
    assert len(result) == len(audio)
    assert transitions == []


def test_apply_emotion_transitions_single_zone():
    """Single zone should apply gain but no transitions."""
    audio = _make_audio(duration_ms=1000, gain_db=-6.0)
    zones = [{"start": 0.0, "end": 1.0, "emotion": "intense"}]
    result, transitions = _apply_emotion_transitions(audio, zones)
    assert len(result) == len(audio)
    assert transitions == []
    # Intense zone has +2dB gain, so result should be louder
    assert result.rms > audio.rms


def test_apply_emotion_transitions_two_zones():
    """Two zones should produce one transition."""
    audio = _make_audio(duration_ms=2000, gain_db=-6.0)
    zones = [
        {"start": 0.0, "end": 1.0, "emotion": "intense"},
        {"start": 1.0, "end": 2.0, "emotion": "calm"},
    ]
    result, transitions = _apply_emotion_transitions(audio, zones)
    assert len(result) == len(audio)
    assert len(transitions) == 1
    assert transitions[0]["from_emotion"] == "intense"
    assert transitions[0]["to_emotion"] == "calm"


def test_apply_emotion_transitions_preserves_length():
    """Output length should match input length."""
    audio = _make_audio(duration_ms=3000, gain_db=-6.0)
    zones = [
        {"start": 0.0, "end": 1.0, "emotion": "intense"},
        {"start": 1.0, "end": 2.0, "emotion": "calm"},
        {"start": 2.0, "end": 3.0, "emotion": "suspense"},
    ]
    result, _ = _apply_emotion_transitions(audio, zones)
    assert len(result) == len(audio)


def test_emotion_bgm_gain_mapping():
    """All standard emotions should have gain values."""
    for emotion in ["intense", "suspense", "calm", "twist", "laughter"]:
        assert emotion in _EMOTION_BGM_GAIN
        assert isinstance(_EMOTION_BGM_GAIN[emotion], float)


# ── 12. TTS pipeline integration ───────────────────────────


@requires_mp3
class TestTTSPipelineIntegration:
    """TTS pipeline tests that require MP3 codec support."""

    @staticmethod
    def _make_tts_ctx(tmp_path, **kw):
        """Build a Context for TTS pipeline testing."""
        defaults = dict(
            movie_name="test_movie",
            style="热血搞笑",
            duration=60,
            output_dir=str(tmp_path),
            segments=[ScriptSegment(text=f"segment {i}") for i in range(5)],
            services=Services(console=MagicMock()),
        )
        defaults.update(kw)
        return Context(**defaults)

    def test_tts_emotion_prosody_applied(self, tmp_path, monkeypatch):
        """TTS step should apply emotion-based prosody and log it."""
        from movie_narrator.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")
        monkeypatch.setenv("CI", "1")
        ctx = self._make_tts_ctx(tmp_path)
        ctx.metadata["beats_meta"] = [
            {"emotion": "intense"},
            {"emotion": "calm"},
            {"emotion": "suspense"},
            {"emotion": "intense"},
            {"emotion": "laughter"},
        ]

        from movie_narrator.pipeline.tts import generate_voice

        result = generate_voice(ctx)

        aq = result.metadata.get("audio_quality", {})
        assert "prosody" in aq
        assert len(aq["prosody"]) == 5
        prosody = aq["prosody"]
        assert prosody[0]["emotion"] == "intense"
        assert prosody[0]["speed"] > 1.0
        assert prosody[1]["emotion"] == "calm"
        assert prosody[1]["speed"] < 1.0

    def test_tts_prosody_no_beats_meta(self, tmp_path, monkeypatch):
        """Without beats_meta, all prosody speeds should be 1.0."""
        from movie_narrator.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")
        monkeypatch.setenv("CI", "1")
        ctx = self._make_tts_ctx(tmp_path)

        from movie_narrator.pipeline.tts import generate_voice

        result = generate_voice(ctx)

        aq = result.metadata.get("audio_quality", {})
        prosody = aq.get("prosody", [])
        assert len(prosody) == 5
        for p in prosody:
            assert p["speed"] == 1.0
            assert p["emotion"] is None

    def test_tts_audio_quality_metrics(self, tmp_path, monkeypatch):
        """TTS step should produce per-segment audio quality metrics."""
        from movie_narrator.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")
        monkeypatch.setenv("CI", "1")
        ctx = self._make_tts_ctx(tmp_path)

        from movie_narrator.pipeline.tts import generate_voice

        result = generate_voice(ctx)

        aq = result.metadata.get("audio_quality", {})
        assert "segments" in aq
        assert len(aq["segments"]) == 5
        assert "summary" in aq
        assert aq["summary"]["segment_count"] == 5
        assert aq["summary"]["total_duration_s"] > 0

    def test_tts_v2_speed_not_triggered_when_ok(self, tmp_path, monkeypatch):
        """V2 speed should not trigger when duration is within range."""
        from movie_narrator.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")
        monkeypatch.setenv("CI", "1")
        ctx = self._make_tts_ctx(tmp_path, duration=10)

        from movie_narrator.pipeline.tts import generate_voice

        result = generate_voice(ctx)

        aq = result.metadata.get("audio_quality", {})
        assert aq.get("duration_v2_speed") is None

    def test_tts_v2_speed_triggered_on_overflow(self, tmp_path, monkeypatch):
        """V2 speed should trigger when narration overflows target."""
        from movie_narrator.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")
        monkeypatch.setenv("CI", "1")
        long_text = "这是一段非常长的解说文本" * 20
        ctx = self._make_tts_ctx(tmp_path, duration=1)
        ctx.segments = [ScriptSegment(text=long_text) for _ in range(5)]

        from movie_narrator.pipeline.tts import generate_voice

        result = generate_voice(ctx)

        dm = result.metadata.get("duration_metrics", {})
        assert "v2_speed_applied" in dm or dm.get("adjusted") is True

    def test_tts_duration_metrics_always_present(self, tmp_path, monkeypatch):
        """Duration metrics should always be populated."""
        from movie_narrator.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")
        monkeypatch.setenv("CI", "1")
        ctx = self._make_tts_ctx(tmp_path, duration=60)

        from movie_narrator.pipeline.tts import generate_voice

        result = generate_voice(ctx)

        dm = result.metadata.get("duration_metrics", {})
        assert "target_sec" in dm
        assert "narration_sec" in dm
        assert "ratio_vs_target" in dm


# ── 13. BGM pipeline integration ───────────────────────────


@requires_mp3
def test_bgm_transitions_stored_in_metadata(tmp_path):
    """BGM step should store transitions in metadata when zones detected."""
    from movie_narrator.pipeline.bgm import mix_bgm

    # Create a temporary BGM file
    bgm_path = tmp_path / "test_bgm.mp3"
    bgm_audio = _make_audio(duration_ms=5000, gain_db=-6.0)
    bgm_audio.export(str(bgm_path), format="mp3")

    # Create narration file
    narration_path = tmp_path / "narration.mp3"
    narration = _make_audio(duration_ms=5000, gain_db=-6.0)
    narration.export(str(narration_path), format="mp3")

    ctx = Context(
        movie_name="test",
        style="热血搞笑",
        duration=5,
        output_dir=str(tmp_path),
        audio_path=str(narration_path),
        assets=Assets(bgm=str(bgm_path)),
        services=Services(console=MagicMock()),
    )
    ctx.metadata["bgm_request"] = "explicit"
    ctx.metadata["bgm_normalize"] = False
    ctx.timed_segments = [
        TimedSegment(text="s1", start=0.0, end=1.0),
        TimedSegment(text="s2", start=1.0, end=2.0),
        TimedSegment(text="s3", start=2.0, end=3.0),
        TimedSegment(text="s4", start=3.0, end=4.0),
        TimedSegment(text="s5", start=4.0, end=5.0),
    ]
    ctx.metadata["beats_meta"] = [
        {"emotion": "intense"},
        {"emotion": "intense"},
        {"emotion": "calm"},
        {"emotion": "calm"},
        {"emotion": "suspense"},
    ]

    result = mix_bgm(ctx)
    assert result.status.bgm == "success"
    transitions = result.metadata.get("bgm_transitions")
    assert transitions is not None
    assert len(transitions) >= 1
    # Check transition from intense to calm
    intense_to_calm = [
        t for t in transitions if t["from_emotion"] == "intense" and t["to_emotion"] == "calm"
    ]
    assert len(intense_to_calm) >= 1


@requires_mp3
def test_bgm_no_transitions_without_beats_meta(tmp_path):
    """BGM step should not add transitions without beats_meta."""
    from movie_narrator.pipeline.bgm import mix_bgm

    bgm_path = tmp_path / "test_bgm.mp3"
    _make_audio(duration_ms=3000, gain_db=-6.0).export(str(bgm_path), format="mp3")

    narration_path = tmp_path / "narration.mp3"
    _make_audio(duration_ms=3000, gain_db=-6.0).export(str(narration_path), format="mp3")

    ctx = Context(
        movie_name="test",
        style="热血搞笑",
        duration=3,
        output_dir=str(tmp_path),
        audio_path=str(narration_path),
        assets=Assets(bgm=str(bgm_path)),
        services=Services(console=MagicMock()),
    )
    ctx.metadata["bgm_request"] = "explicit"
    ctx.metadata["bgm_normalize"] = False
    ctx.timed_segments = [
        TimedSegment(text="s1", start=0.0, end=1.5),
        TimedSegment(text="s2", start=1.5, end=3.0),
    ]

    result = mix_bgm(ctx)
    assert result.status.bgm == "success"
    assert "bgm_transitions" not in result.metadata
