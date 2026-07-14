"""MiMo TTSProvider — wraps Xiaomi MiMo TTS via OpenAI-compatible chat.completions API.

Supports three models (all limited-time free):
  - mimo-v2.5-tts: Named voice (e.g. "Chloe") + optional style prompt
  - mimo-v2.5-tts-voiceclone: Clone voice from audio file (base64 data URI)
  - mimo-v2.5-tts-voicedesign: Design voice from text description

All three return base64-encoded wav audio in completion.choices[0].message.audio.data.
The provider converts wav → mp3 so the pipeline's AudioSegment.from_mp3() works unchanged.
"""

import asyncio
import base64
from io import BytesIO
from pathlib import Path
from typing import Final

from pydub import AudioSegment

from ..config import Settings
from ..utils.errors import ConfigError
from .base import BaseTTSProvider

# Model names
MIMO_TTS: Final[str] = "mimo-v2.5-tts"
MIMO_VOICECLONE: Final[str] = "mimo-v2.5-tts-voiceclone"
MIMO_VOICEDESIGN: Final[str] = "mimo-v2.5-tts-voicedesign"

_MIMO_MODELS: Final[frozenset[str]] = frozenset({MIMO_TTS, MIMO_VOICECLONE, MIMO_VOICEDESIGN})


class MimoTTSProvider(BaseTTSProvider):
    """Xiaomi MiMo TTS — three modes via chat.completions API.

    The ``voice`` parameter from the pipeline is mode-dependent:
      - mimo-v2.5-tts: voice name (e.g. "Chloe")
      - mimo-v2.5-tts-voiceclone: path to voice clone audio file
      - mimo-v2.5-tts-voicedesign: voice description text (e.g. "young male tone")
    """

    def __init__(self, settings: Settings):
        api_key = settings.mimo_api_key or settings.llm_api_key
        if not api_key:
            raise ConfigError(
                "MiMo TTS requires MN_MIMO_API_KEY or MN_LLM_API_KEY set."
            )
        # Lazy import keeps startup lighter and allows future optional packaging.
        from openai import OpenAI

        self._client = OpenAI(
            api_key=api_key,
            base_url=settings.mimo_base_url,
        )
        self._model = settings.mimo_tts_model
        self._style_prompt = settings.mimo_style_prompt
        self._voice_b64_cache: dict[str, str] = {}

    async def _real_synthesize(self, text: str, voice: str, output_path: Path) -> None:
        if self._model == MIMO_TTS:
            user_content = self._style_prompt
            audio_param = {"format": "wav", "voice": voice}
        elif self._model == MIMO_VOICECLONE:
            user_content = ""
            voice_data_uri = self._encode_voice_file(voice)
            audio_param = {"format": "wav", "voice": voice_data_uri}
        elif self._model == MIMO_VOICEDESIGN:
            user_content = voice  # voice description goes in user message
            audio_param = {"format": "wav", "optimize_text_preview": True}
        else:
            raise ConfigError(
                f"Unsupported MiMo TTS model: {self._model!r}. "
                f"Supported: {sorted(_MIMO_MODELS)}"
            )

        completion = await asyncio.to_thread(
            self._call_api, text, user_content, audio_param
        )

        message = completion.choices[0].message
        if not getattr(message, "audio", None) or not message.audio.data:
            raise RuntimeError(
                f"MiMo TTS returned no audio data (model={self._model})"
            )

        raw = base64.b64decode(message.audio.data)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        # MiMo outputs wav; convert to mp3 so the pipeline's from_mp3() works.
        audio = AudioSegment.from_file(BytesIO(raw), format="wav")
        audio.export(output_path, format="mp3")

    def _call_api(self, text: str, user_content: str, audio_param: dict):
        return self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "user", "content": user_content},
                {"role": "assistant", "content": text},
            ],
            audio=audio_param,
        )

    def _encode_voice_file(self, voice_path: str) -> str:
        """Read voice clone file and return base64 data URI.

        Cached per path to avoid re-reading on every segment.
        """
        if voice_path not in self._voice_b64_cache:
            p = Path(voice_path)
            if not p.exists():
                raise ConfigError(
                    f"Voice clone file not found: {voice_path}"
                )
            b64 = base64.b64encode(p.read_bytes()).decode("utf-8")
            self._voice_b64_cache[voice_path] = f"data:audio/wav;base64,{b64}"
        return self._voice_b64_cache[voice_path]
