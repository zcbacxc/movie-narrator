from pathlib import Path

from pydub import AudioSegment

from ..models import Context, StepResult
from ..utils.audio_mix import duck_bgm, normalize_loudnorm, normalize_peak


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
    # EP6: Use RMS-based loudnorm when configured, else peak normalization
    if ctx.metadata.get("bgm_loudnorm", False):
        normalized = normalize_loudnorm(narration, target_dbfs=target)
    else:
        normalized = normalize_peak(narration, target_dbfs=target)
    return _export_robust(normalized, out)


def ensure_final_audio(ctx: Context) -> Context:
    """Guarantee that ctx.final_audio_path is normalized (AQ-04 fix).

    All BGM exit paths (skip, fail, exception) must go through this
    function. If the final audio is still the raw narration (not mixed),
    normalize it so that the exception/fail path is not worse than the
    success path.

    Called by mix_bgm at every exit point and by runner.py as a safety
    net before render.
    """
    if not ctx.audio_path:
        return ctx

    # Already mixed (BGM success path) — nothing to do
    if ctx.final_audio_path and ctx.final_audio_path != ctx.audio_path:
        return ctx

    # Raw narration — normalize if configured
    do_norm = ctx.metadata.get("bgm_normalize", True)
    if not do_norm:
        ctx.final_audio_path = ctx.audio_path
        return ctx

    try:
        narration = AudioSegment.from_file(ctx.audio_path)
        ctx.final_audio_path = _normalize_narration(ctx, narration)
    except Exception:
        # Last resort: use raw audio as-is (better than nothing)
        ctx.final_audio_path = ctx.audio_path

    return ctx


def mix_bgm(ctx: Context) -> Context:
    if not ctx.audio_path:
        ctx.status.bgm = "skipped"
        ctx.final_audio_path = ctx.audio_path
        return ctx

    req = ctx.metadata.get("bgm_request", "none")

    if req == "none" or (req == "default" and not ctx.assets.bgm):
        # No BGM — normalize narration for production consistency.
        ctx.status.bgm = "skipped"
        return ensure_final_audio(ctx)

    if req == "explicit" and not ctx.assets.bgm:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = "explicit BGM missing"
        ctx.status.bgm = "failed"
        return ensure_final_audio(ctx)

    if not ctx.assets.bgm:
        ctx.status.bgm = "skipped"
        return ensure_final_audio(ctx)

    try:
        narration = AudioSegment.from_file(ctx.audio_path)
        gain_db = ctx.metadata.get("bgm_gain_db", -18.0)
        duck_db = ctx.metadata.get("bgm_duck_db", -10.0)
        bgm_raw = AudioSegment.from_file(ctx.assets.bgm)

        mixed = duck_bgm(
            narration, bgm_raw,
            bgm_gain_db=gain_db, duck_db=duck_db,
        )
        do_norm = ctx.metadata.get("bgm_normalize", True)
        if do_norm:
            target = ctx.metadata.get("audio_target_dbfs", -14.0)
            # EP6: Use RMS-based loudnorm when configured, else peak normalization
            if ctx.metadata.get("bgm_loudnorm", False):
                mixed = normalize_loudnorm(mixed, target_dbfs=target)
            else:
                mixed = normalize_peak(mixed, target_dbfs=target)
        out = Path(ctx.output_dir) / "mixed.mp3"
        ctx.final_audio_path = _export_robust(mixed, out)
        ctx.status.bgm = "success"
        return ctx
    except Exception as e:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        ctx.status.bgm = "failed"
        return ensure_final_audio(ctx)
