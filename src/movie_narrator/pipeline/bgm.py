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


def select_bgm_by_emotion(ctx: Context) -> Optional[str]:
    """Select a BGM file whose mood matches the dominant narration emotion.

    NA-M4-S1: emotion-based BGM auto-selection.

    The dominant emotion is the most frequent ``emotion`` value across
    ``ctx.metadata["beats_meta"]`` (ignoring None / missing values). The
    BGM metadata file (``ctx.metadata["bgm_metadata_path"]``, defaulting
    to the packaged ``assets/bgm_metadata.yaml`` template) is then scanned
    for the first sample whose ``mood`` equals the dominant emotion.

    Returns the resolved path to the selected BGM file, or ``None`` when:
      - no ``beats_meta`` / no usable emotions
      - no BGM metadata file exists (or is unreadable)
      - no sample matches the dominant emotion
      - the matched file does not exist on disk

    This is a SOFT enhancement: callers fall back to the existing BGM
    selection logic when ``None`` is returned, so existing behaviour is
    preserved when no metadata is available.
    """
    beats_meta = ctx.metadata.get("beats_meta") or []
    # Compute the dominant emotion (most frequent, ignoring None values).
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
    dominant_emotion = max(counts, key=lambda k: counts[k])

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

    # Resolve filenames relative to the metadata file's directory.
    base_dir = metadata_path.parent
    for sample in samples:
        if not isinstance(sample, dict):
            continue
        if sample.get("mood") != dominant_emotion:
            continue
        filename = sample.get("filename")
        if not isinstance(filename, str) or not filename:
            continue
        candidate = Path(filename)
        if not candidate.is_absolute():
            candidate = (base_dir / filename).resolve()
        if candidate.is_file():
            return str(candidate)
    return None


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
