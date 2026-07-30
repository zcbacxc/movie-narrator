# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""BaseTTSProvider — owns CI silent fallback; subclasses implement _real_synthesize."""

import os
from abc import abstractmethod
from pathlib import Path

from pydub import AudioSegment

from ..workflow.errors import ProviderError, is_network_error
from .protocol import TTSProvider


def is_ci() -> bool:
    """Single source of truth for CI detection.

    The pipeline imports this — no duplicate ``os.getenv("CI")`` checks.
    """
    return bool(os.getenv("CI"))


# Calibration rationale:
#   Real Edge-TTS Chinese ≈ 13 chars/sec.
#   Real OpenAI tts-1 Mandarin ≈ 12–14 chars/sec.
# We pick a SHORTER density (~10 chars/sec) than real so the CI
# placeholder audio OVER-estimates real duration slightly. Better
# to land a few extra seconds of silence than to clip a cue mid-word:
# the latter produces a render with a missing subtitle line.
_EST_CHARS_PER_SEC = 10.0
_SILENT_FLOOR_SEC = 1.0


def _estimate_duration_s(text: str) -> float:
    return max(_SILENT_FLOOR_SEC, len(text) / _EST_CHARS_PER_SEC)


class BaseTTSProvider(TTSProvider):
    """Concrete providers implement ``_real_synthesize``. CI behavior is shared."""

    async def synthesize(self, text: str, voice: str, output_path: Path) -> None:
        if is_ci():
            await self._silent_synthesize(text, output_path)
            return
        # R2-NA-ORCH: wrap the real (network-dependent) synthesis call so
        # transient failures are flagged retryable. Network/timeout errors
        # (ConnectionError, TimeoutError, openai APITimeoutError /
        # APIConnectionError / RateLimitError) become a ProviderError with
        # retryable=True, which the pipeline runner uses to offer interactive
        # [R]etry/[S]kip/[A]bort when --retry is enabled. Config/logic errors
        # (ConfigError, invalid voice, missing voice-clone file) are NOT
        # network errors and are re-raised unchanged (non-retryable).
        try:
            await self._real_synthesize(text, voice, output_path)
        except Exception as e:
            if is_network_error(e):
                raise ProviderError(
                    f"TTS synthesis failed (transient network error): {e}",
                    retryable=True,
                ) from e
            raise

    async def _silent_synthesize(self, text: str, output_path: Path) -> None:
        """Write silence sized to text; CI mode never blocks on network.

        Duration is NOT returned — the pipeline probes it from the
        resulting file via ``AudioSegment.from_mp3``. This keeps duration
        calculation in one place across cache-hit, cache-miss, and CI paths.
        """
        dur = _estimate_duration_s(text)
        audio = AudioSegment.silent(duration=int(dur * 1000))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        audio.export(output_path, format="mp3")

    @abstractmethod
    async def _real_synthesize(self, text: str, voice: str, output_path: Path) -> None:
        ...
