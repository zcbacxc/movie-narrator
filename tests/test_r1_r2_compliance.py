# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for compliance items R1 (edge-tts commercial warning) and
R2 (TMDB attribution).

- R1: the first-run notice must warn that the default `edge` TTS channel is
  for personal/non-commercial use.
- R2: TMDB-sourced research must carry the required "Powered by TMDB"
  attribution in research.json.
"""

import io
from unittest.mock import patch

from movie_narrator import config
from movie_narrator.providers.tmdb import TMDB_ATTRIBUTION


def test_r1_first_run_notice_contains_commercial_warning():
    """R1: the first-run notice must warn about edge-tts commercial use."""
    buf = io.StringIO()
    # Ensure CI is unset so the notice is actually emitted (the CI guard
    # strips it in CI runs).
    with patch("sys.stderr", buf), patch.dict("os.environ", {}, clear=False) as env:
        env.pop("CI", None)
        config._print_first_run_notice(__import__("pathlib").Path("/tmp/.env.example"))
    out = buf.getvalue()
    assert "edge" in out.lower()
    assert "商用" in out or "commercial" in out.lower()
    assert "openai" in out or "mimo" in out


def test_r1_first_run_notice_skipped_in_ci():
    """R1: the notice must be suppressed in CI mode."""
    buf = io.StringIO()
    with (
        patch("sys.stderr", buf),
        patch.dict("os.environ", {"CI": "1"}),
    ):
        config._print_first_run_notice(__import__("pathlib").Path("/tmp/.env.example"))
    assert buf.getvalue() == ""


def test_r2_tmdb_attribution_constant():
    """R2: the attribution constant matches the official TMDB wording."""
    assert "uses the TMDB API" in TMDB_ATTRIBUTION
    assert "not endorsed or certified by TMDB" in TMDB_ATTRIBUTION
