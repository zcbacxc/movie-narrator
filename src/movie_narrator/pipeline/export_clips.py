import json
import shutil
from pathlib import Path

from ..models import Context, StepResult
from ..utils.optional_deps import probe


def export_clips(ctx: Context) -> Context:
    if ctx.metadata.get("workflow_steps", {}).get("export") is False:
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
    output_dir = Path(ctx.output_dir)
    clips_dir = output_dir / "clips"
    temp_dir = output_dir / ".tmp"
    clips_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector
        from moviepy.editor import VideoFileClip
        if ctx.source_video_path:
            source = VideoFileClip(ctx.source_video_path)
            for scene in ctx.scenes:
                try:
                    subclip = source.subclip(scene.start, scene.end)
                    clip_path = clips_dir / f"scene_{scene.index:04d}.mp4"
                    temp_audio = str(temp_dir / f"scene_{scene.index:04d}_TEMP_MPY_wvf_snd.mp4")
                    subclip.write_videofile(
                        str(clip_path),
                        codec="libx264",
                        audio_codec="aac",
                        verbose=False,
                        logger="bar",
                        temp_audiofile_path=temp_audio,
                    )
                    scene.clip_path = str(clip_path)
                    subclip.close()
                except Exception as e:
                    ctx.services.console.inline_warn(f"skip scene {scene.index}: {e}")
            source.close()
        # Clean up temp directory
        if temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        ctx.clips_dir = str(clips_dir)
        ctx.status.export = "success"
        return ctx
    except Exception as e:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        ctx.status.export = "failed"
        return ctx
