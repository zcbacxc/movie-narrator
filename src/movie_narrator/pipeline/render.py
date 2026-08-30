# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Video rendering step — compose the final video."""

import json
import logging
import os
import shutil
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

from moviepy import AudioFileClip, ColorClip, CompositeVideoClip, ImageClip, VideoFileClip
from PIL import Image, ImageDraw
from proglog import TqdmProgressBarLogger

from ..models import Context, MatchedClip, TimedSegment
from ..utils.console import step_timing
from ..utils.ffmpeg_bin import ffmpeg_bin
from ..utils.gpu_detect import (
    REASON_10BIT_GPU_UNSUPPORTED,
    REASON_GPU_RUNTIME_FALLBACK,
    get_encoder_info,
    resolve_encoder,
)
from ..utils.metadata_export import build_metadata_json
from ..utils.process import terminate_processes_matching
from ..utils.resources import check_render_admission, env_flag_enabled
from ..utils.text_image import create_text_image as _create_text_image
from ..utils.video_layout import compute_fit_box
from ..utils.transitions import apply_transition, get_transition_duration
from ..utils.text_anim import apply_text_animation, get_animation_duration
from .bgm import ensure_final_audio

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """Read an integer env var, falling back to *default* on any problem."""
    try:
        return int(os.environ.get(name, "").strip() or default)
    except ValueError:
        return default


# Minimum segment duration floor for speed scaling.
# Prevents division-by-zero when seg_duration is extremely short
# (e.g. 0-length segment from alignment glitch). 0.1s is intentional:
# below this, speed scaling produces visually absurd fast-forward.
_SEG_DURATION_FLOOR = 0.1

# Default ffmpeg mux timeout (seconds) when render_ffmpeg_timeout
# is not specified in job params. 10 min is generous for 4K + slow preset.
_DEFAULT_MUX_TIMEOUT = 600

# Default deadline (seconds) for the blocking MoviePy main encode when
# render_main_encode_timeout is not specified. 30 min covers slow CPU presets
# + long-form output; on expiry the ffmpeg worker tree is killed and the step
# fails with a TimeoutError instead of blocking forever.
_DEFAULT_MAIN_ENCODE_TIMEOUT = 1800.0

# Vertical (9:16) safe area defaults.
# On vertical video, platform UI (TikTok/Douyin caption area, like/share
# buttons) can cover the bottom 20-25% of the screen. These conservative
# ratios push subtitles above the danger zone.
_VERTICAL_BOTTOM_MARGIN_RATIO = 0.15  # vs 0.08 default for 16:9
_VERTICAL_MAX_WIDTH_RATIO = 0.82  # vs 0.90 default for 16:9

# v1.4.1: subtitle delivery modes (metadata["subtitle_delivery"]).
#   "burned"  = hard-burn SRT overlay into the frames (historical default);
#   "sidecar" = no burn-in, the SRT sidecar files are the delivery;
#   "muxed"   = no burn-in, the SRT is muxed as a soft mov_text track into
#               the mp4 during the final ffmpeg pass.
_SUBTITLE_DELIVERY_MODES = frozenset({"burned", "sidecar", "muxed"})

# mov_text is an MP4-family subtitle codec; other containers (mkv...) use
# text codecs we do not mux, so muxed degrades to burned for them.
_MP4_FAMILY_FORMATS = frozenset({"mp4", "m4v", "mov"})

# ── v1.5.0: pixel pipeline (bit depth + color metadata) ────────────
# SDR outputs are tagged explicitly (bt709) so every deliverable carries
# the same color interpretation ffmpeg previously applied implicitly;
# hdr10 uses BT.2020 primaries + SMPTE ST 2084 (PQ) transfer + BT.2020
# NC matrix. Mastering-display / MaxCLL/MaxFALL SEI injection is out of
# scope for v1.5.0 (see docs/ADR.md ADR-017).
#
# The tags are written during the STAGE-2 copy mux — NOT the MoviePy
# encode: with libx264 the encode-level ``-color_primaries`` /
# ``-color_trc`` options are parsed but silently dropped (only
# ``-colorspace`` reaches the H.264 VUI — verified on ffmpeg 7.1/8.1),
# while stream-copy output options land in the container for every
# encoder, GPU backends included. Note BT.2020 primaries are spelled
# ``bt2020``: ffmpeg has no ``bt2020nc`` primaries constant (the
# non-constant-luminance distinction lives in the colorspace only).
_SDR_COLOR_TAGS = {"color_primaries": "bt709", "color_trc": "bt709", "colorspace": "bt709"}
_HDR10_COLOR_TAGS = {
    "color_primaries": "bt2020",
    "color_trc": "smpte2084",
    "colorspace": "bt2020nc",
}
_PIX_FMT_8BIT = "yuv420p"
_PIX_FMT_10BIT = "yuv420p10le"

# ISO 639-1 → ISO 639-2 for the languages this project documents (voice
# map + subtitle translation targets). ffmpeg's mp4 muxer writes the
# ``language`` tag verbatim and only 3-letter codes survive (verified on
# the imageio-ffmpeg 7.1 build); 2-letter tags are normalized here and
# unknown values pass through unchanged (best-effort metadata).
_ISO_639_1_TO_2 = {
    "zh": "zho",
    "en": "eng",
    "ja": "jpn",
    "ko": "kor",
    "de": "deu",
    "fr": "fra",
    "es": "spa",
    "pt": "por",
    "ru": "rus",
    "it": "ita",
    "th": "tha",
    "vi": "vie",
    "ar": "ara",
    "hi": "hin",
    "id": "ind",
    "ms": "msa",
    "tr": "tur",
    "nl": "nld",
    "pl": "pol",
    "sv": "swe",
    "uk": "ukr",
}


def _mux_subtitle_language(ctx: Context) -> str:
    """Language tag for the muxed soft subtitle track (v1.4.1).

    Mirrors the render-track selection semantics: translated / bilingual
    tracks carry the translation target language (``subtitle_lang``);
    the original track carries the narration language (``lang``,
    default ``zh``). 2-letter ISO 639-1 tags (optionally BCP-47 with a
    region suffix, e.g. ``zh-TW``) are normalized to their 3-letter
    ISO 639-2 form because ffmpeg's mp4 muxer drops 2-letter tags.
    """
    mode = ctx.metadata.get("subtitle_mode", "original")
    if mode in ("translated", "bilingual") and ctx.metadata.get("subtitle_lang"):
        lang = str(ctx.metadata["subtitle_lang"])
    else:
        lang = str(ctx.metadata.get("lang") or "zh")
    base = lang.split("-")[0].lower()
    return _ISO_639_1_TO_2.get(base, lang)


def _resolve_subtitle_delivery(ctx: Context, output_name: str) -> tuple[str, str | None]:
    """Resolve the effective subtitle delivery mode (v1.4.1).

    Args:
        ctx: Pipeline context. ``metadata["subtitle_delivery"]`` carries
            the request (absent = ``"burned"``).
        output_name: Final output filename — its suffix decides whether a
            ``muxed`` mov_text track is representable in the container.

    Returns:
        ``(effective_mode, fallback_reason)``. ``fallback_reason`` is
        ``None`` unless a requested ``muxed`` degraded to ``burned``
        (``"missing_srt"``, ``"non_mp4_container"`` or
        ``"invalid_mode"``). Muxed requests never fail the render — they
        degrade to the historical burned behaviour and the reason is
        recorded in metadata for auditability.
    """
    requested = ctx.metadata.get("subtitle_delivery") or "burned"
    if requested not in _SUBTITLE_DELIVERY_MODES:
        # JobParams validates the job.yaml surface; direct metadata
        # injection (plugins / cloud worker) is normalized defensively.
        ctx.services.console.inline_warn(
            f"Unknown subtitle_delivery {requested!r} — falling back to 'burned'."
        )
        return "burned", "invalid_mode"
    if requested != "muxed":
        return requested, None

    # muxed requires an mp4-family container (mov_text is an MP4 codec).
    target_format = Path(output_name).suffix.lstrip(".").lower()
    if target_format not in _MP4_FAMILY_FORMATS:
        return "burned", "non_mp4_container"

    # The muxed SRT is the same mode-selected track the burn path would
    # have rendered (ctx.render_subtitle_path, set by generate_subtitle).
    srt = ctx.render_subtitle_path
    if not srt or not Path(srt).is_file():
        return "burned", "missing_srt"
    return "muxed", None


def _build_mux_cmd(
    ffmpeg: str,
    video_only_path: str | Path,
    audio_path: str | Path,
    partial_path: str | Path,
    *,
    audio_codec: str,
    faststart: bool,
    target_format: str,
    subtitle_srt: str | None = None,
    subtitle_language: str | None = None,
    color_args: list | None = None,
) -> list:
    """Build the STAGE-2 ffmpeg mux argv (v1.4.1 helper for testability).

    Input order is fixed: 0 = video-only stream, 1 = narration audio,
    2 = optional soft subtitle SRT. The SRT is mapped ``2:s:0``, encoded
    as ``mov_text`` and tagged with a per-stream ``language`` metadata
    (mp4-family containers only — callers degrade muxed to burned
    otherwise via :func:`_resolve_subtitle_delivery`).

    v1.5.0: ``color_args`` — explicit color metadata
    (``-color_primaries/-color_trc/-colorspace``), appended directly
    after ``-c:v copy``. Because the video is stream-copied (no encoder
    involved), the options are applied to the output stream verbatim —
    this is the one place the v1.5.0 color tags reliably land (encode-
    level color options are dropped by libx264, see the module comment).
    ``None`` (default) keeps the v1.4.1 argv shape.
    """
    cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(video_only_path),
        "-i",
        str(audio_path),
    ]
    if subtitle_srt is not None:
        cmd += ["-i", str(subtitle_srt)]
    cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    if subtitle_srt is not None:
        cmd += ["-map", "2:s:0"]
    cmd += [
        "-c:v",
        "copy",
        "-c:a",
        audio_codec if not audio_codec.startswith("lib") else audio_codec[3:],
    ]
    if color_args:
        cmd += list(color_args)
    if subtitle_srt is not None:
        cmd += ["-c:s", "mov_text"]
        if subtitle_language:
            cmd += ["-metadata:s:s:0", f"language={subtitle_language}"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    if target_format:
        cmd += ["-f", target_format]
    cmd.append(str(partial_path))
    return cmd


class _RenderProgressLogger(TqdmProgressBarLogger):
    """MoviePy progress logger with readable bar descriptions.

    Replaces the cryptic ``t:`` prefix (from ``iter_bar(t=...)``) with
    ``Rendering:`` so the progress bar is self-explanatory.
    """

    _BAR_LABELS = {
        "t": "Rendering",
    }

    def bars_callback(self, bar, attr, value, old_value):
        """Callback for progress bar updates during rendering."""
        # Rename bar title before tqdm creates the bar (first callback only)
        if bar in self.bars and self.bars[bar]["title"] == bar:
            self.bars[bar]["title"] = self._BAR_LABELS.get(bar, bar)
        super().bars_callback(bar, attr, value, old_value)


def _get_video_sizes(ctx: Context) -> dict:
    """
    Returns:
        Video_sizes dict from job params (ctx.metadata) with defaults fallback.

        The metadata value (from YAML) is already a dict; ``{"16:9": (1920, 1080), "9:16": (1080, 1920)}``
        is also a dict — no JSON parsing needed.
    """
    raw = ctx.metadata.get("video_sizes", {"16:9": (1920, 1080), "9:16": (1080, 1920)})
    return {k: tuple(v) for k, v in raw.items()}


def _resolve_pixel_plan(requested_bit_depth: int, color_space: str) -> dict:
    """Resolve the v1.5.0 pixel pipeline plan (pure helper, unit-tested).

    Args:
        requested_bit_depth: Requested bit depth (``render_bit_depth``,
            8 or 10; other values normalize defensively to 8).
        color_space: Requested color space (``render_color_space``,
            ``"sdr"`` or ``"hdr10"``; other values normalize defensively
            to ``"sdr"``).

    Returns:
        A plan dict:

        - ``bit_depth``: effective bit depth — ``hdr10`` forces 10-bit
          (recorded in ``forced_note``, never rejected — simpler UX);
        - ``forced_note``: human-readable note when hdr10 forced the
          depth up, else ``None``;
        - ``color_space``: the (normalized) color space;
        - ``pix_fmt``: the expected output pixel format;
        - ``color_tags``: ``{"color_primaries", "color_trc", "colorspace"}``
          as written to the output;
        - ``pixel_args``: encode-level ffmpeg args — the 10-bit
          ``-pix_fmt``/``-profile:v high10`` pair, appended to the
          encoder base (empty for 8-bit so the historical argv stays
          byte-identical);
        - ``color_args``: mux-level ffmpeg args — the explicit color tags,
          applied during the STAGE-2 copy mux (see the module comment for
          why the tags live at the mux, not the encode).
    """
    try:
        depth = int(requested_bit_depth)
    except (TypeError, ValueError):
        depth = 8
    if depth not in (8, 10):
        depth = 8
    cs = color_space if color_space in ("sdr", "hdr10") else "sdr"

    forced_note = None
    if cs == "hdr10" and depth != 10:
        depth = 10
        forced_note = "hdr10 requires 10-bit — render_bit_depth auto-forced from 8 to 10"

    if depth == 10:
        pix_fmt = _PIX_FMT_10BIT
        pixel_args = ["-pix_fmt", _PIX_FMT_10BIT, "-profile:v", "high10"]
    else:
        pix_fmt = _PIX_FMT_8BIT
        pixel_args = []

    tags = dict(_HDR10_COLOR_TAGS if cs == "hdr10" else _SDR_COLOR_TAGS)
    color_args = [
        "-color_primaries",
        tags["color_primaries"],
        "-color_trc",
        tags["color_trc"],
        "-colorspace",
        tags["colorspace"],
    ]
    return {
        "bit_depth": depth,
        "forced_note": forced_note,
        "color_space": cs,
        "pix_fmt": pix_fmt,
        "color_tags": tags,
        "pixel_args": pixel_args,
        "color_args": color_args,
    }


def _overlay_text(ctx: Context, idx: int, seg: TimedSegment) -> str:
    """Pick the overlay text for a narration segment per `subtitle_mode`.

    Safe accessor (spec §7.3): never IndexError if `translated_texts`
    is shorter than `timed_segments` — falls back to the original.
    """
    mode = ctx.metadata.get("subtitle_mode", "original")
    t = ctx.translated_texts[idx] if idx < len(ctx.translated_texts) else None
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
    """Export cover.jpg from the highest-score matched frame.

    Extracts the midpoint frame of the highest-score MatchedClip using
    ffmpeg, then overlays the movie name with a semi-transparent gradient
    using PIL. The result is saved as ``cover.jpg`` in the output dir.

    Failures are non-fatal (warn-only) — cover.jpg is a bonus artifact,
    not a pipeline requirement.
    """
    if not usable_clips or not ctx.source_video_path:
        ctx.services.console.debug("  cover: no usable clips or source video — skipping")
        return

    # Find the highest-score clip (embedding source preferred)
    scored = [mc for mc in usable_clips if mc.score is not None and mc.score > 0]
    if not scored:
        ctx.services.console.debug("  cover: no scored clips — skipping")
        return

    best = max(scored, key=lambda mc: mc.score)
    mid_ts = (best.src_start + best.src_end) / 2.0

    cover_raw = output_dir / "_cover_raw.jpg"
    cover_final = output_dir / "cover.jpg"

    # Extract frame via ffmpeg
    ffmpeg = ffmpeg_bin()
    if not ffmpeg or ffmpeg == "ffmpeg":
        ctx.services.console.debug("  cover: ffmpeg not found — skipping")
        return

    extract_cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-ss",
        f"{mid_ts:.2f}",
        "-i",
        str(ctx.source_video_path),
        "-frames:v",
        "1",
        "-q:v",
        "2",
        str(cover_raw),
    ]
    try:
        proc = subprocess.run(
            extract_cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if proc.returncode != 0 or not cover_raw.exists():
            ctx.services.console.debug(f"  cover: ffmpeg extract failed: {proc.stderr[:200]}")
            return
    except (OSError, subprocess.SubprocessError) as e:
        ctx.services.console.debug(f"  cover: extract error: {e}")
        logger.debug("cover: ffmpeg extract failed", exc_info=True)
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
            f"  cover: exported cover.jpg from segment {best.segment_index} "
            f"(score={best.score:.3f}, ts={mid_ts:.1f}s)"
        )
    except (OSError, ValueError) as e:
        ctx.services.console.debug(f"  cover: overlay error: {e}")
        logger.debug("cover: overlay failed", exc_info=True)
    finally:
        # Clean up raw frame
        cover_raw.unlink(missing_ok=True)


def _substitute_movie(text: str, movie_name: str) -> str:
    """Replace the ``{movie}`` placeholder with the actual movie name.

    Returns:
        The original text unchanged when the placeholder is absent.
    """
    if not text:
        return text
    return text.replace("{movie}", movie_name or "")


def _create_watermark_image(text: str, size: tuple, fontsize: int = 36):
    """Create a full-canvas transparent image with small
    semi-transparent text anchored to the top-right corner.

    Returns:
        A ``numpy.ndarray`` (RGBA) suitable for ``ImageClip``.
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
        (x, y),
        text,
        fill=(255, 255, 255, 140),
        font=font,
        stroke_width=1,
        stroke_fill=(0, 0, 0, 120),
    )
    return np.array(img)


def _write_videofile_with_deadline(
    final_video,
    video_only_path: Path,
    video_write_kwargs: dict,
    timeout: float,
) -> None:
    """Run MoviePy ``write_videofile`` with a wall-clock deadline.

    MoviePy's ``write_videofile`` blocks without a deadline and launches
    ffmpeg outside our direct control. To bound it, the encode runs in a
    background thread while the caller ``join``s with ``timeout``. On expiry
    the ffmpeg process tree writing to ``video_only_path`` is terminated and
    a :class:`TimeoutError` is raised.

    Args:
        final_video: The composite clip to encode.
        video_only_path: Path MoviePy writes to; used to identify the runaway
            ffmpeg worker via its command line.
        video_write_kwargs: Keyword args forwarded to ``write_videofile``.
        timeout: Deadline in seconds. Values ``<= 0`` (or ``None``) disable
            the deadline and call ``write_videofile`` synchronously.

    Raises:
        TimeoutError: If encoding exceeds ``timeout`` (the ffmpeg worker has
            been terminated).
        Exception: Any exception raised by ``write_videofile`` is re-raised
            unchanged on the calling thread, preserving codec-failure
            semantics for the GPU→CPU fallback.
    """
    if timeout is None or timeout <= 0:
        final_video.write_videofile(str(video_only_path), **video_write_kwargs)
        return

    result: dict = {}

    def _encode() -> None:
        try:
            final_video.write_videofile(str(video_only_path), **video_write_kwargs)
        except BaseException as exc:  # noqa: BLE001 — re-raise on the calling thread
            result["exc"] = exc

    worker = threading.Thread(target=_encode, name="moviepy-main-encode", daemon=True)
    worker.start()
    worker.join(timeout)
    if worker.is_alive():
        logger.error(
            "main encode exceeded %.1fs deadline — terminating ffmpeg writing to %s",
            timeout,
            video_only_path,
        )
        terminate_processes_matching(str(video_only_path))
        # Give the worker a moment to observe EOF/termination; it is a daemon
        # thread, so it will not block process shutdown either way.
        worker.join(10.0)
        raise TimeoutError(
            f"Main video encode exceeded {timeout:.0f}s deadline "
            f"(output={video_only_path}); runaway ffmpeg worker terminated."
        )
    if "exc" in result:
        raise result["exc"]


def render_video(ctx: Context) -> Context:
    """Render the final narrated video.

    Args:
        ctx: Pipeline execution context.

    Returns:
        Updated pipeline context with rendered output.
    """
    # ── v1.5.0: pixel pipeline plan (bit depth + color metadata) ──
    # Resolved once, up front: the admission heuristic needs the bit
    # depth (10-bit ≈ ×1.25 temp space) and the encode below needs the
    # pix_fmt/profile/color args. hdr10 forces the bit depth to 10 with
    # a recorded note (JobParams never rejects the 8-bit + hdr10 combo).
    pixel_plan = _resolve_pixel_plan(
        ctx.metadata.get("render_bit_depth", 8),
        ctx.metadata.get("render_color_space", "sdr"),
    )
    if pixel_plan["forced_note"]:
        ctx.services.console.info(
            "  render_color_space=hdr10 — forcing 10-bit encode "
            f"({pixel_plan['pix_fmt']})"
        )

    # v1.3.2: resource-aware admission preflight (opt-in via
    # MN_ADMISSION_DISK_CHECK; default off = zero behavior change).
    # Aborts before any heavy work when the temp volume cannot hold the
    # estimated render intermediates (see utils/resources.py).
    if env_flag_enabled("MN_ADMISSION_DISK_CHECK"):
        _admission = check_render_admission(
            resolution=_get_video_sizes(ctx).get(
                ctx.metadata.get("video_format", "16:9"), (1920, 1080)
            ),
            duration_estimate_s=float(ctx.duration),
            temp_dir=Path(ctx.output_dir) / "cache",
            min_free_disk_bytes=_env_int("MN_MIN_FREE_DISK_BYTES", 0),
            cpu_count=os.cpu_count(),
            bit_depth=pixel_plan["bit_depth"],
        )
        if not _admission.ok:
            raise RuntimeError(f"Render admission check failed: {'; '.join(_admission.reasons)}")

    # Safety net: ensure final audio is normalized even if mix_bgm
    # was skipped or failed. This guarantees render never receives raw
    # unnormalized narration when bgm_normalize=True.
    ensure_final_audio(ctx)

    output_dir = Path(ctx.output_dir)
    video_format = ctx.metadata.get("video_format", "16:9")
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
        ctx.timed_segments = truncate_segments_for_preview(ctx.timed_segments, total_duration)

    # ── v1.4.1: subtitle delivery (burned | sidecar | muxed) ──
    # Resolve the effective mode BEFORE clip assembly so the burn-in
    # overlay can be skipped for sidecar/muxed without touching the
    # encode path. Degraded muxed requests fall back to the historical
    # burned behaviour (never fail the render); the effective mode and
    # any fallback reason are recorded in metadata for auditability.
    default_output_name = "preview.mp4" if preview_mode else "final.mp4"
    output_name = ctx.metadata.get("render_output_name", default_output_name)
    subtitle_delivery, subtitle_fallback_reason = _resolve_subtitle_delivery(ctx, str(output_name))
    ctx.metadata["subtitle_delivery_used"] = subtitle_delivery
    mux_subtitle_lang: str | None = None
    if subtitle_delivery == "muxed":
        mux_subtitle_lang = _mux_subtitle_language(ctx)
        ctx.metadata["subtitle_mux_language"] = mux_subtitle_lang
    if subtitle_fallback_reason is not None:
        ctx.metadata["subtitle_delivery_fallback_reason"] = subtitle_fallback_reason
        ctx.services.console.inline_warn(
            f"subtitle_delivery=muxed unavailable ({subtitle_fallback_reason}) — "
            f"falling back to burned subtitles."
        )
        logger.warning(
            "SubtitleDeliveryFallback",
            extra={
                "event": "subtitle_delivery_fallback",
                "task_id": ctx.metadata.get("run_id"),
                "requested": "muxed",
                "effective": subtitle_delivery,
                "reason": subtitle_fallback_reason,
            },
        )
    elif subtitle_delivery == "sidecar":
        ctx.services.console.info(
            "  Subtitle delivery: sidecar — SRT files only, skipping burn-in overlay"
        )
    elif subtitle_delivery == "muxed":
        ctx.services.console.info(
            f"  Subtitle delivery: muxed — soft mov_text track "
            f"(language={mux_subtitle_lang}), skipping burn-in overlay"
        )

    # Production-quality render knobs (spec §7.2).
    fit_mode = ctx.metadata.get("render_fit_mode", "cover")
    subtitle_position = ctx.metadata.get("render_subtitle_position", "bottom")
    max_width_ratio = ctx.metadata.get("render_subtitle_max_width_ratio", 0.9)
    bottom_margin_ratio = ctx.metadata.get("render_subtitle_bottom_margin_ratio", 0.08)

    # Render template (read once, reused for title/end cards, watermark,
    # disclaimer, and aspect_safe_area).  Falls back to {} when absent
    # so existing behaviour is unchanged (render_template is optional).
    render_template = ctx.metadata.get("render_template") or {}
    aspect_safe_area = render_template.get("aspect_safe_area") or {}

    # Vertical (9:16) safe area auto-adjustment.
    # Platform UI on vertical video (TikTok/Douyin caption area, like/share
    # buttons) covers the bottom 20-25% of the screen. When enabled,
    # push subtitles higher and narrow them so they stay visible.
    # When the render_template provides ``aspect_safe_area`` ratios, those
    # values replace the hardcoded defaults so each preset can tune the
    # safe area for its target platform.
    vertical_safe = ctx.metadata.get("render_vertical_safe_area", True)
    if vertical_safe and video_format == "9:16":
        safe_max_width = aspect_safe_area.get("max_width_ratio", _VERTICAL_MAX_WIDTH_RATIO)
        safe_bottom_margin = aspect_safe_area.get(
            "bottom_margin_ratio", _VERTICAL_BOTTOM_MARGIN_RATIO
        )
        max_width_ratio = min(max_width_ratio, safe_max_width)
        bottom_margin_ratio = max(bottom_margin_ratio, safe_bottom_margin)
        ctx.services.console.debug(
            f"  vertical safe area: max_width={max_width_ratio:.2f} "
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
        except (OSError, RuntimeError) as e:
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
                        subclip = subclip.with_speed_scaled(
                            factor=src_duration / max(seg_duration, _SEG_DURATION_FLOOR)
                        )

                    # Fit source frame onto the canvas (cover=crop+fill,
                    # contain=letterbox+center). Keeps footage from overflowing
                    # or distorting the output resolution.
                    box = compute_fit_box(
                        (subclip.w, subclip.h),
                        size,
                        mode=fit_mode,
                    )
                    if fit_mode == "cover":
                        fitted = subclip.cropped(
                            x1=box.crop_x,
                            y1=box.crop_y,
                            x2=box.crop_x + box.crop_w,
                            y2=box.crop_y + box.crop_h,
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
                            ctx.metadata.get("render_transition_duration", 0.5),
                        )
                        fitted = apply_transition(fitted, transition_type, trans_dur)

                    clips.append(fitted.with_start(mc.narr_start))
                except (ValueError, RuntimeError) as ie:
                    ctx.services.console.debug(f"  fallback for segment {mc.segment_index}: {ie}")
                    logger.debug("clip fallback for segment %d", mc.segment_index, exc_info=True)
                    # v1.4.1: the fallback text card is part of the burn-in
                    # surface. sidecar/muxed deliver narration text via the
                    # SRT (sidecar file or soft track), so no text card is
                    # burned for segments whose footage failed to load.
                    if subtitle_delivery == "burned":
                        img_array = _create_text_image(
                            _overlay_text(
                                ctx, mc.segment_index, ctx.timed_segments[mc.segment_index]
                            ),
                            size,
                            fontsize=font_size,
                            position=subtitle_position,
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
    #
    # v1.4.1: burn-in happens ONLY for the default "burned" delivery.
    # sidecar/muxed skip the overlay entirely (faster render): the
    # narration text reaches the viewer via the SRT sidecar files or the
    # soft mov_text track muxed during the final ffmpeg pass.
    if subtitle_delivery == "burned":

        def _make_subtitle_image(i, seg, pos):
            img_array = _create_text_image(
                _overlay_text(ctx, i, seg),
                size,
                fontsize=font_size,
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
                    seg.end - seg.start, ctx.metadata.get("render_text_animation_duration", 0.3)
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

    # Title card overlay — show movie name at the beginning for a
    # polished opening. Uses a larger centered font with fade in/out.
    # Duration is controlled by render_title_card_sec (0 = disabled).
    #
    # If a render_template is provided with ``title_card_text``,
    # use it (with ``{movie}`` replaced by ctx.movie_name) instead of the
    # bare movie name.  Falls back to ctx.movie_name when no template is
    # present so existing behaviour is unchanged.
    # (render_template was read earlier for aspect_safe_area consumption.)
    title_card_sec = ctx.metadata.get("render_title_card_sec", 0)
    title_card_template = render_template.get("title_card_text")
    if title_card_template:
        title_card_text = _substitute_movie(title_card_template, ctx.movie_name)
    else:
        title_card_text = ctx.movie_name
    if title_card_sec and title_card_sec > 0 and title_card_text:
        title_font_size = int(font_size * 1.4)
        title_img = _create_text_image(
            title_card_text,
            size,
            fontsize=title_font_size,
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
        except (ImportError, ValueError):
            logger.debug("title card fade effect failed", exc_info=True)
        clips.append(title_clip)
        ctx.services.console.debug(f"  title card: {title_card_text} ({title_card_sec}s)")

    # End card overlay — show end card text at the end of the
    # video (similar to the title card but at the closing).  Soft addition:
    # skipped entirely when ``end_card_text`` is absent from the template.
    end_card_template = render_template.get("end_card_text")
    if end_card_template:
        end_card_text = _substitute_movie(end_card_template, ctx.movie_name)
        end_card_sec = title_card_sec if (title_card_sec and title_card_sec > 0) else 1.0
        end_font_size = int(font_size * 1.4)
        end_img = _create_text_image(
            end_card_text,
            size,
            fontsize=end_font_size,
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
        except (ImportError, ValueError):
            logger.debug("end card fade effect failed", exc_info=True)
        clips.append(end_clip)
        ctx.services.console.debug(f"  end card: {end_card_text} ({end_card_sec}s)")

    # Watermark overlay — small semi-transparent text in the
    # top-right corner, visible for the entire video duration.
    watermark_template = render_template.get("watermark_text")
    if watermark_template:
        watermark_text = _substitute_movie(watermark_template, ctx.movie_name)
        wm_img = _create_watermark_image(
            watermark_text,
            size,
            fontsize=max(24, int(font_size * 0.36)),
        )
        wm_clip = ImageClip(wm_img, is_mask=False)
        wm_clip = wm_clip.with_duration(total_duration).with_start(0)
        clips.append(wm_clip)
        ctx.services.console.debug(f"  watermark: {watermark_text}")

    # Disclaimer overlay — small text at the very bottom,
    # visible for the entire video duration.  Uses a smaller font and a
    # minimal bottom margin so it sits beneath the subtitle band.
    disclaimer_template = render_template.get("disclaimer_text")
    if disclaimer_template:
        disclaimer_text = _substitute_movie(disclaimer_template, ctx.movie_name)
        disc_img = _create_text_image(
            disclaimer_text,
            size,
            fontsize=max(20, int(font_size * 0.42)),
            position="bottom",
            max_width_ratio=0.9,
            bottom_margin_ratio=0.02,
        )
        disc_clip = ImageClip(disc_img, is_mask=False)
        disc_clip = disc_clip.with_duration(total_duration).with_start(0)
        clips.append(disc_clip)
        ctx.services.console.debug(f"  disclaimer: {disclaimer_text}")

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
    # (v1.4.1: ``output_name`` was resolved before clip assembly for the
    # subtitle-delivery decision — reused here unchanged.)
    video_path = output_dir / output_name

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

    # v1.2: wall-clock deadline for the blocking MoviePy main encode below.
    # A runaway ffmpeg would otherwise hold CPU/GPU indefinitely; on expiry
    # the worker process tree is terminated and the step fails fast.
    main_encode_timeout = cast(
        float,
        ctx.metadata.get("render_main_encode_timeout") or _DEFAULT_MAIN_ENCODE_TIMEOUT,
    )

    # ── Codec ownership (v1.2) ──────────────────────────────────────
    # The FINAL video's codec is decided entirely by ``render_encoder`` via
    # ``resolve_encoder`` below. The ``render_video_codec`` metadata key is
    # deliberately NOT consulted here — it is the clip-export-only encoder
    # used by ``export_clips``. These two knobs were historically conflated;
    # do not treat them as interchangeable.
    #
    # v0.7.0: GPU encoder resolution. ``render_encoder`` accepts "auto"
    # (default, probe + fall back to libx264), "cpu", or an explicit backend
    # ("nvenc" / "vaapi" / "videotoolbox"). ``resolve_encoder`` returns a
    # ``(codec, ffmpeg_params)`` tuple; the params are backend-specific and
    # replace the libx264-only ``-crf``/``-preset`` knobs when a GPU encoder
    # is active. See ..utils.gpu_detect for the probe + caching logic.
    render_encoder_hint = ctx.metadata.get("render_encoder")
    gpu_codec, gpu_params = resolve_encoder(render_encoder_hint)
    # v1.5.0 (ADR-017): 10-bit renders are CPU-only — the H.264 GPU
    # backends this pipeline supports (NVENC / VAAPI / VideoToolbox) are
    # 8-bit only, and v1.5.0 deliberately does NOT probe GPU 10-bit
    # capability (HEVC main10 is future work). When a GPU encoder
    # resolved, force libx264 and record why so ``encoder_info`` stays
    # truthful about the codec that actually encoded the output.
    tenbit_gpu_reason = None
    if pixel_plan["bit_depth"] == 10 and gpu_codec != "libx264":
        tenbit_gpu_reason = REASON_10BIT_GPU_UNSUPPORTED
        ctx.services.console.inline_warn(
            f"10-bit render requested — GPU encoder ({gpu_codec}) is 8-bit only; "
            "encoding with libx264 (CPU)."
        )
        gpu_codec, gpu_params = "libx264", []
    # v1.2.1: non-None when a runtime GPU→libx264 fallback fires below; used to
    # override metadata.json so the actual encoder + reason are truthful.
    runtime_fallback_reason = None

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
    # v1.5.0: 10-bit renders append -pix_fmt yuv420p10le + -profile:v high10
    # to the encoder base. For 8-bit jobs the plan's pixel args are empty,
    # so the encode argv stays byte-identical to v1.4.2 (the color tags of
    # the requested color space are applied at the STAGE-2 mux instead —
    # see _resolve_pixel_plan / the module-level comment).
    video_ffmpeg_params = video_ffmpeg_params + pixel_plan["pixel_args"]
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
            _write_videofile_with_deadline(
                final_video, video_only_path, video_write_kwargs, main_encode_timeout
            )
        except TimeoutError:
            # Deadline exceeded — the ffmpeg worker was already terminated.
            # This is NOT a codec failure, so never trigger the GPU→CPU
            # fallback (a hung GPU encode would just time out again).
            raise
        except (OSError, subprocess.SubprocessError, RuntimeError) as gpu_err:
            if gpu_codec != "libx264":
                # v0.7.0: GPU encoding failed (no hardware, driver issue,
                # unsupported option, etc.) — retry with CPU libx264 so the
                # pipeline degrades gracefully instead of aborting.
                ctx.services.console.inline_warn(
                    f"GPU encoding ({gpu_codec}) failed: {gpu_err}. Retrying with libx264 (CPU)."
                )
                # v1.2.1: record the runtime fallback so metadata.json reflects
                # the encoder actually used (see encoder_info below).
                runtime_fallback_reason = REASON_GPU_RUNTIME_FALLBACK
                logger.debug(
                    "GpuEncoderFallback",
                    extra={
                        "event": "encoder_fallback",
                        "task_id": ctx.metadata.get("run_id"),
                        "pid": os.getpid(),
                        "from_codec": gpu_codec,
                        "to_codec": "libx264",
                        "reason": REASON_GPU_RUNTIME_FALLBACK,
                        "error": str(gpu_err),
                    },
                )
                logger.debug("GPU encoding failed, falling back to CPU", exc_info=True)
                gpu_codec = "libx264"
                video_write_kwargs["codec"] = "libx264"
                # The runtime fallback can only fire for an 8-bit encode —
                # 10-bit requests never reach a GPU encoder (see the
                # ADR-017 override above) — and the v1.5.0 color tags live
                # at the mux, so the retry argv is the historical one.
                video_write_kwargs["ffmpeg_params"] = ["-crf", str(crf), "-preset", str(preset)]
                _write_videofile_with_deadline(
                    final_video, video_only_path, video_write_kwargs, main_encode_timeout
                )
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
    ffmpeg = ffmpeg_bin()
    if ffmpeg == "ffmpeg":  # pragma: no cover - ffmpeg is required
        raise RuntimeError(
            "ffmpeg binary unavailable — required for production-quality "
            "mux. Install ffmpeg (https://ffmpeg.org/download.html) and retry."
        )
    assert ffmpeg is not None

    # v1.2: write the muxed output to a ``.part`` sibling inside .tmp first,
    # then atomically ``os.replace`` it into place only after a successful,
    # non-empty result is validated. This prevents consumers (QA, publish,
    # export-clips) from ever observing a truncated ``final.mp4``.
    partial_path = tmp_dir / f"{video_path.name}.part"

    # ffmpeg cannot infer the container from the ``.part`` staging suffix, so
    # pass the format explicitly (derived from the final target extension).
    target_format = video_path.suffix.lstrip(".")
    # v1.4.1: muxed subtitle delivery — add the mode-selected SRT as a soft
    # mov_text track (input 2, mapped 2:s:0). Burned/sidecar keep the
    # historical two-input mux.
    # v1.5.0: the pixel plan's color tags are applied here, on the copy
    # mux, so every deliverable (any encoder, GPU included) carries the
    # requested color metadata.
    mux_cmd = _build_mux_cmd(
        ffmpeg,
        video_only_path,
        str(audio_path),
        partial_path,
        audio_codec=audio_codec,
        faststart=faststart,
        target_format=target_format,
        subtitle_srt=ctx.render_subtitle_path if subtitle_delivery == "muxed" else None,
        subtitle_language=mux_subtitle_lang,
        color_args=pixel_plan["color_args"],
    )

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
            raise RuntimeError(f"ffmpeg mux failed (exit={proc.returncode}): {proc.stderr}")
        # Validate a real, non-empty artifact before atomically publishing it.
        if not partial_path.exists() or partial_path.stat().st_size == 0:
            raise RuntimeError("ffmpeg mux produced no output — refusing to publish an empty file")
        os.replace(partial_path, video_path)
    finally:
        # Remove any leftover partial (it is already gone after a successful
        # replace) and clean the .tmp directory (video_only.mp4 + intermediates).
        partial_path.unlink(missing_ok=True)
        try:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        except OSError:
            pass

    # v0.7.0: Record which encoder was actually used (requested vs detected
    # vs active) so renders are reproducible/auditable. Stored before
    # build_metadata_json so it is included in metadata.json.
    encoder_info = get_encoder_info(render_encoder_hint)
    if runtime_fallback_reason is not None:
        # v1.2.1: a runtime GPU→libx264 fallback fired, so the encoder we ended
        # up using differs from the probe's detection — make it truthful.
        encoder_info["active"] = "libx264"
        encoder_info["fallback_reason"] = runtime_fallback_reason
    if tenbit_gpu_reason is not None:
        # v1.5.0: the ADR-017 CPU-only 10-bit policy forced libx264 over the
        # resolved GPU encoder — record the reason so the report reflects
        # why the detected GPU was not used.
        encoder_info["active"] = "libx264"
        encoder_info["fallback_reason"] = tenbit_gpu_reason
    ctx.metadata["encoder_info"] = encoder_info

    # v1.5.0: pixel pipeline report — effective bit depth, expected pix_fmt,
    # color space + tags, and the encoder path actually taken (the runtime
    # GPU→CPU fallback above can still demote a GPU encode to CPU).
    final_codec = video_write_kwargs["codec"]
    encoder_path = "cpu" if final_codec == "libx264" else "gpu"
    render_pixel_report = {
        "bit_depth": pixel_plan["bit_depth"],
        "pix_fmt": pixel_plan["pix_fmt"],
        "color_space": pixel_plan["color_space"],
        "color_tags": pixel_plan["color_tags"],
        "encoder_path": encoder_path,
    }
    if pixel_plan["forced_note"]:
        render_pixel_report["note"] = pixel_plan["forced_note"]
    ctx.metadata["render_pixel"] = render_pixel_report

    metadata = build_metadata_json(ctx)
    with open(output_dir / "metadata.json", "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    if not keep_cache:
        cache_dir = output_dir / "cache"
        if cache_dir.exists():
            shutil.rmtree(cache_dir)

    ctx.video_path = str(video_path)

    # ── Footage coverage (warn-only gate) ───────────
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
    coverage_ratio = footage_segments_count / total_segments if total_segments > 0 else 0.0
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

    # ── Duration metrics ────────────────────────────
    target_duration = ctx.metadata.get("duration")
    actual_duration = total_duration
    if target_duration:
        duration_ratio = actual_duration / target_duration
        ctx.metadata["duration_metrics"] = {
            "target_sec": target_duration,
            "actual_sec": round(actual_duration, 2),
            "ratio": round(duration_ratio, 4),
        }

    # ── Cover.jpg export ─────────────────────────
    # Export a cover image from the highest-score matched frame,
    # with movie name overlay. Controlled by render_cover_export param.
    # Failures are non-fatal — cover.jpg is a bonus artifact.
    cover_export = ctx.metadata.get("render_cover_export", False)
    if cover_export:
        _export_cover_image(ctx, usable_clips, output_dir)

    return ctx
