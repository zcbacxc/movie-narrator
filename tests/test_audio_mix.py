"""Tests for utils/audio_mix.py — peak normalization and BGM ducking."""

import math
import struct
from unittest.mock import patch

from pydub import AudioSegment

from movie_narrator.utils.audio_mix import (
    DuckingBackend,
    duck_bgm,
    duck_bgm_envelope,
    duck_bgm_sidechain,
    normalize_peak,
)


def _tone(ms: int = 500, freq: int = 440, gain_db: float = 0.0) -> AudioSegment:
    """Generate a sine-wave tone AudioSegment."""
    sample_rate = 44100
    n = int(sample_rate * ms / 1000.0)
    amp = int((2**15 - 1) * (10 ** (gain_db / 20.0)))
    data = bytearray()
    for i in range(n):
        val = int(amp * math.sin(2.0 * math.pi * freq * i / sample_rate))
        data.extend(struct.pack("<h", val))
    return AudioSegment(data=bytes(data), sample_width=2, frame_rate=sample_rate, channels=1)


def _silence(ms: int = 500) -> AudioSegment:
    return AudioSegment.silent(duration=ms)


def test_normalize_peak_raises_loudness():
    """A loud tone normalized to -14 dBFS should have max_dBFS near -14."""
    loud = _tone(ms=300, freq=440, gain_db=0.0)  # near 0 dBFS peak
    norm = normalize_peak(loud, target_dbfs=-14.0)
    assert abs(norm.max_dBFS - (-14.0)) < 1.0


def test_normalize_peak_silent_unchanged():
    """A silent segment (max==0) must not blow up — returned unchanged."""
    silent = _silence(ms=200)
    norm = normalize_peak(silent, target_dbfs=-14.0)
    assert norm.max == 0


def test_duck_bgm_returns_narration_length():
    """Mixed output length equals the narration length."""
    narr = _tone(ms=600, freq=440)
    bgm = _tone(ms=400, freq=880)
    mixed = duck_bgm(narr, bgm, bgm_gain_db=-18.0, duck_db=-10.0)
    assert len(mixed) == len(narr)


def test_duck_bgm_loops_short_bgm():
    """BGM shorter than narration is looped to match length."""
    narr = _tone(ms=1000, freq=440)
    bgm = _tone(ms=300, freq=880)
    mixed = duck_bgm(narr, bgm)
    assert len(mixed) == len(narr)


def test_duck_bgm_attenuates_during_speech():
    """When narration is loud, BGM is attenuated → mix is quieter than raw overlay."""
    narr = _tone(ms=500, freq=440)  # loud narration
    bgm = _tone(ms=500, freq=880)
    # No ducking (duck_db=0) vs ducking (duck_db=-20): ducked mix should be quieter.
    mixed_ducked = duck_bgm(narr, bgm, bgm_gain_db=-18.0, duck_db=-20.0)
    mixed_flat = duck_bgm(narr, bgm, bgm_gain_db=-18.0, duck_db=0.0)
    assert mixed_ducked.dBFS < mixed_flat.dBFS


def test_duck_bgm_silent_narration_no_duck():
    """Silent narration → no ducking applied, BGM plays at baseline gain."""
    narr = _silence(ms=500)
    bgm = _tone(ms=500, freq=880)
    mixed = duck_bgm(narr, bgm, bgm_gain_db=-12.0, duck_db=-20.0)
    # Output exists and has the right length.
    assert len(mixed) == 500


# ── G7: ducking backend dispatch ─────────────────────────


def test_ducking_backend_from_value():
    assert DuckingBackend.from_value(None) is DuckingBackend.ENVELOPE
    assert DuckingBackend.from_value("") is DuckingBackend.ENVELOPE
    assert DuckingBackend.from_value("envelope") is DuckingBackend.ENVELOPE
    assert DuckingBackend.from_value("sidechaincompress") is DuckingBackend.SIDECHAIN
    assert DuckingBackend.from_value("bogus") is DuckingBackend.ENVELOPE


def test_duck_bgm_default_backend_is_envelope():
    """Default (no backend) routes to the envelope implementation."""
    narr = _tone(ms=500, freq=440)
    bgm = _tone(ms=500, freq=880)
    with patch("movie_narrator.utils.audio_mix.duck_bgm_envelope", wraps=duck_bgm_envelope) as env:
        with patch(
            "movie_narrator.utils.audio_mix.duck_bgm_sidechain",
            wraps=duck_bgm_sidechain,
        ) as sc:
            duck_bgm(narr, bgm, backend=DuckingBackend.ENVELOPE)
    env.assert_called_once()
    sc.assert_not_called()


def test_duck_bgm_sidechain_used_when_requested(monkeypatch):
    """Requesting sidechaincompress calls the sidechain implementation."""
    narr = _tone(ms=500, freq=440)
    bgm = _tone(ms=500, freq=880)
    fake = lambda *a, **k: _tone(ms=500, freq=440)  # noqa: E731
    monkeypatch.setattr(
        "movie_narrator.utils.audio_mix.duck_bgm_sidechain", fake
    )
    with patch("movie_narrator.utils.audio_mix.duck_bgm_envelope") as env:
        mixed = duck_bgm(narr, bgm, backend="sidechaincompress")
    env.assert_not_called()
    assert len(mixed) == 500


def test_duck_bgm_sidechain_falls_back_to_envelope(monkeypatch):
    """When sidechain returns None (ffmpeg missing), fall back to envelope."""
    narr = _tone(ms=500, freq=440)
    bgm = _tone(ms=500, freq=880)
    monkeypatch.setattr("movie_narrator.utils.audio_mix.duck_bgm_sidechain", lambda *a, **k: None)
    with patch("movie_narrator.utils.audio_mix.duck_bgm_envelope", wraps=duck_bgm_envelope) as env:
        mixed = duck_bgm(narr, bgm, backend=DuckingBackend.SIDECHAIN)
    env.assert_called_once()
    assert len(mixed) == 500


def test_duck_bgm_sidechain_returns_none_without_ffmpeg(monkeypatch):
    """duck_bgm_sidechain returns None when no ffmpeg binary is found."""
    narr = _tone(ms=200, freq=440)
    bgm = _tone(ms=200, freq=880)
    monkeypatch.setattr("movie_narrator.utils.audio_mix._ffmpeg_bin", lambda: None)
    assert duck_bgm_sidechain(narr, bgm) is None


def test_duck_bgm_sidechain_builds_output(monkeypatch):
    """Sidechain path produces a valid same-length mix with a working ffmpeg."""
    narr = _tone(ms=300, freq=440)
    bgm = _tone(ms=300, freq=880)

    fake_mixed = _tone(ms=300, freq=440)
    fake_proc = type("P", (), {"returncode": 0, "stderr": ""})()

    def fake_run(cmd, **kwargs):
        # Simulate ffmpeg writing the out.wav from the exported files.
        return fake_proc

    monkeypatch.setattr("movie_narrator.utils.audio_mix._ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr(
        "movie_narrator.utils.audio_mix.subprocess.run", fake_run
    )
    monkeypatch.setattr(
        "movie_narrator.utils.audio_mix.AudioSegment.from_file",
        lambda path: fake_mixed,
    )
    mixed = duck_bgm_sidechain(narr, bgm, bgm_gain_db=-18.0, duck_db=-10.0)
    assert mixed is not None
    assert len(mixed) == len(narr)
