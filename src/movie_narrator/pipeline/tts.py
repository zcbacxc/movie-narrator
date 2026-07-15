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

PAUSE_MS = 300
MAX_CONCURRENT = 3

__all__ = ["generate_voice", "PAUSE_MS"]


def generate_voice(ctx: Context) -> Context:
    settings = get_settings()
    output_dir = Path(ctx.output_dir)
    cache_root = output_dir / "cache" / "tts" / settings.tts_provider.value
    cache_root.mkdir(parents=True, exist_ok=True)

    voice = ctx.metadata.get("voice") or settings.default_voice
    provider = get_tts_provider(settings)

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
            pause_ms=PAUSE_MS,
        )

    async def _run_all():
        sem = asyncio.Semaphore(MAX_CONCURRENT)

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
                        await provider.synthesize(seg.text, voice, cached)
                    audio = AudioSegment.from_mp3(cached)
                return audio, round(len(audio) / 1000.0, 3)

        return await tqdm_asyncio.gather(
            *[_one(s) for s in ctx.segments],
            desc="Narrating",
            unit="seg",
        )

    results = run_async(_run_all())

    combined = AudioSegment.empty()
    timed_segments = []
    current_time = 0.0
    for i, (audio, duration) in enumerate(results):
        combined += audio
        pause = (PAUSE_MS / 1000.0) if i < len(ctx.segments) - 1 else 0
        if pause > 0:
            combined += AudioSegment.silent(duration=PAUSE_MS)
        timed_segments.append(
            TimedSegment(text=ctx.segments[i].text, start=current_time, end=current_time + duration)
        )
        current_time += duration + pause

    audio_path = output_dir / "narration.mp3"
    combined.export(audio_path, format="mp3")
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
