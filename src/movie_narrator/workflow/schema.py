# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Job configuration schema definitions."""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class JobSteps(BaseModel):
    """Job step configuration."""

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
    """Job parameter configuration."""

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
    match_timeline_mode: Optional[str] = None  # "uniform" (default) | "weighted_acts"
    match_act_weights: Optional[list] = None  # e.g. [0.15, 0.25, 0.40, 0.20]
    match_topk: Optional[int] = None  # Top-K rerank (default 5, 0/1 = top-1)
    match_topk_reuse_penalty: Optional[float] = (
        None  # Score deduction for recently used scenes (default 0.15)
    )
    embedding_model_name: Optional[str] = None
    # ── Vision ──
    # "none" (default) | "stub" | plugin-registered providers (via register_vision)
    vision_captioner: Optional[str] = None
    # ── Scene filtering ──
    # intro skip, dark frame drop, highlight window (all opt-in, default off)
    match_skip_intro_sec: Optional[float] = (
        None  # drop scenes ending before this offset (default 0)
    )
    match_drop_dark_luma: Optional[float] = (
        None  # mean luma threshold for dark frame drop (default 0 = disabled; try 16-30)
    )
    match_source_window: Optional[list] = (
        None  # [start_ratio, end_ratio] highlight window (default [0.0, 1.0])
    )
    # ── BGM ──
    bgm_gain_db: Optional[float] = None
    bgm_duck_db: Optional[float] = None
    bgm_normalize: Optional[bool] = None
    audio_target_dbfs: Optional[float] = None
    # RMS-based loudness normalization (more consistent than peak)
    bgm_loudnorm: Optional[bool] = None
    # Path to a BGM metadata YAML used for emotion-based selection
    bgm_metadata_path: Optional[str] = None
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
    render_profile: Optional[str] = None  # "publish" (default) | "draft" (fast iteration)
    # Title card duration (seconds, 0 = disabled)
    render_title_card_sec: Optional[float] = None
    # Export cover.jpg from highest-score frame (bool, default False)
    render_cover_export: Optional[bool] = None
    # Auto-adjust subtitle margins for 9:16 vertical safe area (bool, default True)
    render_vertical_safe_area: Optional[bool] = None
    # v0.7.0: GPU encoding — "auto" (default) | "cpu" | "nvenc" | "vaapi" | "videotoolbox"
    render_encoder: Optional[str] = None
    # v0.7.1: scene transition type — "none" (default) | "fade" | "dissolve" | "slide"
    render_transition: Optional[str] = None
    # v0.7.1: transition duration in seconds (default 0.5)
    render_transition_duration: Optional[float] = None
    # v0.7.1: text animation type — "none" (default) | "fade" | "slide_up" | "slide_left"
    render_text_animation: Optional[str] = None
    # v0.7.1: text animation duration in seconds (default 0.3)
    render_text_animation_duration: Optional[float] = None
    # v0.7.1: ambient/SFX audio track for multi-track mixing
    bgm_ambient_path: Optional[str] = None
    # v0.7.1: ambient track gain reduction in dB (default -12)
    bgm_ambient_gain_db: Optional[float] = None
    # v0.7.2: preview mode — render only first N seconds for quick iteration
    render_preview_mode: Optional[bool] = None
    # v0.7.2: preview duration in seconds (default 10, range 3-60)
    render_preview_sec: Optional[float] = None
    # ── QA ──
    qa_enabled: Optional[bool] = None
    qa_max_silence_db: Optional[float] = None
    qa_min_duration_ratio: Optional[float] = None
    qa_max_duration_ratio: Optional[float] = None
    # ── Prompt shaping (preset-driven) ──
    prompt_target_sentences: Optional[int] = None
    prompt_target_segment_duration: Optional[float] = None
    prompt_max_chars_per_sentence: Optional[int] = None
    prompt_hook_seconds: Optional[int] = None
    # Hook templates and set pieces for narrative hook enhancement
    hook_templates: Optional[list] = None  # e.g. ["你敢信？{movie}里这段直接封神", ...]
    set_pieces: Optional[list] = None  # e.g. ["公交车战", "天台对峙"] — injected into Phase1 beats
    # ── Async ──
    async_timeout: Optional[int] = None
    async_max_workers: Optional[int] = None
    # ── Video sizes ──
    video_sizes: Optional[Dict[str, list]] = None
    # Target platform for tone adaptation
    target_platform: Optional[str] = None  # "douyin" | "bilibili" | "youtube" | None
    # Narration language (single source of truth)
    lang: Optional[str] = None  # "zh" (default) | "en" | "ja" | ...
    # Render template — per-preset styling options (title card,
    # disclaimer, watermark, slogan, end card text).  The {movie}
    # placeholder in any string value is replaced with the movie name at
    # render time.  All keys are optional.
    render_template: Optional[Dict[str, Any]] = None
    # Narrator perspective & character anchor.
    # Perspective mode: "omniscient" (default, neutral bird's-eye view),
    # "character" (subjective, tied to focus_character), or "detective"
    # (mystery gradually unfolding).  When empty/None, behaviour is
    # backward-compatible (no perspective hint injected).
    narrator_perspective: Optional[str] = None
    # Name of the character to anchor the narration on (used with
    # "character" perspective).  Ignored for other modes.
    focus_character: Optional[str] = None


VALID_SUBTITLE_MODES = frozenset({"original", "translated", "bilingual"})


class JobConfig(BaseModel):
    """Complete job configuration."""

    model_config = ConfigDict(extra="forbid")

    movie: Optional[str] = None
    style: Optional[str] = None
    duration: Optional[int] = None
    voice: Optional[str] = None
    # GAP-5 (v0.8.0): renamed from ``format`` (backward-compatible YAML
    # alias handled in workflow/load.py + workflow/merge.py).
    video_format: Optional[str] = None
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
    lang: Optional[str] = None  # Narration language (default "zh")
    steps: Optional[JobSteps] = None
    params: Optional[JobParams] = None

    @field_validator("duration")
    @classmethod
    def _check_duration(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and v <= 0:
            raise ValueError("duration must be > 0")
        return v

    @field_validator("video_format")
    @classmethod
    def _check_video_format(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in ("16:9", "9:16"):
            raise ValueError("video_format must be '16:9' or '9:16'")
        return v

    @field_validator("subtitle_mode")
    @classmethod
    def _check_subtitle_mode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in VALID_SUBTITLE_MODES:
            raise ValueError(f"subtitle_mode must be one of {sorted(VALID_SUBTITLE_MODES)}")
        return v


class ResolvedJob(BaseModel):
    """Fully resolved job configuration after merging defaults."""

    movie: str
    style: str
    duration: int
    voice: Optional[str] = None
    video_format: str
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
    lang: str = "zh"  # Narration language (default Chinese)
