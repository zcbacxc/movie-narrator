# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Video-audio transcription for scene-level captions."""

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def _video_audio_hash(video_path: str) -> str:
    """Lightweight cache key from file stat — avoids reading the full
    video file so cache hits incur zero I/O overhead.

    Uses ``mtime`` + ``size``: collisions are practically impossible
    for this use-case (same values = file wasn't re-encoded).
    """
    s = os.stat(video_path)
    raw = f"{s.st_mtime}_{s.st_size}".encode()
    return hashlib.sha256(raw).hexdigest()[:16]


def _cache_key(video_path: str, model_name: str, language: str) -> str:
    """Build a cache key that includes model and language.

    Without model/language in the key, switching from small/zh to
    medium/en would silently reuse the wrong transcript.
    """
    file_hash = _video_audio_hash(video_path)
    return f"transcript_{file_hash}_{model_name}_{language}.json"


def _transcribe_video_audio(
    video_path: str,
    output_dir: Path,
    device: str = "cpu",
    model_name: str = "medium",
    language: str = "zh",
) -> Optional[List[dict]]:
    """Transcribe the video's audio track for scene-level captions.

    Tries WhisperX first (word-level alignment). If WhisperX is not
    importable or fails (common on Windows CPU due to k2-fsa missing),
    falls back to faster-whisper (CTranslate2, no pyannote/k2-fsa deps).

    Returns:
        A list of ``{"start", "end", "text"}`` dicts, or ``None``
        when both backends are unavailable or transcription fails.
        Results are cached per video file hash + model + language.
    """
    cache_path = output_dir / _cache_key(video_path, model_name, language)

    # Cache hit
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("corrupt transcription cache at %s", cache_path, exc_info=True)

    # Try WhisperX first (preserves forced alignment if available)
    try:
        import whisperx

        audio = whisperx.load_audio(video_path)
        model = whisperx.load_model(model_name, device=device)
        result = model.transcribe(audio, language=language)

        segments = []
        if result and "segments" in result:
            for wseg in result["segments"]:
                start = wseg.get("start", 0.0)
                end = wseg.get("end", 0.0)
                text = wseg.get("text", "").strip()
                if text:
                    segments.append({"start": start, "end": end, "text": text})

        if segments:
            cache_path.write_text(
                json.dumps(segments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return segments if segments else None
    except Exception as wx_err:
        logger.warning("WhisperX video transcription failed: %s", wx_err, exc_info=True)
        # Fall through to faster-whisper

    # Fallback: faster-whisper (works on Windows CPU where k2-fsa missing)
    try:
        from .._align_backend import transcribe_with_faster_whisper

        segments = transcribe_with_faster_whisper(
            audio_path=video_path,
            device=device,
            language=language,
            # Use "small" for faster-whisper fallback (int8 on CPU);
            # WhisperX's "medium" would be too slow without GPU.
            model_size="small" if model_name == "medium" else model_name,
        )
        if segments:
            cache_path.write_text(
                json.dumps(segments, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        return segments if segments else None
    except Exception as fw_err:
        logger.warning("faster-whisper video transcription failed: %s", fw_err, exc_info=True)
        return None
