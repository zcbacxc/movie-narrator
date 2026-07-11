from pathlib import Path
from ..models import Context


def export_script_md(ctx: Context) -> Context:
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
