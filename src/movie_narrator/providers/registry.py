"""Provider registry for TTS, Vision, and other pluggable providers.

Each provider category (TTS, Vision, etc.) has a global
:class:`ProviderRegistry` instance. Providers register a factory
callable that takes configuration (settings/kwargs) and returns an
instance of the provider protocol.

Example (TTS)::

    from movie_narrator import register_tts

    @register_tts("elevenlabs")
    def make_elevenlabs(settings):
        from .elevenlabs import ElevenLabsProvider
        return ElevenLabsProvider(settings)

Example (Vision)::

    from movie_narrator import register_vision

    @register_vision("blip")
    def make_blip(**kwargs):
        from .blip import BlipCaptioner
        return BlipCaptioner(**kwargs)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional


class ProviderRegistry:
    """Registry for provider factories.

    A factory is a callable ``(config) -> provider_instance``. The
    config type varies by category (Settings for TTS, kwargs for Vision)
    but the registration interface is identical.
    """

    def __init__(self, category: str = "provider") -> None:
        self._category = category
        self._factories: Dict[str, Callable[..., Any]] = {}

    def register(
        self,
        name: str,
        factory: Callable[..., Any],
    ) -> Callable[..., Any]:
        """Register a provider factory and return it (decorator-friendly).

        Args:
            name: Unique provider identifier (e.g. ``"edge"``, ``"mimo"``).
            factory: Callable that accepts config and returns a provider
                instance.

        Raises:
            ValueError: if *name* is already registered.
        """
        if name in self._factories:
            raise ValueError(
                f"{self._category} provider '{name}' is already registered. "
                f"Use a different name or unregister first."
            )
        self._factories[name] = factory
        return factory

    def unregister(self, name: str) -> None:
        """Remove a provider factory."""
        if name not in self._factories:
            raise KeyError(f"{self._category} provider '{name}' is not registered.")
        del self._factories[name]

    def get(self, name: str) -> Optional[Callable[..., Any]]:
        """Return the factory for *name*, or None."""
        return self._factories.get(name)

    def create(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Create a provider instance by name.

        Args:
            name: Provider identifier.
            *args, **kwargs: Passed to the factory.

        Raises:
            ValueError: if *name* is not registered.
        """
        factory = self._factories.get(name)
        if factory is None:
            available = sorted(self._factories.keys())
            raise ValueError(
                f"Unknown {self._category} provider: {name!r}. "
                f"Registered: {available}"
            )
        return factory(*args, **kwargs)

    def names(self) -> List[str]:
        """Return all registered provider names."""
        return list(self._factories.keys())

    def contains(self, name: str) -> bool:
        """Check if a provider name is registered."""
        return name in self._factories

    def clear(self) -> None:
        """Remove all registered factories. For testing only."""
        self._factories.clear()


# ── Global registry instances ────────────────────────────

tts_registry = ProviderRegistry(category="tts")
vision_registry = ProviderRegistry(category="vision")


# ── Decorators ────────────────────────────────────────────


def register_tts(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a TTS provider factory.

    Usage::

        @register_tts("elevenlabs")
        def make_elevenlabs(settings):
            return ElevenLabsProvider(settings)
    """

    def decorator(factory: Callable[..., Any]) -> Callable[..., Any]:
        return tts_registry.register(name, factory)

    return decorator


def register_vision(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a Vision captioner provider factory.

    Usage::

        @register_vision("blip")
        def make_blip(**kwargs):
            return BlipCaptioner(**kwargs)
    """

    def decorator(factory: Callable[..., Any]) -> Callable[..., Any]:
        return vision_registry.register(name, factory)

    return decorator
