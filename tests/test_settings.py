from movie_narrator.config import Settings
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
