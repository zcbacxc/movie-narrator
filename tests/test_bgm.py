from pathlib import Path

from pydub import AudioSegment

from movie_narrator.models import Assets, Context
from movie_narrator.pipeline.bgm import mix_bgm


def _write_silent_mp3(path: Path, ms: int = 500):
    AudioSegment.silent(duration=ms).export(path, format="mp3")


def test_mix_bgm_skipped_none(tmp_path):
    narr = tmp_path / "narration.mp3"
    _write_silent_mp3(narr)
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    ctx.metadata["bgm_request"] = "none"
    mix_bgm(ctx)
    assert ctx.status.bgm == "skipped"
    assert ctx.final_audio_path == str(narr)


def test_mix_bgm_explicit_missing_failed(tmp_path):
    narr = tmp_path / "narration.mp3"
    _write_silent_mp3(narr)
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(narr))
    ctx.metadata["bgm_request"] = "explicit"
    mix_bgm(ctx)
    assert ctx.status.bgm == "failed"
    assert ctx.final_audio_path == str(narr)


def test_mix_bgm_success(tmp_path):
    narr = tmp_path / "narration.mp3"
    bgm = tmp_path / "bgm.mp3"
    _write_silent_mp3(narr, 800)
    _write_silent_mp3(bgm, 400)
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        audio_path=str(narr),
        assets=Assets(bgm=str(bgm)),
    )
    ctx.metadata["bgm_request"] = "explicit"
    mix_bgm(ctx)
    assert ctx.status.bgm == "success"
    assert Path(ctx.final_audio_path).name == "mixed.mp3"
    assert Path(ctx.final_audio_path).exists()
