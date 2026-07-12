import json
from pathlib import Path
from typing import Optional

from ..config import get_settings
from ..models import Context, Scene
from ..utils.optional_deps import probe


def detect_scenes(ctx: Context) -> Context:
    ok, hint = probe("scenedetect")
    if not ok:
        ctx.status.scene = "disabled"
        print(f"⏭ detect_scenes: {hint}")
        return ctx
    if not ctx.source_video_path:
        ctx.status.scene = "skipped"
        print("⏭ detect_scenes: no source video")
        return ctx
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector

        # Spec §3/§4: use Settings.scene_threshold; allow metadata override (e.g. mn scenes --threshold).
        threshold = ctx.metadata.get("scene_threshold", get_settings().scene_threshold)

        video = open_video(ctx.source_video_path)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=threshold))
        scene_manager.detect_scenes(video, show_progress=False)
        scene_list = scene_manager.get_scene_list()
        scenes = []
        for i, (start, end) in enumerate(scene_list):
            scenes.append(
                Scene(
                    index=i,
                    start=start.get_seconds(),
                    end=end.get_seconds(),
                )
            )
        ctx.scenes = scenes
        ctx.status.scene = "success"
        print(f"✓ detect_scenes: {len(scenes)} scenes")
        return ctx
    except Exception as e:
        print(f"✗ detect_scenes: {e}")
        ctx.status.scene = "failed"
        return ctx
