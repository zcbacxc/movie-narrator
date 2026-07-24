"""Factory: settings → VisionCaptioner instance (EP8).

Currently only returns the StubVisionCaptioner. When real vision
providers (BLIP, LLaVA, etc.) are implemented, they will be
dispatched here based on settings/configuration.
"""

from .protocol import VisionCaptioner
from .stub import StubVisionCaptioner


def get_vision_captioner(
    provider: str = "stub",
    **kwargs,
) -> VisionCaptioner:
    """Return a VisionCaptioner instance.

    Args:
        provider: "stub" (default) — returns StubVisionCaptioner.
            Future: "blip", "llava", etc.
        **kwargs: provider-specific configuration.

    Returns:
        A VisionCaptioner instance.

    Raises:
        ValueError: when the provider is unknown.
    """
    if provider == "stub":
        return StubVisionCaptioner()

    # Future providers will be dispatched here:
    # elif provider == "blip":
    #     from .blip import BlipVisionCaptioner
    #     return BlipVisionCaptioner(**kwargs)

    raise ValueError(
        f"Unsupported vision captioner provider: {provider!r}. "
        f"Supported: ['stub']"
    )
