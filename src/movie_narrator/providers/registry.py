# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Provider registry for TTS, Vision, LLM, Research, and other pluggable providers.

Each provider category (TTS, Vision, LLM, Research, etc.) has a global
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

Example (LLM)::

    from movie_narrator import register_llm

    @register_llm("anthropic")
    def make_anthropic():
        ...
        return context_manager

Example (Research)::

    from movie_narrator import register_research

    @register_research("web_search")
    def make_web_search(ctx, settings):
        return ResearchInfo(...)
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Type


class ProviderRegistry:
    """Registry for provider factories.

    A factory is a callable ``(config) -> provider_instance``. The
    config type varies by category (Settings for TTS, kwargs for Vision)
    but the registration interface is identical.

    Args:
        category: Human-readable category name for error messages.
        protocol: Optional ABC/Protocol for ``create()`` return-value
            validation. When set, ``create()`` raises ``TypeError`` if
            the factory returns an object that is not an instance of
            *protocol*. Defaults to ``None`` (no validation).
    """

    def __init__(
        self,
        category: str = "provider",
        protocol: Optional[Type[Any]] = None,
    ) -> None:
        self._category = category
        self._factories: Dict[str, Callable[..., Any]] = {}
        self._protocol = protocol

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
        """
        Returns:
            The factory for *name*, or None.
        """
        return self._factories.get(name)

    def create(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Create a provider instance by name.

        Args:
            name: Provider identifier.
            *args, **kwargs: Passed to the factory.

        Raises:
            ValueError: if *name* is not registered.
            TypeError: if the factory returns an object that is not an
                instance of the registry's *protocol* (when configured).
        """
        factory = self._factories.get(name)
        if factory is None:
            available = sorted(self._factories.keys())
            raise ValueError(
                f"Unknown {self._category} provider: {name!r}. "
                f"Registered: {available}"
            )
        instance = factory(*args, **kwargs)
        if self._protocol is not None and not isinstance(instance, self._protocol):
            raise TypeError(
                f"{self._category} provider '{name}' factory returned "
                f"{type(instance).__name__} which is not a "
                f"{self._protocol.__name__} instance."
            )
        return instance

    def names(self) -> List[str]:
        """
        Returns:
            All registered provider names.
        """
        return list(self._factories.keys())

    def contains(self, name: str) -> bool:
        """Check if a provider name is registered."""
        return name in self._factories

    def info(self) -> List[Dict[str, Any]]:
        """
        Returns:
            A list of dicts describing each registered provider.

            Each dict contains the provider name, category, and whether
            protocol validation is enabled.
        """
        return [
            {
                "name": name,
                "category": self._category,
                "protocol_validated": self._protocol is not None,
            }
            for name in self._factories
        ]

    def clear(self) -> None:
        """Remove all registered factories. For testing only."""
        self._factories.clear()

    def set_protocol(self, protocol: Optional[Type[Any]]) -> None:
        """Set or clear the protocol for ``create()`` return-value validation.

        This is intended for use by factory modules that import the
        provider ABC (e.g. ``tts/protocol.py``) — setting the protocol
        at import time in ``providers/registry.py`` would cause circular
        imports.

        Args:
            protocol: ABC/Protocol class, or ``None`` to disable validation.
        """
        self._protocol = protocol


# ── Global registry instances ────────────────────────────

# TTS and Vision registries validate protocol conformance at create() time.
# LLM and Research registries do not (LLM factories return context managers,
# Research factories return ResearchInfo — neither has a shared ABC).

tts_registry = ProviderRegistry(category="tts")
vision_registry = ProviderRegistry(category="vision")
llm_registry = ProviderRegistry(category="llm")
research_registry = ProviderRegistry(category="research")


# ── Decorators ────────────────────────────────────────────


def register_tts(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a TTS provider factory.

    Usage::

        @register_tts("elevenlabs")
        def make_elevenlabs(settings):
            return ElevenLabsProvider(settings)
    """

    def decorator(factory: Callable[..., Any]) -> Callable[..., Any]:
        """Register a decorator function."""
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
        """Register a decorator function."""
        return vision_registry.register(name, factory)

    return decorator


def register_llm(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register an LLM provider factory.

    The factory should return a context manager that yields an
    :class:`~movie_narrator.utils.llm.LLMClient`.

    Usage::

        @register_llm("anthropic")
        def make_anthropic():
            @contextmanager
            def _cm():
                ...
                yield LLMClient(client=..., model=...)
            return _cm()
    """

    def decorator(factory: Callable[..., Any]) -> Callable[..., Any]:
        """Register a decorator function."""
        return llm_registry.register(name, factory)

    return decorator


def register_research(name: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator to register a research provider factory.

    The factory receives ``(ctx, settings)`` and should return a
    :class:`~movie_narrator.models.ResearchInfo` instance.

    Usage::

        @register_research("web_search")
        def make_web_search(ctx, settings):
            return ResearchInfo(...)
    """

    def decorator(factory: Callable[..., Any]) -> Callable[..., Any]:
        """Register a decorator function."""
        return research_registry.register(name, factory)

    return decorator
