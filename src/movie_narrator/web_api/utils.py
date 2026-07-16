"""Utility functions for the Web API — uploads, artifacts, zip."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import List, Optional


def save_upload(upload_file, destination_dir: Path, prefix: str = "") -> str:
    """Save an uploaded file to destination_dir. Returns the path string."""
    destination_dir.mkdir(parents=True, exist_ok=True)
    filename = f"{prefix}{upload_file.filename}" if prefix else upload_file.filename
    dest = destination_dir / filename
    with dest.open("wb") as f:
        shutil.copyfileobj(upload_file.file, f)
    return str(dest)


def collect_artifacts(ctx, output_dir: Path) -> List[str]:
    """Collect all output artifacts for a completed task."""
    artifacts = []
    # Video output
    if ctx.video_path and Path(ctx.video_path).exists():
        artifacts.append(str(ctx.video_path))
    # SRT subtitle
    srt_path = output_dir / "subtitle.srt"
    if srt_path.exists():
        artifacts.append(str(srt_path))
    # Script JSON
    script_path = output_dir / "script.json"
    if script_path.exists():
        artifacts.append(str(script_path))
    # Narration audio
    if ctx.audio_path and Path(ctx.audio_path).exists():
        artifacts.append(ctx.audio_path)
    # Metadata
    meta_path = output_dir / "metadata.json"
    if meta_path.exists():
        artifacts.append(str(meta_path))
    return artifacts


def zip_artifacts(artifacts: List[str], zip_path: Path) -> str:
    """Create a zip file containing all artifacts. Returns zip path."""
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for artifact in artifacts:
            p = Path(artifact)
            if p.exists():
                zf.write(p, p.name)
    return str(zip_path)
