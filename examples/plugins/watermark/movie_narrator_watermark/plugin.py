"""WatermarkPlugin — reference implementation of the Plugin protocol.

Registers a soft pipeline step ``add_watermark`` that runs immediately
after ``render_video``. The step overlays a watermark image (PNG with
alpha) onto the final video using ffmpeg.

This is a teaching example, not a production-ready watermark solution.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

from movie_narrator import Context, PluginContext, register_step


class WatermarkPlugin:
    """Plugin that adds a watermark overlay step to the pipeline."""

    name = "watermark"

    def register(self, ctx: PluginContext) -> None:
        """Register the watermark step with the step registry."""
        ctx.steps.register(
            "add_watermark",
            _add_watermark_step,
            soft=True,
            status_field="watermark",
            consequence="watermark overlay skipped — final video will have no watermark",
            after="render_video",
        )


def _add_watermark_step(ctx: Context) -> Context:
    """Burn a watermark image into the final video.

    Reads the watermark path from ``ctx.assets.watermark``. If no
    watermark is configured, the step is a no-op (status=skipped).

    Uses ffmpeg's ``overlay`` filter to composite the watermark at
    the bottom-right corner with 50% opacity.
    """
    watermark_path = ctx.assets.watermark
    if not watermark_path:
        ctx.step_state.result = ctx.step_state.result.__class__("skipped")
        ctx.step_state.message = "no watermark asset configured"
        return ctx

    if not ctx.video_path:
        ctx.step_state.result = ctx.step_state.result.__class__("skipped")
        ctx.step_state.message = "no video to watermark"
        return ctx

    video_path = Path(ctx.video_path)
    if not video_path.exists():
        ctx.step_state.result = ctx.step_state.result.__class__("skipped")
        ctx.step_state.message = f"video not found: {video_path}"
        return ctx

    wm_path = Path(watermark_path)
    if not wm_path.exists():
        ctx.step_state.result = ctx.step_state.result.__class__("skipped")
        ctx.step_state.message = f"watermark not found: {wm_path}"
        return ctx

    output_path = video_path.with_suffix(".watermarked.mp4")

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-i", str(wm_path),
        "-filter_complex",
        "[1:v]format=rgba,colorchannelmixer=aa=0.5[wm];"
        "[0:v][wm]overlay=W-w-20:H-h-20",
        "-c:a", "copy",
        str(output_path),
    ]

    result = subprocess.run(
        ffmpeg_cmd,
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"ffmpeg watermark overlay failed (exit {result.returncode}): "
            f"{result.stderr[:500]}"
        )

    # Replace the original video with the watermarked version
    backup = video_path.with_suffix(".orig.mp4")
    shutil.move(str(video_path), str(backup))
    shutil.move(str(output_path), str(video_path))

    ctx.step_state.result = ctx.step_state.result.__class__("success")
    ctx.step_state.message = f"watermark applied from {wm_path.name}"

    # Log via services.logger if available
    logger: Any = ctx.services.logger if ctx.services else None
    if logger and hasattr(logger, "info"):
        logger.info("WatermarkPlugin: applied %s to %s", wm_path.name, video_path.name)

    return ctx
