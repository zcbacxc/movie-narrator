from pathlib import Path

from pydub import AudioSegment

from movie_narrator.models import Assets, Context
from movie_narrator.pipeline.bgm import mix_bgm


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


def test_mix_bgm_explicit_missing_failed(tmp_path):
    narr = _write_silent_mp3(tmp_path / "narration.mp3")
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    ctx.metadata["bgm_request"] = "explicit"
    mix_bgm(ctx)
    assert ctx.status.bgm == "failed"
    assert ctx.final_audio_path == str(narr)


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
