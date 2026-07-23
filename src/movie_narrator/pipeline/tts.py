import asyncio
from pathlib import Path

from pydub import AudioSegment
from tqdm.asyncio import tqdm_asyncio

from ..config import get_settings, TTSProviderType
from ..models import Context, TimedSegment
from ..utils.async_utils import run_async
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
            pause_ms=pause_ms,
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
                                await provider.synthesize(seg.text, voice, cached)
                                last_err = None
                                break
                            except Exception as e:
                                last_err = e
                                if attempt < _TTS_SEGMENT_RETRIES - 1:
                                    await asyncio.sleep(_TTS_RETRY_DELAY)
                                else:
                                    console.inline_warn(
                                        f"TTS failed for segment after {_TTS_SEGMENT_RETRIES} attempts: {e}"
                                    )
                        if last_err is not None:
                            raise last_err
                    audio = AudioSegment.from_mp3(cached)
                return audio, round(len(audio) / 1000.0, 3)

        return await tqdm_asyncio.gather(
            *[_one(s) for s in ctx.segments],
            desc="Narrating",
            unit="seg",
        )

    results = run_async(_run_all())

    combined, timed_segments = _build_audio(
        results, ctx.segments, pause_ms
    )

    # ── WP5: duration pause feedback ─────────────────────────
    # If narration exceeds target duration by >15%, try reducing pause_ms
    # and rebuilding.  This is a v1 approach: only adjusts pause, does NOT
    # re-run TTS or trim sentences.
    target_duration = ctx.metadata.get("duration") or ctx.duration
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
