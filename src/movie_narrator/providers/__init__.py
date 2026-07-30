# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Provider package: unified registry for TTS, Vision, LLM, Research providers.

This package hosts the :class:`ProviderRegistry` and global registry
instances for each provider category. External plugins register their
factories here via :func:`register_tts` / :func:`register_vision` /
:func:`register_llm` / :func:`register_research`.
"""

from .registry import (
    ProviderRegistry,
    llm_registry,
    register_llm,
    register_research,
    register_tts,
    register_vision,
    research_registry,
    tts_registry,
    vision_registry,
)

__all__ = [
    "ProviderRegistry",
    "register_tts",
    "register_vision",
    "register_llm",
    "register_research",
    "tts_registry",
    "vision_registry",
    "llm_registry",
    "research_registry",
]
