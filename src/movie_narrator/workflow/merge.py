# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Job configuration merging and defaults."""

import warnings
from typing import Any, Dict, Optional

from ..config import Settings
from .errors import JobConfigError
from .schema import JobConfig, ResolvedJob, VALID_SUBTITLE_MODES

# These mirror the typer Option defaults in cli.py — they are used as
# the "typer_default" sentinel in pick_defaulted() to detect whether the
# CLI value was explicitly set or left at the default.  They must stay in
# sync with cli.py but are NOT user-configurable Settings.
_STYLE_DEFAULT = "热血搞笑"
_DURATION_DEFAULT = 60
_FORMAT_DEFAULT = "16:9"


def merge_job(
    cli: Dict[str, Any],
    job: Optional[JobConfig],
    settings: Settings,
) -> ResolvedJob:
    """Merge job configuration with defaults."""
    has_job = job is not None

    def yaml_get(name: str):
        """Get a value from a YAML dictionary with optional fallback."""
        if job is None:
            return None
        return getattr(job, name)

    def pick_optional(cli_val, yaml_val, default=None):
        """Pick an optional value from source if present."""
        if cli_val is not None and cli_val != "":
            return cli_val
        if yaml_val is not None and yaml_val != "":
            return yaml_val
        return default

    def pick_bool_true_explicit(cli_val: bool, yaml_val: Optional[bool]) -> bool:
        """Pick a boolean value that must be explicitly true."""
        if cli_val is True:
            return True
        if yaml_val is not None:
            return bool(yaml_val)
        return False

    def pick_defaulted(cli_val, yaml_val, typer_default):
        """Pick a value with a default fallback."""
        if has_job and yaml_val is not None and cli_val == typer_default:
            return yaml_val
        return (
            cli_val
            if cli_val is not None
            else (yaml_val if yaml_val is not None else typer_default)
        )

    movie = pick_optional(cli.get("movie"), yaml_get("movie"), "")

    style = pick_defaulted(cli.get("style"), yaml_get("style"), _STYLE_DEFAULT)
    duration = pick_defaulted(cli.get("duration"), yaml_get("duration"), _DURATION_DEFAULT)
    # GAP-5 (v0.8.0): ``format`` renamed to ``video_format``. Accept the
    # legacy ``format`` CLI key as a deprecated alias (video_format wins
    # when both are present). YAML ``format`` backward compat is handled
    # in load.py, so the JobConfig only exposes ``video_format``.
    cli_fmt = cli.get("video_format")
    if cli_fmt is None and "format" in cli:
        warnings.warn(
            "The 'format' key is deprecated, use 'video_format' instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        cli_fmt = cli.get("format")
    fmt = pick_defaulted(cli_fmt, yaml_get("video_format"), _FORMAT_DEFAULT)

    voice = pick_optional(cli.get("voice"), yaml_get("voice"), None)
    video = pick_optional(cli.get("video"), yaml_get("video"), None)
    library_dir = pick_optional(cli.get("library_dir"), yaml_get("library_dir"), None)
    bgm = pick_optional(cli.get("bgm"), yaml_get("bgm"), None)

    keep_cache = pick_bool_true_explicit(bool(cli.get("keep_cache")), yaml_get("keep_cache"))
    no_bgm = pick_bool_true_explicit(bool(cli.get("no_bgm")), yaml_get("no_bgm"))
    no_clips = pick_bool_true_explicit(bool(cli.get("no_clips")), yaml_get("no_clips"))
    strict = pick_bool_true_explicit(bool(cli.get("strict")), yaml_get("strict"))

    research = cli.get("research")
    workflow_steps: Dict[str, bool] = {}
    if job is not None and job.steps is not None:
        for key in ("research", "align", "scene", "match", "bgm", "export", "translate"):
            val = getattr(job.steps, key)
            if val is not None:
                workflow_steps[key] = bool(val)
        if research is None and "research" in workflow_steps:
            research = workflow_steps["research"]

    params: Dict[str, Any] = {}
    if job is not None and job.params is not None:
        for key in (
            # Scene detection
            "scene_threshold",
            "scene_frame_skip",
            # Match
            "match_min_score",
            "match_speed_clamp_min",
            "match_speed_clamp_max",
            "scene_merge_min_duration",
            "match_drop_scene_min_duration",
            "match_diversity_window",
            "match_max_scene_reuse",
            "match_timeline_mode",
            "match_act_weights",
            "match_topk",
            "match_topk_reuse_penalty",
            "embedding_model_name",
            # BGM
            "bgm_gain_db",
            "bgm_duck_db",
            "bgm_normalize",
            "audio_target_dbfs",
            # TTS pacing
            "tts_pause_ms",
            "tts_max_concurrent",
            "tts_audio_format",
            "tts_audio_bitrate",
            # Translate
            "translate_source_lang",
            "translate_provider",
            "translate_retries",
            "translate_chunk_chars",
            "translate_chunk_size",
            # Research
            "research_provider",
            # WhisperX
            "whisperx_device",
            "whisperx_model",
            "whisperx_language",
            "align_backend",
            # Render
            "render_fps",
            "render_video_codec",
            "render_audio_codec",
            "render_threads",
            "render_bg_color",
            "render_font_size",
            "render_output_name",
            "render_ffmpeg_timeout",
            "render_fit_mode",
            "render_crf",
            "render_preset",
            "render_faststart",
            "render_subtitle_position",
            "render_subtitle_max_width_ratio",
            "render_subtitle_bottom_margin_ratio",
            "render_require_footage",
            "render_min_footage_coverage",
            "render_profile",
            "render_title_card_sec",
            "render_cover_export",
            "render_vertical_safe_area",
            "bgm_loudnorm",
            # QA
            "qa_enabled",
            "qa_max_silence_db",
            "qa_min_duration_ratio",
            "qa_max_duration_ratio",
            # Prompt shaping (preset-driven, but also YAML-configurable)
            "prompt_target_sentences",
            "prompt_target_segment_duration",
            "prompt_max_chars_per_sentence",
            "prompt_hook_seconds",
            "hook_templates",
            "set_pieces",
            # Async
            "async_timeout",
            "async_max_workers",
            # Video sizes
            "video_sizes",
            # Render template styling
            "render_template",
        ):
            val = getattr(job.params, key)
            if val is not None:
                params[key] = val

    # Multi-language subtitle (v0.3).
    subtitle_lang = pick_optional(cli.get("subtitle_lang"), yaml_get("subtitle_lang"), None)
    if subtitle_lang is not None:
        subtitle_lang = str(subtitle_lang).strip() or None
    subtitle_mode = (
        pick_optional(cli.get("subtitle_mode"), yaml_get("subtitle_mode"), None) or "original"
    )
    if subtitle_mode not in VALID_SUBTITLE_MODES:
        raise JobConfigError(
            f"subtitle_mode must be one of {sorted(VALID_SUBTITLE_MODES)}, got {subtitle_mode!r}"
        )
    # Spec §5.1: mode in {translated, bilingual} without lang → error.
    if subtitle_mode in {"translated", "bilingual"} and not subtitle_lang:
        raise JobConfigError(
            f"subtitle_mode={subtitle_mode!r} requires --subtitle-lang / subtitle_lang"
        )

    narration_preset = pick_optional(
        cli.get("narration_preset"), yaml_get("narration_preset"), None
    )

    # Resolve narration language (default "zh").
    # Only add to params if explicitly set by user (CLI or YAML).
    # The default "zh" is handled by build_context's parameter default.
    lang = pick_optional(cli.get("lang"), yaml_get("lang"), None)
    if lang:
        lang = str(lang).strip().lower()
        if "lang" not in params:
            params["lang"] = lang
    else:
        lang = "zh"

    # Narrator perspective & focus character — CLI overrides YAML.
    perspective = pick_optional(
        cli.get("narrator_perspective"),
        getattr(job.params, "narrator_perspective", None) if job and job.params else None,
        None,
    )
    if perspective:
        params["narrator_perspective"] = str(perspective).strip()

    focus_char = pick_optional(
        cli.get("focus_character"),
        getattr(job.params, "focus_character", None) if job and job.params else None,
        None,
    )
    if focus_char:
        params["focus_character"] = str(focus_char).strip()

    config_path = cli.get("config_path")
    if config_path is not None:
        config_path = str(config_path)

    return ResolvedJob(
        movie=movie if movie is not None else "",
        style=style,
        duration=int(duration),
        voice=voice,
        video_format=fmt,
        keep_cache=keep_cache,
        video=video,
        library_dir=library_dir,
        bgm=bgm,
        no_bgm=no_bgm,
        no_clips=no_clips,
        strict=strict,
        research=research,
        workflow_steps=workflow_steps,
        params=params,
        config_path=config_path,
        subtitle_lang=subtitle_lang,
        subtitle_mode=subtitle_mode,
        narration_preset=narration_preset,
        lang=lang,
    )
