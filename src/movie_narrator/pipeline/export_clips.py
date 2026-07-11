from ..models import Context
from ..utils.optional_deps import probe


def export_clips(ctx: Context) -> Context:
    if not ctx.metadata.get("export_clips", True):
        ctx.status.export = "skipped"
        print("⏭ export_clips: disabled by flag")
        return ctx
    ok, hint = probe("scenedetect")
    if not ok:
        ctx.status.export = "disabled"
        print(f"⏭ export_clips: {hint}")
        return ctx
    if not ctx.scenes and not ctx.matched_clips:
        ctx.status.export = "skipped"
        print("⏭ export_clips: nothing to export")
        return ctx
    ctx.status.export = "skipped"
    print("⏭ export_clips: implementation pending M3")
    return ctx
