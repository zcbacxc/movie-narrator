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

    # ── Scene detection ──
    scene_threshold: Optional[float] = None
    scene_frame_skip: Optional[int] = None
    # ── Match ──
    match_min_score: Optional[float] = None
    match_speed_clamp_min: Optional[float] = None
    match_speed_clamp_max: Optional[float] = None
    scene_merge_min_duration: Optional[float] = None
    match_drop_scene_min_duration: Optional[float] = None
    match_diversity_window: Optional[int] = None
    match_max_scene_reuse: Optional[int] = None
    embedding_model_name: Optional[str] = None
    # ── BGM ──
    bgm_gain_db: Optional[float] = None
    bgm_duck_db: Optional[float] = None
    bgm_normalize: Optional[bool] = None
    audio_target_dbfs: Optional[float] = None
    # ── TTS pacing ──
    tts_pause_ms: Optional[int] = None
    tts_max_concurrent: Optional[int] = None
    tts_audio_format: Optional[str] = None
    tts_audio_bitrate: Optional[str] = None
    # ── Translate ──
    translate_source_lang: Optional[str] = None
    translate_provider: Optional[str] = None
    translate_retries: Optional[int] = None
    translate_chunk_chars: Optional[int] = None
    translate_chunk_size: Optional[int] = None
    # ── Research ──
    research_provider: Optional[str] = None
    # ── WhisperX ──
    whisperx_device: Optional[str] = None
    whisperx_model: Optional[str] = None
    whisperx_language: Optional[str] = None
    align_backend: Optional[str] = None  # "whisperx" | "faster_whisper" | None (auto)
    # ── Render ──
    render_fps: Optional[int] = None
    render_video_codec: Optional[str] = None
    render_audio_codec: Optional[str] = None
    render_threads: Optional[int] = None
    render_bg_color: Optional[str] = None
    render_font_size: Optional[int] = None
    render_output_name: Optional[str] = None
    render_ffmpeg_timeout: Optional[int] = None
    # ── Render: production quality ──
    render_fit_mode: Optional[str] = None
    render_crf: Optional[int] = None
    render_preset: Optional[str] = None
    render_faststart: Optional[bool] = None
    render_subtitle_position: Optional[str] = None
    render_subtitle_max_width_ratio: Optional[float] = None
    render_subtitle_bottom_margin_ratio: Optional[float] = None
    render_require_footage: Optional[bool] = None
    render_min_footage_coverage: Optional[float] = None
    # ── QA ──
    qa_enabled: Optional[bool] = None
    qa_max_silence_db: Optional[float] = None
    qa_min_duration_ratio: Optional[float] = None
    qa_max_duration_ratio: Optional[float] = None
    # ── Prompt shaping (preset-driven) ──
    prompt_target_sentences: Optional[int] = None
    prompt_max_chars_per_sentence: Optional[int] = None
    prompt_hook_seconds: Optional[int] = None
    # ── Async ──
    async_timeout: Optional[int] = None
    async_max_workers: Optional[int] = None
    # ── Video sizes ──
    video_sizes: Optional[Dict[str, list]] = None


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
    narration_preset: Optional[str] = None
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
    narration_preset: Optional[str] = None
