from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TYPE_CHECKING, TypedDict

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .utils.console import Console, SilentConsole

StepStatus = Literal["disabled", "skipped", "success", "failed"]


class MetadataDict(TypedDict, total=False):
    """Type-safe metadata keys for Context.metadata.

    All keys are optional (total=False).  Provides IDE autocompletion and
    static-analysis catch for typos — zero runtime overhead.
    """
    # Pipeline I/O
    voice: str
    format: str
    keep_cache: bool
    # Step toggles
    research_enabled: bool
    workflow_steps: dict
    strict: bool
    # BGM
    bgm_request: str
    no_bgm: bool
    # Scene matching
    scene_threshold: float
    match_min_score: float
    no_clips: bool
    export_clips: bool
    # Subtitles
    subtitle_lang: str
    subtitle_mode: str
    source_lang: str
    translate_provider: str
    translate_retries: int
    translate_chunk_chars: int
    translate_chunk_size: int
    # Status tracking
    script_source: str
    script_degraded: bool
    tts_provider: str
    voice_used: str
    # Warnings
    warnings: list


# For static analysis (IDE, mypy, pyright): metadata is typed via MetadataDict.
# For Pydantic runtime: metadata is a plain Dict[str, Any] so arbitrary keys
# are accepted without validation errors.
if TYPE_CHECKING:
    _MetadataType = MetadataDict
else:
    _MetadataType = Dict[str, Any]


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
    # translate defaults to "skipped" (feature off, not explicitly disabled)
    # — distinct semantics from "disabled" (explicit workflow_steps=false or
    # provider unknown). See multi-language-subtitle-design.md §4.1.
    translate: StepStatus = "skipped"


class Assets(BaseModel):
    intro: Optional[str] = None
    bgm: Optional[str] = None
    watermark: Optional[str] = None
    font: Optional[str] = None


class SubtitlePaths(BaseModel):
    """Paths to the three subtitle files produced when translation path runs.

    - `original` is always populated (subtitle.srt)
    - `translated` / `bilingual` populated only when translation succeeded
      (or degraded-with-originals — same on-disk content, but the field
      is still set so render_subtitle_path resolution can pick the track).
    """
    original: str
    translated: Optional[str] = None
    bilingual: Optional[str] = None


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
    subtitle_path: Optional[str] = None  # ALWAYS original subtitle.srt (invariant)
    script_md_path: Optional[str] = None
    clips_dir: Optional[str] = None

    # ── Multi-language subtitle (v0.3) ──────────────────────
    # translated_texts is parallel to timed_segments (texts only, no time axis).
    # subtitle_paths bundles the three possible files; render_subtitle_path is
    # the mode-selected track for the renderer.
    translated_texts: List[str] = Field(default_factory=list)
    subtitle_paths: Optional[SubtitlePaths] = None
    render_subtitle_path: Optional[str] = None

    research: ResearchInfo = Field(default_factory=ResearchInfo)
    assets: Assets = Field(default_factory=Assets)
    scenes: List[Scene] = Field(default_factory=list)
    matched_clips: List[MatchedClip] = Field(default_factory=list)
    status: PipelineStatus = Field(default_factory=PipelineStatus)

    # Infrastructure — strictly required (no Optional, no default). The
    # Pydantic model_validator below guarantees `ctx.services` is never
    # `None` at runtime: a `SilentConsole`-backed `Services` is injected
    # when the caller omits the field (e.g. in unit tests that build a
    # bare Context). Production paths (the runner) always pass a real
    # `Services(console=build_console(...))`.
    services: Services

    # Single-step return state — consumed by runner, reset after each step
    step_state: StepState = Field(default_factory=StepState)

    metadata: _MetadataType = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _fill_missing_services(cls, data: Any) -> Any:
        """Inject a `SilentConsole`-backed Services when caller omits it.

        Why: tests across 13 files construct `Context(movie_name=...)`
        without wiring up `services=`. Rather than mutate each test (or
        introduce a test-only conftest that monkey-patches Context), we
        let the model itself guarantee `services` is always set. The
        field stays strictly typed (`Services`, no Optional, no default);
        only the *input* may be missing and gets a sentinel default.
        """
        if isinstance(data, dict) and "services" not in data:
            data["services"] = Services(console=SilentConsole())
        return data

    @property
    def output_path(self) -> Path:
        """``Path`` view of ``output_dir`` — eliminates repeated ``Path(ctx.output_dir)``."""
        return Path(self.output_dir)
