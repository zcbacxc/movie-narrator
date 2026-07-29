from pathlib import Path
from typing import Dict, Optional

import yaml
from pydub import AudioSegment

from ..models import Context, StepResult
from ..utils.audio_mix import duck_bgm, normalize_loudnorm, normalize_peak


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
    except Exception:
        wav_out = out.with_suffix(".wav")
        seg.export(wav_out, format="wav")
        return str(wav_out)


def _normalize_narration(ctx: Context, narration: AudioSegment) -> str:
    """Normalize narration and write to a side file, returning the path."""
    out = Path(ctx.output_dir) / "narration_normalized.mp3"
    target = ctx.metadata.get("audio_target_dbfs", -14.0)
    # EP6: Use RMS-based loudnorm when configured, else peak normalization
    if ctx.metadata.get("bgm_loudnorm", False):
        normalized = normalize_loudnorm(narration, target_dbfs=target)
    else:
        normalized = normalize_peak(narration, target_dbfs=target)
    return _export_robust(normalized, out)


def ensure_final_audio(ctx: Context) -> Context:
    """Guarantee that ctx.final_audio_path is normalized (AQ-04 fix).

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
    except Exception:
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

    NA-M4-S1+: emotion-weighted BGM auto-selection.

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
    except Exception:
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


def mix_bgm(ctx: Context) -> Context:
    if not ctx.audio_path:
        ctx.status.bgm = "skipped"
        ctx.final_audio_path = ctx.audio_path
        return ctx

    req = ctx.metadata.get("bgm_request", "none")

    # NA-M4-S1: emotion-based BGM auto-selection for "default" requests.
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

        mixed = duck_bgm(
            narration, bgm_raw,
            bgm_gain_db=gain_db, duck_db=duck_db,
        )
        do_norm = ctx.metadata.get("bgm_normalize", True)
        if do_norm:
            target = ctx.metadata.get("audio_target_dbfs", -14.0)
            # EP6: Use RMS-based loudnorm when configured, else peak normalization
            if ctx.metadata.get("bgm_loudnorm", False):
                mixed = normalize_loudnorm(mixed, target_dbfs=target)
            else:
                mixed = normalize_peak(mixed, target_dbfs=target)
        out = Path(ctx.output_dir) / "mixed.mp3"
        ctx.final_audio_path = _export_robust(mixed, out)
        ctx.status.bgm = "success"
        return ctx
    except Exception as e:
        ctx.step_state.result = StepResult.WARNING
        ctx.step_state.message = str(e)
        ctx.status.bgm = "failed"
        return ensure_final_audio(ctx)
