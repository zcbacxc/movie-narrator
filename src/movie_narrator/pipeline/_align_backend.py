"""Environment-adaptive backend selection for audio alignment.

WhisperX (the original backend) depends on pyannote VAD → speechbrain →
k2-fsa, whose C++ extension has no prebuilt Windows CPU wheel. On top of
that, torch 2.8's ``weights_only=True`` default rejects pyannote's
omegaconf pickle. These are upstream issues that make WhisperX unusable
on Windows CPU and fragile on Linux CPU.

faster-whisper is a CTranslate2 reimplementation that does not depend on
pyannote / speechbrain / k2-fsa. L2 handtest showed it transcribes 60s
of Chinese audio in 1.9s on CPU with the ``small`` model, producing 18
accurate segments. The only thing it lacks is word-level forced
alignment, but ``subtitle.py`` only consumes segment-level timestamps
(``seg.start`` / ``seg.end`` / ``seg.text``), so the loss has zero
downstream impact.

This module decides which backend to use, runs it, and returns a
uniform ``wx_segments`` list that ``align.py``'s remapping loop consumes
unchanged.
"""

from __future__ import annotations

import platform
from typing import List, Tuple

from ..models import Context
from ..utils.optional_deps import probe


class BackendUnavailable(Exception):
    """Raised when a backend cannot be initialized."""


def select_align_backend(ctx: Context) -> Tuple[str, str]:
    """Return ``(backend, reason)``.

    ``backend`` is one of ``"whisperx"``, ``"faster_whisper"``, ``"none"``.
    The decision is based on:

    1. Explicit override via ``ctx.metadata['align_backend']``
    2. GPU available + whisperx importable → whisperx
    3. CPU + whisperx importable + non-Windows → whisperx
       (k2-fsa has prebuilt wheels on Linux/macOS)
    4. Windows CPU + whisperx importable → faster_whisper
       (k2-fsa has no prebuilt Windows CPU wheel)
    5. whisperx not importable → faster_whisper
    6. faster_whisper not importable → none
    """
    # 1. Explicit override
    override = ctx.metadata.get("align_backend")
    if override in ("whisperx", "faster_whisper"):
        ok, _ = probe(override)
        if ok:
            return override, f"explicit override (align_backend={override})"
        # Override requested but unavailable — fall through to auto-detect
        # and record the failure reason in metadata
        ctx.metadata.setdefault("align_backend_attempted", []).append(
            f"{override}: explicit override but import failed"
        )

    # 2-5. Auto-detect
    wx_ok, _ = probe("whisperx")
    fw_ok, _ = probe("faster_whisper")
    device = ctx.metadata.get("whisperx_device", "cpu")
    is_windows = platform.system() == "Windows"

    if wx_ok:
        if device == "cuda":
            return "whisperx", "GPU available + whisperx importable"
        if not is_windows:
            # Linux/macOS CPU: k2-fsa has prebuilt wheels
            return "whisperx", "CPU (non-Windows) + whisperx importable"
        # Windows CPU: k2-fsa likely missing → prefer faster_whisper
        if fw_ok:
            return "faster_whisper", "Windows CPU (k2-fsa may be unavailable) → faster_whisper"
        # faster_whisper not installed, but whisperx is — try whisperx
        # and let it fail at load_align_model time (graceful fallback)
        return "whisperx", "Windows CPU but faster_whisper not installed; trying whisperx"

    # whisperx not importable
    if fw_ok:
        return "faster_whisper", "whisperx not importable → faster_whisper"

    return "none", "neither whisperx nor faster_whisper importable"


def run_faster_whisper(ctx: Context) -> List[dict]:
    """Transcribe with faster-whisper, return ``wx_segments`` list.

    Each element: ``{"start": float, "end": float, "text": str}`` — the
    same shape WhisperX's transcribe produces, so ``align.py``'s
    remapping loop consumes it unchanged.

    No forced alignment (word-level timestamps) — segment-level only.
    This is sufficient for ``subtitle.py`` which only reads
    ``seg.start`` / ``seg.end`` / ``seg.text``.
    """
    try:
        from faster_whisper import WhisperModel
    except ImportError as e:
        raise BackendUnavailable(f"faster-whisper not installed: {e}") from e

    device = ctx.metadata.get("whisperx_device", "cpu")
    language = ctx.metadata.get("whisperx_language", "zh")
    model_size = ctx.metadata.get("whisperx_model", "small")

    # CPU: int8 quantization (fast + small); GPU: float16
    if device == "cuda":
        compute_type = "float16"
        fw_device = "cuda"
    else:
        compute_type = "int8"
        fw_device = "cpu"

    model = WhisperModel(model_size, device=fw_device, compute_type=compute_type)
    segments, _info = model.transcribe(ctx.audio_path, language=language)

    wx_segments: List[dict] = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            wx_segments.append({
                "start": float(seg.start),
                "end": float(seg.end),
                "text": text,
            })
    return wx_segments
