from movie_narrator.utils.optional_deps import probe


def test_probe_returns_bool_and_str():
    ok, hint = probe("scenedetect")
    assert isinstance(ok, bool)
    assert isinstance(hint, str)


def test_probe_unknown_name():
    ok, hint = probe("nonexistent_package_xyz")
    assert ok is False
    assert isinstance(hint, str)
    assert len(hint) > 0
