# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""FunASR Chinese ASR backend (G6).

Wraps FunASR's Paraformer-zh model into a :class:`FunASRProvider` that
produces the same ``wx_segments`` shape (``{"start", "end", "text"}``) as
:func:`movie_narrator.pipeline._align_backend.transcribe_with_faster_whisper`,
so the align remapping loop consumes it unchanged.

Key features proxied from FunASR:

- **Integrated timestamps** — Paraformer-zh emits character/word-level
  timestamps inline; we aggregate them into sentence-level segments by
  splitting on punctuation boundaries.
- **Hotwords** — an optional hotword phrase string is passed through to the
  model's ``generate(..., hotword=...)`` to bias recognition toward domain
  terms.

This module is strictly optional: importing it never requires FunASR to be
installed. The model is loaded lazily on first ``transcribe`` and the
underlying dependency is exposed only via the ``[ml]`` extra.
"""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Default model + revision (Paraformer-zh, integrated timestamps).
_DEFAULT_MODEL = "paraformer-zh"
_DEFAULT_VAD_MODEL = "fsmn-vad"

# CJK/Latin punctuation that ends a sentence segment in the merged output.
_SENTENCE_BOUNDARIES = ("。", "！", "？", "!", "?", ".", "，", ",", "；", ";")


class FunASRUnavailable(Exception):
    """Raised when FunASR is not installed."""


def _segments_from_timestamps(text: str, timestamp: Optional[list]) -> List[dict]:
    """Merge char/word-level timestamps into sentence-level segments.

    ``timestamp`` is FunASR's per-character ``[start, end]`` list aligned
    with ``text``. When the alignment is missing or the lengths differ, we
    fall back to a single segment spanning the first/last timestamp.

    Returns:
        A ``wx_segments`` list (``{"start", "end", "text"}``).
    """
    text = (text or "").strip()
    if not text:
        return []

    if not timestamp or len(timestamp) != len(text):
        if timestamp:
            start = float(timestamp[0][0])
            end = float(timestamp[-1][1])
            return [{"start": start, "end": end, "text": text}]
        return [{"start": 0.0, "end": 0.0, "text": text}]

    segments: List[dict] = []
    cur_chars: List[str] = []
    cur_start: Optional[float] = None
    cur_end: float = 0.0

    for char, (seg_start, seg_end) in zip(text, timestamp):
        if cur_start is None:
            cur_start = float(seg_start)
        cur_chars.append(char)
        cur_end = float(seg_end)
        if char in _SENTENCE_BOUNDARIES:
            sentence = "".join(cur_chars).strip()
            if sentence:
                segments.append(
                    {"start": cur_start, "end": cur_end, "text": sentence}
                )
            cur_chars = []
            cur_start = None

    if cur_chars:
        sentence = "".join(cur_chars).strip()
        if sentence:
            segments.append({"start": cur_start, "end": cur_end, "text": sentence})

    return segments


class FunASRProvider:
    """Lazy-loaded FunASR Paraformer-zh provider.

    The model is instantiated on first ``transcribe`` so that merely
    importing this module has no FunASR dependency or model-download cost.
    """

    def __init__(
        self,
        device: str = "cpu",
        model: Optional[str] = None,
        vad_model: Optional[str] = None,
        model_dir: Optional[str] = None,
    ) -> None:
        self.device = device
        self.model = model or _DEFAULT_MODEL
        self.vad_model = vad_model or _DEFAULT_VAD_MODEL
        self.model_dir = model_dir
        self._auto_model = None

    def _ensure_model(self):
        """Import FunASR and build the AutoModel (once)."""
        if self._auto_model is not None:
            return self._auto_model
        try:
            from funasr import AutoModel
        except ImportError as exc:
            raise FunASRUnavailable(
                f"funasr not installed: {exc}. Run: pip install \"movie-narrator[ml]\""
            ) from exc

        kwargs: Dict[str, object] = {}
        if self.model_dir:
            kwargs["model"] = self.model_dir
        else:
            kwargs["model"] = self.model
            kwargs["vad_model"] = self.vad_model
        if self.device == "cuda":
            kwargs["device"] = "cuda:0"
        logger.debug("building FunASR AutoModel with %s", {k: v for k, v in kwargs.items() if k != "model"})
        self._auto_model = AutoModel(**kwargs)
        return self._auto_model

    def transcribe(
        self,
        audio_path: str,
        language: str = "zh",
        hotword: Optional[str] = None,
        batch_size_s: int = 300,
    ) -> List[dict]:
        """Transcribe ``audio_path`` and return ``wx_segments``.

        Args:
            audio_path: Input audio/video file path.
            language: Target language tag (FunASR Paraformer is zh-focused).
            hotword: Optional space-separated hotword phrase to bias decoding.
            batch_size_s: VAD batch duration in seconds for integrated timestamps.

        Returns:
            List of ``{"start", "end", "text"}`` segments.
        """
        model = self._ensure_model()
        gen_kwargs: Dict[str, object] = {"batch_size_s": batch_size_s}
        if hotword:
            gen_kwargs["hotword"] = hotword
        result = model.generate(input=audio_path, **gen_kwargs)

        if not result:
            return []
        first = result[0]
        if isinstance(first, dict):
            text = first.get("text") or ""
            timestamp = first.get("timestamp")
        else:
            text = getattr(first, "text", "") or ""
            timestamp = getattr(first, "timestamp", None)
        return _segments_from_timestamps(text, timestamp)


def transcribe_with_funasr(
    audio_path: str,
    device: str = "cpu",
    language: str = "zh",
    model: Optional[str] = None,
    model_dir: Optional[str] = None,
    hotword: Optional[str] = None,
) -> List[dict]:
    """Convenience wrapper around :class:`FunASRProvider`.

    Shared backend for the align step (narration audio). Returns the same
    ``wx_segments`` shape as the faster-whisper and whisperx backends.
    """
    provider = FunASRProvider(device=device, model=model, model_dir=model_dir)
    return provider.transcribe(audio_path=audio_path, language=language, hotword=hotword)
