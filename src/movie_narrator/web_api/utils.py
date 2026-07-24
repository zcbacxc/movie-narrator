"""Utility functions for the Web API — uploads, artifacts, zip."""

from __future__ import annotations

import shutil
import zipfile
from pathlib import Path
from typing import List, Optional

# Upload limits
MAX_VIDEO_SIZE = 2 * 1024 * 1024 * 1024  # 2 GB
MAX_BGM_SIZE = 50 * 1024 * 1024  # 50 MB
CHUNK_SIZE = 1024 * 1024  # 1 MB streaming chunks

# Extension whitelists (lowercase, no dot)
VIDEO_EXTENSIONS = {"mp4", "mkv", "mov", "webm", "avi"}
BGM_EXTENSIONS = {"mp3", "wav", "m4a", "flac", "ogg"}


class UploadError(Exception):
    """Raised when an upload fails validation (size, extension)."""

    def __init__(self, message: str, status_code: int = 422) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(message)


def save_upload(
    upload_file,
    destination_dir: Path,
    prefix: str = "",
    max_size: int = MAX_VIDEO_SIZE,
    allowed_extensions: Optional[set[str]] = None,
) -> str:
    """Save an uploaded file to destination_dir. Returns the path string.

    - Strips directory components from filename (path traversal protection)
    - Validates file extension against whitelist
    - Streams file in chunks, rejects if size exceeds max_size
    - Deletes partial file on size violation
    """
    destination_dir.mkdir(parents=True, exist_ok=True)

    # Strip directory components — only keep the basename
    raw_name = Path(upload_file.filename).name if upload_file.filename else "upload"
    filename = f"{prefix}{raw_name}" if prefix else raw_name

    # Extension whitelist check
    if allowed_extensions is not None:
        ext = Path(raw_name).suffix.lower().lstrip(".")
        if ext not in allowed_extensions:
            raise UploadError(
                f"File extension '.{ext}' not allowed. Allowed: {sorted(allowed_extensions)}",
                status_code=415,
            )

    dest = destination_dir / filename

    # Stream-read in chunks, enforce size limit
    total = 0
    try:
        with dest.open("wb") as f:
            while True:
                chunk = upload_file.file.read(CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_size:
                    f.close()
                    dest.unlink(missing_ok=True)
                    raise UploadError(
                        f"File too large: {total} bytes exceeds limit {max_size} bytes",
                        status_code=413,
                    )
                f.write(chunk)
    except UploadError:
        raise
    except Exception:
        dest.unlink(missing_ok=True)
        raise

    return str(dest)


def cleanup_uploads(upload_dir: Path, task_id: str) -> None:
    """Best-effort cleanup of uploaded files for a completed task.

    Removes files prefixed with 'video_' or 'bgm_' that were saved
    for this task. Non-fatal if files are missing or deletion fails.
    """
    try:
        for p in upload_dir.glob("*"):
            if p.is_file() and task_id in p.name:
                p.unlink(missing_ok=True)
    except Exception:
        pass  # best-effort, never fail the task


def collect_artifacts(ctx, output_dir: Path) -> List[str]:
    """Collect all output artifacts for a completed task.

    ``ctx`` is expected to satisfy the ``PipelineResult`` protocol
    (see ``movie_narrator.contract``). It is typed as ``Any`` here
    to avoid importing the full ``Context`` model — the contract
    layer formalizes the attribute access surface.
    """
    artifacts = []
    # Video output
    if ctx.video_path and Path(ctx.video_path).exists():
        artifacts.append(str(ctx.video_path))
    # SRT subtitles (original + translated + bilingual)
    for name in ("subtitle.srt", "subtitle.bilingual.srt"):
        p = output_dir / name
        if p.exists():
            artifacts.append(str(p))
    # Translated subtitle (dynamic lang suffix)
    if ctx.subtitle_paths and ctx.subtitle_paths.translated:
        p = Path(ctx.subtitle_paths.translated)
        if p.exists():
            artifacts.append(str(p))
    # Script markdown
    script_path = output_dir / "script.md"
    if script_path.exists():
        artifacts.append(str(script_path))
    # Research data
    research_path = output_dir / "research.json"
    if research_path.exists():
        artifacts.append(str(research_path))
    # Mixed audio (when BGM enabled)
    mixed_path = output_dir / "mixed.mp3"
    if mixed_path.exists():
        artifacts.append(str(mixed_path))
    # Narration audio
    if ctx.audio_path and Path(ctx.audio_path).exists():
        artifacts.append(ctx.audio_path)
    # Metadata
    meta_path = output_dir / "metadata.json"
    if meta_path.exists():
        artifacts.append(str(meta_path))
    # Clips directory (per-segment .mp4 files from export_clips step)
    clips_dir = Path(ctx.clips_dir) if ctx.clips_dir else output_dir / "clips"
    if clips_dir.is_dir():
        for clip in sorted(clips_dir.glob("*.mp4")):
            artifacts.append(str(clip))
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
