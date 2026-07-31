# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

import asyncio
import os
from pathlib import Path

from pydub import AudioSegment
from tqdm.asyncio import tqdm_asyncio

from ..config import get_settings, TTSProviderType
from ..models import Context, TimedSegment
from ..utils.async_utils import run_async
from ..utils.audio_qa import analyze_segment, aggregate_metrics
from ..utils.console import step_timing
from ..utils.prosody import emotion_to_speed, apply_speed, map_segment_emotions
from ..tts import TTSCacheKey, get_tts_provider, is_ci
from ..tts.cache import (
    cache_path_for,
    CACHE_SCHEMA_VERSION,
    PROVIDER_CACHE_VERSIONS,
)

__all__ = ["generate_voice"]

# Per-segment TTS retry: network hiccups shouldn't kill the entire batch.
_TTS_SEGMENT_RETRIES = 3
_TTS_RETRY_DELAY = 1.0  # seconds

# v0.5.9: V2 duration feedback — speed adjustment for overflow segments.
_MAX_SPEEDUP = 1.15          # cap at 15% faster to avoid chipmunk effect
_OVERFLOW_THRESHOLD_V2 = 1.10  # trigger v2 when >10% over target after v1


def _build_audio(
    results: list[tuple[AudioSegment, float]],
    segments: list,
    pause_ms: int,
) -> tuple[AudioSegment, list[TimedSegment]]:
    """Assemble per-segment audio into a single track with inter-segment pauses.

    Returns (combined_audio, timed_segments) where timed_segments[i] has
    start/end timestamps relative to the combined track.
    """
    combined = AudioSegment.empty()
    timed_segments: list[TimedSegment] = []
    current_time = 0.0
    for i, (audio, duration) in enumerate(results):
        combined += audio
        pause = (pause_ms / 1000.0) if i < len(segments) - 1 else 0
        if pause > 0:
            combined += AudioSegment.silent(duration=pause_ms)
        timed_segments.append(
            TimedSegment(text=segments[i].text, start=current_time, end=current_time + duration)
        )
        current_time += duration + pause
    return combined, timed_segments


def generate_voice(ctx: Context) -> Context:
    settings = get_settings()
    output_dir = Path(ctx.output_dir)
    cache_root = output_dir / "cache" / "tts" / settings.tts_provider.value
    cache_root.mkdir(parents=True, exist_ok=True)

    voice = ctx.metadata.get("voice") or settings.default_voice
    provider = get_tts_provider(settings)
    pause_ms = ctx.metadata.get("tts_pause_ms", 300)
    max_concurrent = ctx.metadata.get("tts_max_concurrent", 3)
    audio_fmt = ctx.metadata.get("tts_audio_format", "mp3")
    audio_bitrate = ctx.metadata.get("tts_audio_bitrate", "128k")
    console = ctx.services.console
    # ST-08: style_prompt affects MiMo TTS audio output; must be in cache key
    style_prompt = ctx.metadata.get("tts_style_prompt", "")

    def _key(seg_text: str) -> TTSCacheKey:
        return TTSCacheKey(
            schema_version=CACHE_SCHEMA_VERSION,
            provider=settings.tts_provider.value,
            provider_version=PROVIDER_CACHE_VERSIONS[settings.tts_provider.value],
            model=(
                settings.openai_tts_model
                if settings.tts_provider is TTSProviderType.OPENAI
                else settings.mimo_tts_model
                if settings.tts_provider is TTSProviderType.MIMO
                else ""
            ),
            voice=voice,
            text=seg_text,
            style_prompt=style_prompt,
        )

    async def _run_all():
        sem = asyncio.Semaphore(max_concurrent)

        async def _one(seg):
            async with sem:
                key = _key(seg.text)
                cached = cache_path_for(cache_root, key)
                if is_ci():
                    # CI bypasses cache: synthesize to a temp path, probe, then
                    # delete. Silent-audio files must never enter the cache —
                    # otherwise a subsequent non-CI run would hit the silent
                    # cache and skip real synthesis.
                    tmp = output_dir / f".ci_{cached.name}"
                    await provider.synthesize(seg.text, voice, tmp)
                    audio = AudioSegment.from_mp3(tmp)
                    tmp.unlink(missing_ok=True)
                else:
                    if not cached.exists():
                        # Per-segment retry: a single network hiccup shouldn't
                        # kill the entire batch.  Retry up to _TTS_SEGMENT_RETRIES
                        # times with a short delay before giving up.
                        last_err = None
                        for attempt in range(_TTS_SEGMENT_RETRIES):
                            try:
                                # ST-07: atomic write — synthesize to .partial
                                # then os.replace to final path. Prevents
                                # corrupt cache files from interrupted writes.
                                partial = cached.with_suffix(".partial")
                                await provider.synthesize(seg.text, voice, partial)
                                os.replace(str(partial), str(cached))
                                last_err = None
                                break
                            except Exception as e:
                                last_err = e
                                partial.unlink(missing_ok=True)
                                if attempt < _TTS_SEGMENT_RETRIES - 1:
                                    await asyncio.sleep(_TTS_RETRY_DELAY)
                                else:
                                    console.inline_warn(
                                        f"TTS failed for segment after {_TTS_SEGMENT_RETRIES} attempts: {e}"
                                    )
                        if last_err is not None:
                            raise last_err
                    # ST-07: if cached file is corrupt (from a previous
                    # interrupted write before the fix), delete and retry once.
                    try:
                        audio = AudioSegment.from_mp3(cached)
                    except Exception:
                        console.inline_warn(
                            f"Corrupt TTS cache file detected, re-synthesizing: {cached.name}"
                        )
                        cached.unlink(missing_ok=True)
                        await provider.synthesize(seg.text, voice, cached)
                        audio = AudioSegment.from_mp3(cached)
                return audio, round(len(audio) / 1000.0, 3)

        return await tqdm_asyncio.gather(
            *[_one(s) for s in ctx.segments],
            desc="Narrating",
            unit="seg",
        )

    with step_timing(console, "tts_batch_synthesize"):
        results = run_async(_run_all())

    # v0.7.0: Record TTS usage for cost tracking
    if hasattr(ctx, 'cost_tracker') and ctx.cost_tracker is not None:
        provider_name = settings.tts_provider.value
        tts_model = (
            settings.openai_tts_model if settings.tts_provider is TTSProviderType.OPENAI
            else settings.mimo_tts_model if settings.tts_provider is TTSProviderType.MIMO
            else ""
        )
        for seg in ctx.segments:
            ctx.cost_tracker.record_tts_call(
                provider=provider_name,
                model=tts_model,
                characters=len(seg.text),
                segments=1,
                cached=False,
            )

    # ── v0.5.9: Emotion-aware prosody ───────────────────────
    # Apply per-segment speed/pitch adjustment based on beat emotion labels.
    # Uses pydub's frame-rate trick: changes both speed and pitch, which is
    # desirable for emotion expression (intense→faster, suspense→slower).
    beats_meta = ctx.metadata.get("beats_meta") or []
    segment_emotions = map_segment_emotions(len(results), beats_meta)
    prosody_log: list[dict] = []
    for i in range(len(results)):
        emotion = segment_emotions[i] if i < len(segment_emotions) else None
        speed = emotion_to_speed(emotion)
        if speed != 1.0:
            audio, _ = results[i]
            adjusted = apply_speed(audio, speed)
            results[i] = (adjusted, round(len(adjusted) / 1000.0, 3))
        prosody_log.append({
            "index": i, "emotion": emotion, "speed": round(speed, 3),
        })

    combined, timed_segments = _build_audio(
        results, ctx.segments, pause_ms
    )

    # ── v1 duration pause feedback ─────────────────────
    # If narration exceeds target duration by >15%, try reducing pause_ms
    # and rebuilding.  This is a v1 approach: only adjusts pause, does NOT
    # re-run TTS or trim sentences.
    target_duration = ctx.metadata.get("duration") or ctx.duration
    applied_pause_ms = pause_ms
    v1_adjusted = False

    if target_duration and pause_ms > 50:
        actual_duration = timed_segments[-1].end if timed_segments else 0
        ratio = actual_duration / target_duration if target_duration else 1.0
        if ratio > 1.15:
            # Calculate a pause that should bring us closer to target
            # total = sum(audio) + (n-1) * pause
            # We want: sum(audio) + (n-1) * new_pause ≈ target
            audio_only = sum(d for _, d in results)
            n_pause = max(1, len(results) - 1)
            new_pause_ms = max(50, int((target_duration - audio_only) * 1000 / n_pause))
            if new_pause_ms < pause_ms:
                console.inline_warn(
                    f"Narration {actual_duration:.1f}s exceeds target {target_duration:.1f}s "
                    f"(ratio {ratio:.2f}). Reducing pause {pause_ms}ms → {new_pause_ms}ms."
                )
                applied_pause_ms = new_pause_ms
                v1_adjusted = True
                combined, timed_segments = _build_audio(
                    results, ctx.segments, new_pause_ms
                )
                ctx.metadata["duration_metrics"] = {
                    "target_sec": target_duration,
                    "narration_sec": round(timed_segments[-1].end, 2) if timed_segments else 0,
                    "ratio_vs_target": round(
                        (timed_segments[-1].end / target_duration) if timed_segments and target_duration else 0, 3
                    ),
                    "pause_ms_original": pause_ms,
                    "pause_ms_applied": new_pause_ms,
                    "adjusted": True,
                }
            else:
                ctx.metadata["duration_metrics"] = {
                    "target_sec": target_duration,
                    "narration_sec": round(actual_duration, 2),
                    "ratio_vs_target": round(ratio, 3),
                    "pause_ms_original": pause_ms,
                    "pause_ms_applied": pause_ms,
                    "adjusted": False,
                    "reason": "pause_already_at_floor",
                }
        else:
            ctx.metadata["duration_metrics"] = {
                "target_sec": target_duration,
                "narration_sec": round(actual_duration, 2),
                "ratio_vs_target": round(ratio, 3),
                "pause_ms_original": pause_ms,
                "pause_ms_applied": pause_ms,
                "adjusted": False,
            }

    # ── v0.5.9: V2 speed feedback ───────────────────────────
    # If narration still overflows after v1 pause reduction, apply a uniform
    # speedup to all segments.  This is more aggressive than v1 but avoids
    # re-running TTS — the speedup is applied via pydub post-processing.
    v2_speed = 1.0
    if target_duration:
        actual_duration = timed_segments[-1].end if timed_segments else 0
        ratio = actual_duration / target_duration if target_duration else 1.0
        if ratio > _OVERFLOW_THRESHOLD_V2:
            v2_speed = min(_MAX_SPEEDUP, target_duration / actual_duration)
            if v2_speed > 1.01:
                console.inline_warn(
                    f"Narration {actual_duration:.1f}s still exceeds target "
                    f"{target_duration:.1f}s after v1. Applying v2 speedup {v2_speed:.2f}x."
                )
                adjusted_results = []
                for audio, _ in results:
                    adj = apply_speed(audio, v2_speed)
                    adjusted_results.append((adj, round(len(adj) / 1000.0, 3)))
                results = adjusted_results
                combined, timed_segments = _build_audio(
                    results, ctx.segments, applied_pause_ms
                )
                dm = ctx.metadata.get("duration_metrics", {})
                dm["v2_speed_applied"] = round(v2_speed, 3)
                dm["narration_sec_v2"] = round(
                    timed_segments[-1].end, 2
                ) if timed_segments else 0
                dm["ratio_v2"] = round(
                    (timed_segments[-1].end / target_duration)
                    if timed_segments and target_duration else 0, 3
                )
                ctx.metadata["duration_metrics"] = dm

    # ── v0.5.9: Audio quality validation ────────────────────
    # Run clipping, SNR, and silence checks on each segment.  Issues are
    # advisory (soft gate) — logged as warnings but never block the pipeline.
    segment_metrics = []
    for i, (audio, _) in enumerate(results):
        m = analyze_segment(audio, i)
        segment_metrics.append(m)
        for issue in m.issues:
            console.inline_warn(f"Audio QA segment {i + 1}: {issue}")

    # ── v0.5.9: Audio quality aggregation ───────────────────
    # Store per-segment metrics and summary in metadata for diagnostics.
    ctx.metadata["audio_quality"] = {
        "segments": [m.to_dict() for m in segment_metrics],
        "summary": aggregate_metrics(segment_metrics),
        "prosody": prosody_log,
        "duration_v2_speed": round(v2_speed, 3) if v2_speed > 1.0 else None,
    }

    audio_path = output_dir / f"narration.{audio_fmt}"
    # Explicit bitrate prevents pydub's default 32 kbps export, which
    # produces MPEG v2.5 audio that ffmpeg (used by MoviePy) can fail
    # to decode — resulting in a silent final video.
    combined.export(audio_path, format=audio_fmt, bitrate=audio_bitrate)
    ctx.audio_path = str(audio_path)
    ctx.timed_segments = timed_segments
    ctx.metadata["voice_used"] = voice
    ctx.metadata["tts_provider"] = settings.tts_provider.value

    # LRU eviction: remove oldest cache files when total size exceeds threshold.
    # Scans the parent cache dir (all providers) so switching providers
    # doesn't leave stale files accumulating forever.
    _max_bytes = settings.tts_cache_max_mb * 1024 * 1024
    cache_parent = output_dir / "cache" / "tts"
    if cache_parent.exists():
        mp3_files = list(cache_parent.rglob("*.mp3"))
        total = sum(f.stat().st_size for f in mp3_files)
        if total > _max_bytes:
            for oldest in sorted(mp3_files, key=lambda f: f.stat().st_mtime):
                if total <= _max_bytes:
                    break
                total -= oldest.stat().st_size
                oldest.unlink(missing_ok=True)

    return ctx
