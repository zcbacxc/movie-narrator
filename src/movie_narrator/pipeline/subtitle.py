from pathlib import Path

from ..models import Context


def _format_time(seconds: float) -> str:
    total_ms = round(seconds * 1000)
    hrs, rem = divmod(total_ms, 3_600_000)
    mins, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hrs:02d}:{mins:02d}:{secs:02d},{millis:03d}"


def generate_subtitle(ctx: Context) -> Context:
    output_dir = Path(ctx.output_dir)
    srt_path = output_dir / "subtitle.srt"
    with open(srt_path, "w", encoding="utf-8") as f:
        for idx, seg in enumerate(ctx.timed_segments, 1):
            f.write(f"{idx}\n")
            f.write(f"{_format_time(seg.start)} --> {_format_time(seg.end)}\n")
            f.write(f"{seg.text}\n\n")
    ctx.subtitle_path = str(srt_path)
    return ctx
