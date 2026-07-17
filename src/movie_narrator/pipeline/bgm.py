from pathlib import Path

from pydub import AudioSegment

from ..models import Context, StepResult
from ..utils.audio_mix import duck_bgm, normalize_peak


def _export_robust(seg: AudioSegment, out: Path) -> str:
    """Export audio, falling back to WAV when MP3 encoding is unavailable.

    pydub's MP3 export requires ffmpeg's libmp3lame; some minimal ffmpeg
    builds (and the imageio-ffmpeg bundle) lack it. WAV is native PCM and
    needs no encoder, so it is a safe fallback. The render step reads the
    result back via MoviePy, which accepts both.
    """
    try:
        seg.export(out, format="mp3")
        return str(out)
    except Exception:
        wav_out = out.with_suffix(".wav")
        seg.export(wav_out, format="wav")
        return str(wav_out)


def _normalize_narration(ctx: Context, narration: AudioSegment) -> str:
    """Normalize narration and write to a side file, returning the path."""
    out = Path(ctx.output_dir) / "narration_normalized.mp3"
    target = ctx.metadata.get("audio_target_dbfs", -14.0)
    normalized = normalize_peak(narration, target_dbfs=target)
    return _export_robust(normalized, out)


def mix_bgm(ctx: Context) -> Context:
    if not ctx.audio_path:
        ctx.status.bgm = "skipped"
        ctx.final_audio_path = ctx.audio_path
        return ctx

    req = ctx.metadata.get("bgm_request", "none")
    do_norm = ctx.metadata.get("bgm_normalize", True)

    if req == "none" or (req == "default" and not ctx.assets.bgm):
        # No BGM — still normalize narration for production consistency.
        if do_norm:
            try:
                narration = AudioSegment.from_file(ctx.audio_path)
                ctx.final_audio_path = _normalize_narration(ctx, narration)
            except Exception:
                ctx.final_audio_path = ctx.audio_path
        else:
            ctx.final_audio_path = ctx.audio_path
        ctx.status.bgm = "skipped"
        return ctx

    if req == "explicit" and not ctx.assets.bgm:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = "explicit BGM missing"
        ctx.status.bgm = "failed"
        ctx.final_audio_path = ctx.audio_path
        return ctx

    if not ctx.assets.bgm:
        ctx.status.bgm = "skipped"
        ctx.final_audio_path = ctx.audio_path
        return ctx

    try:
        narration = AudioSegment.from_file(ctx.audio_path)
        gain_db = ctx.metadata.get("bgm_gain_db", -18.0)
        duck_db = ctx.metadata.get("bgm_duck_db", -10.0)
        bgm_raw = AudioSegment.from_file(ctx.assets.bgm)

        mixed = duck_bgm(
            narration, bgm_raw,
            bgm_gain_db=gain_db, duck_db=duck_db,
        )
        if do_norm:
            target = ctx.metadata.get("audio_target_dbfs", -14.0)
            mixed = normalize_peak(mixed, target_dbfs=target)
        out = Path(ctx.output_dir) / "mixed.mp3"
        ctx.final_audio_path = _export_robust(mixed, out)
        ctx.status.bgm = "success"
        return ctx
    except Exception as e:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        ctx.status.bgm = "failed"
        ctx.final_audio_path = ctx.audio_path
        return ctx
