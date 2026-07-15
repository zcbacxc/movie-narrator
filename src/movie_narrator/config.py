import os
from enum import Enum
from pathlib import Path
from typing import Optional

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_USER_DIR = Path.home() / ".movie-narrator"
_USER_ENV = _USER_DIR / ".env"

# Template written to ~/.movie-narrator/.env on first run.
# Mirrors .env.example — kept inline so it works regardless of install method.
_DEFAULT_ENV_TEMPLATE = """\
# ============================================================
# Movie Narrator — global configuration
# ============================================================
# This file was auto-created on first run.  Edit values as needed.
# Project-level .env (current directory) and MN_* environment
# variables override entries here.
# ============================================================

# ── LLM ──
MN_LLM_BASE_URL=http://localhost:11434/v1
MN_LLM_API_KEY=ollama
MN_LLM_MODEL=qwen2.5:7b

# ── TTS voice (unified, interpreted per-provider) ──
MN_DEFAULT_VOICE=zh-CN-YunxiNeural

# ── Video output ──
MN_DEFAULT_FORMAT=16:9

# ── TTS provider: edge | openai | mimo ──
# MN_TTS_PROVIDER=edge

# ── OpenAI TTS (active when MN_TTS_PROVIDER=openai) ──
# MN_OPENAI_TTS_MODEL=tts-1
# MN_OPENAI_TTS_API_KEY=           # falls back to MN_LLM_API_KEY
# MN_OPENAI_TTS_BASE_URL=          # falls back to MN_LLM_BASE_URL

# ── MiMo TTS (active when MN_TTS_PROVIDER=mimo) ──
# MN_MIMO_TTS_MODEL=mimo-v2.5-tts
# MN_MIMO_API_KEY=                 # falls back to MN_LLM_API_KEY
# MN_MIMO_BASE_URL=https://api.xiaomimimo.com/v1
# MN_MIMO_STYLE_PROMPT=            # style description, mimo-v2.5-tts only

# ── v0.2 optional ──
# MN_LIBRARY_DIR=
# MN_DEFAULT_BGM=
# MN_RESEARCH_ENABLED=false
# MN_RESEARCH_PROVIDER=llm
# MN_SCENE_THRESHOLD=27.0
# MN_SCENE_FRAME_SKIP=10
# MN_MATCH_MIN_SCORE=0.25
# MN_EXPORT_CLIPS_DEFAULT=true

# ── v0.3 multi-language subtitles ──
# MN_SUBTITLE_LANG=                # empty = feature off
# MN_SUBTITLE_MODE=original
# MN_TRANSLATE_PROVIDER=llm
# MN_TRANSLATE_RETRIES=3
# MN_TRANSLATE_CHUNK_CHARS=4000
# MN_TRANSLATE_CHUNK_SIZE=20

# ── Match ──
# MN_TTS_CACHE_MAX_MB=500
# MN_TTS_PAUSE_MS=300

# ── BGM ──
# MN_BGM_GAIN_DB=-18

# ── Match ──
# MN_MATCH_SPEED_CLAMP_MIN=0.5
# MN_MATCH_SPEED_CLAMP_MAX=3.0
# MN_SCENE_MERGE_MIN_DURATION=0
# MN_EMBEDDING_MODEL_NAME=paraphrase-multilingual-MiniLM-L12-v2

# ── Render ──
# MN_RENDER_FPS=24
# MN_RENDER_VIDEO_CODEC=libx264
# MN_RENDER_AUDIO_CODEC=aac
# MN_RENDER_THREADS=4
"""


def ensure_user_config() -> Path:
    """Create ``~/.movie-narrator/.env`` from defaults if it does not exist.

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
                f.write(_DEFAULT_ENV_TEMPLATE)
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
    # ── BGM mixing ──
    bgm_gain_db: float = -18.0                  # gain applied to BGM track before mixing with narration
    # ── Embedding model for match re-rank ──
    embedding_model_name: str = "paraphrase-multilingual-MiniLM-L12-v2"
    # ── Render ──
    render_fps: int = 24
    render_video_codec: str = "libx264"
    render_audio_codec: str = "aac"
    render_threads: int = 4
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
