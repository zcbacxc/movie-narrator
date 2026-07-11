from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field

StepStatus = Literal["disabled", "skipped", "success", "failed"]


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

    metadata: Dict[str, Any] = Field(default_factory=dict)
