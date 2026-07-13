from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .errors import JobConfigError


class JobSteps(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research: Optional[bool] = None
    align: Optional[bool] = None
    scene: Optional[bool] = None
    match: Optional[bool] = None
    bgm: Optional[bool] = None
    export: Optional[bool] = None


class JobParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_threshold: Optional[float] = None
    scene_frame_skip: Optional[int] = None
    match_min_score: Optional[float] = None
    research_provider: Optional[str] = None


class JobConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    movie: Optional[str] = None
    style: Optional[str] = None
    duration: Optional[int] = None
    voice: Optional[str] = None
    format: Optional[str] = None
    keep_cache: Optional[bool] = None
    video: Optional[str] = None
    library_dir: Optional[str] = None
    bgm: Optional[str] = None
    no_bgm: Optional[bool] = None
    no_clips: Optional[bool] = None
    strict: Optional[bool] = None
    steps: Optional[JobSteps] = None
    params: Optional[JobParams] = None

    @field_validator("duration")
    @classmethod
    def _check_duration(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("duration must be > 0")
        return v

    @field_validator("format")
    @classmethod
    def _check_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("16:9", "9:16"):
            raise ValueError("format must be '16:9' or '9:16'")
        return v


class ResolvedJob(BaseModel):
    movie: str
    style: str
    duration: int
    voice: Optional[str] = None
    format: str
    keep_cache: bool = False
    video: Optional[str] = None
    library_dir: Optional[str] = None
    bgm: Optional[str] = None
    no_bgm: bool = False
    no_clips: bool = False
    strict: bool = False
    research: Optional[bool] = None
    workflow_steps: Dict[str, bool] = Field(default_factory=dict)
    params: Dict[str, Any] = Field(default_factory=dict)
    config_path: Optional[str] = None
