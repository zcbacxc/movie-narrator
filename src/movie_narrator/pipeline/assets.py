# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Asset preparation step — download and organize required assets."""

from pathlib import Path
from ..models import Context


def prepare_assets(ctx: Context) -> Context:
    """Prepare and download required assets.

    Args:
        ctx: Pipeline execution context.

    Returns:
        Updated pipeline context with asset information.
    """
    if ctx.assets.bgm:
        p = Path(ctx.assets.bgm)
        if not p.is_file():
            ctx.metadata["bgm_error"] = f"bgm not found: {ctx.assets.bgm}"
            ctx.assets.bgm = None
        else:
            ctx.assets.bgm = str(p.resolve())
    return ctx
