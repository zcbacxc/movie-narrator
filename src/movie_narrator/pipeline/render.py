import json
import shutil
from pathlib import Path

import numpy as np
from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip
from PIL import Image, ImageDraw
from proglog import TqdmProgressBarLogger

from ..models import Context, TimedSegment
from ..utils.font import get_font
from ..utils.metadata_export import build_metadata_json


class _RenderProgressLogger(TqdmProgressBarLogger):
    """MoviePy progress logger with readable bar descriptions.

    Replaces the cryptic ``t:`` prefix (from ``iter_bar(t=...)``) with
    ``Rendering:`` so the progress bar is self-explanatory.
    """

    _BAR_LABELS = {
        "t": "Rendering",
    }

    def bars_callback(self, bar, attr, value, old_value):
        # Rename bar title before tqdm creates the bar (first callback only)
        if bar in self.bars and self.bars[bar]["title"] == bar:
            self.bars[bar]["title"] = self._BAR_LABELS.get(bar, bar)
        super().bars_callback(bar, attr, value, old_value)

VIDEO_SIZES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
}


def _overlay_text(ctx: Context, idx: int, seg: TimedSegment) -> str:
    """Pick the overlay text for a narration segment per `subtitle_mode`.

    Safe accessor (spec §7.3): never IndexError if `translated_texts`
    is shorter than `timed_segments` — falls back to the original.
    """
    mode = ctx.metadata.get("subtitle_mode", "original")
    t = (
        ctx.translated_texts[idx]
        if idx < len(ctx.translated_texts)
        else None
    )
    if mode == "translated" and t:
        return t
    if mode == "bilingual" and t:
        return f"{seg.text}\n{t}"
    return seg.text


def _create_text_image(text: str, size: tuple, fontsize: int = 100) -> np.ndarray:
    """Render text to a transparent RGBA image, supporting multi-line.

    Multi-line behavior (spec §7.3):
    - Lines are split on `\n`.
    - Fontscale per line: `1.0 - 0.1 * (line_count - 1)`, clamped to `[0.6, 1.0]`.
    - Lines are stacked vertically with a small spacing proportional to fontsize.
    """
    lines = text.split("\n")
    line_count = len(lines)
    scale = max(0.6, min(1.0, 1.0 - 0.1 * (line_count - 1)))
    eff_fontsize = max(1, int(round(fontsize * scale)))

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = get_font(eff_fontsize)

    # Measure each line, compute total stack height.
    line_spacing = max(2, int(round(eff_fontsize * 0.15)))
    line_metrics = []
    total_h = 0
    max_w = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        line_metrics.append((w, h))
        total_h += h
        max_w = max(max_w, w)
    total_h += line_spacing * (line_count - 1)

    # Stack lines centered.
    y = (size[1] - total_h) // 2
    for line, (w, h) in zip(lines, line_metrics):
        x = (size[0] - w) // 2
        draw.text(
            (x, y), line, fill=(255, 255, 255, 255), font=font,
            stroke_width=2, stroke_fill=(0, 0, 0, 255),
        )
        y += h + line_spacing
    return np.array(img)


def render_video(ctx: Context) -> Context:
    output_dir = Path(ctx.output_dir)
    video_format = ctx.metadata.get("format", "16:9")
    size = VIDEO_SIZES.get(video_format, (1920, 1080))
    keep_cache = ctx.metadata.get("keep_cache", False)

    audio_path = ctx.final_audio_path or ctx.audio_path
    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration

    bg_clip = ColorClip(size=size, color=(20, 20, 30), duration=total_duration)
    clips: list = [bg_clip]

    # Spec §2: render must ignore accidental source="fallback" rows (construction default).
    usable_clips = [mc for mc in ctx.matched_clips if mc.source != "fallback"]
    source = None

    if usable_clips and ctx.source_video_path:
        try:
            source = VideoFileClip(ctx.source_video_path)
        except Exception as e:
            ctx.services.console.debug(f"  fallback to text: cannot open source video: {e}")
            usable_clips = []
        else:
            for mc in usable_clips:
                seg_duration = mc.narr_end - mc.narr_start
                src_duration = mc.src_end - mc.src_start
                try:
                    subclip = source.subclipped(mc.src_start, mc.src_end)
                    if src_duration > 0:
                        subclip = subclip.with_speed_scaled(factor=src_duration / max(seg_duration, 0.1))
                    subclip = subclip.with_start(mc.narr_start)
                    clips.append(subclip)
                except Exception as ie:
                    ctx.services.console.debug(f"  fallback for segment {mc.segment_index}: {ie}")
                    img_array = _create_text_image(
                        _overlay_text(ctx, mc.segment_index, ctx.timed_segments[mc.segment_index]),
                        size, fontsize=100,
                    )
                    img_clip = ImageClip(img_array, is_mask=False)
                    img_clip = img_clip.with_duration(seg_duration).with_start(mc.narr_start)
                    clips.append(img_clip)
            # NOTE: source must NOT be closed here — subclips still need its reader during write_videofile.

    # Always add text overlays for any segment not covered by footage
    footage_segments = set()
    for mc in usable_clips:
        footage_segments.add(mc.segment_index)

    for i, seg in enumerate(ctx.timed_segments):
        if i in footage_segments:
            continue
        img_array = _create_text_image(_overlay_text(ctx, i, seg), size, fontsize=100)
        img_clip = ImageClip(img_array, is_mask=False)
        img_clip = img_clip.with_duration(seg.end - seg.start).with_start(seg.start)
        clips.append(img_clip)

    final_video = CompositeVideoClip(clips).with_audio(audio_clip)
    video_path = output_dir / "final.mp4"


    tmp_dir = output_dir / ".tmp"
    tmp_dir.mkdir(exist_ok=True)

    write_kwargs = dict(
        fps=24,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        logger=_RenderProgressLogger(),
        temp_audiofile=str(tmp_dir / "temp_audio.wav"),
    )
    try:
        final_video.write_videofile(str(video_path), **write_kwargs)
    finally:
        final_video.close()
        audio_clip.close()
        if source is not None:
            source.close()
        for clip in clips:
            if hasattr(clip, "close"):
                clip.close()

    metadata = build_metadata_json(ctx)
    with open(output_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    if not keep_cache:
        cache_dir = output_dir / "cache"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)

    ctx.video_path = str(video_path)
    return ctx
