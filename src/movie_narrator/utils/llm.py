# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""LLM client factory backed by the provider registry.

The built-in ``"openai"`` provider is registered at import time.
External plugins can register additional LLM providers via
:func:`register_llm`.

``get_llm_client()`` remains a zero-argument callable that returns a
context manager yielding :class:`LLMClient`. This preserves backward
compatibility with all existing call sites and test patches.
"""

from dataclasses import dataclass
from contextlib import contextmanager

import httpx
from openai import OpenAI

from ..config import get_settings
from ..providers import llm_registry, register_llm


@dataclass
class LLMClient:
    client: OpenAI
    model: str


# ── Built-in "openai" provider ───────────────────────────


@register_llm("openai")
def _make_openai_llm():
    """Factory for the OpenAI-compatible LLM provider.

    Returns a context manager that yields an :class:`LLMClient`
    backed by a managed ``httpx.Client`` (closed on exit).
    """

    @contextmanager
    def _cm():
        settings = get_settings()
        http_client = httpx.Client(timeout=settings.llm_timeout)
        try:
            client = OpenAI(
                base_url=settings.llm_base_url,
                api_key=settings.llm_api_key,
                http_client=http_client,
            )
            yield LLMClient(client=client, model=settings.llm_model)
        finally:
            http_client.close()

    return _cm()


# ── Public factory function ──────────────────────────────


def get_llm_client():
    """Yield an LLMClient via the llm_registry.

    Dispatches to the provider configured by ``settings.llm_provider``
    (default: ``"openai"``). The returned object is a context manager
    that must be used in a ``with`` statement.

    This function's module path (``movie_narrator.utils.llm``) and
    zero-argument signature are preserved for backward compatibility
    with existing call sites and test patches.
    """
    settings = get_settings()
    return llm_registry.create(settings.llm_provider)
