import os
from pathlib import Path
from typing import Optional

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

_USER_ENV = Path.home() / ".movie-narrator" / ".env"


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
    export_clips_default: bool = True
    # v0.3 multi-language subtitle defaults.
    # Empty/None subtitle_lang = feature off (status.translate=skipped).
    subtitle_lang: Optional[str] = None
    subtitle_mode: str = "original"
    translate_provider: str = "llm"
    translate_retries: int = 3
    translate_chunk_chars: int = 4000
    translate_chunk_size: int = 20

    model_config = SettingsConfigDict(
        env_file=(".env", str(_USER_ENV)),
        env_file_encoding="utf-8",
        env_prefix="MN_",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
