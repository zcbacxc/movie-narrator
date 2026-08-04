# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Core data models and type definitions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, TYPE_CHECKING, TypedDict

from pydantic import BaseModel, ConfigDict, Field, SkipValidation, model_validator

from .utils.console import Console, SilentConsole
from .utils.cost_tracker import CostTracker
from .utils.log import AppLogger

StepStatus = Literal["disabled", "skipped", "success", "failed", "partial"]


class MetadataDict(TypedDict, total=False):
    """Type-safe metadata keys for Context.metadata.

    All keys are optional (total=False).  Provides IDE autocompletion and
    static-analysis catch for typos — zero runtime overhead.
    """

    # Pipeline I/O
    voice: str
    video_format: str
    keep_cache: bool
    format: str
    aspect_ratio: str
    video_arg: str
    video_sizes: dict
    # Step toggles
    research_enabled: bool
    workflow_steps: dict
    strict: bool
    qa_enabled: bool
    # Research
    research_provider: str
    # BGM
    bgm_request: str
    no_bgm: bool
    bgm_gain_db: float
    bgm_duck_db: float
    bgm_normalize: bool
    bgm_loudnorm: bool
    bgm_metadata_path: str
    bgm_ambient_path: str
    bgm_ambient_gain_db: float
    audio_target_dbfs: float
    # Scene detection
    scene_threshold: float
    scene_frame_skip: int
    scene_merge_min_duration: float
    # Scene matching
    match_min_score: float
    match_topk: int
    match_topk_reuse_penalty: float
    match_speed_clamp_min: float
    match_speed_clamp_max: float
    match_drop_scene_min_duration: float
    match_skip_intro_sec: float
    match_drop_dark_luma: float
    match_source_window: list
    match_timeline_mode: str
    match_act_weights: list
    match_diversity_window: int
    match_max_scene_reuse: int
    match_transcript_cached: bool
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
    # i18n pipeline (v0.9.6): language-aware narration / script / match / translate.
    # ``lang`` is the single source of truth for the narration language
    # (default "zh"); ``narration_lang`` / ``script_lang`` record the language
    # the script step actually used; ``translate_*`` describe the subtitle
    # translation direction; ``match_lang`` / ``match_text_source`` record which
    # language the match step embedded against.
    lang: str
    narration_lang: str
    script_lang: str
    translate_source_lang: str
    translate_target_lang: str
    match_lang: str
    match_text_source: str
    # Status tracking
    script_source: str
    script_degraded: bool
    tts_provider: str
    voice_used: str
    # Warnings
    warnings: list
    # Research / Movie card
    movie_card: MovieCard
    movie_card_source: str
    tmdb_corrections: list
    # Script step
    set_pieces: list
    beats_meta: list
    script_truncated: dict
    script_qa: dict
    script_judge: dict
    script_phase: str
    script_target_count: int
    script_beat_count: int
    script_segment_count: int
    # Script prompt tuning
    prompt_target_sentences: int
    prompt_max_chars_per_sentence: int
    prompt_target_segment_duration: float
    prompt_hook_seconds: int
    hook_templates: list
    target_platform: str
    narrator_perspective: str
    focus_character: str
    # Align step
    align_degraded: bool
    align_backend_used: str
    align_backend_reason: str
    align_backend_attempted: list
    align_backend: str
    align_fallback: bool
    align_word_segments: int
    align_words_assigned: int
    align_word_tightened: int
    alignment_qa: dict
    align_segments: int
    align_backward_skipped: int
    # WhisperX / alignment backend config
    whisperx_model: str
    whisperx_language: str
    whisperx_device: str
    embedding_model_name: str
    # Vision / captioning
    vision_captioner: str
    # TTS step
    duration_metrics: dict
    audio_quality: dict
    tts_style_prompt: str
    tts_audio_format: str
    tts_audio_bitrate: str
    tts_max_concurrent: int
    tts_pause_ms: int
    # Match step
    wp6_intro_dropped: int
    wp6_dark_dropped: int
    wp6_window_dropped: int
    match_captions_fake: bool
    match_quality: dict
    match_summary: dict
    # BGM step
    bgm_selection: dict
    bgm_transitions: list
    bgm_error: str
    ambient_track: dict
    # Subtitle / Translate
    subtitle_qa: dict
    untranslated_indices: list
    untranslated_count: int
    translation_glossary: dict
    # Render step
    encoder_info: dict
    footage_coverage: dict
    render_encoder: str
    render_video_codec: str
    render_audio_codec: str
    render_preset: str
    render_crf: int
    render_fps: int
    render_threads: int
    render_faststart: bool
    render_fit_mode: str
    render_bg_color: str
    render_template: dict
    render_output_name: str
    render_cover_export: bool
    render_require_footage: bool
    render_min_footage_coverage: float
    footage_coverage_ratio: float
    total_segments: int
    render_ffmpeg_timeout: int
    render_preview_mode: str
    render_preview_sec: float
    render_title_card_sec: int
    render_transition: str
    render_transition_duration: float
    render_text_animation: str
    render_text_animation_duration: float
    render_font_size: int
    render_subtitle_position: str
    render_subtitle_max_width_ratio: float
    render_subtitle_bottom_margin_ratio: float
    render_vertical_safe_area: str
    # QA step
    qa_report: dict
    video_qa: dict
    quality_dashboard: dict
    qa_gate: dict
    qa_max_silence_db: float
    qa_min_duration_ratio: float
    qa_max_duration_ratio: float
    qa_baseline_path: str
    # Runner / CLI / infra
    narration_preset: str
    narration_preset_tags: dict
    render_profile: str
    config_path: str
    run_id: str
    version: str
    environment: dict
    created_at: str
    duration: float
    scene_detection_degraded: bool
    pause_at: str
    distributed_render: bool
    # Internal: degraded step tracking (list of step names)
    _degraded_steps: list


# For static analysis (IDE, mypy, pyright): metadata is typed via MetadataDict.
# For Pydantic runtime: metadata is a plain Dict[str, Any] so arbitrary keys
# are accepted without validation errors.
if TYPE_CHECKING:
    _MetadataType = MetadataDict
else:
    _MetadataType = Dict[str, Any]


# ── Step result ────────────────────────────────────────────


class StepResult(Enum):
    """Enumeration of pipeline step result states."""

    SUCCESS = "success"
    SKIPPED = "skipped"
    WARNING = "warning"


@dataclass
class StepState:
    """Mutable state of the current pipeline step."""

    result: StepResult = StepResult.SUCCESS
    message: str | None = None
    # Records whether the failure that produced this state was a
    # retryable (transient, network-type) error. Defaults to False so every
    # existing call site stays non-retryable unless it explicitly opts in.
    # Consumed for audit/diagnostics; the runner reads the exception's
    # ``retryable`` attribute directly for the retry/skip/abort decision.
    step_retryable: bool = False


# ── Services container ──────────────────────────────────────


@dataclass
class Services:
    """Container for infrastructure services injected into the pipeline.

    Plugins can access ``ctx.services.logger`` to emit structured log
    messages without importing a specific logging framework. The logger
    field accepts any object with ``info``, ``warning``, and ``error``
    methods (duck-typed), defaulting to ``None`` when not configured.
    """

    console: Console
    logger: Optional[AppLogger] = None


class ScriptSegment(BaseModel):
    """A single segment of the narration script."""

    text: str


class WordSegment(BaseModel):
    """A single word with timing and confidence from forced alignment.

    v0.5.11: Populated by WhisperX ``align()`` word-level output.
    """

    word: str
    start: float
    end: float
    score: float = 0.0


class TimedSegment(BaseModel):
    """A script segment with timing information from alignment."""

    text: str
    start: float
    end: float
    # v0.5.11: word-level alignment data from WhisperX forced alignment.
    # Populated only when whisperx.align() succeeds; empty otherwise.
    words: List[WordSegment] = Field(default_factory=list)
    # v0.5.11: alignment confidence score (0.0–1.0), computed from
    # word-level scores. 0.0 when no word-level data is available.
    confidence: float = 0.0


class PipelineStatus(BaseModel):
    """Overall pipeline execution status."""

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
    # v0.5.12: QA gate step status
    qa_gate: StepStatus = "disabled"


class Assets(BaseModel):
    """Asset paths and availability information."""

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
    """Movie research information gathered from external sources."""

    title: str = ""
    year: Optional[int] = None
    summary: str = ""
    genres: List[str] = Field(default_factory=list)
    cast: List[str] = Field(default_factory=list)
    keywords: List[str] = Field(default_factory=list)


class MovieCard(BaseModel):
    """Structured movie metadata card.

    A focused, typed snapshot of movie metadata extracted during the
    research step. Carrying title / year / genres / director / cast /
    set_pieces as explicit fields (rather than relying on free-form
    summary prose) gives downstream prompt construction a stable,
    hallucination-resistant context.

    The card is optional and backward compatible: code that does not
    read it is unaffected. When the research step is skipped or fails,
    the card is simply absent from ``ctx.metadata["movie_card"]``.
    """

    title: str
    year: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    summary: str = ""
    director: Optional[str] = None
    cast: List[str] = Field(default_factory=list)
    set_pieces: List[str] = Field(default_factory=list)


class Scene(BaseModel):
    """A detected scene in the source video."""

    index: int
    start: float
    end: float
    clip_path: Optional[str] = None
    thumbnail_path: Optional[str] = None


class MatchedClip(BaseModel):
    """A script segment matched to a video scene."""

    segment_index: int
    text: str
    narr_start: float
    narr_end: float
    src_start: float
    src_end: float
    score: float
    scene_index: Optional[int] = None
    source: Literal[
        "scene", "heuristic", "embedding", "embedding_topk", "embedding_top1", "fallback"
    ] = "fallback"
    # v0.5.11: per-dimension quality scores for composite scoring.
    # None for heuristic clips where no embedding/rhythm data exists.
    embedding_score: Optional[float] = None
    rhythm_score: Optional[float] = None
    diversity_score: Optional[float] = None
    composite_score: Optional[float] = None


class Context(BaseModel):
    """Pipeline execution context — carries state between steps."""

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

    # v0.7.0: per-run cost tracking
    cost_tracker: Optional[SkipValidation[CostTracker]] = None

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
