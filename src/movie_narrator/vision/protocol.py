"""VisionCaptioner abstract interface (EP8).

Defines the contract for visual scene captioning — producing a
short text description of each scene from video frames, which
feeds the embedding re-rank in match.py for improved D2 (scene-
dialogue relevance).

The current implementation (StubVisionCaptioner) returns placeholder
labels. Future providers (e.g. BLIP, LLaVA) will subclass this
interface and produce real visual descriptions.
"""

from abc import ABC, abstractmethod
from typing import List, Optional

from ..models import Scene


class VisionCaptioner(ABC):
    """Abstract visual scene captioner.

    Concrete providers implement :meth:`caption_scenes` to produce
    a text label per scene from the video's visual frames.

    The labels feed the embedding re-rank in match.py, replacing
    or supplementing audio-transcript-based captions. When vision
    captions are available and high-quality, the fake-caption guard
    (>70% placeholders) is bypassed, unlocking embedding re-rank.
    """

    @abstractmethod
    def caption_scenes(
        self,
        scenes: List[Scene],
        video_path: Optional[str] = None,
    ) -> List[str]:
        """Generate a text caption for each scene.

        Args:
            scenes: list of Scene objects (with .start, .end, .index).
            video_path: path to the source video file. May be None
                when no source video is available (stub returns
                placeholders; real providers should raise).

        Returns:
            List of caption strings, one per scene, aligned 1:1 with
            the input ``scenes`` list. Each caption should be a concise
            visual description suitable for sentence-transformer
            embedding (e.g. "a man in a red jacket runs through rain").

        Raises:
            Any IO or model exception. Callers handle via fallback
            to audio-transcript-based captions.
        """
