from pathlib import Path

import pytest

from movie_narrator.models import Context, ScriptSegment
from movie_narrator.pipeline.script_export import export_script_md


def test_export_script_md(tmp_path):
    ctx = Context(
        movie_name="飞驰人生",
        output_dir=str(tmp_path),
        segments=[
            ScriptSegment(text="一个过气车神突然复出了。"),
            ScriptSegment(text="所有人都觉得他疯了。"),
        ],
    )
    export_script_md(ctx)
    path = Path(ctx.script_md_path)
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "# 飞驰人生" in text
    assert "一个过气车神突然复出了。" in text
    assert "## 1" in text
    assert "## 2" in text
