from pathlib import Path
from unittest.mock import patch

from movie_narrator.config import Settings, ensure_user_config, _read_example_env
from movie_narrator.utils.environment import collect_environment


def test_settings_llm_tts_defaults():
    s = Settings()
    # LLM
    assert s.llm_base_url
    assert s.llm_api_key
    assert s.llm_model
    assert s.llm_timeout == 60
    assert s.script_temperature == 0.7
    assert s.script_max_tokens == 2048
    assert s.script_retries == 3
    assert s.script_retry_delay == 1.5
    assert s.research_temperature == 0.3
    assert s.research_max_tokens == 1024
    assert s.translate_max_tokens == 4096
    # TTS
    assert s.default_voice
    assert s.tts_cache_max_mb == 500
    # Pipeline fields should NOT be in Settings (they're in job.yaml)
    assert not hasattr(s, "scene_threshold")
    assert not hasattr(s, "match_min_score")
    assert not hasattr(s, "render_video_codec")
    assert not hasattr(s, "library_dir")


def test_collect_environment_keys():
    env = collect_environment()
    assert "python" in env
    assert "platform" in env
    assert "ffmpeg" in env


def test_ensure_user_config_creates_file(tmp_path):
    """ensure_user_config creates ~/.movie-narrator/.env when missing."""
    fake_home = tmp_path / "fakehome"
    fake_env = fake_home / ".movie-narrator" / ".env"
    with patch("movie_narrator.config._USER_DIR", fake_home / ".movie-narrator"), \
         patch("movie_narrator.config._USER_ENV", fake_env):
        result = ensure_user_config()
    assert result == fake_env
    assert fake_env.exists()
    content = fake_env.read_text(encoding="utf-8")
    assert "MN_LLM_BASE_URL" in content
    assert "MN_DEFAULT_VOICE" in content


def test_ensure_user_config_no_overwrite(tmp_path):
    """ensure_user_config never overwrites an existing file."""
    fake_home = tmp_path / "fakehome"
    fake_dir = fake_home / ".movie-narrator"
    fake_dir.mkdir(parents=True)
    fake_env = fake_dir / ".env"
    original = "MN_LLM_MODEL=my-custom-model\n"
    fake_env.write_text(original, encoding="utf-8")
    with patch("movie_narrator.config._USER_DIR", fake_dir), \
         patch("movie_narrator.config._USER_ENV", fake_env):
        ensure_user_config()
    assert fake_env.read_text(encoding="utf-8") == original
