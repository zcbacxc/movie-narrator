from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

StepStatus = Literal["disabled", "skipped", "success", "failed"]


# ── Step result ────────────────────────────────────────────


class StepResult(Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    WARNING = "warning"


@dataclass
class StepState:
    result: StepResult = StepResult.SUCCESS
    message: str | None = None


# ── Services container ──────────────────────────────────────


@runtime_checkable
class Console(Protocol):
    """Output abstraction — console rendering + log dispatch."""

    def step(self, name: str) -> None: ...
    def step_ok(self, name: str, elapsed: float) -> None: ...
    def step_skip(self, name: str, reason: str) -> None: ...
    def step_warn(self, name: str, reason: str) -> None: ...
    def step_err(self, name: str, exc: Exception, elapsed: float) -> None: ...
    def warn(self, msg: str) -> None: ...
    def debug(self, msg: str) -> None: ...
    def inline_warn(self, msg: str) -> None: ...
    def final(self, msg: str) -> None: ...
    def progress(self, *args, **kwargs): ...


@dataclass
class Services:
    console: Console


class ScriptSegment(BaseModel):
    text: str


class TimedSegment(BaseModel):
    text: str
    start: float
    end: float


class PipelineStatus(BaseModel):
    research: StepStatus = "disabled"
    align: StepStatus = "disabled"
    scene: StepStatus = "disabled"
    match: StepStatus = "disabled"
    bgm: StepStatus = "disabled"
    export: StepStatus = "disabled"


class Assets(BaseModel):
    intro: Optional[str] = None
    bgm: Optional[str] = None
    watermark: Optional[str] = None
    font: Optional[str] = None


class ResearchInfo(BaseModel):
    title: str = ""
    year: Optional[int] = None
    summary: str = ""
    genres: List[str] = Field(default_factory=list)
    cast: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)


class Scene(BaseModel):
    index: int
    start: float
    end: float
    clip_path: Optional[str] = None
    thumbnail_path: Optional[str] = None


class MatchedClip(BaseModel):
    segment_index: int
    text: str
    narr_start: float
    narr_end: float
    src_start: float
    src_end: float
    score: float
    scene_index: Optional[int] = None
    source: Literal["scene", "heuristic", "embedding", "fallback"] = "fallback"


class Context(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    movie_name: str
    style: str = "热血搞笑"
    duration: int = 60

    output_dir: str
    library_dir: Optional[str] = None

    source_video_path: Optional[str] = None
    video_path: Optional[str] = None

    segments: List[ScriptSegment] = Field(default_factory=list)
    timed_segments: List[TimedSegment] = Field(default_factory=list)

    audio_path: Optional[str] = None
    final_audio_path: Optional[str] = None
    subtitle_path: Optional[str] = None
    script_md_path: Optional[str] = None
    clips_dir: Optional[str] = None

    research: ResearchInfo = Field(default_factory=ResearchInfo)
    assets: Assets = Field(default_factory=Assets)
    scenes: List[Scene] = Field(default_factory=list)
    matched_clips: List[MatchedClip] = Field(default_factory=list)
    status: PipelineStatus = Field(default_factory=PipelineStatus)

    # Infrastructure — injected by build_context(), excluded from serialization
    services: Optional[Services] = None

    # Single-step return state — consumed by runner, reset after each step
    step_state: StepState = Field(default_factory=StepState)

    metadata: Dict[str, Any] = Field(default_factory=dict)
