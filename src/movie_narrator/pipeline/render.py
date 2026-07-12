import json
import shutil
from pathlib import Path

import numpy as np
from moviepy.editor import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip
from PIL import Image, ImageDraw

from ..models import Context
from ..utils.font import get_font
from ..utils.metadata_export import build_metadata_json

VIDEO_SIZES = {
    "16:9": (1920, 1080),
    "9:16": (1080, 1920),
}


def _find_ffmpeg() -> str | None:
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def _create_text_image(text: str, size: tuple, fontsize: int = 100) -> np.ndarray:
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = get_font(fontsize)
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    x = (size[0] - text_w) // 2
    y = (size[1] - text_h) // 2
    draw.text((x, y), text, fill=(255, 255, 255, 255), font=font,
              stroke_width=2, stroke_fill=(0, 0, 0, 255))
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

    if usable_clips and ctx.source_video_path:
        from moviepy.editor import VideoFileClip
        try:
            source = VideoFileClip(ctx.source_video_path)
        except Exception as e:
            print(f"  fallback to text: cannot open source video: {e}")
            usable_clips = []
        else:
            for mc in usable_clips:
                seg_duration = mc.narr_end - mc.narr_start
                src_duration = mc.src_end - mc.src_start
                try:
                    subclip = source.subclip(mc.src_start, mc.src_end)
                    if src_duration > 0:
                        subclip = subclip.speedx(factor=src_duration / max(seg_duration, 0.1))
                    subclip = subclip.set_start(mc.narr_start)
                    clips.append(subclip)
                except Exception as ie:
                    print(f"  fallback for segment {mc.segment_index}: {ie}")
                    img_array = _create_text_image(mc.text, size, fontsize=100)
                    img_clip = ImageClip(img_array, transparent=True)
                    img_clip = img_clip.set_duration(seg_duration).set_start(mc.narr_start)
                    clips.append(img_clip)
            source.close()

    # Always add text overlays for any segment not covered by footage
    footage_segments = set()
    for mc in usable_clips:
        footage_segments.add(mc.segment_index)

    for i, seg in enumerate(ctx.timed_segments):
        if i in footage_segments:
            continue
        img_array = _create_text_image(seg.text, size, fontsize=100)
        img_clip = ImageClip(img_array, transparent=True)
        img_clip = img_clip.set_duration(seg.end - seg.start).set_start(seg.start)
        clips.append(img_clip)

    final_video = CompositeVideoClip(clips).set_audio(audio_clip)
    video_path = output_dir / "final.mp4"
    write_kwargs = dict(
        fps=24,
        codec="libx264",
        audio_codec="aac",
        threads=4,
        logger=None,
    )
    try:
        final_video.write_videofile(str(video_path), **write_kwargs)
    finally:
        final_video.close()
        audio_clip.close()
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
