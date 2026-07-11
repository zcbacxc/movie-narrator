from pathlib import Path

import pytest

from movie_narrator.models import Context
from movie_narrator.pipeline.errors import PipelineStrictError
from movie_narrator.pipeline.runner import _check_strict


def test_strict_raises_on_failed_status(tmp_path):
    ctx = Context(movie_name="m", output_dir=str(tmp_path))
    ctx.metadata["strict"] = True
    ctx.status.bgm = "failed"
    with pytest.raises(PipelineStrictError) as exc:
        _check_strict(ctx, "mix_bgm")
    assert exc.value.step == "mix_bgm"
    assert exc.value.status["bgm"] == "failed"


def test_strict_no_raise_when_disabled_or_skipped(tmp_path):
    ctx = Context(movie_name="m", output_dir=str(tmp_path))
    ctx.metadata["strict"] = True
    ctx.status.scene = "disabled"
    ctx.status.bgm = "skipped"
    _check_strict(ctx, "detect_scenes")
    _check_strict(ctx, "mix_bgm")


def test_strict_no_raise_without_flag(tmp_path):
    ctx = Context(movie_name="m", output_dir=str(tmp_path))
    ctx.status.bgm = "failed"
    _check_strict(ctx, "mix_bgm")
