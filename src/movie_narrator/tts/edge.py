"""EdgeTTSProvider — wraps edge_tts.Communicate."""

from pathlib import Path

from .base import BaseTTSProvider


class EdgeTTSProvider(BaseTTSProvider):
    """Microsoft Edge-TTS (free, network-dependent, async websocket)."""

    async def _real_synthesize(self, text: str, voice: str, output_path: Path) -> None:
        import edge_tts

        output_path.parent.mkdir(parents=True, exist_ok=True)
        await edge_tts.Communicate(text, voice).save(str(output_path))
