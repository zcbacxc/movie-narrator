"""Factory: settings → TTSProvider instance.

Uses the :data:`tts_registry` to dispatch provider creation.
Built-in providers (edge, openai, mimo) are registered at import
time below. External plugins can register additional providers
via :func:`register_tts`.

Backward compatibility: if a provider name is not in the registry,
the old if/elif chain is tried as a fallback. This ensures existing
code that depends on ``TTSProviderType`` continues to work during
the transition period.
"""

from ..config import Settings, TTSProviderType
from ..providers import tts_registry
from ..utils.errors import ConfigError
from .protocol import TTSProvider


# ── Register built-in TTS providers ───────────────────────


def _make_edge(settings: Settings) -> TTSProvider:
    from .edge import EdgeTTSProvider
    return EdgeTTSProvider()


def _make_openai(settings: Settings) -> TTSProvider:
    from .openai_provider import OpenAITTSProvider
    return OpenAITTSProvider(settings)


def _make_mimo(settings: Settings) -> TTSProvider:
    from .mimo_provider import MimoTTSProvider
    return MimoTTSProvider(settings)


# Register only if not already registered (handles reload scenarios).
for _name, _factory in [
    (TTSProviderType.EDGE.value, _make_edge),
    (TTSProviderType.OPENAI.value, _make_openai),
    (TTSProviderType.MIMO.value, _make_mimo),
]:
    if not tts_registry.contains(_name):
        tts_registry.register(_name, _factory)


# ── Public factory function ───────────────────────────────


def get_tts_provider(settings: Settings) -> TTSProvider:
    """Return a TTSProvider instance for the configured provider.

    Looks up the provider name in :data:`tts_registry` first.
    Falls back to the enum-based if/elif chain for backward
    compatibility with any code that might register custom
    TTSProviderType values without updating the registry.

    Raises:
        ConfigError: when the provider is unsupported.
    """
    provider_name = (
        settings.tts_provider.value
        if isinstance(settings.tts_provider, TTSProviderType)
        else str(settings.tts_provider)
    )

    # Registry path (preferred)
    if tts_registry.contains(provider_name):
        return tts_registry.create(provider_name, settings)

    # Legacy fallback (should not be reached for built-in providers)
    if settings.tts_provider is TTSProviderType.EDGE:
        return _make_edge(settings)
    elif settings.tts_provider is TTSProviderType.OPENAI:
        return _make_openai(settings)
    elif settings.tts_provider is TTSProviderType.MIMO:
        return _make_mimo(settings)

    raise ConfigError(
        f"Unsupported TTS provider: {settings.tts_provider!r}. "
        f"Registered: {tts_registry.names()}"
    )
