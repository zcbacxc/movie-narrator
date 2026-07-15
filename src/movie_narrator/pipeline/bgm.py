from pathlib import Path

from pydub import AudioSegment

from ..config import get_settings
from ..models import Context, StepResult


def mix_bgm(ctx: Context) -> Context:
    if not ctx.audio_path:
        ctx.status.bgm = "skipped"
        ctx.final_audio_path = ctx.audio_path
        return ctx

    req = ctx.metadata.get("bgm_request", "none")
    if req == "none" or (req == "default" and not ctx.assets.bgm):
        ctx.status.bgm = "skipped"
        ctx.final_audio_path = ctx.audio_path
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
        gain_db = ctx.metadata.get("bgm_gain_db", get_settings().bgm_gain_db)
        bgm = AudioSegment.from_file(ctx.assets.bgm).apply_gain(gain_db)
        if len(bgm) < len(narration):
            times = len(narration) // max(len(bgm), 1) + 1
            bgm = bgm * times
        bgm = bgm[: len(narration)]
        mixed = narration.overlay(bgm)
        out = Path(ctx.output_dir) / "mixed.mp3"
        mixed.export(out, format="mp3")
        ctx.final_audio_path = str(out)
        ctx.status.bgm = "success"
        return ctx
    except Exception as e:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        ctx.status.bgm = "failed"
        ctx.final_audio_path = ctx.audio_path
        return ctx
