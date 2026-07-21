from pathlib import Path
from typing import Any, Dict, Union

import yaml
from pydantic import ValidationError

from .errors import JobConfigError
from .schema import JobConfig

_PATH_KEYS = ("video", "bgm", "library_dir")

_ALLOWED_TOP = (
    "movie",
    "style",
    "duration",
    "voice",
    "format",
    "keep_cache",
    "video",
    "library_dir",
    "bgm",
    "no_bgm",
    "no_clips",
    "strict",
    "steps",
    "params",
    "subtitle_lang",
    "subtitle_mode",
    "narration_preset",
    "align_backend",
)


def load_job_config(path: Union[str, Path]) -> JobConfig:
    config_path = Path(path)
    if not config_path.is_file():
        raise JobConfigError(f"config not found: {config_path}")

    try:
        raw_text = config_path.read_text(encoding="utf-8")
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        if mark is not None:
            raise JobConfigError(
                f"invalid YAML: {e.problem} (line {mark.line + 1})"
            ) from e
        raise JobConfigError(f"invalid YAML: {e}") from e

    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise JobConfigError("job config must be a mapping")

    unknown = [k for k in data.keys() if k not in _ALLOWED_TOP]
    if unknown:
        raise JobConfigError(
            f"unknown key: '{unknown[0]}' (allowed: {', '.join(_ALLOWED_TOP)})"
        )

    if "steps" in data and data["steps"] is not None:
        if not isinstance(data["steps"], dict):
            raise JobConfigError("steps must be a mapping")
        allowed_steps = {"research", "align", "scene", "match", "bgm", "export", "translate"}
        for k in data["steps"].keys():
            if k not in allowed_steps:
                raise JobConfigError(
                    f"unknown key: '{k}' (allowed: {', '.join(sorted(allowed_steps))})"
                )

    if "params" in data and data["params"] is not None:
        if not isinstance(data["params"], dict):
            raise JobConfigError("params must be a mapping")
        allowed_params = {
            # Scene detection
            "scene_threshold", "scene_frame_skip",
            # Match
            "match_min_score", "match_speed_clamp_min", "match_speed_clamp_max",
            "scene_merge_min_duration", "embedding_model_name",
            "match_drop_scene_min_duration",
            # BGM + loudness
            "bgm_gain_db", "bgm_duck_db", "bgm_normalize", "audio_target_dbfs",
            # TTS pacing
            "tts_pause_ms", "tts_max_concurrent", "tts_audio_format", "tts_audio_bitrate",
            # Translate
            "translate_source_lang", "translate_provider", "translate_retries",
            "translate_chunk_chars", "translate_chunk_size",
            # Research
            "research_provider",
            # WhisperX
            "whisperx_device", "whisperx_model", "whisperx_language",
            # Render
            "render_fps", "render_video_codec", "render_audio_codec", "render_threads",
            "render_bg_color", "render_font_size", "render_output_name", "render_ffmpeg_timeout",
            # Render production quality
            "render_fit_mode", "render_crf", "render_preset", "render_faststart",
            "render_subtitle_position", "render_subtitle_max_width_ratio",
            "render_subtitle_bottom_margin_ratio",
            # Deliverable QA
            "qa_enabled", "qa_max_silence_db",
            "qa_min_duration_ratio", "qa_max_duration_ratio",
            # Prompt shaping (preset-driven)
            "prompt_target_sentences", "prompt_max_chars_per_sentence",
            "prompt_hook_seconds",
            # Async
            "async_timeout", "async_max_workers",
            # Video sizes
            "video_sizes",
        }
        for k in data["params"].keys():
            if k not in allowed_params:
                raise JobConfigError(
                    f"unknown key: '{k}' (allowed: {', '.join(sorted(allowed_params))})"
                )

    base = config_path.resolve().parent
    for key in _PATH_KEYS:
        val = data.get(key)
        if isinstance(val, str) and val and not Path(val).is_absolute():
            data[key] = str((base / val).resolve())

    try:
        return JobConfig.model_validate(data)
    except JobConfigError:
        raise
    except ValidationError as e:
        msg = _format_validation_error(e)
        raise JobConfigError(msg) from e


def _format_validation_error(err: ValidationError) -> str:
    parts = []
    for er in err.errors():
        loc = ".".join(str(x) for x in er.get("loc", ())) or "config"
        etype = er.get("type", "")
        if etype == "extra_forbidden":
            parts.append(f"unknown key: '{loc}' (allowed: {', '.join(_ALLOWED_TOP)})")
        else:
            parts.append(f"{loc}: {er.get('msg', 'invalid value')}")
    for er in err.errors():
        msg = er.get("msg", "")
        if "unsupported config version" in msg:
            return msg.replace("Value error, ", "")
    return parts[0] if parts else str(err)
