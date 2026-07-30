# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

import json
import logging
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip
from PIL import Image, ImageDraw, ImageFilter
from proglog import TqdmProgressBarLogger

from ..models import Context, MatchedClip, StepResult, TimedSegment
from ..utils.console import step_timing
from ..utils.gpu_detect import get_encoder_info, resolve_encoder
from ..utils.metadata_export import build_metadata_json
from ..utils.text_image import create_text_image as _create_text_image
from ..utils.video_layout import compute_fit_box
from ..utils.transitions import apply_transition, get_transition_duration
from ..utils.text_anim import apply_text_animation, get_animation_duration
from .bgm import ensure_final_audio

logger = logging.getLogger(__name__)

# RS-07: Minimum segment duration floor for speed scaling.
# Prevents division-by-zero when seg_duration is extremely short
# (e.g. 0-length segment from alignment glitch). 0.1s is intentional:
# below this, speed scaling produces visually absurd fast-forward.
_SEG_DURATION_FLOOR = 0.1

# RS-08: Default ffmpeg mux timeout (seconds) when render_ffmpeg_timeout
# is not specified in job params. 10 min is generous for 4K + slow preset.
_DEFAULT_MUX_TIMEOUT = 600

# EP5: Vertical (9:16) safe area defaults.
# On vertical video, platform UI (TikTok/Douyin caption area, like/share
# buttons) can cover the bottom 20-25% of the screen. These conservative
# ratios push subtitles above the danger zone.
_VERTICAL_BOTTOM_MARGIN_RATIO = 0.15  # vs 0.08 default for 16:9
_VERTICAL_MAX_WIDTH_RATIO = 0.82      # vs 0.90 default for 16:9


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


def _export_cover_image(
    ctx: Context,
    usable_clips: list[MatchedClip],
    output_dir: Path,
) -> None:
    """EP5 V6: Export cover.jpg from the highest-score matched frame.

    Extracts the midpoint frame of the highest-score MatchedClip using
    ffmpeg, then overlays the movie name with a semi-transparent gradient
    using PIL. The result is saved as ``cover.jpg`` in the output dir.

    Failures are non-fatal (warn-only) — cover.jpg is a bonus artifact,
    not a pipeline requirement.
    """
    if not usable_clips or not ctx.source_video_path:
        ctx.services.console.debug("  EP5 cover: no usable clips or source video — skipping")
        return

    # Find the highest-score clip (embedding source preferred)
    scored = [mc for mc in usable_clips if mc.score is not None and mc.score > 0]
    if not scored:
        ctx.services.console.debug("  EP5 cover: no scored clips — skipping")
        return

    best = max(scored, key=lambda mc: mc.score)
    mid_ts = (best.src_start + best.src_end) / 2.0

    cover_raw = output_dir / "_cover_raw.jpg"
    cover_final = output_dir / "cover.jpg"

    # Extract frame via ffmpeg
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        ctx.services.console.debug("  EP5 cover: ffmpeg not found — skipping")
        return

    extract_cmd = [
        ffmpeg_bin, "-y", "-loglevel", "error",
        "-ss", f"{mid_ts:.2f}",
        "-i", str(ctx.source_video_path),
        "-frames:v", "1",
        "-q:v", "2",
        str(cover_raw),
    ]
    try:
        proc = subprocess.run(
            extract_cmd, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=30,
        )
        if proc.returncode != 0 or not cover_raw.exists():
            ctx.services.console.debug(
                f"  EP5 cover: ffmpeg extract failed: {proc.stderr[:200]}"
            )
            return
    except Exception as e:
        ctx.services.console.debug(f"  EP5 cover: extract error: {e}")
        logger.debug("EP5 cover: ffmpeg extract failed", exc_info=True)
        return

    # Overlay movie name with PIL
    try:
        img = Image.open(cover_raw).convert("RGB")
        w, h = img.size

        # Resize to a standard cover size (1280px wide, maintain aspect)
        if w > 1280:
            new_h = int(h * 1280 / w)
            img = img.resize((1280, new_h))
            w, h = img.size

        draw = ImageDraw.Draw(img)

        # Semi-transparent gradient at bottom for text readability
        gradient_height = int(h * 0.35)
        gradient = Image.new("RGBA", (w, gradient_height), (0, 0, 0, 0))
        g_draw = ImageDraw.Draw(gradient)
        for y in range(gradient_height):
            alpha = int(180 * (y / gradient_height))
            g_draw.line([(0, y), (w, y)], fill=(0, 0, 0, alpha))
        img.paste(gradient, (0, h - gradient_height), gradient)

        # Draw movie name
        from ..utils.font import get_font
        font_size = max(28, int(w * 0.06))
        font = get_font(font_size)
        text = ctx.movie_name or ""

        # Wrap text
        from ..utils.text_image import _wrap_line
        lines = _wrap_line(text, draw, font, int(w * 0.85))
        line_height = font_size + 6
        total_text_h = len(lines) * line_height
        y_start = h - gradient_height // 2 - total_text_h // 2

        for i, line in enumerate(lines):
            bbox = draw.textbbox((0, 0), line, font=font)
            text_w = bbox[2] - bbox[0]
            x = (w - text_w) // 2
            y = y_start + i * line_height
            # Shadow for readability
            draw.text((x + 2, y + 2), line, fill=(0, 0, 0), font=font)
            draw.text((x, y), line, fill=(255, 255, 255), font=font)

        img.save(str(cover_final), "JPEG", quality=90)
        ctx.services.console.debug(
            f"  EP5 cover: exported cover.jpg from segment {best.segment_index} "
            f"(score={best.score:.3f}, ts={mid_ts:.1f}s)"
        )
    except Exception as e:
        ctx.services.console.debug(f"  EP5 cover: overlay error: {e}")
        logger.debug("EP5 cover: overlay failed", exc_info=True)
    finally:
        # Clean up raw frame
        cover_raw.unlink(missing_ok=True)


def _substitute_movie(text: str, movie_name: str) -> str:
    """NA-M6-S1: Replace the ``{movie}`` placeholder with the actual movie name.

    Returns the original text unchanged when the placeholder is absent.
    """
    if not text:
        return text
    return text.replace("{movie}", movie_name or "")


def _create_watermark_image(text: str, size: tuple, fontsize: int = 36):
    """NA-M6-S1: Create a full-canvas transparent image with small
    semi-transparent text anchored to the top-right corner.

    Returns a ``numpy.ndarray`` (RGBA) suitable for ``ImageClip``.
    """
    import numpy as np
    from ..utils.font import get_font

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = get_font(fontsize)

    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    margin = max(10, int(size[0] * 0.03))
    x = size[0] - text_w - margin
    y = margin

    # Semi-transparent white text with a faint black stroke for legibility.
    draw.text(
        (x, y), text, fill=(255, 255, 255, 140), font=font,
        stroke_width=1, stroke_fill=(0, 0, 0, 120),
    )
    return np.array(img)


def render_video(ctx: Context) -> Context:
    # AQ-04 safety net: ensure final audio is normalized even if mix_bgm
    # was skipped or failed. This guarantees render never receives raw
    # unnormalized narration when bgm_normalize=True.
    ensure_final_audio(ctx)

    output_dir = Path(ctx.output_dir)
    video_format = ctx.metadata.get("format", "16:9")
    size = _get_video_sizes(ctx).get(video_format, (1920, 1080))
    keep_cache = ctx.metadata.get("keep_cache", False)
    font_size = ctx.metadata.get("render_font_size", 100)

    audio_path = ctx.final_audio_path or ctx.audio_path
    audio_clip = AudioFileClip(audio_path)
    total_duration = audio_clip.duration

    # v0.7.2: Preview mode — truncate to first N seconds for fast iteration.
    # When enabled the audio, background clip and subtitle segments are all
    # cut to the preview window so the rendered file is a faithful (short)
    # representation of the final output.  Preview mode is OFF by default
    # (backward compatible).
    preview_mode = ctx.metadata.get("render_preview_mode", False)
    if preview_mode:
        from ..utils.preview import get_preview_duration, truncate_segments_for_preview
        preview_sec = get_preview_duration(
            ctx.metadata.get("render_preview_sec", 10.0), total_duration
        )
        total_duration = min(total_duration, preview_sec)
        ctx.services.console.info(f"  Preview mode: rendering first {preview_sec:.0f}s")
        # Truncate the audio so the muxed output is exactly preview_sec long.
        audio_clip = audio_clip.subclipped(0, total_duration)
        # Truncate timed segments so subtitle overlays respect the preview
        # window (segments beyond the cut are dropped; spanning segments are
        # clamped to end at the boundary).
        ctx.timed_segments = truncate_segments_for_preview(
            ctx.timed_segments, total_duration
        )

    # Production-quality render knobs (spec §7.2).
    fit_mode = ctx.metadata.get("render_fit_mode", "cover")
    subtitle_position = ctx.metadata.get("render_subtitle_position", "bottom")
    max_width_ratio = ctx.metadata.get("render_subtitle_max_width_ratio", 0.9)
    bottom_margin_ratio = ctx.metadata.get("render_subtitle_bottom_margin_ratio", 0.08)

    # EP5 V5: Vertical (9:16) safe area auto-adjustment.
    # Platform UI on vertical video (TikTok/Douyin caption area, like/share
    # buttons) covers the bottom 20-25% of the screen. When enabled,
    # push subtitles higher and narrow them so they stay visible.
    vertical_safe = ctx.metadata.get("render_vertical_safe_area", True)
    if vertical_safe and video_format == "9:16":
        max_width_ratio = min(max_width_ratio, _VERTICAL_MAX_WIDTH_RATIO)
        bottom_margin_ratio = max(bottom_margin_ratio, _VERTICAL_BOTTOM_MARGIN_RATIO)
        ctx.services.console.debug(
            f"  EP5 vertical safe area: max_width={max_width_ratio:.2f} "
            f"bottom_margin={bottom_margin_ratio:.2f}"
        )

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
            # v0.7.0: VideoFileClip opens the source via a streaming reader
            # that seeks on demand rather than decoding the entire file into
            # memory. This keeps peak RAM bounded even for very large source
            # files; avoid replacing it with a full-decode approach.
            source = VideoFileClip(ctx.source_video_path)
        except Exception as e:
            ctx.services.console.inline_warn(
                f"Cannot open source video ({ctx.source_video_path}): {e}. "
                f"Falling back to text-only video — no footage will be shown."
            )
            logger.debug("source video open failed", exc_info=True)
            usable_clips = []
        else:
            for mc in usable_clips:
                seg_duration = mc.narr_end - mc.narr_start
                src_duration = mc.src_end - mc.src_start
                try:
                    subclip = source.subclipped(mc.src_start, mc.src_end)
                    if src_duration > 0:
                        subclip = subclip.with_speed_scaled(factor=src_duration / max(seg_duration, _SEG_DURATION_FLOOR))

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

                    # v0.7.1: apply scene transition to video clips
                    transition_type = ctx.metadata.get("render_transition", "none")
                    if transition_type != "none":
                        trans_dur = get_transition_duration(
                            mc.narr_end - mc.narr_start,
                            ctx.metadata.get("render_transition_duration", 0.5)
                        )
                        fitted = apply_transition(fitted, transition_type, trans_dur)

                    clips.append(fitted.with_start(mc.narr_start))
                except Exception as ie:
                    ctx.services.console.debug(f"  fallback for segment {mc.segment_index}: {ie}")
                    logger.debug("clip fallback for segment %d", mc.segment_index, exc_info=True)
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

    # v0.7.0: Render parallelization — generate subtitle overlay images in a
    # thread pool. Text rasterisation (PIL) is CPU-bound and releases the GIL
    # during the native font/blend work, so a small worker pool cuts wall time
    # for videos with many segments without complicating clip ordering (each
    # future carries its own index/segment; results are appended in submit
    # order which is deterministic).
    def _make_subtitle_image(i, seg, pos):
        img_array = _create_text_image(
            _overlay_text(ctx, i, seg), size, fontsize=font_size,
            position=pos,
            max_width_ratio=max_width_ratio,
            bottom_margin_ratio=bottom_margin_ratio,
        )
        img_clip = ImageClip(img_array, is_mask=False)
        img_clip = img_clip.with_duration(seg.end - seg.start).with_start(seg.start)

        # v0.7.1: apply text animation to subtitle overlays
        text_anim_type = ctx.metadata.get("render_text_animation", "none")
        if text_anim_type != "none":
            anim_dur = get_animation_duration(
                seg.end - seg.start,
                ctx.metadata.get("render_text_animation_duration", 0.3)
            )
            img_clip = apply_text_animation(img_clip, text_anim_type, anim_dur)

        return img_clip

    with ThreadPoolExecutor(max_workers=4) as pool:
        subtitle_futures = []
        for i, seg in enumerate(ctx.timed_segments):
            pos = "bottom" if i in footage_segments else subtitle_position
            subtitle_futures.append(pool.submit(_make_subtitle_image, i, seg, pos))
        for future in subtitle_futures:
            clips.append(future.result())

    # EP5: Title card overlay — show movie name at the beginning for a
    # polished opening. Uses a larger centered font with fade in/out.
    # Duration is controlled by render_title_card_sec (0 = disabled).
    #
    # NA-M6-S1: If a render_template is provided with ``title_card_text``,
    # use it (with ``{movie}`` replaced by ctx.movie_name) instead of the
    # bare movie name.  Falls back to ctx.movie_name when no template is
    # present so existing behaviour is unchanged.
    render_template = ctx.metadata.get("render_template") or {}
    title_card_sec = ctx.metadata.get("render_title_card_sec", 0)
    title_card_template = render_template.get("title_card_text")
    if title_card_template:
        title_card_text = _substitute_movie(title_card_template, ctx.movie_name)
    else:
        title_card_text = ctx.movie_name
    if title_card_sec and title_card_sec > 0 and title_card_text:
        title_font_size = int(font_size * 1.4)
        title_img = _create_text_image(
            title_card_text, size, fontsize=title_font_size,
            position="center",
            max_width_ratio=0.85,
        )
        title_clip = ImageClip(title_img, is_mask=False)
        title_clip = title_clip.with_duration(title_card_sec).with_start(0)
        # Fade in/out for polish (graceful degradation if MoviePy fx unavailable)
        try:
            from moviepy.video.fx import FadeIn, FadeOut
            fade_dur = min(0.3, title_card_sec / 3)
            title_clip = title_clip.with_effects([FadeIn(fade_dur), FadeOut(fade_dur)])
        except Exception:
            logger.debug("title card fade effect failed", exc_info=True)
        clips.append(title_clip)
        ctx.services.console.debug(
            f"  EP5 title card: {title_card_text} ({title_card_sec}s)"
        )

    # NA-M6-S1: End card overlay — show end card text at the end of the
    # video (similar to the title card but at the closing).  Soft addition:
    # skipped entirely when ``end_card_text`` is absent from the template.
    end_card_template = render_template.get("end_card_text")
    if end_card_template:
        end_card_text = _substitute_movie(end_card_template, ctx.movie_name)
        end_card_sec = title_card_sec if (title_card_sec and title_card_sec > 0) else 1.0
        end_font_size = int(font_size * 1.4)
        end_img = _create_text_image(
            end_card_text, size, fontsize=end_font_size,
            position="center",
            max_width_ratio=0.85,
        )
        end_clip = ImageClip(end_img, is_mask=False)
        end_start = max(0.0, total_duration - end_card_sec)
        end_clip = end_clip.with_duration(end_card_sec).with_start(end_start)
        try:
            from moviepy.video.fx import FadeIn, FadeOut
            fade_dur = min(0.3, end_card_sec / 3)
            end_clip = end_clip.with_effects([FadeIn(fade_dur), FadeOut(fade_dur)])
        except Exception:
            logger.debug("end card fade effect failed", exc_info=True)
        clips.append(end_clip)
        ctx.services.console.debug(
            f"  NA-M6-S1 end card: {end_card_text} ({end_card_sec}s)"
        )

    # NA-M6-S1: Watermark overlay — small semi-transparent text in the
    # top-right corner, visible for the entire video duration.
    watermark_template = render_template.get("watermark_text")
    if watermark_template:
        watermark_text = _substitute_movie(watermark_template, ctx.movie_name)
        wm_img = _create_watermark_image(
            watermark_text, size, fontsize=max(24, int(font_size * 0.36)),
        )
        wm_clip = ImageClip(wm_img, is_mask=False)
        wm_clip = wm_clip.with_duration(total_duration).with_start(0)
        clips.append(wm_clip)
        ctx.services.console.debug(
            f"  NA-M6-S1 watermark: {watermark_text}"
        )

    # NA-M6-S1: Disclaimer overlay — small text at the very bottom,
    # visible for the entire video duration.  Uses a smaller font and a
    # minimal bottom margin so it sits beneath the subtitle band.
    disclaimer_template = render_template.get("disclaimer_text")
    if disclaimer_template:
        disclaimer_text = _substitute_movie(disclaimer_template, ctx.movie_name)
        disc_img = _create_text_image(
            disclaimer_text, size, fontsize=max(20, int(font_size * 0.42)),
            position="bottom",
            max_width_ratio=0.9,
            bottom_margin_ratio=0.02,
        )
        disc_clip = ImageClip(disc_img, is_mask=False)
        disc_clip = disc_clip.with_duration(total_duration).with_start(0)
        clips.append(disc_clip)
        ctx.services.console.debug(
            f"  NA-M6-S1 disclaimer: {disclaimer_text}"
        )

    final_video = CompositeVideoClip(clips).with_audio(audio_clip)
    # Free clip references before encoding to reduce peak memory (v0.7.0).
    # The CompositeVideoClip retains its own references to the child clips via
    # ``final_video.clips``; the standalone ``clips`` list is no longer needed
    # and dropping it lets GC reclaim the list shell during the expensive
    # write_videofile call below.
    del clips
    # v0.7.2: In preview mode, default the output name to preview.mp4 so the
    # short render is never mistaken for the final deliverable.  An explicit
    # render_output_name from the user always takes precedence.
    default_output_name = "preview.mp4" if preview_mode else "final.mp4"
    video_path = output_dir / ctx.metadata.get("render_output_name", default_output_name)


    tmp_dir = output_dir / ".tmp"
    tmp_dir.mkdir(exist_ok=True)

    audio_codec = ctx.metadata.get("render_audio_codec", "aac")
    # The mux passes ``audio_codec`` (or its lib-prefix-stripped form)
    # directly to ``ffmpeg -c:a`` later in this function, so no temp
    # file extension translation is needed here.

    # Production-quality encode: CRF + preset + faststart (spec §7.2).
    # faststart moves the moov atom to the front so the video can begin
    # playback before the full file downloads (required for web preview).
    crf = ctx.metadata.get("render_crf", 18)
    preset = ctx.metadata.get("render_preset", "slow")
    faststart = ctx.metadata.get("render_faststart", True)

    # v0.7.0: GPU encoder resolution. ``render_encoder`` accepts "auto"
    # (default, probe + fall back to libx264), "cpu", or an explicit backend
    # ("nvenc" / "vaapi" / "videotoolbox"). ``resolve_encoder`` returns a
    # ``(codec, ffmpeg_params)`` tuple; the params are backend-specific and
    # replace the libx264-only ``-crf``/``-preset`` knobs when a GPU encoder
    # is active. See ..utils.gpu_detect for the probe + caching logic.
    render_encoder_hint = ctx.metadata.get("render_encoder")
    gpu_codec, gpu_params = resolve_encoder(render_encoder_hint)

    # TWO-STAGE ENCODE: write a video-only mp4 via MoviePy (which is
    # stable in isolation), then mux audio with ffmpeg in a second pass.
    #
    # This avoids a recurring failure mode on Windows + Python 3.14 +
    # MoviePy 2.x where ``write_videofile`` writes audio + video through
    # a single Popen pipe and the rawvideo stdin write raises
    # ``OSError [Errno 22] Invalid argument`` partway through — leaving
    # the final file with a corrupted ftyp/mdat layout (no moov atom).
    # See commit notes on PR #37 for the empirical reproduction.
    video_only_path = tmp_dir / "video_only.mp4"

    # When using libx264 (CPU), pass CRF + preset. For GPU encoders
    # (h264_nvenc / h264_vaapi / h264_videotoolbox) the GPU-specific params
    # from resolve_encoder() replace crf/preset — those flags are not valid
    # for hardware encoders and would be silently ignored or error out.
    if gpu_codec == "libx264":
        video_ffmpeg_params = ["-crf", str(crf), "-preset", str(preset)]
    else:
        video_ffmpeg_params = list(gpu_params)
    # NOTE: do NOT include +faststart here — we apply it deterministically
    # during the second-pass ffmpeg mux below, which is more reliable than
    # bundling it into MoviePy's subprocess invocation.
    video_write_kwargs = dict(
        fps=ctx.metadata.get("render_fps", 24),
        codec=gpu_codec,
        audio=False,  # ← key: defer audio mux to step 2
        threads=ctx.metadata.get("render_threads", 4),
        logger=_RenderProgressLogger(),
        ffmpeg_params=video_ffmpeg_params,
    )
    try:
        try:
            final_video.write_videofile(str(video_only_path), **video_write_kwargs)
        except Exception as gpu_err:
            if gpu_codec != "libx264":
                # v0.7.0: GPU encoding failed (no hardware, driver issue,
                # unsupported option, etc.) — retry with CPU libx264 so the
                # pipeline degrades gracefully instead of aborting.
                ctx.services.console.inline_warn(
                    f"GPU encoding ({gpu_codec}) failed: {gpu_err}. "
                    f"Retrying with libx264 (CPU)."
                )
                logger.debug("GPU encoding failed, falling back to CPU", exc_info=True)
                gpu_codec = "libx264"
                video_write_kwargs["codec"] = "libx264"
                video_write_kwargs["ffmpeg_params"] = ["-crf", str(crf), "-preset", str(preset)]
                final_video.write_videofile(str(video_only_path), **video_write_kwargs)
            else:
                raise
    finally:
        # Exception-safe cleanup: each close is guarded so one failure
        # doesn't prevent the remaining resources from being released.
        # NOTE: source must NOT be closed before write_videofile — MoviePy 2.x
        # subclipped() clips share the parent reader, so closing source early
        # would crash during encoding.
        #
        # v0.7.0: ``clips`` was deleted before encoding to reduce peak memory.
        # MoviePy 2.1.x CompositeVideoClip.close() only closes its bg/audio —
        # it does NOT cascade to the child clips — so we recover them via
        # ``final_video.clips`` for explicit cleanup. ``list(...)`` is safe for
        # both the real CompositeVideoClip (returns the clip list) and test
        # mocks (MagicMock.__iter__ yields an empty sequence).
        try:
            child_clips = list(final_video.clips) if final_video is not None else []
        except (AttributeError, TypeError):
            child_clips = []
        for obj in (final_video, audio_clip, source, *child_clips):
            if obj is not None:
                try:
                    obj.close()
                except Exception:  # noqa: BLE001
                    logger.debug("resource close failed for %s", obj, exc_info=True)
        # `final_video` already closed above; slice the audio so we can
        # write the final mux without keeping the original AudioFileClip alive.
        del audio_clip

    # STAGE 2: deterministic audio mux via ffmpeg. ffmpeg is significantly
    # more robust than MoviePy for muxing (it's what MoviePy ultimately
    # shells out to internally) and lets us apply +faststart atomically
    # alongside the mux.
    if shutil.which("ffmpeg") is None:  # pragma: no cover - ffmpeg is required
        raise RuntimeError(
            "ffmpeg binary not found on PATH — required for production-quality "
            "mux. Install ffmpeg (https://ffmpeg.org/download.html) and retry."
        )

    mux_cmd = [
        shutil.which("ffmpeg"),
        "-y",
        "-loglevel", "error",
        "-i", str(video_only_path),
        "-i", str(audio_path),
        "-map", "0:v:0",
        "-map", "1:a:0",
        "-c:v", "copy",
        "-c:a", audio_codec if not audio_codec.startswith("lib") else audio_codec[3:],
    ]
    if faststart:
        mux_cmd += ["-movflags", "+faststart"]
    mux_cmd.append(str(video_path))

    try:
        with step_timing(ctx.services.console, "ffmpeg_mux"):
            proc = subprocess.run(
                mux_cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=ctx.metadata.get("render_ffmpeg_timeout", _DEFAULT_MUX_TIMEOUT),
            )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg mux failed (exit={proc.returncode}): {proc.stderr}"
            )
    finally:
        # RS-09: Clean up the .tmp directory (video_only.mp4 and any
        # other intermediates) to keep the output dir tidy.
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass

    # v0.7.0: Record which encoder was actually used (requested vs detected
    # vs active) so renders are reproducible/auditable. Stored before
    # build_metadata_json so it is included in metadata.json.
    ctx.metadata["encoder_info"] = get_encoder_info(render_encoder_hint)

    metadata = build_metadata_json(ctx)
    with open(output_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    if not keep_cache:
        cache_dir = output_dir / "cache"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)

    ctx.video_path = str(video_path)

    # ── WP4: footage coverage (warn-only gate) ───────────
    # Calculate what fraction of narration segments have real footage
    # (vs text-only fallback). This catches the failure mode where
    # detect_scenes found 0 scenes or match_clips produced no usable
    # matches — the final video would be all text cards.
    #
    # NOTE: This is a WARN-ONLY gate, not an abort gate. The video is
    # already rendered by this point — we can only flag the issue in
    # metadata and _degraded_steps. To enforce footage coverage as a
    # hard requirement, check metadata.footage_coverage.ratio in a
    # post-pipeline script or use --strict with custom logic.
    total_segments = len(ctx.timed_segments)
    footage_segments_count = len(footage_segments)
    coverage_ratio = (
        footage_segments_count / total_segments if total_segments > 0 else 0.0
    )
    ctx.metadata["footage_coverage"] = {
        "total_segments": total_segments,
        "footage_segments": footage_segments_count,
        "text_only_segments": total_segments - footage_segments_count,
        "ratio": round(coverage_ratio, 4),
    }

    # Gate: if render_require_footage is True and coverage is too low,
    # warn but don't fail (the video is still produced, just flagged).
    require_footage = ctx.metadata.get("render_require_footage", False)
    min_coverage = ctx.metadata.get("render_min_footage_coverage", 0.5)
    if require_footage and coverage_ratio < min_coverage:
        ctx.services.console.inline_warn(
            f"Footage coverage {coverage_ratio:.0%} < required {min_coverage:.0%} "
            f"({footage_segments_count}/{total_segments} segments have footage). "
            f"Final video may be mostly text-only."
        )
        ctx.metadata.setdefault("_degraded_steps", [])
        if "render_video" not in ctx.metadata["_degraded_steps"]:
            ctx.metadata["_degraded_steps"].append("render_video")

    # ── WP5: duration metrics ────────────────────────────
    target_duration = ctx.metadata.get("duration")
    actual_duration = total_duration
    if target_duration:
        duration_ratio = actual_duration / target_duration
        ctx.metadata["duration_metrics"] = {
            "target_sec": target_duration,
            "actual_sec": round(actual_duration, 2),
            "ratio": round(duration_ratio, 4),
        }

    # ── EP5 V6: cover.jpg export ─────────────────────────
    # Export a cover image from the highest-score matched frame,
    # with movie name overlay. Controlled by render_cover_export param.
    # Failures are non-fatal — cover.jpg is a bonus artifact.
    cover_export = ctx.metadata.get("render_cover_export", False)
    if cover_export:
        _export_cover_image(ctx, usable_clips, output_dir)

    return ctx
