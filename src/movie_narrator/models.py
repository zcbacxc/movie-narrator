from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class ScriptSegment(BaseModel):
    text: str


class TimedSegment(BaseModel):
    text: str
    start: float
    end: float


class Context(BaseModel):
    movie_name: str
    style: str = "热血搞笑"
    duration: int = 60
    segments: List[ScriptSegment] = Field(default_factory=list)
    timed_segments: List[TimedSegment] = Field(default_factory=list)
    audio_path: Optional[str] = None
    subtitle_path: Optional[str] = None
    video_path: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
