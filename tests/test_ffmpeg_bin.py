# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the shared ffmpeg binary resolution policy."""

import os
import sys
import types
from unittest.mock import patch

from movie_narrator.utils.ffmpeg_bin import _FFMPEG_OVERRIDE_ENV, ffmpeg_bin


def _fake_imageio(path: str):
    """Build a fake imageio_ffmpeg module whose get_ffmpeg_exe returns ``path``."""
    fake = types.ModuleType("imageio_ffmpeg")
    fake.get_ffmpeg_exe = lambda: path
    return fake


def test_override_env_wins():
    """An explicit MN_FFMPEG_BIN override beats everything else."""
    fake = _fake_imageio("/bundled/exe")
    with (
        patch.dict(os.environ, {_FFMPEG_OVERRIDE_ENV: "/custom/ffmpeg"}, clear=False),
        patch("movie_narrator.utils.ffmpeg_bin.os.path.isfile", return_value=True),
        patch.dict(sys.modules, {"imageio_ffmpeg": fake}),
        patch("movie_narrator.utils.ffmpeg_bin.shutil.which", return_value="/usr/bin/ffmpeg"),
    ):
        assert ffmpeg_bin() == "/custom/ffmpeg"


def test_override_env_ignored_when_missing():
    """An override pointing at a non-existent file is skipped."""
    fake = _fake_imageio("/bundled/exe")
    with (
        patch.dict(os.environ, {_FFMPEG_OVERRIDE_ENV: "/does/not/exist"}, clear=False),
        patch("movie_narrator.utils.ffmpeg_bin.os.path.isfile", return_value=False),
        patch.dict(sys.modules, {"imageio_ffmpeg": fake}),
        patch("movie_narrator.utils.ffmpeg_bin.shutil.which", return_value="/usr/bin/ffmpeg"),
    ):
        assert ffmpeg_bin() == "/bundled/exe"


def test_imageio_bundled_wins_over_system():
    """The bundled imageio build is preferred over a system ffmpeg on PATH."""
    fake = _fake_imageio("/bundled/exe")
    with (
        patch.dict(os.environ, {}, clear=True),
        patch.dict(sys.modules, {"imageio_ffmpeg": fake}),
        patch("movie_narrator.utils.ffmpeg_bin.shutil.which", return_value="/usr/bin/ffmpeg"),
    ):
        assert ffmpeg_bin() == "/bundled/exe"


def test_fallback_to_system_ffmpeg():
    """When imageio is unavailable, fall back to a system ffmpeg on PATH."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch.dict(sys.modules, {"imageio_ffmpeg": None}),
        patch("movie_narrator.utils.ffmpeg_bin.shutil.which", return_value="/usr/bin/ffmpeg"),
    ):
        assert ffmpeg_bin() == "/usr/bin/ffmpeg"


def test_fallback_to_bare_ffmpeg_when_import_fails():
    """When imageio can't be imported and no system ffmpeg, return bare 'ffmpeg'."""
    with (
        patch.dict(os.environ, {}, clear=True),
        patch.dict(sys.modules, {"imageio_ffmpeg": None}),
        patch("movie_narrator.utils.ffmpeg_bin.shutil.which", return_value=None),
    ):
        assert ffmpeg_bin() == "ffmpeg"


def test_fallback_when_get_ffmpeg_exe_raises():
    """A runtime failure from get_ffmpeg_exe falls back to system ffmpeg."""
    fake = types.ModuleType("imageio_ffmpeg")

    def boom():
        raise RuntimeError("no bundled binary")

    fake.get_ffmpeg_exe = boom
    with (
        patch.dict(os.environ, {}, clear=True),
        patch.dict(sys.modules, {"imageio_ffmpeg": fake}),
        patch("movie_narrator.utils.ffmpeg_bin.shutil.which", return_value="/usr/bin/ffmpeg"),
    ):
        assert ffmpeg_bin() == "/usr/bin/ffmpeg"