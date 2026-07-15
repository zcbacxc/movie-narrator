import os
from enum import Enum
from pathlib import Path
from typing import Optional

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_USER_DIR = Path.home() / ".movie-narrator"
_USER_ENV = _USER_DIR / ".env"

# Package-level .env.example — single source of truth for default config.
# At runtime we resolve it relative to this file so it works in editable
# installs, wheels, and source checkouts alike.
_PACKAGE_DIR = Path(__file__).resolve().parent          # src/movie_narrator/
_SRC_DIR = _PACKAGE_DIR.parent                            # src/
_PROJECT_ROOT = _SRC_DIR.parent                           # movie-narrator/
_EXAMPLE_ENV = _PROJECT_ROOT / ".env.example"


def _read_example_env() -> str:
    """Return the contents of ``.env.example``.

    Falls back to a minimal inline template if the file is missing
    (e.g. some packaging configurations strip it).
    """
    if _EXAMPLE_ENV.is_file():
        return _EXAMPLE_ENV.read_text(encoding="utf-8")
    # Minimal fallback — kept short to avoid drift with .env.example.
    return (
        "# Movie Narrator — auto-generated minimal config\n"
        "MN_LLM_BASE_URL=http://localhost:11434/v1\n"
        "MN_LLM_API_KEY=ollama\n"
        "MN_LLM_MODEL=qwen2.5:7b\n"
        "MN_DEFAULT_VOICE=zh-CN-YunxiNeural\n"
        "MN_DEFAULT_FORMAT=16:9\n"
    )


def ensure_user_config() -> Path:
    """Create ``~/.movie-narrator/.env`` from ``.env.example`` if it does not exist.

    Returns the path to the user-level .env (existing or newly created).
    Safe to call multiple times — never overwrites an existing file.

    Write is atomic (temp file + ``os.replace``) to prevent partial writes
    if the process is interrupted mid-write (TOCTOU safe).
    """
    if not _USER_ENV.exists():
        import os
        import tempfile

        _USER_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(dir=_USER_DIR, suffix=".env.tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(_read_example_env())
            os.replace(tmp_path, _USER_ENV)  # atomic on same filesystem
        except BaseException:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
    return _USER_ENV


class TTSProviderType(str, Enum):
    EDGE = "edge"
    OPENAI = "openai"
    MIMO = "mimo"


class Settings(BaseSettings):
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:7b"
    # ── LLM call tuning ──
    llm_timeout: int = 60                    # httpx client timeout (seconds)
    script_temperature: float = 0.7          # script generation LLM temperature
    script_max_tokens: int = 2048            # script generation LLM max_tokens
    script_retries: int = 3                  # script generation retry attempts
    script_retry_delay: float = 1.5          # delay between retries (seconds)
    research_temperature: float = 0.3        # research LLM temperature
    research_max_tokens: int = 1024          # research LLM max_tokens
    translate_max_tokens: int = 4096         # translation LLM max_tokens
    default_voice: str = "zh-CN-YunxiNeural"
    default_format: str = "16:9"
    # v0.2 optional
    library_dir: Optional[str] = None
    default_bgm: Optional[str] = None
    research_enabled: bool = False
    research_provider: str = "llm"
    scene_threshold: float = 27.0
    scene_frame_skip: int = 10
    match_min_score: float = 0.25
    # ── Match speed clamp (v0.4.6) ──
    # Limits the speed scaling factor applied during render to prevent
    # extreme fast-forward (>3x) or slow-motion (<0.5x) that causes
    # visual jarring between adjacent narration segments.
    match_speed_clamp_min: float = 0.5
    match_speed_clamp_max: float = 3.0
    # Merge consecutive scenes shorter than this (0 = disabled).
    # Reduces extreme speed factors by giving match_clips wider windows.
    scene_merge_min_duration: float = 0.0
    export_clips_default: bool = True
    # v0.3 multi-language subtitle defaults.
    # Empty/None subtitle_lang = feature off (status.translate=skipped).
    subtitle_lang: Optional[str] = None
    subtitle_mode: str = "original"
    translate_provider: str = "llm"
    translate_retries: int = 3
    translate_chunk_chars: int = 4000
    translate_chunk_size: int = 20
    # ── TTS abstraction (v0.4) ──
    tts_provider: TTSProviderType = TTSProviderType.EDGE
    openai_tts_model: str = "tts-1"
    openai_tts_api_key: Optional[str] = None   # falls back to llm_api_key
    openai_tts_base_url: Optional[str] = None  # falls back to llm_base_url
    # ── MiMo TTS (v0.4.1) ──
    # Three models: mimo-v2.5-tts (named voice), mimo-v2.5-tts-voiceclone (audio file),
    #               mimo-v2.5-tts-voicedesign (text description)
    mimo_tts_model: str = "mimo-v2.5-tts"
    mimo_api_key: Optional[str] = None         # falls back to llm_api_key
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    mimo_style_prompt: str = ""                # style description for user message (mimo-v2.5-tts only)
    # ── TTS cache management ──
    tts_cache_max_mb: int = 500                 # LRU eviction threshold for TTS cache
    # ── TTS pacing ──
    tts_pause_ms: int = 300                     # silence inserted between narration segments
    tts_max_concurrent: int = 3                 # max parallel TTS synthesis tasks
    tts_audio_format: str = "mp3"              # narration audio export format
    tts_audio_bitrate: str = "128k"            # narration audio export bitrate
    # ── BGM mixing ──
    bgm_gain_db: float = -18.0                  # gain applied to BGM track before mixing with narration
    # ── Embedding model for match re-rank ──
    embedding_model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"
    # ── WhisperX (align step, optional [ml] dep) ──
    whisperx_device: str = "cpu"                # inference device: cpu | cuda
    whisperx_model: str = "medium"              # WhisperX model size: tiny|base|small|medium|large
    whisperx_language: str = "zh"               # WhisperX transcription language
    # ── Translate ──
    translate_source_lang: str = "zh-CN"        # default source language for subtitle translation
    # ── Render ──
    render_fps: int = 24
    render_video_codec: str = "libx264"
    render_audio_codec: str = "aac"
    render_threads: int = 4
    render_bg_color: str = "20,20,30"           # RGB background color (comma-separated)
    render_font_size: int = 100                 # text overlay font size
    render_output_name: str = "final.mp4"       # final video output filename
    render_ffmpeg_timeout: int = 300            # export_clips ffmpeg subprocess timeout (seconds)
    # ── Async ──
    async_timeout: int = 300                    # async task timeout (seconds)
    async_max_workers: int = 2                  # thread pool size for async execution
    # ── Video resolution presets ──
    # JSON string, parsed at render time: {"16:9": [1920, 1080], "9:16": [1080, 1920]}
    video_sizes: str = '{"16:9": [1920, 1080], "9:16": [1080, 1920]}'

    model_config = SettingsConfigDict(
        env_file=(".env", str(_USER_ENV)),
        env_file_encoding="utf-8",
        env_prefix="MN_",
    )


@lru_cache
def get_settings() -> Settings:
    ensure_user_config()
    return Settings()
