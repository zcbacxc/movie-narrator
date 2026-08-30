# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""FCP7 XML writer — the ``premiere`` backend of the timeline_export plugin.

Serialises the unified :class:`~movie_narrator_timeline_export.plugin.Timeline`
to a Final Cut Pro 7 XML (``xmeml``) interchange file — the format Adobe
Premiere Pro imports natively via ``File > Import``. One ``.xml`` file is
written per export (no sidecar files, no third-party dependency — the
writer uses only the stdlib ``xml.etree.ElementTree``).

Structure emitted (the minimal set Premiere needs on import):

- ``project > children > sequence`` with a ``rate`` (``timebase`` = the
  pipeline render fps, default 24 — the same default the core render
  step reads from metadata).
- A video track of ``clipitem`` elements (``start``/``end`` timeline
  positions, ``in``/``out`` source points, all in frames) referencing
  the source video ``file`` (defined once, then referenced by ``id``).
- A second video track of ``generatoritem`` text elements for the
  subtitle/title overlays (parity with the jianying ``texts`` array).
- An audio track carrying the narration stem (``final_audio_path`` —
  narration + BGM mix — falling back to ``audio_path``) when the
  context provides one; omitted otherwise.

All values are best-effort interchange data for hand-tuning; the FCP7
schema is large and this writer emits the widely-supported subset.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:  # pragma: no cover - import cycle guard (plugin imports us)
    from .plugin import Timeline

#: Default timebase when the render fps is unknown (matches the core
#: render step's ``ctx.metadata.get("render_fps", 24)`` default).
DEFAULT_FPS = 24

#: Sequence raster written into the format block. Best-effort, matching
#: the jianying draft's hardcoded 16:9 assumption; Premiere re-maps the
#: media on import.
SEQUENCE_WIDTH = 1920
SEQUENCE_HEIGHT = 1080


def _frames(seconds: float, fps: int) -> int:
    """Convert *seconds* to whole frames at *fps* (round-half-up)."""
    return int(round(seconds * fps))


def _sub(
    parent: ET.Element,
    tag: str,
    text: Optional[Union[str, int, float]] = None,
    **attrib: str,
) -> ET.Element:
    """Append a child element, optionally carrying text/attributes."""
    el = ET.SubElement(parent, tag, attrib)
    if text is not None:
        el.text = str(text)
    return el


def _rate(parent: ET.Element, fps: int) -> ET.Element:
    """Append an FCP7 ``rate`` block (``timebase`` + non-NTSC flag)."""
    rate = _sub(parent, "rate")
    _sub(rate, "timebase", fps)
    _sub(rate, "ntsc", "FALSE")
    return rate


def _file_ref(
    clipitem: ET.Element,
    file_id: str,
    path: Optional[str],
    fps: int,
    duration_frames: int,
    audio_only: bool = False,
) -> None:
    """Append a ``file`` element (full definition on first reference)."""
    file_el = _sub(clipitem, "file", id=file_id)
    _sub(file_el, "name", Path(path).name if path else "source")
    if path:
        # FCP7 consumers expect an absolute file URL; the timeline may carry
        # repo-relative paths (CI runs the smoke test from the repo root), so
        # resolve against the working directory first.
        _sub(file_el, "pathurl", Path(path).resolve().as_uri())
    _rate(file_el, fps)
    _sub(file_el, "duration", duration_frames)
    media = _sub(file_el, "media")
    if not audio_only:
        _sub(media, "video")
    audio = _sub(media, "audio")
    _sub(audio, "channelcount", 2)


def _clipitem(
    track: ET.Element,
    *,
    item_id: str,
    name: str,
    fps: int,
    start: float,
    end: float,
    src_in: float,
    src_out: float,
    file_id: str,
    path: Optional[str],
    file_defined: bool,
    file_audio_only: bool = False,
) -> None:
    """Append a ``clipitem`` (timeline start/end + source in/out, frames)."""
    ci = _sub(track, "clipitem", id=item_id)
    _sub(ci, "name", name)
    _sub(ci, "enabled", "TRUE")
    _sub(ci, "duration", _frames(end - start, fps))
    _rate(ci, fps)
    _sub(ci, "start", _frames(start, fps))
    _sub(ci, "end", _frames(end, fps))
    _sub(ci, "in", _frames(src_in, fps))
    _sub(ci, "out", _frames(src_out, fps))
    if file_defined:
        # FCP7 convention: subsequent references are empty, id-only.
        _sub(ci, "file", id=file_id)
    else:
        _file_ref(
            ci, file_id, path, fps, _frames(end - start, fps), audio_only=file_audio_only
        )


def _write_premiere(tl: Timeline, out_dir: Path) -> Path:
    """Serialise the timeline to an FCP7 XML ``.xml`` file Premiere imports."""
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{tl.movie_name}.xml"
    fps = max(int(tl.fps), 1)

    xmeml = ET.Element("xmeml", {"version": "4"})
    project = _sub(xmeml, "project")
    _sub(project, "name", tl.movie_name)
    children = _sub(project, "children")

    sequence = _sub(children, "sequence", id="sequence-1")
    _sub(sequence, "name", tl.movie_name)
    total_frames = max(
        (_frames(c.timeline_start + c.duration, fps) for c in tl.clips), default=0
    )
    _sub(sequence, "duration", total_frames)
    _rate(sequence, fps)

    media = _sub(sequence, "media")
    video = _sub(media, "video")
    fmt = _sub(video, "format")
    characteristics = _sub(fmt, "samplecharacteristics")
    _rate(characteristics, fps)
    _sub(characteristics, "width", SEQUENCE_WIDTH)
    _sub(characteristics, "height", SEQUENCE_HEIGHT)

    # Video track: one clipitem per matched clip, all referencing the
    # same source file (defined fully on the first clipitem).
    track = _sub(video, "track")
    src = tl.source_video_path
    for i, clip in enumerate(tl.clips, start=1):
        _clipitem(
            track,
            item_id=f"clipitem-{i}",
            name=clip.label,
            fps=fps,
            start=clip.timeline_start,
            end=clip.timeline_start + clip.duration,
            src_in=clip.src_start,
            src_out=clip.src_end,
            file_id="file-1",
            path=src,
            file_defined=i > 1,
        )

    # Text track: generatoritems for subtitle/title cards (parity with
    # the jianying draft's ``texts`` array and the OTIO text track).
    if tl.text_overlays:
        text_track = _sub(video, "track")
        for j, ov in enumerate(tl.text_overlays, start=1):
            gi = _sub(text_track, "generatoritem", id=f"generator-{j}")
            _sub(gi, "name", ov.text)
            _sub(gi, "enabled", "TRUE")
            duration = max(ov.end - ov.start, 0.0)
            _sub(gi, "duration", _frames(duration, fps))
            _rate(gi, fps)
            _sub(gi, "start", _frames(ov.start, fps))
            _sub(gi, "end", _frames(ov.end, fps))
            effect = _sub(gi, "effect")
            _sub(effect, "name", "Text")
            _sub(effect, "effectid", "Text")
            _sub(effect, "effectcategory", "Text")
            _sub(effect, "effecttype", "generator")
            _sub(effect, "mediatype", "video")

    # Audio track: the narration stem (final mix — narration + BGM —
    # when available) spanning the full timeline, if the context has one.
    if tl.narration_audio_path:
        audio = _sub(media, "audio")
        audio_track = _sub(audio, "track")
        _clipitem(
            audio_track,
            item_id="clipitem-audio-1",
            name=Path(tl.narration_audio_path).stem or "narration",
            fps=fps,
            start=0.0,
            end=total_frames / fps,
            src_in=0.0,
            src_out=total_frames / fps,
            file_id="file-audio-1",
            path=tl.narration_audio_path,
            file_defined=False,
            file_audio_only=True,
        )

    ET.indent(xmeml, space="  ")
    header = '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE xmeml>\n'
    out_path.write_text(header + ET.tostring(xmeml, encoding="unicode") + "\n", encoding="utf-8")
    return out_path
