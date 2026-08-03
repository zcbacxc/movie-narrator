# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

import os
import sys
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

    On first creation, prints a one-time informational message to stderr
    telling the user where the config was created and which fields to edit.
    This is non-interactive (no prompt) so the CLI remains scriptable.

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

        # One-time first-run notice (non-interactive, goes to stderr
        # so it doesn't pollute stdout in piped workflows).
        _print_first_run_notice(_USER_ENV)
    return _USER_ENV


def _print_first_run_notice(env_path: Path) -> None:
    """Print a one-time message when the config file is first created."""
    # CI mode: skip the notice entirely (CI runs don't need it).
    if os.getenv("CI"):
        return
    print(
        f"\n[movie-narrator] 首次运行：已创建配置文件\n"
        f"  路径: {env_path}\n"
        f"  请编辑此文件，填入你的 LLM 和 TTS 配置：\n"
        f"    MN_LLM_BASE_URL  — LLM API 地址 (如 http://localhost:11434/v1)\n"
        f"    MN_LLM_API_KEY   — LLM API 密钥\n"
        f"    MN_LLM_MODEL     — LLM 模型名称\n"
        f"    MN_DEFAULT_VOICE — TTS 语音 (如 zh-CN-YunxiNeural)\n"
        f"  配置完成后重新运行即可。\n",
        file=sys.stderr,
    )


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
    # ── API server / Remote inference (v0.8.0) ──
    # X-API-Key for authenticating the remote inference API server
    # (``mn serve``). When None, the API server runs unauthenticated —
    # safe only on loopback. Required when binding to a public interface.
    api_key: Optional[str] = None
    # v0.9.2: graceful-shutdown drain budget (seconds). After SIGINT /
    # SIGTERM, ``mn serve`` and ``TaskAPIServer.stop()`` wait up to this
    # long for in-flight tasks to finish before force-cancelling them.
    graceful_shutdown_timeout: float = 30.0
    # ── Scheduled jobs (v0.9.3) ──
    # When enabled, ``mn serve`` starts a background thread that submits
    # cron-scheduled jobs to the task queue. The API routes (POST/GET/
    # DELETE /schedules) remain available even when disabled — only the
    # trigger loop is off.
    scheduler_enabled: bool = True
    scheduler_poll_interval: float = 15.0
    # ── LLM ──
    llm_provider: str = "openai"  # registered LLM provider name (see llm_registry)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "ollama"
    llm_model: str = "qwen2.5:7b"
    llm_timeout: int = 60
    script_temperature: float = 0.7
    script_expand_temperature: float = 0.5
    script_max_tokens: int = 2048
    script_retries: int = 3
    script_retry_delay: float = 1.5
    research_temperature: float = 0.3
    research_max_tokens: int = 1024
    research_retries: int = 3
    research_retry_delay: float = 1.5
    translate_max_tokens: int = 4096
    # ── TMDB (external movie database for fact verification) ──
    tmdb_api_key: Optional[str] = None
    tmdb_base_url: str = "https://api.themoviedb.org/3"
    tmdb_language: str = "zh-CN"
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
    # ── Reliability (v0.9.1) ──
    # Circuit breaker protects external API calls (LLM / TTS / TMDB / VLM)
    # from repeatedly hitting an unhealthy endpoint. ``failure_threshold``
    # consecutive failures open the circuit; after ``recovery_timeout``
    # seconds it half-opens and allows ``half_open_max_calls`` concurrent
    # probe requests before deciding whether to close again.
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout: float = 30.0
    circuit_half_open_max_calls: int = 1

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
