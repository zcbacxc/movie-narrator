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
    match_min_score: float = 0.25
    export_clips_default: bool = True

    model_config = SettingsConfigDict(
        env_file=(".env", str(_USER_ENV)),
        env_file_encoding="utf-8",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
