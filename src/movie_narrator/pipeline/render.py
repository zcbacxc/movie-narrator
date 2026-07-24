import json
import shutil
import subprocess
from pathlib import Path

from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip
from proglog import TqdmProgressBarLogger

from ..models import Context, MatchedClip, StepResult, TimedSegment
from ..utils.metadata_export import build_metadata_json
from ..utils.text_image import create_text_image as _create_text_image
from ..utils.video_layout import compute_fit_box
from .bgm import ensure_final_audio


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

    # EP5: Title card overlay — show movie name at the beginning for a
    # polished opening. Uses a larger centered font with fade in/out.
    # Duration is controlled by render_title_card_sec (0 = disabled).
    title_card_sec = ctx.metadata.get("render_title_card_sec", 0)
    if title_card_sec and title_card_sec > 0 and ctx.movie_name:
        title_font_size = int(font_size * 1.4)
        title_img = _create_text_image(
            ctx.movie_name, size, fontsize=title_font_size,
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
            pass  # no fade — title card still visible
        clips.append(title_clip)
        ctx.services.console.debug(
            f"  EP5 title card: {ctx.movie_name} ({title_card_sec}s)"
        )

    final_video = CompositeVideoClip(clips).with_audio(audio_clip)
    video_path = output_dir / ctx.metadata.get("render_output_name", "final.mp4")


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

    video_ffmpeg_params = ["-crf", str(crf), "-preset", str(preset)]
    # NOTE: do NOT include +faststart here — we apply it deterministically
    # during the second-pass ffmpeg mux below, which is more reliable than
    # bundling it into MoviePy's subprocess invocation.
    video_write_kwargs = dict(
        fps=ctx.metadata.get("render_fps", 24),
        codec=ctx.metadata.get("render_video_codec", "libx264"),
        audio=False,  # ← key: defer audio mux to step 2
        threads=ctx.metadata.get("render_threads", 4),
        logger=_RenderProgressLogger(),
        ffmpeg_params=video_ffmpeg_params,
    )
    try:
        final_video.write_videofile(str(video_only_path), **video_write_kwargs)
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
        proc = subprocess.run(
            mux_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,  # 10 min — generous for slow ffmpeg mux
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"ffmpeg mux failed (exit={proc.returncode}): {proc.stderr}"
            )
    finally:
        # Clean up the video-only intermediate to keep the output dir tidy.
        try:
            video_only_path.unlink(missing_ok=True)
        except OSError:
            pass

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

    return ctx
