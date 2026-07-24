"""Vision captioning abstraction layer (EP8).

Public API:
    from movie_narrator.vision import (
        VisionCaptioner, StubVisionCaptioner, get_vision_captioner,
    )
"""

from .factory import get_vision_captioner
from .protocol import VisionCaptioner
from .stub import StubVisionCaptioner

__all__ = [
    "StubVisionCaptioner",
    "VisionCaptioner",
    "get_vision_captioner",
]
