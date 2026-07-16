import json
import shutil
from pathlib import Path

from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip
from proglog import TqdmProgressBarLogger

from ..models import Context, MatchedClip, StepResult, TimedSegment
from ..utils.metadata_export import build_metadata_json
from ..utils.text_image import create_text_image as _create_text_image
from ..utils.video_layout import compute_fit_box


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


def _get_video_sizes(ctx: Context) -> dict:
    """Return video_sizes dict from job params (ctx.metadata) with defaults fallback.

    The metadata value (from YAML) is already a dict; ``{"16:9": (1920, 1080), "9:16": (1080, 1920)}``
    is also a dict — no JSON parsing needed.
    """
    raw = ctx.metadata.get("video_sizes", {"16:9": (1920, 1080), "9:16": (1080, 1920)})
    return {k: tuple(v) for k, v in raw.items()}


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


def render_video(ctx: Context) -> Context:
    output_dir = Path(ctx.output_dir)
    video_format = ctx.metadata.get("format", "16:9")
    size = _get_video_sizes(ctx).get(video_format, (1920, 1080))
    keep_cache = ctx.metadata.get("keep_cache", False)
    font_size = ctx.metadata.get("render_font_size", 100)

    audio_path = ctx.final_audio_path or ctx.audio_path
    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration

    # Production-quality render knobs (spec §7.2).
    fit_mode = ctx.metadata.get("render_fit_mode", "cover")
    subtitle_position = ctx.metadata.get("render_subtitle_position", "bottom")
    max_width_ratio = ctx.metadata.get("render_subtitle_max_width_ratio", 0.9)
    bottom_margin_ratio = ctx.metadata.get("render_subtitle_bottom_margin_ratio", 0.08)

    # Parse background color "R,G,B" → tuple
    bg_color_str = ctx.metadata.get("render_bg_color", "20,20,30")
    bg_parts = [int(x.strip()) for x in bg_color_str.split(",")]
    bg_color = tuple(bg_parts[:3])
    bg_clip = ColorClip(size=size, color=bg_color, duration=total_duration)
    clips: list = [bg_clip]

    # Spec §2: render must ignore accidental source="fallback" rows (construction default).
    usable_clips = [mc for mc in ctx.matched_clips if mc.source != "fallback"]
    source = None

    if usable_clips and ctx.source_video_path:
        try:
            source = VideoFileClip(ctx.source_video_path)
        except Exception as e:
            ctx.services.console.inline_warn(
                f"Cannot open source video ({ctx.source_video_path}): {e}. "
                f"Falling back to text-only video — no footage will be shown."
            )
            usable_clips = []
        else:
            for mc in usable_clips:
                seg_duration = mc.narr_end - mc.narr_start
                src_duration = mc.src_end - mc.src_start
                try:
                    subclip = source.subclipped(mc.src_start, mc.src_end)
                    if src_duration > 0:
                        subclip = subclip.with_speed_scaled(factor=src_duration / max(seg_duration, 0.1))

                    # Fit source frame onto the canvas (cover=crop+fill,
                    # contain=letterbox+center). Keeps footage from overflowing
                    # or distorting the output resolution.
                    box = compute_fit_box(
                        (subclip.w, subclip.h), size, mode=fit_mode,
                    )
                    if fit_mode == "cover":
                        fitted = subclip.cropped(
                            x1=box.crop_x, y1=box.crop_y,
                            x2=box.crop_x + box.crop_w, y2=box.crop_y + box.crop_h,
                        ).resized((box.out_w, box.out_h))
                        fitted = fitted.with_position((0, 0))
                    else:  # contain
                        fitted = subclip.resized((box.out_w, box.out_h))
                        pos_x = (size[0] - box.out_w) // 2
                        pos_y = (size[1] - box.out_h) // 2
                        fitted = fitted.with_position((pos_x, pos_y))

                    clips.append(fitted.with_start(mc.narr_start))
                except Exception as ie:
                    ctx.services.console.debug(f"  fallback for segment {mc.segment_index}: {ie}")
                    img_array = _create_text_image(
                        _overlay_text(ctx, mc.segment_index, ctx.timed_segments[mc.segment_index]),
                        size, fontsize=font_size, position=subtitle_position,
                        max_width_ratio=max_width_ratio,
                        bottom_margin_ratio=bottom_margin_ratio,
                    )
                    img_clip = ImageClip(img_array, is_mask=False)
                    img_clip = img_clip.with_duration(seg_duration).with_start(mc.narr_start)
                    clips.append(img_clip)
            # NOTE: source must NOT be closed here — subclips still need its reader during write_videofile.

    # Always draw subtitle overlays for ALL narration segments — including
    # footage-covered ones. Publishable recaps need visible subtitles even
    # over footage; footage segments use the "bottom" position so the text
    # sits under the action instead of obscuring it.
    footage_segments = set()
    for mc in usable_clips:
        footage_segments.add(mc.segment_index)

    for i, seg in enumerate(ctx.timed_segments):
        pos = "bottom" if i in footage_segments else subtitle_position
        img_array = _create_text_image(
            _overlay_text(ctx, i, seg), size, fontsize=font_size,
            position=pos,
            max_width_ratio=max_width_ratio,
            bottom_margin_ratio=bottom_margin_ratio,
        )
        img_clip = ImageClip(img_array, is_mask=False)
        img_clip = img_clip.with_duration(seg.end - seg.start).with_start(seg.start)
        clips.append(img_clip)

    final_video = CompositeVideoClip(clips).with_audio(audio_clip)
    video_path = output_dir / ctx.metadata.get("render_output_name", "final.mp4")


    tmp_dir = output_dir / ".tmp"
    tmp_dir.mkdir(exist_ok=True)

    # temp_audiofile extension MUST match the audio_codec so the final
    # mux step ("-acodec copy") reads a correctly-typed bitstream.
    # With audio_codec="aac", a ".wav" extension causes a RIFF/WAV
    # header wrapping AAC data — ffmpeg then decodes only ~6 ms.
    audio_codec = ctx.metadata.get("render_audio_codec", "aac")
    temp_ext = audio_codec
    # "copy" is a passthrough pseudo-codec; "aac" works for both AAC-LC
    # and the HE-AAC variants.  Map libmp3lame → mp3 so the temp file
    # always carries a real container extension.
    if temp_ext == "copy":
        temp_ext = "aac"
    elif temp_ext.startswith("lib"):
        temp_ext = temp_ext[3:]  # libmp3lame → mp3lame
    # Normalise a few known aliases to a short file extension.
    _EXT_NORM = {"mp3lame": "mp3", "libfdk_aac": "aac", "pcm_s16le": "wav"}
    temp_ext = _EXT_NORM.get(temp_ext, temp_ext)

    # Production-quality encode: CRF + preset + faststart (spec §7.2).
    # faststart moves the moov atom to the front so the video can begin
    # playback before the full file downloads (required for web preview).
    crf = ctx.metadata.get("render_crf", 18)
    preset = ctx.metadata.get("render_preset", "slow")
    faststart = ctx.metadata.get("render_faststart", True)
    ffmpeg_params = ["-crf", str(crf), "-preset", str(preset)]
    if faststart:
        ffmpeg_params += ["-movflags", "+faststart"]

    write_kwargs = dict(
        fps=ctx.metadata.get("render_fps", 24),
        codec=ctx.metadata.get("render_video_codec", "libx264"),
        audio_codec=audio_codec,
        threads=ctx.metadata.get("render_threads", 4),
        logger=_RenderProgressLogger(),
        temp_audiofile=str(tmp_dir / f"temp_audio.{temp_ext}"),
        ffmpeg_params=ffmpeg_params,
    )
    try:
        final_video.write_videofile(str(video_path), **write_kwargs)
    finally:
        # Exception-safe cleanup: each close is guarded so one failure
        # doesn't prevent the remaining resources from being released.
        # NOTE: source must NOT be closed before write_videofile — MoviePy 2.x
        # subclipped() clips share the parent reader, so closing source early
        # would crash during encoding.
        for obj in (final_video, audio_clip, source, *clips):
            if obj is not None:
                try:
                    obj.close()
                except Exception:  # noqa: BLE001
                    pass

    metadata = build_metadata_json(ctx)
    with open(output_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    if not keep_cache:
        cache_dir = output_dir / "cache"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)

    ctx.video_path = str(video_path)
    return ctx
