"""Provider package: unified registry for TTS, Vision, and future providers.

This package hosts the :class:`ProviderRegistry` and global registry
instances for each provider category. External plugins register their
factories here via :func:`register_tts` / :func:`register_vision`.
"""

from .registry import (
    ProviderRegistry,
    register_tts,
    register_vision,
    tts_registry,
    vision_registry,
)

__all__ = [
    "ProviderRegistry",
    "register_tts",
    "register_vision",
    "tts_registry",
    "vision_registry",
]
