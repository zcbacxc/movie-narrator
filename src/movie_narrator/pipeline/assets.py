from pathlib import Path
from ..models import Context


def prepare_assets(ctx: Context) -> Context:
    if ctx.assets.bgm:
        p = Path(ctx.assets.bgm)
        if not p.is_file():
            ctx.metadata["bgm_error"] = f"bgm not found: {ctx.assets.bgm}"
            ctx.assets.bgm = None
        else:
            ctx.assets.bgm = str(p.resolve())
    return ctx
