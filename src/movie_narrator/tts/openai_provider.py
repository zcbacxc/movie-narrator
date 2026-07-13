"""OpenAITTSProvider — wraps sync OpenAI SDK via asyncio.to_thread."""

import asyncio
from pathlib import Path
from typing import Final

from ..config import Settings
from ..utils.errors import ConfigError
from .base import BaseTTSProvider

OPENAI_TTS_VOICES: Final[frozenset[str]] = frozenset(
    {"alloy", "echo", "fable", "onyx", "nova", "shimmer"}
)


class OpenAITTSProvider(BaseTTSProvider):
    """OpenAI TTS via sync SDK wrapped in asyncio.to_thread."""

    def __init__(self, settings: Settings):
        api_key = settings.openai_tts_api_key or settings.llm_api_key
        base_url = settings.openai_tts_base_url or settings.llm_base_url
        if not api_key:
            raise ConfigError(
                "OpenAI TTS requires MN_OPENAI_TTS_API_KEY or MN_LLM_API_KEY set."
            )
        # Lazy import keeps startup lighter and allows future optional packaging.
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = settings.openai_tts_model

    async def _real_synthesize(self, text: str, voice: str, output_path: Path) -> None:
        if voice not in OPENAI_TTS_VOICES:
            raise ConfigError(
                f"OpenAI TTS voice must be one of {sorted(OPENAI_TTS_VOICES)}, "
                f"got {voice!r}. Edge-TTS voices are not accepted by OpenAI."
            )
        output_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._write_audio, text, voice, output_path)

    def _write_audio(self, text: str, voice: str, output_path: Path) -> None:
        with self._client.audio.speech.with_streaming_response.create(
            model=self._model,
            voice=voice,
            input=text,
        ) as resp:
            resp.stream_to_file(str(output_path))
