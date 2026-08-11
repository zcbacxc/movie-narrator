# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scene visual feature extraction (G9 stage-1 skeleton).

Stage 1 is a pure-FFmpeg + PIL pipeline that extracts low-level visual
features (mean luma + RGB histogram) per scene. It deliberately does NOT
carry semantic information — it is a *pipeline skeleton* so a stage-2
semantic encoder (CLIP / Qwen3-VL) can be swapped in later through the
same interface.

Cost controls (per the G9 plan):
  - 480p width downscale (720p/1080p -> 480p) to reduce decode cost.
  - Optional low-fps sampling + static-frame skipping (adjacent frames whose
    L1 difference is below a threshold collapse to one representative frame).

All failures degrade gracefully to ``None`` so the match re-rank fallback
chain is never broken.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass
from typing import List, Optional

from ..models import Scene
from .ffmpeg_bin import ffmpeg_bin

logger = logging.getLogger(__name__)

# Default downscale width for frame extraction (480p).
_DEFAULT_WIDTH = 480

# Default static-frame skip threshold (normalized L1/dim difference).
_DEFAULT_STATIC_SKIP = 0.02

# Histogram bins per RGB channel (keeps the vector compact: 3*16 + 1 = 49 dims).
_DEFAULT_HIST_BINS = 16


def _ffprobe_fps(video_path: str) -> float:
    """Best-effort FPS probe; returns 0.0 when unavailable."""
    import shutil

    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 0.0
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=r_frame_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                video_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        num, _, den = proc.stdout.strip().partition("/")
        if den and float(den) != 0:
            return float(num) / float(den)
        return float(num) if num else 0.0
    except Exception:  # noqa: BLE001
        return 0.0


def _extract_frame_jpg(video_path: str, t: float, width: int) -> Optional[str]:
    """Extract a single frame at time ``t`` (480p) to a temp JPEG.

    Returns:
        The temp file path, or ``None`` on failure.
    """
    ffmpeg = ffmpeg_bin()
    tmp = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
    tmp.close()
    try:
        proc = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-ss",
                str(t),
                "-i",
                video_path,
                "-frames:v",
                "1",
                "-vf",
                f"scale={width}:-2",
                "-y",
                tmp.name,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        if proc.returncode != 0 or not os.path.getsize(tmp.name):
            os.unlink(tmp.name)
            return None
        return tmp.name
    except Exception:  # noqa: BLE001
        logger.debug("frame extraction failed at t=%s", t, exc_info=True)
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return None


def _frame_luma_hist(jpg_path: str, hist_bins: int) -> Optional[tuple[float, list[float]]]:
    """Compute (mean luma, normalized RGB histogram) from a JPEG.

    Returns:
        ``None`` when the image cannot be decoded.
    """
    try:
        from PIL import Image
    except Exception:  # noqa: BLE001
        return None
    try:
        img = Image.open(jpg_path).convert("RGB")
        # Mean luma (Rec. 601 weights).
        luma = 0.299 * sum(img.getchannel("R").getdata()) / (img.width * img.height)
        luma += 0.587 * sum(img.getchannel("G").getdata()) / (img.width * img.height)
        luma += 0.114 * sum(img.getchannel("B").getdata()) / (img.width * img.height)
        hist_rgb: list[float] = []
        for channel in ("R", "G", "B"):
            h = img.getchannel(channel).histogram()
            bins = [
                sum(h[i * 256 // hist_bins : (i + 1) * 256 // hist_bins])
                for i in range(hist_bins)
            ]
            total = sum(bins) or 1.0
            hist_rgb.extend(b / total for b in bins)
        return luma, hist_rgb
    except Exception:  # noqa: BLE001
        logger.debug("PIL feature extraction failed", exc_info=True)
        return None


@dataclass
class VisualFeature:
    """Serializable low-level visual feature for one scene."""

    scene_index: int
    luma: float
    hist_rgb: list[float]

    def to_dict(self) -> dict:
        """Return a JSON-safe dict."""
        return {
            "scene_index": self.scene_index,
            "luma": round(self.luma, 4),
            "hist_rgb": [round(x, 4) for x in self.hist_rgb],
        }


def visual_feature_vector(feature: VisualFeature) -> Optional[list[float]]:
    """Flatten a feature into a fixed-dim vector (luma + histogram).

    Returns:
        ``None`` for an empty histogram.
    """
    if not feature.hist_rgb:
        return None
    return [feature.luma] + list(feature.hist_rgb)


def _static_skip_collapse(
    video_path: str,
    start: float,
    end: float,
    *,
    width: int,
    hist_bins: int,
    fps: float,
    threshold: float,
) -> Optional[tuple[float, list[float]]]:
    """Sample N frames at low FPS and collapse near-identical ones.

    Adjacent frames whose normalized L1 difference is below ``threshold``
    are treated as static and skipped; the first frame of each run is kept
    and averaged. Requires a known FPS.
    """
    if fps <= 0:
        return None
    n_frames = max(1, int((end - start) * fps))
    if n_frames > 30:
        n_frames = 30
    kept: list[tuple[float, list[float]]] = []
    prev: Optional[tuple[float, list[float]]] = None
    for i in range(n_frames):
        t = start + (end - start) * (i + 0.5) / n_frames
        jpg = _extract_frame_jpg(video_path, t, width)
        if jpg is None:
            continue
        try:
            feat = _frame_luma_hist(jpg, hist_bins)
        finally:
            try:
                os.unlink(jpg)
            except OSError:
                pass
        if feat is None:
            continue
        if prev is not None and _l1_diff(prev[1], feat[1]) < threshold:
            continue  # static frame — skip
        kept.append(feat)
        prev = feat
    if not kept:
        return None
    n = float(len(kept))
    luma = sum(f[0] for f in kept) / n
    hist = [sum(f[1][i] for f in kept) / n for i in range(len(kept[0][1]))]
    return luma, hist


def _l1_diff(a: list[float], b: list[float]) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / (len(a) or 1)


def extract_scene_visual_features(
    video_path: str,
    scenes: List[Scene],
    *,
    width: int = _DEFAULT_WIDTH,
    fps: float = 0.0,
    static_skip_threshold: float = _DEFAULT_STATIC_SKIP,
    hist_bins: int = _DEFAULT_HIST_BINS,
) -> Optional[List[VisualFeature]]:
    """Extract one low-level visual feature per scene.

    Args:
        video_path: Source video path.
        scenes: Scene objects with ``.start``/``.end``/``.index``.
        width: Downscale width (480p default).
        fps: If > 0, sample multiple frames per scene at this FPS and
            collapse static frames; if 0, extract the scene midpoint only.
        static_skip_threshold: L1/dim difference below which frames are
            treated as static and skipped (only used when ``fps > 0``).
        hist_bins: RGB histogram bins per channel.

    Returns:
        A list of :class:`VisualFeature` aligned 1:1 with ``scenes``, or
        ``None`` when extraction is wholly unavailable (e.g. no ffmpeg).
    """
    probe_fps = _ffprobe_fps(video_path) if fps > 0 else 0.0
    features: List[VisualFeature] = []
    for scene in scenes:
        if fps > 0 and probe_fps > 0:
            feat = _static_skip_collapse(
                video_path,
                scene.start,
                scene.end,
                width=width,
                hist_bins=hist_bins,
                fps=min(fps, probe_fps),
                threshold=static_skip_threshold,
            )
        else:
            mid = (scene.start + scene.end) / 2.0
            jpg = _extract_frame_jpg(video_path, mid, width)
            feat = None
            if jpg is not None:
                try:
                    feat = _frame_luma_hist(jpg, hist_bins)
                finally:
                    try:
                        os.unlink(jpg)
                    except OSError:
                        pass
        if feat is None:
            # One scene failed — degrade the whole batch (consistent with
            # the VLM path, which falls back to heuristic per scene).
            logger.debug("visual feature extraction failed for scene %s", scene.index)
            return None
        features.append(VisualFeature(scene_index=scene.index, luma=feat[0], hist_rgb=feat[1]))
    return features
