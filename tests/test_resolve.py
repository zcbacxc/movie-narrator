from pathlib import Path

import pytest

from movie_narrator.models import Context
from movie_narrator.pipeline.resolve import find_in_library, normalize_title, resolve_video


def test_normalize_title_strips_year_and_width():
    assert "功夫" in normalize_title("功夫 (2004)")
    assert normalize_title(" Foo ") == normalize_title("foo")


def test_find_in_library(tmp_path):
    f = tmp_path / "飞驰人生.mp4"
    f.write_bytes(b"00")
    hit = find_in_library("飞驰人生", str(tmp_path))
    assert hit is not None
    assert Path(hit).name == "飞驰人生.mp4"


def test_resolve_video_explicit(tmp_path):
    v = tmp_path / "a.mp4"
    v.write_bytes(b"00")
    ctx = Context(movie_name="a", output_dir=str(tmp_path))
    ctx.metadata["video_arg"] = str(v)
    resolve_video(ctx)
    assert ctx.source_video_path == str(v.resolve())


def test_resolve_video_missing_library(tmp_path):
    ctx = Context(
        movie_name="Nope",
        output_dir=str(tmp_path),
        library_dir=str(tmp_path),
    )
    resolve_video(ctx)
    assert ctx.source_video_path is None
