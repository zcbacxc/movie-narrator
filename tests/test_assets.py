from pathlib import Path

from movie_narrator.models import Assets, Context
from movie_narrator.pipeline.assets import prepare_assets


def test_prepare_assets_missing_bgm_clears(tmp_path):
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        assets=Assets(bgm=str(tmp_path / "no.mp3")),
    )
    ctx.metadata["bgm_request"] = "explicit"
    prepare_assets(ctx)
    assert ctx.assets.bgm is None
    assert ctx.metadata.get("bgm_error")


def test_prepare_assets_ok(tmp_path):
    bgm = tmp_path / "b.mp3"
    bgm.write_bytes(b"ID3")
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        assets=Assets(bgm=str(bgm)),
    )
    ctx.metadata["bgm_request"] = "explicit"
    prepare_assets(ctx)
    assert ctx.assets.bgm is not None
    assert Path(ctx.assets.bgm).is_absolute()
    assert Path(ctx.assets.bgm).is_file()
