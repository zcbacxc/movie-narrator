"""Run-state dataclasses for the Web UI session."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..models import Context


class RunStatus(str, Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class WebRun:
    """Per-session run state held in ``gr.State``.

    ``current_step`` is surfaced to the UI by bridge yields (each poll
    iteration packs it into the generator payload). Gradio ``State``
    mutation alone should not be relied upon for UI refresh — only the
    yielded payload triggers a re-render.

    Never written into ``metadata.json`` or ``PipelineStatus``: cancel
    is a runtime concern, not a generation result.
    """

    status: RunStatus = RunStatus.IDLE
    context: Optional[Context] = None
    controller: Optional[object] = None  # GradioController, typed loosely to avoid import cycle
    current_step: str = ""
    error: str = ""
    # Artifact cache (filled at terminal state by collect_artifacts)
    video_path: Optional[str] = None
    audio_path: Optional[str] = None
    subtitle_path: Optional[str] = None
    script_md_path: Optional[str] = None
    output_dir: Optional[str] = None
