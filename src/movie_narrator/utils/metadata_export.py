from typing import Any, Dict

from .. import __version__
from ..models import Context


def build_metadata_json(ctx: Context) -> Dict[str, Any]:
    meta: Dict[str, Any] = {
        "version": ctx.metadata.get("version", __version__),
        "movie_name": ctx.movie_name,
        "source_video": ctx.source_video_path,
        "video_path": ctx.video_path,
        "clips_dir": ctx.clips_dir,
        "status": ctx.status.model_dump(),
        "environment": ctx.metadata.get("environment", {}),
        "script_source": ctx.metadata.get("script_source"),
        "bgm_request": ctx.metadata.get("bgm_request"),
        "created_at": ctx.metadata.get("created_at"),
        "input": {
            "movie": ctx.movie_name,
            "style": ctx.style,
            "duration": ctx.duration,
            "voice": ctx.metadata.get("voice_used"),
            "format": ctx.metadata.get("format"),
        },
        "segments_count": len(ctx.timed_segments),
        "segments": [
            {"text": s.text, "start": s.start, "end": s.end}
            for s in ctx.timed_segments
        ],
    }
    return meta
