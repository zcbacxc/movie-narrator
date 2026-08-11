from unittest.mock import patch

from movie_narrator.utils.optional_deps import DepStatus, probe, probe_status


def test_probe_returns_bool_and_str():
    ok, hint = probe("scenedetect")
    assert isinstance(ok, bool)
    assert isinstance(hint, str)


def test_probe_unknown_name():
    ok, hint = probe("nonexistent_package_xyz")
    assert ok is False
    assert isinstance(hint, str)
    assert len(hint) > 0


def test_probe_status_not_installed():
    with patch(
        "movie_narrator.utils.optional_deps.import_module",
        side_effect=ModuleNotFoundError("No module named 'whisperx'", name="whisperx"),
    ):
        status, hint = probe_status("whisperx")
    assert status is DepStatus.NOT_INSTALLED
    assert "movie-narrator[ml]" in hint


def test_probe_status_missing_transitive_dep():
    with patch(
        "movie_narrator.utils.optional_deps.import_module",
        side_effect=ModuleNotFoundError("No module named 'torchaudio'", name="torchaudio"),
    ):
        status, hint = probe_status("whisperx")
    assert status is DepStatus.MISSING_DEPS
    assert "torchaudio" in hint
    assert "whisperx" in hint


def test_probe_collapses_missing_deps_to_unavailable():
    with patch(
        "movie_narrator.utils.optional_deps.import_module",
        side_effect=ModuleNotFoundError("No module named 'sklearn'", name="sklearn"),
    ):
        ok, hint = probe("sentence_transformers")
    assert ok is False
    assert "sklearn" in hint
