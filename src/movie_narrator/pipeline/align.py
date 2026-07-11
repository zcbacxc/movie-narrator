from ..models import Context
from ..utils.optional_deps import probe


def align_audio(ctx: Context) -> Context:
    ok, hint = probe("whisperx")
    if not ok:
        ctx.status.align = "disabled"
        print(f"⏭ align_audio: {hint}")
        return ctx
    if not ctx.audio_path:
        ctx.status.align = "skipped"
        print("⏭ align_audio: no audio")
        return ctx
    ctx.status.align = "skipped"
    print("⏭ align_audio: implementation pending M3")
    return ctx
