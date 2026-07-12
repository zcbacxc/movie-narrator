import json
from pathlib import Path

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
    clips_dir = Path(ctx.output_dir) / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)
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
                    subclip.write_videofile(
                        str(clip_path),
                        codec="libx264",
                        audio_codec="aac",
                        verbose=False,
                        logger=None,
                    )
                    scene.clip_path = str(clip_path)
                    subclip.close()
                except Exception as e:
                    print(f" skip scene {scene.index}: {e}")
            source.close()
        ctx.clips_dir = str(clips_dir)
        ctx.status.export = "success"
        print(f"✓ export_clips: {len(ctx.scenes)} scenes exported")
        return ctx
    except Exception as e:
        print(f"✗ export_clips: {e}")
        ctx.status.export = "failed"
        return ctx
