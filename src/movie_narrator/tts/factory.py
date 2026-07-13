"""Factory: settings → TTSProvider instance."""

from ..config import Settings, TTSProviderType
from ..utils.errors import ConfigError
from .protocol import TTSProvider


def get_tts_provider(settings: Settings) -> TTSProvider:
    if settings.tts_provider is TTSProviderType.EDGE:
        from .edge import EdgeTTSProvider
        return EdgeTTSProvider()
    elif settings.tts_provider is TTSProviderType.OPENAI:
        from .openai_provider import OpenAITTSProvider
        return OpenAITTSProvider(settings)
    else:
        raise ConfigError(
            f"Unsupported TTS provider: {settings.tts_provider!r}. "
            f"Supported: {list(TTSProviderType)}"
        )
