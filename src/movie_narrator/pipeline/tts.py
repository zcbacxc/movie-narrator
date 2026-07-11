import os

import pydub
from pydub import AudioSegment

from ..config import get_settings
from ..models import Context, TimedSegment
from ..utils.async_utils import run_async

DEFAULT_VOICE = get_settings().default_voice
PAUSE_MS = 300


def _estimate_duration(text: str) -> float:
    return max(1.0, len(text) * 0.35)


async def _generate_all(segments, voice: str, cache_dir) -> list[tuple[AudioSegment, float]]:
    if os.getenv("CI"):
        durations = [_estimate_duration(seg.text) for seg in segments]
        return [(AudioSegment.silent(duration=int(d * 1000)), d) for d in durations]

    import edge_tts, hashlib, json
    from pathlib import Path

    CACHE_VERSION = "v1"
    MAX_CONCURRENT = 3

    def _cache_key(text: str, voice: str) -> str:
        data = {"version": CACHE_VERSION, "text": text, "voice": voice, "pause_ms": PAUSE_MS}
        return hashlib.md5(json.dumps(data, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

    sem = __import__("asyncio").Semaphore(MAX_CONCURRENT)

    async def _one(seg):
        async with sem:
            key = _cache_key(seg.text, voice)
            tmp = cache_dir / f"{key}.mp3"
            if not tmp.exists():
                await edge_tts.Communicate(seg.text, voice).save(str(tmp))
            audio = AudioSegment.from_mp3(tmp)
            return audio, round(len(audio) / 1000.0, 3)

    return await __import__("asyncio").gather(*[_one(s) for s in segments])


def generate_voice(ctx: Context) -> Context:
    from pathlib import Path
    output_dir = Path(ctx.metadata["output_dir"])
    cache_dir = output_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    voice = ctx.metadata.get("voice") or DEFAULT_VOICE
    results = run_async(_generate_all(ctx.segments, voice, cache_dir))

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
    return ctx
