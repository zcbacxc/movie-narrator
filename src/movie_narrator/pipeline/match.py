from ..models import Context


def match_clips(ctx: Context) -> Context:
    if not ctx.source_video_path:
        ctx.status.match = "skipped"
        print("⏭ match_clips: no source video")
        return ctx
    if ctx.status.scene == "disabled":
        ctx.status.match = "disabled"
        print("⏭ match_clips: scene disabled")
        return ctx
    if not ctx.scenes:
        ctx.status.match = "skipped"
        print("⏭ match_clips: no scenes")
        return ctx
    ctx.status.match = "skipped"
    print("⏭ match_clips: implementation pending M3")
    return ctx
