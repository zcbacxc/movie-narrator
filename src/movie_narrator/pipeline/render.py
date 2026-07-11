import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from moviepy.editor import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip
from PIL import Image, ImageDraw

from .. import __version__
from ..models import Context
from ..utils.font import get_font

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
    output_dir = Path(ctx.metadata["output_dir"])
    video_format = ctx.metadata.get("format", "16:9")
    size = VIDEO_SIZES.get(video_format, (1920, 1080))
    keep_cache = ctx.metadata.get("keep_cache", False)

    audio_clip = AudioFileClip(ctx.audio_path)
    total_duration = audio_clip.duration

    bg_clip = ColorClip(size=size, color=(20, 20, 30), duration=total_duration)
    clips = [bg_clip]

    for seg in ctx.timed_segments:
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
    ffmpeg_bin = _find_ffmpeg()
    if ffmpeg_bin:
        write_kwargs["ffmpeg_binary"] = ffmpeg_bin

    try:
        final_video.write_videofile(str(video_path), **write_kwargs)
    finally:
        final_video.close()
        audio_clip.close()
        for clip in clips:
            if hasattr(clip, "close"):
                clip.close()

    metadata = {
        "version": __version__,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": {
            "movie": ctx.movie_name,
            "style": ctx.style,
            "duration": ctx.duration,
            "voice": ctx.metadata.get("voice_used"),
            "format": video_format,
        },
        "output": {
            "video": "final.mp4",
            "audio": "narration.mp3",
            "subtitle": "subtitle.srt",
        },
        "cache_kept": keep_cache,
        "segments_count": len(ctx.timed_segments),
        "segments": [
            {"text": seg.text, "start": seg.start, "end": seg.end}
            for seg in ctx.timed_segments
        ],
    }
    with open(output_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    if not keep_cache:
        cache_dir = output_dir / "cache"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)

    ctx.video_path = str(video_path)
    return ctx
