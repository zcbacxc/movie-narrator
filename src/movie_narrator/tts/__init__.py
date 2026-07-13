"""TTS abstraction layer (v0.4).

Public API:
    from movie_narrator.tts import (
        get_tts_provider, TTSProvider, TTSProviderType,
        TTSCacheKey, is_ci,
    )
"""

from .base import BaseTTSProvider, is_ci
from .cache import (
    CACHE_SCHEMA_VERSION,
    PROVIDER_CACHE_VERSIONS,
    TTSCacheKey,
    cache_path_for,
)
from .factory import get_tts_provider
from .protocol import TTSProvider

__all__ = [
    "BaseTTSProvider",
    "CACHE_SCHEMA_VERSION",
    "PROVIDER_CACHE_VERSIONS",
    "TTSCacheKey",
    "TTSProvider",
    "cache_path_for",
    "get_tts_provider",
    "is_ci",
]
