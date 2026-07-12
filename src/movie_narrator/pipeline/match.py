import json
from pathlib import Path
from typing import Optional

from ..config import get_settings
from ..models import Context, MatchedClip


def match_clips(ctx: Context) -> Context:
    if not ctx.source_video_path:
        ctx.status.match = "skipped"
        print("⏭ match_clips: no source video")
        return ctx
    if ctx.status.scene == "disabled":
        ctx.status.match = "disabled"
        print("⏭ match_clips: scene disabled")
        return ctx
    if not ctx.scenes:
        ctx.status.match = "skipped"
        print("⏭ match_clips: no scenes")
        return ctx
    if not ctx.timed_segments:
        ctx.status.match = "skipped"
        print("⏭ match_clips: no timed segments")
        return ctx

    settings = get_settings()
    min_score = settings.match_min_score
    output_dir = Path(ctx.output_dir)

    # Compute total scene span
    scene_start = min(s.start for s in ctx.scenes)
    scene_end = max(s.end for s in ctx.scenes)
    scene_span = scene_end - scene_start

    first_start = ctx.timed_segments[0].start
    last_end = ctx.timed_segments[-1].end
    narr_span = last_end - first_start

    matched_clips = []
    for i, seg in enumerate(ctx.timed_segments):
        # Proportional mapping: narr midpoint -> source time
        narr_mid = (seg.start + seg.end) / 2.0
        if narr_span > 0:
            ratio = (narr_mid - first_start) / narr_span
            src_mid = scene_start + ratio * scene_span
        else:
            src_mid = scene_start

        # Find containing scene
        containing = None
        for scene in ctx.scenes:
            if scene.start <= src_mid <= scene.end:
                containing = scene
                break
        if containing is None:
            containing = ctx.scenes[0]

        src_start = containing.start
        src_end = containing.end
        source = "heuristic"
        score = 1.0

        if score >= min_score:
            matched_clips.append(
                MatchedClip(
                    segment_index=i,
                    text=seg.text,
                    narr_start=seg.start,
                    narr_end=seg.end,
                    src_start=src_start,
                    src_end=src_end,
                    score=score,
                    scene_index=containing.index,
                    source=source,
                )
            )

    ctx.matched_clips = matched_clips

    # Write matches.json
    matches_path = output_dir / "matches.json"
    matches_path.write_text(
        json.dumps(
            [m.model_dump() for m in matched_clips], ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    ctx.status.match = "success"
    print(f"✓ match_clips: {len(matched_clips)}/{len(ctx.timed_segments)} matched")
    return ctx
