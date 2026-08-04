# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Script export step — export script as Markdown."""

from pathlib import Path
from ..models import Context


def export_script_md(ctx: Context) -> Context:
    """Export the narration script as Markdown.

    Args:
        ctx: Pipeline execution context.

    Returns:
        Updated pipeline context with exported script.
    """
    output_dir = Path(ctx.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "script.md"
    lines = [f"# {ctx.movie_name}", ""]
    for i, seg in enumerate(ctx.segments, 1):
        lines.append(f"## {i}")
        lines.append(seg.text)
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
    ctx.script_md_path = str(path)
    return ctx
