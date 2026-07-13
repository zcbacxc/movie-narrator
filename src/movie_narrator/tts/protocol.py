"""TTSProvider abstract interface."""

from abc import ABC, abstractmethod
from pathlib import Path


class TTSProvider(ABC):
    """Abstract TTS backend interface.

    Concrete providers implement :meth:`synthesize` to produce an mp3 file
    at ``output_path``. Duration probing is the pipeline's job — providers
    should NOT call ``AudioSegment.from_mp3`` or similar to compute duration.
    """

    @abstractmethod
    async def synthesize(
        self,
        text: str,
        voice: str,
        output_path: Path,
    ) -> None:
        """Generate one audio segment.

        Args:
            text: narration segment text.
            voice: provider-native voice id.
            output_path: absolute path for the produced mp3. Parent dir must exist.

        Returns:
            None. The caller (pipeline) probes duration from the saved file
            after :meth:`synthesize` returns. Duration is an orchestration
            concern, not a backend concern — keeping it out of providers
            prevents future backends from introducing duplicate duration
            probing and lets every provider focus on "produce mp3 at this
            path".

        Raises:
            Any transport / API / IO exception. Callers handle via CI fallback
            or pipeline error reporting.
        """
