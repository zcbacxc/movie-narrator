from pathlib import Path

from pydub import AudioSegment

from movie_narrator.models import Assets, Context
from movie_narrator.pipeline.bgm import mix_bgm


def _write_silent_wav(path: Path, ms: int = 500):
    AudioSegment.silent(duration=ms).export(path, format="wav")


def _write_tone_wav(path: Path, ms: int = 500, freq: int = 440):
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
    seg.export(path, format="wav")


def test_mix_bgm_skipped_none(tmp_path):
    narr = tmp_path / "narration.wav"
    _write_silent_wav(narr)
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    ctx.metadata["bgm_request"] = "none"
    mix_bgm(ctx)
    assert ctx.status.bgm == "skipped"
    assert ctx.final_audio_path == str(narr)


def test_mix_bgm_explicit_missing_failed(tmp_path):
    narr = tmp_path / "narration.wav"
    _write_silent_wav(narr)
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    ctx.metadata["bgm_request"] = "explicit"
    mix_bgm(ctx)
    assert ctx.status.bgm == "failed"
    assert ctx.final_audio_path == str(narr)


def test_mix_bgm_success(tmp_path):
    narr = tmp_path / "narration.wav"
    bgm = tmp_path / "bgm.wav"
    _write_tone_wav(narr, 800, freq=440)
    _write_tone_wav(bgm, 400, freq=880)
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        audio_path=str(narr),
        assets=Assets(bgm=str(bgm)),
    )
    ctx.metadata["bgm_request"] = "explicit"
    mix_bgm(ctx)
    assert ctx.status.bgm == "success"
    assert Path(ctx.final_audio_path).name == "mixed.wav"
    assert Path(ctx.final_audio_path).exists()
