# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Factory: settings → VisionCaptioner instance (EP8).

Uses the :data:`vision_registry` to dispatch provider creation.
Built-in provider (stub) is registered at import time. External
plugins can register additional providers via :func:`register_vision`.
"""

from ..providers import vision_registry
from .protocol import VisionCaptioner


# ── Register built-in Vision providers ────────────────────


def _make_stub(**kwargs) -> VisionCaptioner:
    from .stub import StubVisionCaptioner
    return StubVisionCaptioner()


def _make_vlm(**kwargs) -> VisionCaptioner:
    from .vlm import VLMCaptioner
    return VLMCaptioner(**kwargs)


# Register only if not already registered (handles reload scenarios).
if not vision_registry.contains("stub"):
    vision_registry.register("stub", _make_stub)

if not vision_registry.contains("vlm"):
    vision_registry.register("vlm", _make_vlm)

# Enable protocol validation: create() will TypeError if a factory
# returns something that is not a VisionCaptioner instance.
vision_registry.set_protocol(VisionCaptioner)


# ── Public factory function ───────────────────────────────


def get_vision_captioner(
    provider: str = "stub",
    **kwargs,
) -> VisionCaptioner:
    """Return a VisionCaptioner instance.

    Looks up the provider name in :data:`vision_registry`.

    Args:
        provider: Provider name (e.g. "stub", or plugin-registered names).
        **kwargs: Provider-specific configuration.

    Returns:
        A VisionCaptioner instance.

    Raises:
        ValueError: when the provider is unknown.
    """
    if vision_registry.contains(provider):
        return vision_registry.create(provider, **kwargs)

    raise ValueError(
        f"Unsupported vision captioner provider: {provider!r}. "
        f"Registered: {vision_registry.names()}"
    )
