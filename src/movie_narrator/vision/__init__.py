# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Vision captioning abstraction layer (VLM captioning).

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
