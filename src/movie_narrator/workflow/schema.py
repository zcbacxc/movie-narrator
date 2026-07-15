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
    # Multi-language subtitle toggle (v0.3). Short status-field key per
    # convention; the step itself also accepts `translate_subtitles`.
    translate: Optional[bool] = None


class JobParams(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_threshold: Optional[float] = None
    scene_frame_skip: Optional[int] = None
    match_min_score: Optional[float] = None
    research_provider: Optional[str] = None
    # Multi-language subtitle tuning (v0.3).
    translate_provider: Optional[str] = None
    translate_retries: Optional[int] = None
    translate_chunk_chars: Optional[int] = None
    translate_chunk_size: Optional[int] = None
    # Match speed clamp + scene merge (v0.4.6).
    match_speed_clamp_min: Optional[float] = None
    match_speed_clamp_max: Optional[float] = None
    scene_merge_min_duration: Optional[float] = None


VALID_SUBTITLE_MODES = frozenset({"original", "translated", "bilingual"})


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
    # Multi-language subtitle (v0.3).
    subtitle_lang: Optional[str] = None
    subtitle_mode: Optional[str] = None
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

    @field_validator("subtitle_mode")
    @classmethod
    def _check_subtitle_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_SUBTITLE_MODES:
            raise ValueError(
                f"subtitle_mode must be one of {sorted(VALID_SUBTITLE_MODES)}"
            )
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
    # Multi-language subtitle (v0.3).
    subtitle_lang: Optional[str] = None
    subtitle_mode: str = "original"
