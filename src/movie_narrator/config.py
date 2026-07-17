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
    """Global LLM + TTS infrastructure configuration.

    Boundary: .env (Settings) = LLM + TTS credentials, endpoints, models,
    call params only. All pipeline behavior (scene, match, render, etc.)
    is configured via job.yaml params — see ``examples/job.example.yaml`` for defaults.
    """
    # ── LLM ──
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:7b"
    llm_timeout: int = 60
    script_temperature: float = 0.7
    script_max_tokens: int = 2048
    script_retries: int = 3
    script_retry_delay: float = 1.5
    research_temperature: float = 0.3
    research_max_tokens: int = 1024
    research_retries: int = 3
    research_retry_delay: float = 1.5
    translate_max_tokens: int = 4096
    # ── TTS ──
    default_voice: str = "zh-CN-YunxiNeural"
    tts_provider: TTSProviderType = TTSProviderType.EDGE
    openai_tts_model: str = "tts-1"
    openai_tts_api_key: Optional[str] = None
    openai_tts_base_url: Optional[str] = None
    mimo_tts_model: str = "mimo-v2.5-tts"
    mimo_api_key: Optional[str] = None
    mimo_base_url: str = "https://api.xiaomimimo.com/v1"
    mimo_style_prompt: str = ""
    tts_cache_max_mb: int = 500

    model_config = SettingsConfigDict(
        env_file=(".env", str(_USER_ENV)),
        env_file_encoding="utf-8",
        env_prefix="MN_",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    ensure_user_config()
    return Settings()
