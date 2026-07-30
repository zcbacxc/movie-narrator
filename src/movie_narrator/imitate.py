# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Reference video imitation (Q-P7).

Analyzes a reference narration video to extract style metrics
(sentence density, cut density, pacing), then generates a temporary
preset that mimics the reference's style when running the pipeline.

This module is analysis-only: it reads metrics from the reference video
and maps them to pipeline parameters. The actual pipeline execution is
delegated to the standard ``build_context`` + ``run_pipeline`` flow.

Extracted metrics
-----------------
- **Duration** — total video length in seconds (via ffprobe)
- **Sentence density** — sentences per minute (via WhisperX/faster-whisper)
- **Cut density** — scene changes per minute (via PySceneDetect)
- **Average segment duration** — duration / sentence_count
- **Average scene duration** — duration / scene_count

Mapping rules
-------------
The extracted metrics are mapped to pipeline parameters:

================================  ========================================
Metric                            Pipeline Parameter
================================  ========================================
sentence_count                    prompt_target_sentences
avg_segment_duration              prompt_target_segment_duration
cut_density > 15/min              match_speed_clamp_max = 1.30 (fast cuts)
cut_density 8-15/min              match_speed_clamp_max = 1.20 (medium)
cut_density < 8/min               match_speed_clamp_max = 1.10 (slow)
scene_count / sentence_count      match_topk (more scenes = wider search)
================================  ========================================
"""

from __future__ import annotations

import json
import logging
import math
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Data structures ────────────────────────────────────────


@dataclass
class ReferenceMetrics:
    """Style metrics extracted from a reference video."""

    duration_sec: float = 0.0
    sentence_count: int = 0
    scene_count: int = 0
    sentences_per_minute: float = 0.0
    cuts_per_minute: float = 0.0
    avg_segment_duration: float = 0.0
    avg_scene_duration: float = 0.0
    # Raw transcript segments for debugging
    transcript_segments: List[Dict[str, Any]] = field(default_factory=list)
    # Raw scene list for debugging
    scenes: List[Dict[str, Any]] = field(default_factory=list)

    def summary(self) -> str:
        """Human-readable one-line summary."""
        return (
            f"duration={self.duration_sec:.1f}s, "
            f"sentences={self.sentence_count} ({self.sentences_per_minute:.1f}/min), "
            f"scenes={self.scene_count} ({self.cuts_per_minute:.1f}/min), "
            f"avg_seg={self.avg_segment_duration:.2f}s, "
            f"avg_scene={self.avg_scene_duration:.2f}s"
        )


# ── Video duration ────────────────────────────────────────


def _get_video_duration(video_path: str) -> float:
    """Get video duration in seconds using ffprobe.

    Falls back to 60.0 if ffprobe is unavailable.
    """
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            data = json.loads(result.stdout)
            return float(data.get("format", {}).get("duration", 60.0))
    except Exception as e:
        logger.debug(f"ffprobe failed: {e}")

    # Fallback: try ffmpeg
    try:
        cmd = ["ffmpeg", "-i", video_path, "-f", "null", "-"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        # Parse "Duration: 00:01:23.45" from stderr
        for line in result.stderr.split("\n"):
            if "Duration:" in line:
                time_str = line.split("Duration:")[1].strip().split(",")[0].strip()
                h, m, s = time_str.split(":")
                return float(h) * 3600 + float(m) * 60 + float(s)
    except Exception as e:
        logger.debug(f"ffmpeg duration probe failed: {e}")

    return 60.0


# ── Scene analysis ────────────────────────────────────────


def _count_scenes(video_path: str, threshold: float = 27.0) -> tuple[int, List[Dict[str, Any]]]:
    """Count scene changes using PySceneDetect.

    Returns (scene_count, scene_list) where scene_list contains
    dicts with start/end timestamps.
    """
    try:
        from scenedetect import open_video, SceneManager
        from scenedetect.detectors import ContentDetector

        video = open_video(video_path)
        scene_manager = SceneManager()
        scene_manager.add_detector(ContentDetector(threshold=threshold))
        scene_manager.detect_scenes(video, show_progress=False)

        scene_list = scene_manager.get_scene_list()
        scenes = []
        for start, end in scene_list:
            scenes.append({
                "start": start.get_seconds(),
                "end": end.get_seconds(),
            })
        return len(scenes), scenes
    except ImportError:
        logger.warning("scenedetect not available — scene count will be 0")
        return 0, []
    except Exception as e:
        logger.warning(f"Scene detection failed: {e}")
        return 0, []


# ── Transcript analysis ───────────────────────────────────


def _transcribe_reference(
    video_path: str,
    output_dir: Optional[Path] = None,
) -> tuple[int, List[Dict[str, Any]]]:
    """Transcribe reference video audio to count sentences.

    Uses WhisperX or faster-whisper (same backend as match.py).
    Returns (sentence_count, segments) where segments contain
    start/end/text.
    """
    try:
        # Try faster-whisper first (more reliable on Windows)
        from faster_whisper import WhisperModel

        model = WhisperModel("base", device="cpu", compute_type="int8")
        segments_gen, _ = model.transcribe(video_path, language="zh")
        segments = []
        for seg in segments_gen:
            text = seg.text.strip()
            if text:
                segments.append({
                    "start": seg.start,
                    "end": seg.end,
                    "text": text,
                })
        return len(segments), segments
    except ImportError:
        pass
    except Exception as e:
        logger.debug(f"faster-whisper failed: {e}")

    try:
        import whisperx

        audio = whisperx.load_audio(video_path)
        model = whisperx.load_model("base", device="cpu")
        result = model.transcribe(audio, language="zh")
        segments = []
        if result and "segments" in result:
            for seg in result["segments"]:
                text = seg.get("text", "").strip()
                if text:
                    segments.append({
                        "start": seg.get("start", 0.0),
                        "end": seg.get("end", 0.0),
                        "text": text,
                    })
        return len(segments), segments
    except ImportError:
        logger.warning(
            "Neither whisperx nor faster-whisper available — "
            "sentence count will be 0"
        )
        return 0, []
    except Exception as e:
        logger.warning(f"Transcription failed: {e}")
        return 0, []


# ── Main analysis function ────────────────────────────────


def analyze_reference(
    video_path: str,
    *,
    scene_threshold: float = 27.0,
    output_dir: Optional[Path] = None,
) -> ReferenceMetrics:
    """Analyze a reference video and extract style metrics.

    Args:
        video_path: Path to the reference video file.
        scene_threshold: Scene detection sensitivity (default 27.0).
        output_dir: Optional directory to save raw analysis data.

    Returns:
        :class:`ReferenceMetrics` with extracted style information.
    """
    if not Path(video_path).exists():
        raise FileNotFoundError(f"Reference video not found: {video_path}")

    metrics = ReferenceMetrics()

    # 1. Duration
    metrics.duration_sec = _get_video_duration(video_path)
    duration_min = metrics.duration_sec / 60.0 if metrics.duration_sec > 0 else 1.0

    # 2. Scene analysis
    metrics.scene_count, metrics.scenes = _count_scenes(video_path, scene_threshold)
    metrics.cuts_per_minute = metrics.scene_count / duration_min if duration_min > 0 else 0.0
    metrics.avg_scene_duration = (
        metrics.duration_sec / metrics.scene_count
        if metrics.scene_count > 0
        else metrics.duration_sec
    )

    # 3. Transcript analysis
    metrics.sentence_count, metrics.transcript_segments = _transcribe_reference(
        video_path, output_dir
    )
    metrics.sentences_per_minute = (
        metrics.sentence_count / duration_min if duration_min > 0 else 0.0
    )
    metrics.avg_segment_duration = (
        metrics.duration_sec / metrics.sentence_count
        if metrics.sentence_count > 0
        else 0.0
    )

    # Save raw data if output_dir is provided
    if output_dir:
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        raw_path = output_dir / "reference_analysis.json"
        raw_path.write_text(
            json.dumps(
                {
                    "video_path": video_path,
                    "duration_sec": metrics.duration_sec,
                    "sentence_count": metrics.sentence_count,
                    "scene_count": metrics.scene_count,
                    "sentences_per_minute": round(metrics.sentences_per_minute, 2),
                    "cuts_per_minute": round(metrics.cuts_per_minute, 2),
                    "avg_segment_duration": round(metrics.avg_segment_duration, 2),
                    "avg_scene_duration": round(metrics.avg_scene_duration, 2),
                    "transcript_segments": metrics.transcript_segments,
                    "scenes": metrics.scenes,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    return metrics


# ── Metrics to preset mapping ─────────────────────────────


def metrics_to_params(metrics: ReferenceMetrics) -> Dict[str, Any]:
    """Convert reference metrics to pipeline parameters.

    The mapping uses heuristics based on the reference video's
    sentence density and cut density to produce a params dict
    suitable for ``build_context``.

    Args:
        metrics: Extracted reference metrics.

    Returns:
        Dict of pipeline parameters that mimic the reference style.
    """
    params: Dict[str, Any] = {}

    # ── Sentence density → prompt shaping ──
    spm = metrics.sentences_per_minute
    if spm > 0:
        # Scale to target duration (default 60s)
        # If reference has 20 sentences in 90s (13.3/min),
        # for a 60s target we want ~13 sentences
        target_duration = 60  # default, overridden by CLI
        target_sentences = round(spm * target_duration / 60.0)
        target_sentences = max(8, min(30, target_sentences))  # clamp

        params["prompt_target_sentences"] = target_sentences
        params["prompt_target_segment_duration"] = round(
            target_duration / target_sentences, 2
        )
        # Max chars per sentence based on avg segment duration
        # Shorter segments = fewer chars
        if metrics.avg_segment_duration > 0:
            # ~3.8 chars/sec is the speaking rate baseline
            max_chars = int(metrics.avg_segment_duration * 3.8)
            params["prompt_max_chars_per_sentence"] = max(8, min(25, max_chars))

    # ── Cut density → speed clamps ──
    cpm = metrics.cuts_per_minute
    if cpm > 15:
        # Fast-cut style (douyin-fast like)
        params["match_speed_clamp_min"] = 0.85
        params["match_speed_clamp_max"] = 1.30
        params["match_drop_scene_min_duration"] = 0.3
        params["scene_merge_min_duration"] = 1.5
    elif cpm >= 8:
        # Medium pace (mainstream-dry like)
        params["match_speed_clamp_min"] = 0.90
        params["match_speed_clamp_max"] = 1.20
        params["match_drop_scene_min_duration"] = 0.5
        params["scene_merge_min_duration"] = 2.0
    elif cpm > 0:
        # Slow pace (bilibili-long like)
        params["match_speed_clamp_min"] = 0.95
        params["match_speed_clamp_max"] = 1.10
        params["match_drop_scene_min_duration"] = 0.8
        params["scene_merge_min_duration"] = 3.0

    # ── Scene-to-sentence ratio → topk ──
    if metrics.sentence_count > 0 and metrics.scene_count > 0:
        ratio = metrics.scene_count / metrics.sentence_count
        if ratio > 3.0:
            # Many scenes available — use wider search
            params["match_topk"] = 8
        elif ratio > 1.5:
            params["match_topk"] = 5
        else:
            params["match_topk"] = 3

    # ── BGM ducking based on sentence density ──
    # Dense narration = deeper ducking to keep voice clear
    if spm > 18:
        params["bgm_duck_db"] = -10.0
        params["tts_pause_ms"] = 120
    elif spm > 10:
        params["bgm_duck_db"] = -8.0
        params["tts_pause_ms"] = 200
    elif spm > 0:
        params["bgm_duck_db"] = -6.0
        params["tts_pause_ms"] = 300

    # ── Hook duration based on first segment ──
    if metrics.transcript_segments:
        first_seg = metrics.transcript_segments[0]
        first_dur = first_seg.get("end", 0) - first_seg.get("start", 0)
        if first_dur > 0:
            params["prompt_hook_seconds"] = min(5, max(2, round(first_dur)))

    return params


def metrics_to_preset_name(metrics: ReferenceMetrics) -> str:
    """Determine the closest matching built-in preset name.

    Uses sentence density and cut density to classify the reference
    into one of the three built-in presets.

    Args:
        metrics: Extracted reference metrics.

    Returns:
        Preset name: "douyin-fast", "mainstream-dry", or "bilibili-long".
    """
    spm = metrics.sentences_per_minute
    cpm = metrics.cuts_per_minute

    # Score each preset by proximity to reference metrics
    # douyin-fast: ~18 sentences, fast cuts
    # mainstream-dry: ~12 sentences, medium cuts
    # bilibili-long: ~8 sentences, slow cuts
    douyin_score = 0
    mainstream_score = 0
    bilibili_score = 0

    if spm > 15:
        douyin_score += 2
    elif spm >= 10:
        mainstream_score += 2
    else:
        bilibili_score += 2

    if cpm > 12:
        douyin_score += 1
    elif cpm >= 6:
        mainstream_score += 1
    else:
        bilibili_score += 1

    scores = {
        "douyin-fast": douyin_score,
        "mainstream-dry": mainstream_score,
        "bilibili-long": bilibili_score,
    }
    return max(scores, key=scores.get)


def format_analysis_report(metrics: ReferenceMetrics) -> str:
    """Format a human-readable analysis report.

    Args:
        metrics: Extracted reference metrics.

    Returns:
        Multi-line string suitable for CLI output.
    """
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append("  Reference Video Analysis")
    lines.append("=" * 60)
    lines.append("")
    lines.append(f"  Duration:              {metrics.duration_sec:.1f}s")
    lines.append(f"  Sentences:             {metrics.sentence_count}")
    lines.append(f"  Sentence density:      {metrics.sentences_per_minute:.1f}/min")
    lines.append(f"  Avg segment duration:  {metrics.avg_segment_duration:.2f}s")
    lines.append(f"  Scenes:                {metrics.scene_count}")
    lines.append(f"  Cut density:           {metrics.cuts_per_minute:.1f}/min")
    lines.append(f"  Avg scene duration:    {metrics.avg_scene_duration:.2f}s")
    lines.append("")

    preset = metrics_to_preset_name(metrics)
    lines.append(f"  Closest preset:        {preset}")
    lines.append("")

    params = metrics_to_params(metrics)
    if params:
        lines.append("  Generated parameters:")
        for key in sorted(params):
            lines.append(f"    {key:<40} {params[key]}")
    else:
        lines.append("  (No parameters generated — transcription may have failed)")

    lines.append("")
    lines.append("=" * 60)

    return "\n".join(lines)
