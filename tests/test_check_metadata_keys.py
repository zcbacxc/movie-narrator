# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Ensure the metadata weak-typing residual fix stays in place."""

import subprocess
import sys
from pathlib import Path

from movie_narrator.models import MetadataDict

REPO_ROOT = Path(__file__).resolve().parent.parent

# The 10 literal metadata keys that were previously missing from MetadataDict.
EXPECTED_KEYS = {
    "reference_media",
    "reference_media_captions",
    "prompt_cache",
    "rerun",
    "usage",
    "plan",
    "plan_policy",
    "artifact_retention",
    "match_visual_features",
    "render_main_encode_timeout",
}


def test_metadata_weak_typing_residuals_declared():
    """MetadataDict declares the 10 keys that were previously missing."""
    missing = EXPECTED_KEYS - set(MetadataDict.__annotations__)
    assert not missing, f"Missing in MetadataDict: {sorted(missing)}"


def test_check_metadata_keys_gate_passes():
    """The gate script exits 0, i.e. all literal metadata keys are declared."""
    result = subprocess.run(
        [sys.executable, "scripts/check_metadata_keys.py"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"gate failed (rc={result.returncode}):\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )