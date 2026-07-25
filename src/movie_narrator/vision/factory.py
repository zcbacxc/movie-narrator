"""Factory: settings → VisionCaptioner instance (EP8).

Uses the :data:`vision_registry` to dispatch provider creation.
Built-in provider (stub) is registered at import time. External
plugins can register additional providers (blip, llava, http_vlm,
etc.) via :func:`register_vision`.
"""

from ..providers import vision_registry
from .protocol import VisionCaptioner


# ── Register built-in Vision providers ────────────────────


def _make_stub(**kwargs) -> VisionCaptioner:
    from .stub import StubVisionCaptioner
    return StubVisionCaptioner()


# Register only if not already registered (handles reload scenarios).
if not vision_registry.contains("stub"):
    vision_registry.register("stub", _make_stub)


# ── Public factory function ───────────────────────────────


def get_vision_captioner(
    provider: str = "stub",
    **kwargs,
) -> VisionCaptioner:
    """Return a VisionCaptioner instance.

    Looks up the provider name in :data:`vision_registry` first.
    Falls back to the old if/elif chain for backward compatibility.

    Args:
        provider: Provider name (e.g. "stub", "http_vlm").
        **kwargs: Provider-specific configuration.

    Returns:
        A VisionCaptioner instance.

    Raises:
        ValueError: when the provider is unknown.
    """
    # Registry path (preferred)
    if vision_registry.contains(provider):
        return vision_registry.create(provider, **kwargs)

    # Legacy fallback
    if provider == "stub":
        return _make_stub(**kwargs)

    raise ValueError(
        f"Unsupported vision captioner provider: {provider!r}. "
        f"Registered: {vision_registry.names()}"
    )
