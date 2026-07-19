import json
from pathlib import Path
from typing import Optional

from ..models import Context, Scene, StepResult
from ..utils.optional_deps import probe


def detect_scenes(ctx: Context) -> Context:
    ok, hint = probe("scenedetect")
    if not ok:
        ctx.status.scene = "disabled"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = hint
        return ctx
    if not ctx.source_video_path:
        ctx.status.scene = "skipped"
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no source video"
        return ctx
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector

        # Spec §3/§4: read scene_threshold / scene_frame_skip from job params
        # (ctx.metadata) with defaults fallback (e.g. mn scenes --threshold).
        threshold = ctx.metadata.get("scene_threshold", 27.0)
        frame_skip = ctx.metadata.get("scene_frame_skip", 10)

        video = open_video(ctx.source_video_path)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=threshold))
        scene_manager.detect_scenes(video, show_progress=True, frame_skip=frame_skip)

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

        # ── WP1: Persist scenes.json for debugging ──────
        # Lets you verify scene detection quality without re-running the
        # pipeline.  File is small (just timestamps), so always write it.
        try:
            output_dir = Path(ctx.output_dir)
            output_dir.mkdir(parents=True, exist_ok=True)
            scenes_data = [
                {"index": s.index, "start": s.start, "end": s.end}
                for s in scenes
            ]
            with open(output_dir / "scenes.json", "w", encoding="utf-8") as f:
                json.dump(
                    {"scene_count": len(scenes_data), "scenes": scenes_data},
                    f, ensure_ascii=False, indent=2,
                )
        except Exception:
            # Best-effort: scenes.json is diagnostic, not critical.
            pass

        return ctx
    except Exception as e:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        ctx.status.scene = "failed"
        return ctx
