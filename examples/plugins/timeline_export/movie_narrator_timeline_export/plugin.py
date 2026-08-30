# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""TimelineExportPlugin — reference implementation of the Plugin protocol.

Registers a soft pipeline step ``timeline_export`` that runs after
``render_video``. The step serialises the current edit into a timeline
draft for manual fine-tuning in an NLE.

Backends
--------
- ``otio`` : OpenTimelineIO (Apache-2.0). Requires the ``[otio]`` extra.
- ``jianying`` : Jianying (CapCut domestic) draft JSON, self-authored in
  this plugin. The format is an unpublished, byte-owned schema and may
  drift; treat it as best-effort and always prefer OTIO for interchange.
- ``premiere`` : Final Cut Pro 7 XML (``xmeml``) — the interchange
  format Adobe Premiere Pro imports natively (``File > Import``).
  Stdlib-only writer (``premiere.py``), no optional dependency.

All timeline values are expressed in seconds. The unified intermediate
representation ``Timeline`` is deliberately backend-agnostic so new
backends (e.g. EDL, AAF) can be added without touching the step logic.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from movie_narrator import Context, PluginContext
from movie_narrator.models import StepResult

from .premiere import _write_premiere

logger = logging.getLogger(__name__)


# ── Unified intermediate representation ─────────────────────────────


@dataclass
class TimelineClip:
    """A source-video clip placed on the timeline."""

    label: str
    src_start: float      # in source video (seconds)
    src_end: float        # in source video (seconds)
    timeline_start: float  # position on the timeline (seconds)
    duration: float       # timeline duration (seconds)

    @property
    def src_url(self) -> str:
        # Overridden by callers that know the real source path.
        return ""


@dataclass
class TextOverlay:
    """A timed text element (subtitle or title/end/watermark card)."""

    text: str
    start: float
    end: float
    kind: str = "subtitle"  # subtitle | title | end | watermark | disclaimer


@dataclass
class Timeline:
    """Backend-agnostic edit description."""

    movie_name: str
    source_video_path: Optional[str] = None
    # Sequence timebase (fps). Filled from the render metadata when
    # available; backends that need a rate (FCP7 XML) fall back to the
    # writer's own default when left at the dataclass default.
    fps: int = 24
    # Narration stem for the audio track: the final mix (narration +
    # BGM) when the BGM step ran, else the raw TTS narration. Optional —
    # backends that carry audio (FCP7 XML) omit the track when absent.
    narration_audio_path: Optional[str] = None
    clips: List[TimelineClip] = field(default_factory=list)
    text_overlays: List[TextOverlay] = field(default_factory=list)


# ── Backend helpers ─────────────────────────────────────────────────


def _probe_otio() -> bool:
    try:
        import opentimelineio  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _write_otio(tl: Timeline, out_dir: Path) -> Path:
    """Serialise the timeline to an OpenTimelineIO ``.otio`` file."""
    import opentimelineio as otio

    out_path = out_dir / f"{tl.movie_name}.otio"

    timeline = otio.schema.Timeline(name=tl.movie_name)
    video_track = otio.schema.Track(name="video", kind="Video")
    text_track = otio.schema.Track(name="text", kind="Video")

    src_url = Path(tl.source_video_path).as_uri() if tl.source_video_path else ""

    for clip in tl.clips:
        mref = otio.schema.ExternalReference(target_url=src_url)
        ot_clip = otio.schema.Clip(
            name=clip.label,
            media_reference=mref,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.from_seconds(clip.src_start),
                duration=otio.opentime.from_seconds(clip.duration),
            ),
        )
        video_track.append(ot_clip)

    for ov in tl.text_overlays:
        # Text overlays become "gap"-free marker clips on the text track.
        mref = otio.schema.MissingReference()
        ot_clip = otio.schema.Clip(
            name=f"{ov.kind}: {ov.text[:40]}",
            media_reference=mref,
            source_range=otio.opentime.TimeRange(
                start_time=otio.opentime.from_seconds(0),
                duration=otio.opentime.from_seconds(max(ov.end - ov.start, 0.0)),
            ),
        )
        text_track.append(ot_clip)

    timeline.tracks.append(video_track)
    timeline.tracks.append(text_track)
    otio.adapters.write_to_file(timeline, str(out_path))
    return out_path


def _write_jianying(tl: Timeline, out_dir: Path) -> Path:
    """Serialise the timeline to a self-authored Jianying draft bundle.

    The Jianying draft schema is unpublished and maintained by ByteDance.
    This generator writes a minimal, self-contained draft that Jianying
    can open; the exact field set is best-effort and may require
    adjustment if the format drifts. Only the ``movies`` segment is
    materialised — audio/text tracks are intentionally omitted.
    """
    draft_dir = out_dir / f"{tl.movie_name}_jianying"
    draft_dir.mkdir(parents=True, exist_ok=True)

    clips = []
    for clip in tl.clips:
        clips.append({
            "id": f"clip_{len(clips)}",
            "material_id": f"m_{len(clips)}",
            "source_timeline_in": clip.src_start,
            "source_timeline_out": clip.src_end,
            "target_timeline_in": clip.timeline_start,
            "target_timeline_out": clip.timeline_start + clip.duration,
            "speed": 1.0,
            "flip": None,
            "rotation": 0.0,
            "transform": {"x": 0.0, "y": 0.0, "scale": 1.0},
        })

    draft_content = {
        "draft_timeline": {
            "accumulated_duration": (max((c.timeline_start + c.duration for c in tl.clips), default=0.0)),
            "video_ratio": "16:9",
            "videos": [
                {
                    "id": "video_1",
                    "tracks": [
                        {
                            "id": "track_video_1",
                            "type": "video",
                            "flag": 0,
                            "segments": [
                                {
                                    "id": c["id"],
                                    "material_id": c["material_id"],
                                    "source_timeline_in": c["source_timeline_in"],
                                    "source_timeline_out": c["source_timeline_out"],
                                    "target_timeline_in": c["target_timeline_in"],
                                    "target_timeline_out": c["target_timeline_out"],
                                    "speed": 1.0,
                                } for c in clips
                            ],
                        }
                    ],
                }
            ],
            "texts": [
                {
                    "id": f"text_{i}",
                    "content": ov.text,
                    "start": ov.start,
                    "end": ov.end,
                    "kind": ov.kind,
                } for i, ov in enumerate(tl.text_overlays)
            ],
        },
        "materials": {
            "videos": [
                {
                    "id": c["material_id"],
                    "path": tl.source_video_path or "",
                    "duration": c["target_timeline_out"] - c["target_timeline_in"],
                } for c in clips
            ],
        },
    }

    (draft_dir / "draft_content.json").write_text(
        json.dumps(draft_content, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (draft_dir / "draft_meta_info.json").write_text(
        json.dumps({"creation_time": 0, "edit_time": 0}, indent=2), encoding="utf-8"
    )
    return draft_dir


# ── Timeline construction ───────────────────────────────────────────


def _build_timeline(ctx: Context) -> Timeline:
    """Assemble the unified Timeline from pipeline context state."""
    render_template = ctx.metadata.get("render_template") or {}
    source = ctx.source_video_path

    clips: List[TimelineClip] = []
    for mc in ctx.matched_clips:
        duration = max(mc.src_end - mc.src_start, 0.0)
        clips.append(
            TimelineClip(
                label=mc.text[:40] or f"segment #{mc.segment_index}",
                src_start=mc.src_start,
                src_end=mc.src_end,
                timeline_start=0.0 if mc.narr_start is None else mc.narr_start,
                duration=duration,
            )
        )

    overlays: List[TextOverlay] = []
    for seg in ctx.timed_segments:
        overlays.append(
            TextOverlay(text=seg.text, start=seg.start, end=seg.end, kind="subtitle")
        )

    movie = ctx.movie_name
    title_card = render_template.get("title_card_text")
    if title_card:
        overlays.append(TextOverlay(text=title_card.replace("{movie}", movie), start=0.0, end=0.1, kind="title"))
    end_card = render_template.get("end_card_text")
    if end_card:
        overlays.append(TextOverlay(text=end_card.replace("{movie}", movie), start=0.0, end=0.1, kind="end"))
    watermark = render_template.get("watermark_text")
    if watermark:
        overlays.append(TextOverlay(text=watermark.replace("{movie}", movie), start=0.0, end=0.1, kind="watermark"))
    disclaimer = render_template.get("disclaimer_text")
    if disclaimer:
        overlays.append(TextOverlay(text=disclaimer.replace("{movie}", movie), start=0.0, end=0.1, kind="disclaimer"))

    return Timeline(
        movie_name=movie,
        source_video_path=source,
        # Same read path as the core render step (pipeline/render.py).
        fps=int(ctx.metadata.get("render_fps", 24) or 24),
        narration_audio_path=ctx.final_audio_path or ctx.audio_path,
        clips=clips,
        text_overlays=overlays,
    )


# ── Plugin ──────────────────────────────────────────────────────────


class TimelineExportPlugin:
    """Plugin that exports a timeline draft for manual fine-tuning."""

    name = "timeline-export"

    def register(self, ctx: PluginContext) -> None:
        """Register the timeline_export step with the step registry."""
        ctx.steps.register(
            "timeline_export",
            _timeline_export_step,
            soft=True,
            status_field="timeline",
            consequence="timeline draft not exported — NLE fine-tuning unavailable",
            after="render_video",
        )


def _timeline_export_step(ctx: Context) -> Context:
    """Build and write the timeline draft for the current edit."""
    backend = ctx.metadata.get("timeline_export_backend", "jianying")

    if not ctx.matched_clips:
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = "no matched clips to export"
        return ctx

    if backend == "otio" and not _probe_otio():
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = 'opentimelineio missing — run: pip install -e ".[otio]"'
        return ctx
    if backend not in ("otio", "jianying", "premiere"):
        ctx.step_state.result = StepResult.SKIPPED
        ctx.step_state.message = f"unknown timeline_export_backend: {backend}"
        return ctx

    out_dir = Path(ctx.output_dir) / "timeline"
    out_dir.mkdir(parents=True, exist_ok=True)

    tl = _build_timeline(ctx)
    try:
        if backend == "otio":
            out_path = _write_otio(tl, out_dir)
        elif backend == "premiere":
            out_path = _write_premiere(tl, out_dir)
        else:
            out_path = _write_jianying(tl, out_dir)
    except Exception as e:  # noqa: BLE001
        logger.exception("timeline_export failed")
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = f"timeline_export failed: {e}"
        return ctx

    ctx.metadata["timeline_export_path"] = str(out_path)
    ctx.step_state.result = StepResult.SUCCESS
    ctx.step_state.message = f"timeline exported to {out_path} ({backend})"
    return ctx
