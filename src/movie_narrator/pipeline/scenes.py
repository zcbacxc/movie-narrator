from ..models import Context
from ..utils.optional_deps import probe


def detect_scenes(ctx: Context) -> Context:
    ok, hint = probe("scenedetect")
    if not ok:
        ctx.status.scene = "disabled"
        print(f"⏭ detect_scenes: {hint}")
        return ctx
    if not ctx.source_video_path:
        ctx.status.scene = "skipped"
        print("⏭ detect_scenes: no source video")
        return ctx
    ctx.status.scene = "skipped"
    print("⏭ detect_scenes: implementation pending M3")
    return ctx
