"""Utility functions for the Web UI: upload handling, artifact collection, filename sanitization."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional

from ..utils.sanitize import sanitize_filename
from ..models import Context

# Temp dirs created by save_upload — tracked for cleanup
_tempdirs: List[Path] = []


def save_upload(gradio_file) -> Optional[str]:
    """Copy a Gradio upload to a managed temp dir, return the path.

    Gradio saves uploads to its own temp area which may be cleaned up
    unpredictably. We copy to ``mn_web_*`` temp dirs so we control the
    lifecycle — the temp dir is cleaned up when the app shuts down.
    """
    if gradio_file is None:
        return None
    # Gradio 4.x returns a file path string (or NamedString)
    src = str(gradio_file) if not hasattr(gradio_file, "name") else gradio_file.name
    if not src or not Path(src).exists():
        return None
    tmpdir = Path(tempfile.mkdtemp(prefix="mn_web_"))
    _tempdirs.append(tmpdir)
    dst = tmpdir / Path(src).name
    shutil.copy2(src, dst)
    return str(dst)


def collect_artifacts(ctx: Optional[Context]) -> Optional[List[str]]:
    """Collect existing artifact file paths from *ctx* for download.

    Called at all terminal states (done / failed / cancelled) to
    provide best-effort artifact access. Only files that actually
    exist on disk are included — partial outputs (e.g. script.md
    written before render failed) are still downloadable.
    """
    if ctx is None:
        return None
    artifacts: List[str] = []
    output_dir = Path(ctx.output_dir)

    # Video
    if ctx.video_path and Path(ctx.video_path).exists():
        artifacts.append(ctx.video_path)
    # Audio (prefer final_audio_path, fall back to audio_path)
    for attr in ("final_audio_path", "audio_path"):
        p = getattr(ctx, attr, None)
        if p and Path(p).exists():
            artifacts.append(p)
            break
    # Subtitles
    if ctx.subtitle_path and Path(ctx.subtitle_path).exists():
        artifacts.append(ctx.subtitle_path)
    if ctx.subtitle_paths:
        for attr in ("translated", "bilingual"):
            p = getattr(ctx.subtitle_paths, attr, None)
            if p and Path(p).exists():
                artifacts.append(p)
    # Script
    if ctx.script_md_path and Path(ctx.script_md_path).exists():
        artifacts.append(ctx.script_md_path)
    # Metadata
    metadata_path = output_dir / "metadata.json"
    if metadata_path.exists():
        artifacts.append(str(metadata_path))

    return artifacts if artifacts else None


def cleanup_tempdirs() -> None:
    """Clean up all temp dirs created by :func:`save_upload`."""
    for d in _tempdirs:
        try:
            shutil.rmtree(d)
        except Exception:
            pass
    _tempdirs.clear()
