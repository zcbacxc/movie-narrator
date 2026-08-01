# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import yaml
from pydub import AudioSegment
from pydub.exceptions import PydubException
from pydub.utils import db_to_float

from ..models import Context, StepResult, TimedSegment
from ..utils.audio_mix import duck_bgm, normalize_loudnorm, normalize_peak
from ..utils.prosody import map_segment_emotions

logger = logging.getLogger(__name__)


def _export_robust(seg: AudioSegment, out: Path) -> str:
    """Export audio, falling back to WAV when MP3 encoding is unavailable.

    pydub's MP3 export requires ffmpeg's libmp3lame; some minimal ffmpeg
    builds (and the imageio-ffmpeg bundle) lack it. WAV is native PCM and
    needs no encoder, so it is a safe fallback. The render step reads the
    result back via MoviePy, which accepts both.
    """
    try:
        seg.export(out, format="mp3")
        return str(out)
    except (OSError, RuntimeError, PydubException):
        logger.debug(
            "MP3 export failed for %s, falling back to WAV", out, exc_info=True
        )
        wav_out = out.with_suffix(".wav")
        seg.export(wav_out, format="wav")
        return str(wav_out)


def _normalize_narration(ctx: Context, narration: AudioSegment) -> str:
    """Normalize narration and write to a side file, returning the path."""
    out = Path(ctx.output_dir) / "narration_normalized.mp3"
    target = ctx.metadata.get("audio_target_dbfs", -14.0)
    # Use RMS-based loudnorm when configured, else peak normalization
    if ctx.metadata.get("bgm_loudnorm", False):
        normalized = normalize_loudnorm(narration, target_dbfs=target)
    else:
        normalized = normalize_peak(narration, target_dbfs=target)
    return _export_robust(normalized, out)


def ensure_final_audio(ctx: Context) -> Context:
    """Guarantee that ctx.final_audio_path is normalized.

    All BGM exit paths (skip, fail, exception) must go through this
    function. If the final audio is still the raw narration (not mixed),
    normalize it so that the exception/fail path is not worse than the
    success path.

    Called by mix_bgm at every exit point and by runner.py as a safety
    net before render.
    """
    if not ctx.audio_path:
        return ctx

    # Already mixed (BGM success path) — nothing to do
    if ctx.final_audio_path and ctx.final_audio_path != ctx.audio_path:
        return ctx

    # Raw narration — normalize if configured
    do_norm = ctx.metadata.get("bgm_normalize", True)
    if not do_norm:
        ctx.final_audio_path = ctx.audio_path
        return ctx

    try:
        narration = AudioSegment.from_file(ctx.audio_path)
        ctx.final_audio_path = _normalize_narration(ctx, narration)
    except (OSError, RuntimeError, PydubException):
        logger.debug(
            "Normalization failed for %s, falling back to raw audio",
            ctx.audio_path,
            exc_info=True,
        )
        # Last resort: use raw audio as-is (better than nothing)
        ctx.final_audio_path = ctx.audio_path

    return ctx


def _compute_emotion_profile(beats_meta: list) -> dict[str, float] | None:
    """Compute the emotion distribution as normalised weights.

    Returns a dict mapping emotion -> fraction (0.0-1.0), or ``None``
    when no usable emotions are present. The distribution considers
    ALL emotions, not just the dominant one, enabling better BGM matching.
    """
    counts: Dict[str, int] = {}
    for bm in beats_meta:
        if not isinstance(bm, dict):
            continue
        emotion = bm.get("emotion")
        if emotion is None:
            continue
        counts[emotion] = counts.get(emotion, 0) + 1
    if not counts:
        return None
    total = sum(counts.values())
    return {e: c / total for e, c in counts.items()}


# Emotion energy mapping: approximate perceived energy level per emotion.
# Used to match BGM energy to the narration's emotional intensity.
_EMOTION_ENERGY: dict[str, float] = {
    "intense": 0.9,
    "suspense": 0.7,
    "twist": 0.6,
    "laughter": 0.5,
    "calm": 0.2,
}


def _score_bgm_candidate(sample: dict, emotion_profile: dict[str, float]) -> float:
    """Score a BGM candidate against the emotion profile.

    Higher score = better match. Scoring considers:
    - Direct mood match weighted by the emotion's fraction
    - Energy alignment between BGM energy and weighted narration energy
    - Versatility bonus when the BGM mood covers a secondary emotion

    Returns 0.0 when the sample's mood doesn't match any emotion.
    """
    mood = sample.get("mood")
    if not isinstance(mood, str) or mood not in emotion_profile:
        return 0.0

    # Primary match: the fraction of this mood in the narration
    primary_score = emotion_profile[mood]

    # Versatility bonus: when the narration has secondary emotions
    # (frac > 20%) beyond the BGM's own mood, the BGM that matches the
    # dominant emotion earns a small bonus for anchoring a multi-emotion arc.
    versatility = 0.0
    for emo, frac in emotion_profile.items():
        if emo == mood:
            continue
        if frac > 0.20:
            versatility += frac * 0.15
    primary_score += versatility

    # Energy alignment: compute the narration's weighted average energy
    # and compare it to the BGM's energy field. Closer = better.
    narration_energy = sum(
        _EMOTION_ENERGY.get(e, 0.5) * f for e, f in emotion_profile.items()
    )
    bgm_energy = sample.get("energy")
    if isinstance(bgm_energy, (int, float)) and 0.0 <= bgm_energy <= 1.0:
        energy_diff = abs(narration_energy - float(bgm_energy))
        # Map energy_diff (0.0=perfect, 1.0=worst) to a bonus/penalty
        energy_score = (1.0 - energy_diff) * 0.2
        primary_score += energy_score

    return primary_score


def select_bgm_by_emotion(ctx: Context) -> Optional[str]:
    """Select a BGM file whose mood best matches the narration's emotion profile.

    Emotion-weighted BGM auto-selection.

    Instead of picking the first BGM matching the single dominant emotion,
    this computes the full emotion distribution and scores ALL candidates
    against it. The best-scoring candidate is selected, considering:

    - How well the BGM mood matches the emotion distribution
    - Energy alignment between narration intensity and BGM energy
    - Versatility for multi-emotion narratives

    Returns the resolved path to the selected BGM file, or ``None`` when:
      - no ``beats_meta`` / no usable emotions
      - no BGM metadata file exists (or is unreadable)
      - no sample matches any emotion in the profile
      - the matched file does not exist on disk

    This is a SOFT enhancement: callers fall back to the existing BGM
    selection logic when ``None`` is returned, so existing behaviour is
    preserved when no metadata is available.
    """
    beats_meta = ctx.metadata.get("beats_meta") or []
    emotion_profile = _compute_emotion_profile(beats_meta)
    if not emotion_profile:
        return None

    # Resolve the BGM metadata file path.
    metadata_path_str = ctx.metadata.get("bgm_metadata_path")
    if metadata_path_str:
        metadata_path = Path(metadata_path_str)
    else:
        # Default to the packaged template alongside this package.
        metadata_path = (
            Path(__file__).resolve().parent.parent / "assets" / "bgm_metadata.yaml"
        )
    if not metadata_path.is_file():
        return None

    # Parse the metadata file.
    try:
        with metadata_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError):
        logger.debug(
            "Failed to parse BGM metadata %s, returning None",
            metadata_path,
            exc_info=True,
        )
        return None
    if not isinstance(data, dict):
        return None

    samples = data.get("bgm_samples")
    if not isinstance(samples, list):
        return None

    # Score all candidates and pick the best match.
    base_dir = metadata_path.parent
    best_score = 0.0
    best_path: Optional[str] = None
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        score = _score_bgm_candidate(sample, emotion_profile)
        if score <= best_score:
            continue
        filename = sample.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        candidate = Path(filename)
        if not candidate.is_absolute():
            candidate = (base_dir / filename).resolve()
        if candidate.is_file():
            best_score = score
            best_path = str(candidate)

    if best_path:
        # Store selection metadata for diagnostics.
        ctx.metadata["bgm_selection"] = {
            "emotion_profile": {k: round(v, 3) for k, v in emotion_profile.items()},
            "score": round(best_score, 3),
        }

    return best_path


# ── v0.5.9: BGM dynamic transition ───────────────────────────

# Emotion → BGM gain adjustment (dB).  Positive = louder, negative = quieter.
# Applied as a per-zone envelope on top of the ducking curve so the BGM
# subtly responds to emotional shifts in the narration.
_EMOTION_BGM_GAIN: dict[str, float] = {
    "intense": +2.0,
    "suspense": -1.0,
    "calm": -3.0,
    "twist": +1.0,
    "laughter": +1.5,
}


def _detect_emotion_zones(
    timed_segments: list[TimedSegment],
    segment_emotions: list[str | None],
) -> list[dict]:
    """Detect contiguous emotion zones from per-segment emotions.

    Returns a list of ``{start, end, emotion, segment_range}`` dicts
    representing contiguous regions where the narration emotion is
    constant.  Zone boundaries are the points where the emotion label
    changes between consecutive segments.
    """
    if not timed_segments or not segment_emotions:
        return []

    zones: list[dict] = []
    current_emotion = segment_emotions[0]
    current_start = timed_segments[0].start
    current_indices = [0]

    for i in range(1, len(timed_segments)):
        emo = segment_emotions[i] if i < len(segment_emotions) else None
        if emo != current_emotion:
            zones.append({
                "start": round(current_start, 3),
                "end": round(timed_segments[i].start, 3),
                "emotion": current_emotion,
                "segment_range": [current_indices[0], current_indices[-1]],
            })
            current_emotion = emo
            current_start = timed_segments[i].start
            current_indices = [i]
        else:
            current_indices.append(i)

    # Final zone
    zones.append({
        "start": round(current_start, 3),
        "end": round(timed_segments[-1].end, 3),
        "emotion": current_emotion,
        "segment_range": [current_indices[0], current_indices[-1]],
    })
    return zones


def _apply_emotion_transitions(
    bgm: AudioSegment,
    zones: list[dict],
    transition_ms: int = 500,
) -> tuple[AudioSegment, list[dict]]:
    """Apply per-zone gain with smooth ramps at emotion boundaries.

    Builds a per-sample amplitude envelope that applies different gains
    to different emotion zones.  At zone boundaries, a linear ramp
    transitions from the previous zone's gain to the new zone's gain
    over ``transition_ms`` milliseconds, avoiding abrupt volume jumps.

    Returns ``(adjusted_bgm, transitions)`` where ``transitions`` is a
    list of ``{position_s, from_emotion, to_emotion, transition_ms}``
    dicts for diagnostics.
    """
    if not zones or len(bgm) == 0:
        return bgm, []

    n_samples = len(bgm.get_array_of_samples())
    sample_rate = bgm.frame_rate
    if n_samples == 0 or sample_rate == 0:
        return bgm, []

    envelope = np.ones(n_samples, dtype=np.float64)
    transitions: list[dict] = []

    for i, zone in enumerate(zones):
        start_sample = int(zone["start"] * sample_rate)
        end_sample = int(zone["end"] * sample_rate)
        start_sample = max(0, min(start_sample, n_samples))
        end_sample = max(0, min(end_sample, n_samples))
        if start_sample >= end_sample:
            continue

        emotion = zone.get("emotion")
        gain_db = _EMOTION_BGM_GAIN.get(emotion, 0.0) if emotion else 0.0
        gain_factor = float(db_to_float(gain_db)) if gain_db != 0.0 else 1.0

        # Apply gain to this zone
        envelope[start_sample:end_sample] *= gain_factor

        # Transition ramp at the start of this zone (except the first)
        if i > 0:
            transition_samples = min(
                int(transition_ms * sample_rate / 1000),
                end_sample - start_sample,
                start_sample,
            )
            if transition_samples > 0:
                prev_emotion = zones[i - 1].get("emotion")
                prev_gain_db = (
                    _EMOTION_BGM_GAIN.get(prev_emotion, 0.0)
                    if prev_emotion else 0.0
                )
                prev_factor = (
                    float(db_to_float(prev_gain_db))
                    if prev_gain_db != 0.0 else 1.0
                )

                ramp_start = max(0, start_sample - transition_samples)
                ramp_len = start_sample - ramp_start
                if ramp_len > 0:
                    ramp = np.linspace(prev_factor, gain_factor, ramp_len)
                    envelope[ramp_start:start_sample] = ramp

                transitions.append({
                    "position_s": zone["start"],
                    "from_emotion": prev_emotion,
                    "to_emotion": emotion,
                    "transition_ms": transition_ms,
                })

    # Apply envelope to BGM samples
    raw = np.array(bgm.get_array_of_samples(), dtype=np.float64)
    raw *= envelope[: len(raw)]
    raw = np.clip(raw, np.iinfo(np.int16).min, np.iinfo(np.int16).max)
    raw = raw.astype(np.int16)

    adjusted = AudioSegment(
        raw.tobytes(),
        frame_rate=bgm.frame_rate,
        sample_width=bgm.sample_width,
        channels=bgm.channels,
    )

    return adjusted, transitions


def _mix_ambient_track(
    narration_or_mixed: AudioSegment,
    ambient_path: str,
    ambient_gain_db: float = -12.0,
    duck_db: float = -10.0,
    timed_segments: list = None,
) -> tuple[AudioSegment, dict]:
    """Mix an ambient/SFX track beneath the narration+BGM audio.

    v0.7.1: Loads the ambient track, loops/trims it to match the
    narration duration, applies gain reduction, and overlays it
    with ducking during active narration segments.

    Returns ``(mixed_audio, ambient_info_dict)``. On any error,
    returns the original audio unchanged with an empty info dict.
    """
    info: dict = {}
    try:
        ambient = AudioSegment.from_file(ambient_path)
    except (OSError, RuntimeError, PydubException) as e:
        logger.debug("ambient track load failed", exc_info=True)
        return narration_or_mixed, {"error": f"ambient load failed: {e}"}

    target_ms = len(narration_or_mixed)
    ambient_ms = len(ambient)

    # Loop ambient track to match target duration
    if ambient_ms < target_ms:
        loops = int(target_ms / ambient_ms) + 1
        ambient = ambient * loops
    ambient = ambient[:target_ms]

    # Apply gain reduction
    ambient = ambient.apply_gain(ambient_gain_db)

    # Simple ducking: reduce ambient volume further during narration
    # We use a moderate fixed duck since per-segment ducking is already
    # applied to BGM — the ambient sits even lower in the mix.
    ambient = ambient.apply_gain(duck_db)

    mixed = narration_or_mixed.overlay(ambient)

    info = {
        "path": str(ambient_path),
        "gain_db": ambient_gain_db,
        "duck_db": duck_db,
        "duration_sec": round(target_ms / 1000.0, 2),
    }
    return mixed, info


def mix_bgm(ctx: Context) -> Context:
    if not ctx.audio_path:
        ctx.status.bgm = "skipped"
        ctx.final_audio_path = ctx.audio_path
        return ctx

    req = ctx.metadata.get("bgm_request", "none")

    # Emotion-based BGM auto-selection for "default" requests.
    # If select_bgm_by_emotion returns a path, use it; otherwise fall
    # through to the existing logic below.
    if req == "default":
        selected = select_bgm_by_emotion(ctx)
        if selected:
            ctx.assets.bgm = selected

    if req == "none" or (req == "default" and not ctx.assets.bgm):
        # No BGM — normalize narration for production consistency.
        ctx.status.bgm = "skipped"
        return ensure_final_audio(ctx)

    if req == "explicit" and not ctx.assets.bgm:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = "explicit BGM missing"
        ctx.status.bgm = "failed"
        return ensure_final_audio(ctx)

    if not ctx.assets.bgm:
        ctx.status.bgm = "skipped"
        return ensure_final_audio(ctx)

    try:
        narration = AudioSegment.from_file(ctx.audio_path)
        gain_db = ctx.metadata.get("bgm_gain_db", -18.0)
        duck_db = ctx.metadata.get("bgm_duck_db", -10.0)
        bgm_raw = AudioSegment.from_file(ctx.assets.bgm)

        # v0.5.9: BGM dynamic transition — adjust BGM gain per emotion zone
        # with smooth ramps at zone boundaries to avoid abrupt mood changes.
        beats_meta = ctx.metadata.get("beats_meta") or []
        segment_emotions = map_segment_emotions(
            len(ctx.timed_segments), beats_meta
        )
        zones = _detect_emotion_zones(ctx.timed_segments, segment_emotions)
        if zones and len(zones) > 1:
            bgm_raw, transitions = _apply_emotion_transitions(bgm_raw, zones)
            if transitions:
                ctx.metadata["bgm_transitions"] = transitions

        mixed = duck_bgm(
            narration, bgm_raw,
            bgm_gain_db=gain_db, duck_db=duck_db,
        )
        do_norm = ctx.metadata.get("bgm_normalize", True)
        if do_norm:
            target = ctx.metadata.get("audio_target_dbfs", -14.0)
            # Use RMS-based loudnorm when configured, else peak normalization
            if ctx.metadata.get("bgm_loudnorm", False):
                mixed = normalize_loudnorm(mixed, target_dbfs=target)
            else:
                mixed = normalize_peak(mixed, target_dbfs=target)

        # v0.7.1: Multi-track mixing — overlay ambient/SFX track if provided.
        # This sits beneath both narration and BGM for a richer soundscape.
        # Soft failure: if the ambient file is missing/corrupt, warn and continue.
        ambient_path = ctx.metadata.get("bgm_ambient_path")
        if ambient_path:
            ambient_gain = ctx.metadata.get("bgm_ambient_gain_db", -12.0)
            ambient_duck = ctx.metadata.get("bgm_duck_db", -10.0)
            mixed, ambient_info = _mix_ambient_track(
                mixed, ambient_path,
                ambient_gain_db=ambient_gain,
                duck_db=ambient_duck,
                timed_segments=ctx.timed_segments,
            )
            if "error" in ambient_info:
                ctx.services.console.inline_warn(
                    f"Ambient track skipped: {ambient_info['error']}"
                )
            else:
                ctx.metadata["ambient_track"] = ambient_info
                ctx.services.console.debug(
                    f"  v0.7.1 ambient: {ambient_path} "
                    f"(gain={ambient_gain}dB, duck={ambient_duck}dB)"
                )

        out = Path(ctx.output_dir) / "mixed.mp3"
        ctx.final_audio_path = _export_robust(mixed, out)
        ctx.status.bgm = "success"
        return ctx
    except (OSError, RuntimeError, PydubException) as e:
        logger.debug("BGM mix failed, falling back to narration", exc_info=True)
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        ctx.status.bgm = "failed"
        return ensure_final_audio(ctx)

