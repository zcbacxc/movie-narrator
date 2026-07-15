from pathlib import Path
from unittest.mock import patch

from movie_narrator.config import Settings, ensure_user_config, _read_example_env
from movie_narrator.utils.environment import collect_environment


def test_settings_v02_defaults():
    s = Settings()
    assert hasattr(s, "library_dir")
    assert s.research_enabled is False
    assert s.research_provider == "llm"
    assert s.scene_threshold == 27.0
    assert s.match_min_score == 0.25
    assert s.export_clips_default is True
    assert s.default_bgm is None


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
