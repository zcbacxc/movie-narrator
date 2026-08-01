# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Scene filtering — intro skip, dark frame drop, highlight window.

These filters run *after* scene detection and *before* match, reducing
the candidate pool to scenes that are likely to carry meaningful footage.

All three filters are opt-in via job params (defaults preserve existing
behavior). Each filter is independently toggleable so manual QA can
isolate the effect of each.
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

from ..models import Scene

logger = logging.getLogger(__name__)


# ── 9.1 Intro skip ─────────────────────────────────────────


def filter_intro_scenes(
    scenes: List[Scene],
    skip_intro_sec: float,
) -> Tuple[List[Scene], int]:
    """Drop scenes that end before ``skip_intro_sec``.

    Studios logos and title cards typically occupy the first 30–90s.
    If filtering would remove *all* scenes, the original list is
    returned unchanged (skip is ignored).

    Returns ``(filtered_scenes, dropped_count)``.
    """
    if skip_intro_sec <= 0 or not scenes:
        return scenes, 0

    filtered = [s for s in scenes if s.end > skip_intro_sec]
    if not filtered:
        # Don't nuke everything — skip is advisory
        return scenes, 0

    dropped = len(scenes) - len(filtered)
    # Re-index
    for i, s in enumerate(filtered):
        s.index = i
    return filtered, dropped


# ── 9.2 Dark frame drop ────────────────────────────────────


def _video_hash(video_path: str) -> str:
    """Lightweight cache key from file stat (mtime + size)."""
    s = os.stat(video_path)
    raw = f"{s.st_mtime}_{s.st_size}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _extract_mid_frame(
    video_path: str,
    timestamp: float,
    output_path: Path,
) -> bool:
    """Extract a single frame at *timestamp* using ffmpeg.

    Returns ``True`` on success, ``False`` on failure (ffmpeg missing,
    corrupt video, etc.).
    """
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return False

    try:
        result = subprocess.run(
            [
                ffmpeg_bin,
                "-y",           # overwrite
                "-ss", str(timestamp),  # seek to timestamp
                "-i", video_path,
                "-frames:v", "1",      # extract exactly 1 frame
                "-q:v", "2",            # high quality JPEG
                "-f", "image2",
                str(output_path),
            ],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0 and output_path.exists()
    except Exception as e:  # noqa: BLE001
        logger.debug("frame extraction failed at %.1fs: %s", timestamp, e)
        return False


def _compute_mean_luma(image_path: Path) -> Optional[float]:
    """Compute mean luminance of an image (0–255 scale).

    Uses PIL's ``convert("L")`` (ITU-R 601-2 luma transform).
    Returns ``None`` when PIL is unavailable or the image is corrupt.
    """
    try:
        from PIL import Image
        img = Image.open(image_path).convert("L")
        # Small images: use histogram; for larger ones, downscale first
        if img.width > 64:
            img = img.resize((64, 64))
        pixels = list(img.getdata())
        if not pixels:
            return None
        return sum(pixels) / len(pixels)
    except Exception as e:  # noqa: BLE001
        logger.debug("luma computation failed: %s", e)
        return None


def filter_dark_scenes(
    scenes: List[Scene],
    video_path: Optional[str],
    luma_threshold: float,
    cache_dir: Optional[Path] = None,
) -> Tuple[List[Scene], int]:
    """Drop scenes whose mid-frame mean luma is below *luma_threshold*.

    Extracts one frame at each scene's midpoint, computes mean luminance
    via PIL, and drops scenes darker than the threshold (e.g. black
    screens, fade-to-black transitions).

    If filtering would remove *all* scenes, the original list is
    returned unchanged. Frames are cached by video hash + scene index
    to avoid re-extraction on re-runs.

    Returns ``(filtered_scenes, dropped_count)``.
    """
    if luma_threshold <= 0 or not scenes or not video_path:
        return scenes, 0

    # Check ffmpeg availability upfront
    if not shutil.which("ffmpeg"):
        logger.debug("ffmpeg not found — dark frame filter skipped")
        return scenes, 0

    # Check PIL availability
    try:
        from PIL import Image  # noqa: F401
    except ImportError:
        logger.debug("PIL not found — dark frame filter skipped")
        return scenes, 0

    vid_hash = _video_hash(video_path)
    if cache_dir is None:
        cache_dir = Path(video_path).parent / ".frame_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    kept: List[Scene] = []
    dropped = 0
    for scene in scenes:
        mid_ts = (scene.start + scene.end) / 2.0
        frame_path = cache_dir / f"{vid_hash}_scene{scene.index}_{mid_ts:.1f}.jpg"

        if not frame_path.exists():
            if not _extract_mid_frame(video_path, mid_ts, frame_path):
                # Extraction failed — keep the scene (don't drop on error)
                kept.append(scene)
                continue

        luma = _compute_mean_luma(frame_path)
        if luma is None:
            # Computation failed — keep the scene
            kept.append(scene)
            continue

        if luma < luma_threshold:
            dropped += 1
            logger.debug(
                "  dark drop: scene %d (luma=%.1f < %.1f)",
                scene.index, luma, luma_threshold,
            )
        else:
            kept.append(scene)

    if not kept:
        return scenes, 0

    # Re-index
    for i, s in enumerate(kept):
        s.index = i
    return kept, dropped


# ── 9.3 Highlight window ───────────────────────────────────


def apply_source_window(
    scenes: List[Scene],
    window: Optional[list],
) -> Tuple[List[Scene], int]:
    """Restrict scenes to the [start_ratio, end_ratio] time window.

    The window is expressed as ratios of the total scene span.
    Scenes that fall entirely outside the window are dropped.
    Scenes that partially overlap are clipped to the window bounds.

    Returns ``(filtered_scenes, dropped_count)``.
    """
    if not window or not scenes or len(window) != 2:
        return scenes, 0

    start_ratio, end_ratio = window[0], window[1]
    if start_ratio <= 0.0 and end_ratio >= 1.0:
        return scenes, 0  # no-op for full-span window

    scene_start = min(s.start for s in scenes)
    scene_end = max(s.end for s in scenes)
    span = scene_end - scene_start
    if span <= 0:
        return scenes, 0

    win_start = scene_start + start_ratio * span
    win_end = scene_start + end_ratio * span

    kept: List[Scene] = []
    dropped = 0
    for scene in scenes:
        # Scene is entirely before or after the window → drop
        if scene.end <= win_start or scene.start >= win_end:
            dropped += 1
            continue
        # Clip scene to window bounds
        new_start = max(scene.start, win_start)
        new_end = min(scene.end, win_end)
        kept.append(Scene(
            index=0,  # re-indexed below
            start=new_start,
            end=new_end,
        ))

    if not kept:
        return scenes, 0

    for i, s in enumerate(kept):
        s.index = i
    return kept, dropped
