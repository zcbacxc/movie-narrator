from typing import Any, Dict

from .. import __version__
from ..models import Context


def build_metadata_json(ctx: Context) -> Dict[str, Any]:
    subtitle_lang = ctx.metadata.get("subtitle_lang")
    # Default source/script/voice to zh-CN per spec §5.4; reserved for
    # future full-pipeline multi-language support.
    content_language = {
        "source_lang": ctx.metadata.get("source_lang", "zh-CN"),
        "script_lang": "zh-CN",
        "voice_lang": "zh-CN",
        "subtitle_lang": subtitle_lang,
    }
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
            "tts_provider": ctx.metadata.get("tts_provider"),
        },
        "segments_count": len(ctx.timed_segments),
        "segments": [
            {"text": s.text, "start": s.start, "end": s.end}
            for s in ctx.timed_segments
        ],
        # Multi-language subtitle (v0.3).
        "content_language": content_language,
        "subtitle_mode": ctx.metadata.get("subtitle_mode", "original"),
        "translate_provider": ctx.metadata.get("translate_provider"),
        "subtitle_paths": (
            ctx.subtitle_paths.model_dump()
            if ctx.subtitle_paths is not None
            else None
        ),
        "render_subtitle_path": ctx.render_subtitle_path,
        "warnings": ctx.metadata.get("warnings", []),
        # ── WP1: diagnostic fields (for L2 hand-test & debugging) ──
        # These let you verify pipeline health from metadata.json alone,
        # without inspecting logs.
        "script_target_count": ctx.metadata.get("script_target_count"),
        "script_beat_count": ctx.metadata.get("script_beat_count"),
        "script_segment_count": ctx.metadata.get("script_segment_count"),
        "script_phase": ctx.metadata.get("script_phase"),
        "narration_preset": ctx.metadata.get("narration_preset"),
        "prompt_target_sentences": ctx.metadata.get("prompt_target_sentences"),
        "prompt_target_segment_duration": ctx.metadata.get("prompt_target_segment_duration"),
        "degraded_steps": ctx.metadata.get("_degraded_steps", []),
        "match_summary": ctx.metadata.get("match_summary"),
        "qa_report": ctx.metadata.get("qa_report"),
        # ── WP4: footage coverage (how many segments have real footage) ──
        "footage_coverage": ctx.metadata.get("footage_coverage"),
        # ── WP5: duration metrics (target vs actual) ──
        "duration_metrics": ctx.metadata.get("duration_metrics"),
        # ── WP5: script truncation audit ──
        "script_truncated": ctx.metadata.get("script_truncated"),
    }
    return meta
