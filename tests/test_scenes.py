from pathlib import Path
from unittest.mock import patch

import pytest

from movie_narrator.models import Context
from movie_narrator.pipeline.scenes import detect_scenes


def test_detect_scenes_disabled_without_dep(tmp_path):
    ctx = Context(movie_name="m", output_dir=str(tmp_path))
    detect_scenes(ctx)
    assert ctx.status.scene == "disabled"


def test_detect_scenes_skipped_no_source(tmp_path):
    ctx = Context(movie_name="m", output_dir=str(tmp_path))
    with patch(
        "movie_narrator.pipeline.scenes.probe", return_value=(True, "")
    ):
        detect_scenes(ctx)
    assert ctx.status.scene == "skipped"
