"""Vision captioning abstraction layer (EP8 / Q-M5).

Public API:
    from movie_narrator.vision import (
        VisionCaptioner, StubVisionCaptioner, VLMCaptioner,
        get_vision_captioner,
    )
"""

from .factory import get_vision_captioner
from .protocol import VisionCaptioner
from .stub import StubVisionCaptioner
from .vlm import VLMCaptioner

__all__ = [
    "StubVisionCaptioner",
    "VLMCaptioner",
    "VisionCaptioner",
    "get_vision_captioner",
]
