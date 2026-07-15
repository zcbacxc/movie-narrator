import json
import shutil
import subprocess
from pathlib import Path

from tqdm import tqdm

from ..models import Context, StepResult
from ..utils.optional_deps import probe
from ..utils.warnings import append_warning


def export_clips(ctx: Context) -> Context:
    if ctx.metadata.get("workflow_steps", {}).get("export_clips") is False:
        ctx.status.export = "disabled"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "disabled by workflow config"
        return ctx
    if not ctx.metadata.get("export_clips", True):
        ctx.status.export = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "disabled by flag"
        return ctx
    ok, hint = probe("scenedetect")
    if not ok:
        ctx.status.export = "disabled"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = hint
        return ctx
    if not ctx.scenes and not ctx.matched_clips:
        ctx.status.export = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "nothing to export"
        return ctx
    if not ctx.source_video_path:
        ctx.status.export = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no source video"
        return ctx

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        ctx.status.export = "disabled"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "ffmpeg not found on PATH"
        return ctx

    output_dir = Path(ctx.output_dir)
    clips_dir = output_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    failed = 0
    for scene in tqdm(ctx.scenes, desc="Exporting clips", unit="clip"):
        try:
            clip_path = clips_dir / f"scene_{scene.index:04d}.mp4"
            # Direct ffmpeg invocation — export_clips only does seek+cut+encode,
            # so MoviePy adds unnecessary overhead.  Direct subprocess gives
            # precise control over codec params, timeout, and error handling.
            cmd = [
                ffmpeg, "-y",
                "-ss", str(scene.start),
                "-to", str(scene.end),
                "-i", ctx.source_video_path,
                "-c:v", "libx264",
                "-c:a", "aac",
                "-movflags", "+faststart",
                str(clip_path),
            ]
            result = subprocess.run(
                cmd,
                capture_output=True,
                timeout=300,
            )
            if result.returncode != 0:
                stderr_tail = result.stderr.decode(errors="replace")[-300:]
                raise RuntimeError(f"ffmpeg exited {result.returncode}: {stderr_tail}")
            scene.clip_path = str(clip_path)
        except Exception as e:
            failed += 1
            tqdm.write(f"  ⚠ skip scene {scene.index}: {e}")

    if failed:
        append_warning(ctx, f"{failed} clip(s) failed to export", prefix="export_clips")
    ctx.clips_dir = str(clips_dir)
    ctx.status.export = "success" if not failed else "partial"
    return ctx
