from pathlib import Path

from pydub import AudioSegment

from movie_narrator.models import Assets, Context
from movie_narrator.pipeline.bgm import ensure_final_audio, mix_bgm


def _export_wav_fallback(seg: AudioSegment, path: Path):
    """Export audio, falling back to WAV when MP3 encoding is unavailable.

    Mirrors the production ``_export_robust`` helper so tests pass on
    minimal ffmpeg builds that lack libmp3lame.
    """
    try:
        seg.export(path, format="mp3")
    except Exception:
        path = path.with_suffix(".wav")
        seg.export(path, format="wav")
    return path


def _write_silent_mp3(path: Path, ms: int = 500):
    return _export_wav_fallback(AudioSegment.silent(duration=ms), path)


def _write_tone_mp3(path: Path, ms: int = 500, freq: int = 440):
    """Write a sine-wave tone so gain is actually applied (non-silent)."""
    import math
    import struct
    sample_rate = 44100
    n_samples = int(sample_rate * ms / 1000.0)
    amplitude = 2 ** 15 - 1
    sine_wave = bytearray()
    for i in range(n_samples):
        val = int(amplitude * math.sin(2.0 * math.pi * freq * i / sample_rate))
        sine_wave.extend(struct.pack("<h", val))
    seg = AudioSegment(
        data=bytes(sine_wave),
        sample_width=2,
        frame_rate=sample_rate,
        channels=1,
    )
    return _export_wav_fallback(seg, path)


def test_mix_bgm_skipped_none(tmp_path):
    narr = _write_silent_mp3(tmp_path / "narration.mp3")
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    ctx.metadata["bgm_request"] = "none"
    ctx.metadata["bgm_normalize"] = False  # isolate skip behaviour
    mix_bgm(ctx)
    assert ctx.status.bgm == "skipped"
    assert ctx.final_audio_path == str(narr)


def test_mix_bgm_skipped_none_normalizes(tmp_path):
    """With bgm_normalize=True (default), the skip path still normalizes
    narration to a side file for production loudness consistency."""
    narr = _write_tone_mp3(tmp_path / "narration.mp3", 500, freq=440)
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    ctx.metadata["bgm_request"] = "none"
    # bgm_normalize defaults to True
    mix_bgm(ctx)
    assert ctx.status.bgm == "skipped"
    name = Path(ctx.final_audio_path).name
    assert name in ("narration_normalized.mp3", "narration_normalized.wav")
    assert Path(ctx.final_audio_path).exists()


# ── AQ-04: ensure_final_audio tests ──


def test_ensure_final_audio_normalizes_raw_narration(tmp_path):
    """ensure_final_audio normalizes when final_audio_path is still raw narration."""
    narr = _write_tone_mp3(tmp_path / "narration.mp3", 800, freq=440)
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    # Simulate: BGM failed, final_audio_path not set (defaults to audio_path)
    ctx.final_audio_path = str(narr)
    ctx.metadata["bgm_normalize"] = True

    ensure_final_audio(ctx)
    assert ctx.final_audio_path != str(narr)  # normalized
    assert Path(ctx.final_audio_path).exists()


def test_ensure_final_audio_skips_already_mixed(tmp_path):
    """ensure_final_audio does nothing when final_audio_path is already mixed."""
    narr = _write_tone_mp3(tmp_path / "narration.mp3", 800, freq=440)
    mixed = _write_tone_mp3(tmp_path / "mixed.mp3", 800, freq=880)
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    ctx.final_audio_path = str(mixed)  # already mixed

    ensure_final_audio(ctx)
    assert ctx.final_audio_path == str(mixed)  # unchanged


def test_ensure_final_audio_skips_when_normalize_disabled(tmp_path):
    """ensure_final_audio uses raw audio when bgm_normalize=False."""
    narr = _write_tone_mp3(tmp_path / "narration.mp3", 800, freq=440)
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    ctx.final_audio_path = str(narr)
    ctx.metadata["bgm_normalize"] = False

    ensure_final_audio(ctx)
    assert ctx.final_audio_path == str(narr)  # raw, not normalized


def test_ensure_final_audio_handles_no_audio_path(tmp_path):
    """ensure_final_audio is a no-op when audio_path is None."""
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=None)
    ensure_final_audio(ctx)
    assert ctx.final_audio_path is None or ctx.final_audio_path == ctx.audio_path


def test_mix_bgm_explicit_missing_failed(tmp_path):
    narr = _write_silent_mp3(tmp_path / "narration.mp3")
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    ctx.metadata["bgm_request"] = "explicit"
    mix_bgm(ctx)
    assert ctx.status.bgm == "failed"
    # AQ-04: even failed path gets normalized (ensure_final_audio)
    assert ctx.final_audio_path != str(narr)  # normalized, not raw


def test_mix_bgm_success(tmp_path):
    narr = _write_tone_mp3(tmp_path / "narration.mp3", 800, freq=440)
    bgm = _write_tone_mp3(tmp_path / "bgm.mp3", 400, freq=880)
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        audio_path=str(narr),
        assets=Assets(bgm=str(bgm)),
    )
    ctx.metadata["bgm_request"] = "explicit"
    mix_bgm(ctx)
    assert ctx.status.bgm == "success"
    name = Path(ctx.final_audio_path).name
    assert name in ("mixed.mp3", "mixed.wav")
    assert Path(ctx.final_audio_path).exists()


def test_mix_bgm_ambient_success(tmp_path):
    """v0.7.1 ambient: a valid ambient track is mixed in and recorded."""
    narr = _write_tone_mp3(tmp_path / "narration.mp3", 800, freq=440)
    bgm = _write_tone_mp3(tmp_path / "bgm.mp3", 400, freq=880)
    ambient = _write_tone_mp3(tmp_path / "ambient.mp3", 600, freq=220)
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        audio_path=str(narr),
        assets=Assets(bgm=str(bgm)),
    )
    ctx.metadata["bgm_request"] = "explicit"
    ctx.metadata["bgm_ambient_path"] = str(ambient)
    mix_bgm(ctx)
    assert ctx.status.bgm == "success"
    assert ctx.metadata.get("ambient_track", {}).get("path") == str(ambient)
    assert ctx.metadata["ambient_track"]["gain_db"] == -12.0
    assert ctx.metadata["ambient_track"]["duck_db"] == -10.0
    assert Path(ctx.final_audio_path).exists()


def test_mix_bgm_ambient_missing_soft_fail(tmp_path):
    """v0.7.1 ambient: a missing ambient file warns and keeps the mix going."""
    narr = _write_tone_mp3(tmp_path / "narration.mp3", 800, freq=440)
    bgm = _write_tone_mp3(tmp_path / "bgm.mp3", 400, freq=880)
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        audio_path=str(narr),
        assets=Assets(bgm=str(bgm)),
    )
    ctx.metadata["bgm_request"] = "explicit"
    ctx.metadata["bgm_ambient_path"] = str(tmp_path / "missing.mp3")
    mix_bgm(ctx)
    # Soft failure: BGM mix still succeeds, ambient skipped with an error.
    assert ctx.status.bgm == "success"
    info = ctx.metadata.get("ambient_track")
    assert info is None or "error" in info
    assert Path(ctx.final_audio_path).exists()


def test_mix_bgm_ambient_ducking_applied(tmp_path):
    """v0.7.1 ambient: ducking must reduce ambient loudness under narration."""
    from movie_narrator.pipeline.bgm import _mix_ambient_track

    narr = _write_tone_mp3(tmp_path / "narration.mp3", 800, freq=440)
    ambient = _write_tone_mp3(tmp_path / "ambient.mp3", 400, freq=220)

    base_mixed = AudioSegment.from_file(narr).overlay(
        AudioSegment.from_file(ambient).apply_gain(0)
    )
    ducked_mixed, info = _mix_ambient_track(
        AudioSegment.from_file(narr),
        str(ambient),
        ambient_gain_db=0.0,
        duck_db=-20.0,
    )
    assert "error" not in info
    # Ducking must attenuate the ambient, so the ducked mix is quieter
    # than an equal-gain overlay (narration energy dominates both).
    assert ducked_mixed.dBFS < base_mixed.dBFS
