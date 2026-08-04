# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""v0.8.0 GAP-4: render_template 模板化测试.

测试覆盖:
  a. 每个内置 preset 的 render_template() 返回正确格式
  b. {movie} 占位符替换正确
  c. 用户 YAML params.render_template 覆盖 preset 默认值
  d. metadata.json 包含 render_template 字段
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from movie_narrator.models import Context, TimedSegment
from movie_narrator.pipeline.render import _substitute_movie
from movie_narrator.pipeline.runner import build_context
from movie_narrator.presets.bilibili_long import BilibiliLongPreset
from movie_narrator.presets.douyin_fast import DouyinFastPreset
from movie_narrator.presets.mainstream_dry import MainstreamDryPreset
from movie_narrator.utils.metadata_export import build_metadata_json

# ── 已识别的 render_template 顶层键 ────────────────────────
_RECOGNISED_KEYS = frozenset({
    "title_card_text",
    "end_card_text",
    "watermark_text",
    "disclaimer_text",
    "slogan_text",
    "aspect_safe_area",
})

_TEST_MOVIE = "飞驰人生"


# ── (a) 每个 preset 的 render_template() 返回正确格式 ──────


@pytest.mark.parametrize(
    "preset_cls",
    [MainstreamDryPreset, DouyinFastPreset, BilibiliLongPreset],
)
def test_render_template_returns_dict(preset_cls):
    """render_template() must return a dict."""
    tpl = preset_cls().render_template()
    assert isinstance(tpl, dict), f"{preset_cls.__name__}.render_template() must return dict"
    assert len(tpl) > 0, f"{preset_cls.__name__}.render_template() must not be empty"


@pytest.mark.parametrize(
    "preset_cls",
    [MainstreamDryPreset, DouyinFastPreset, BilibiliLongPreset],
)
def test_render_template_keys_recognised(preset_cls):
    """All keys in render_template() must be in the recognised vocabulary."""
    tpl = preset_cls().render_template()
    unknown = set(tpl) - _RECOGNISED_KEYS
    assert not unknown, f"{preset_cls.__name__} has unrecognised keys: {unknown}"


@pytest.mark.parametrize(
    "preset_cls",
    [MainstreamDryPreset, DouyinFastPreset, BilibiliLongPreset],
)
def test_render_template_has_aspect_safe_area(preset_cls):
    """Every preset must provide aspect_safe_area as a dict with ratios."""
    tpl = preset_cls().render_template()
    assert "aspect_safe_area" in tpl, f"{preset_cls.__name__} missing aspect_safe_area"
    asa = tpl["aspect_safe_area"]
    assert isinstance(asa, dict), "aspect_safe_area must be a dict"
    assert "max_width_ratio" in asa, "aspect_safe_area must have max_width_ratio"
    assert "bottom_margin_ratio" in asa, "aspect_safe_area must have bottom_margin_ratio"
    assert 0 < asa["max_width_ratio"] <= 1.0
    assert 0 < asa["bottom_margin_ratio"] < 0.5


@pytest.mark.parametrize(
    "preset_cls",
    [MainstreamDryPreset, DouyinFastPreset, BilibiliLongPreset],
)
def test_render_template_has_title_card(preset_cls):
    """Every preset must provide title_card_text (string with {movie} placeholder)."""
    tpl = preset_cls().render_template()
    assert "title_card_text" in tpl, f"{preset_cls.__name__} missing title_card_text"
    assert isinstance(tpl["title_card_text"], str)
    assert "{movie}" in tpl["title_card_text"], "title_card_text should contain {movie}"


def test_mainstream_dry_template_style():
    """mainstream-dry: 简洁, 无水印无免责声明."""
    tpl = MainstreamDryPreset().render_template()
    assert "watermark_text" not in tpl, "mainstream-dry should not have watermark"
    assert "disclaimer_text" not in tpl, "mainstream-dry should not have disclaimer"
    assert "end_card_text" in tpl


def test_douyin_fast_template_style():
    """douyin-fast: 竖屏, 有水印和免责声明."""
    tpl = DouyinFastPreset().render_template()
    assert "watermark_text" in tpl, "douyin-fast should have watermark"
    assert "disclaimer_text" in tpl, "douyin-fast should have disclaimer"
    assert "end_card_text" in tpl
    # 竖屏安全区域应该更窄
    asa = tpl["aspect_safe_area"]
    assert asa["max_width_ratio"] <= 0.85, "douyin-fast safe area should be narrow"
    assert asa["bottom_margin_ratio"] >= 0.12, "douyin-fast bottom margin should be larger"


def test_bilibili_long_template_style():
    """bilibili-long: 横屏, 有片尾卡片和免责声明."""
    tpl = BilibiliLongPreset().render_template()
    assert "end_card_text" in tpl, "bilibili-long should have end card"
    assert "disclaimer_text" in tpl, "bilibili-long should have disclaimer"


@pytest.mark.parametrize(
    "preset_cls",
    [MainstreamDryPreset, DouyinFastPreset, BilibiliLongPreset],
)
def test_params_render_template_matches_method(preset_cls):
    """params()['render_template'] must equal render_template() return value."""
    preset = preset_cls()
    from_params = preset.params().get("render_template")
    from_method = preset.render_template()
    assert from_params == from_method, (
        f"{preset_cls.__name__}: params()['render_template'] != render_template()"
    )


# ── (b) {movie} 占位符替换正确 ─────────────────────────────


@pytest.mark.parametrize(
    "preset_cls",
    [MainstreamDryPreset, DouyinFastPreset, BilibiliLongPreset],
)
def test_movie_placeholder_substitution_in_template(preset_cls):
    """All {movie} placeholders in render_template strings are substituted."""
    tpl = preset_cls().render_template()
    for key, val in tpl.items():
        if isinstance(val, str) and "{movie}" in val:
            substituted = _substitute_movie(val, _TEST_MOVIE)
            assert "{movie}" not in substituted, (
                f"{preset_cls.__name__}.{key}: {{movie}} not replaced"
            )
            assert _TEST_MOVIE in substituted, (
                f"{preset_cls.__name__}.{key}: movie name not in result"
            )


def test_movie_placeholder_substitution_specific():
    """Specific substitution checks for each preset's title card."""
    md_tpl = MainstreamDryPreset().render_template()
    assert _substitute_movie(md_tpl["title_card_text"], _TEST_MOVIE) == _TEST_MOVIE

    dy_tpl = DouyinFastPreset().render_template()
    assert _substitute_movie(dy_tpl["watermark_text"], _TEST_MOVIE) == f"{_TEST_MOVIE}解说"

    bl_tpl = BilibiliLongPreset().render_template()
    assert _substitute_movie(bl_tpl["title_card_text"], _TEST_MOVIE) == _TEST_MOVIE


def test_movie_placeholder_multiple_occurrences():
    """Multiple {movie} placeholders in a single string are all replaced."""
    text = "{movie} - {movie}解说"
    result = _substitute_movie(text, _TEST_MOVIE)
    assert result == f"{_TEST_MOVIE} - {_TEST_MOVIE}解说"


def test_movie_placeholder_empty_movie_name():
    """Empty movie name produces empty replacement."""
    assert _substitute_movie("{movie}解说", "") == "解说"
    assert _substitute_movie("{movie}解说", None) == "解说"


def test_no_placeholder_unchanged():
    """Text without {movie} is returned unchanged."""
    text = "感谢观看"
    assert _substitute_movie(text, _TEST_MOVIE) == text


# ── (c) 用户 YAML render_template 覆盖 preset 默认值 ────────


def test_user_render_template_overrides_preset(tmp_path):
    """User-supplied render_template in params overrides the preset default."""
    user_template: Dict[str, Any] = {
        "title_card_text": "自定义标题",
        "end_card_text": "自定义片尾",
    }

    ctx = build_context(
        movie=_TEST_MOVIE,
        style="热血",
        duration=60,
        voice=None,
        video_format="16:9",
        output_dir=tmp_path,
        narration_preset="douyin-fast",
        params={"render_template": user_template},
    )

    # User's template should win over the preset's
    assert ctx.metadata["render_template"] == user_template
    assert ctx.metadata["render_template"] != DouyinFastPreset().render_template()


def test_preset_render_template_injected_without_user_override(tmp_path):
    """When no user render_template is provided, the preset default is used."""
    ctx = build_context(
        movie=_TEST_MOVIE,
        style="热血",
        duration=60,
        voice=None,
        video_format="9:16",
        output_dir=tmp_path,
        narration_preset="douyin-fast",
    )

    expected = DouyinFastPreset().render_template()
    assert ctx.metadata["render_template"] == expected
    # Verify key fields are present
    assert "watermark_text" in ctx.metadata["render_template"]
    assert "disclaimer_text" in ctx.metadata["render_template"]


def test_no_preset_no_render_template(tmp_path):
    """Without a preset, render_template is absent from metadata."""
    ctx = build_context(
        movie=_TEST_MOVIE,
        style="热血",
        duration=60,
        voice=None,
        video_format="16:9",
        output_dir=tmp_path,
    )
    assert "render_template" not in ctx.metadata or ctx.metadata.get("render_template") is None


def test_all_three_presets_inject_render_template(tmp_path):
    """Each built-in preset injects its render_template into ctx.metadata."""
    for preset_name, preset_cls in [
        ("mainstream-dry", MainstreamDryPreset),
        ("douyin-fast", DouyinFastPreset),
        ("bilibili-long", BilibiliLongPreset),
    ]:
        ctx = build_context(
            movie=_TEST_MOVIE,
            style="热血",
            duration=60,
            voice=None,
            video_format="16:9",
            output_dir=tmp_path,
            narration_preset=preset_name,
        )
        assert ctx.metadata.get("render_template") == preset_cls().render_template(), (
            f"Preset '{preset_name}' did not inject correct render_template"
        )


# ── (d) metadata.json 包含 render_template 字段 ────────────


def test_metadata_json_contains_render_template():
    """build_metadata_json includes render_template in its output."""
    template: Dict[str, Any] = {
        "title_card_text": "{movie}",
        "end_card_text": "感谢观看",
        "watermark_text": "{movie}解说",
    }
    ctx = Context(
        movie_name=_TEST_MOVIE,
        output_dir="/tmp/test",
        timed_segments=[TimedSegment(text="片段", start=0.0, end=2.0)],
    )
    ctx.metadata["render_template"] = template

    meta = build_metadata_json(ctx)
    assert "render_template" in meta
    assert meta["render_template"] == template


def test_metadata_json_render_template_none_when_absent():
    """build_metadata_json returns None for render_template when not set."""
    ctx = Context(
        movie_name=_TEST_MOVIE,
        output_dir="/tmp/test",
        timed_segments=[TimedSegment(text="片段", start=0.0, end=2.0)],
    )
    meta = build_metadata_json(ctx)
    assert "render_template" in meta
    assert meta["render_template"] is None


def test_metadata_json_with_preset_template(tmp_path):
    """Full integration: preset → build_context → build_metadata_json."""
    ctx = build_context(
        movie=_TEST_MOVIE,
        style="热血",
        duration=60,
        voice=None,
        video_format="9:16",
        output_dir=tmp_path,
        narration_preset="douyin-fast",
    )
    meta = build_metadata_json(ctx)
    assert "render_template" in meta
    tpl = meta["render_template"]
    assert tpl is not None
    assert "watermark_text" in tpl
    assert "disclaimer_text" in tpl
    assert "aspect_safe_area" in tpl


# ── 额外: aspect_safe_area 在 render.py 中被消费 ───────────


def test_aspect_safe_area_consumed_in_render(tmp_path, monkeypatch):
    """render_video uses aspect_safe_area from template for vertical safe area."""
    from unittest.mock import MagicMock
    from pathlib import Path

    from movie_narrator.pipeline import render as render_mod

    # Custom template with tighter safe area than the hardcoded defaults
    custom_safe = {"max_width_ratio": 0.75, "bottom_margin_ratio": 0.20}

    ctx = Context(
        movie_name=_TEST_MOVIE,
        output_dir=str(tmp_path),
        timed_segments=[TimedSegment(text="片段一", start=0.0, end=2.0)],
    )
    ctx.audio_path = str(tmp_path / "narration.wav")
    ctx.metadata["video_format"] = "9:16"
    ctx.metadata["render_vertical_safe_area"] = True
    ctx.metadata["render_template"] = {"aspect_safe_area": custom_safe}
    ctx.metadata["render_title_card_sec"] = 0  # disable title card

    # Capture the max_width_ratio and bottom_margin_ratio passed to
    # _create_text_image (it receives them as kwargs).
    captured_ratios: list[float] = []

    def _capture_text_image(text, size, **kwargs):
        captured_ratios.append(kwargs.get("max_width_ratio", -1))
        captured_ratios.append(kwargs.get("bottom_margin_ratio", -1))
        return MagicMock()

    final = MagicMock(name="final")
    final.write_videofile = MagicMock(
        side_effect=lambda path, **kw: Path(path).write_bytes(b"fake")
    )
    final.clips = []
    final.close = MagicMock()

    audio = MagicMock(name="audio")
    audio.duration = 6.0
    audio.close = MagicMock()

    clip_mock = MagicMock(name="clip")
    clip_mock.with_start.return_value = clip_mock
    clip_mock.with_duration.return_value = clip_mock
    clip_mock.with_position.return_value = clip_mock
    clip_mock.with_audio.return_value = clip_mock
    clip_mock.with_effects.return_value = clip_mock
    clip_mock.close = MagicMock()

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stderr = ""

    monkeypatch.setattr(render_mod, "ensure_final_audio", MagicMock())
    monkeypatch.setattr(render_mod, "CompositeVideoClip", MagicMock(return_value=final))
    monkeypatch.setattr(render_mod, "ColorClip", MagicMock(return_value=clip_mock))
    monkeypatch.setattr(render_mod, "ImageClip", MagicMock(return_value=clip_mock))
    monkeypatch.setattr(render_mod, "AudioFileClip", MagicMock(return_value=audio))
    monkeypatch.setattr(render_mod, "_create_text_image", _capture_text_image)
    monkeypatch.setattr(render_mod, "_create_watermark_image", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(render_mod, "build_metadata_json", MagicMock(return_value={}))
    monkeypatch.setattr("subprocess.run", MagicMock(return_value=fake_proc))
    monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))

    render_mod.render_video(ctx)

    # The custom max_width_ratio (0.75) should have been used instead of
    # the hardcoded _VERTICAL_MAX_WIDTH_RATIO (0.82).
    assert 0.75 in captured_ratios, (
        f"Custom max_width_ratio 0.75 not found in captured ratios: {captured_ratios}"
    )
    # The custom bottom_margin_ratio (0.20) should have been used instead of
    # the hardcoded _VERTICAL_BOTTOM_MARGIN_RATIO (0.15).
    assert 0.20 in captured_ratios, (
        f"Custom bottom_margin_ratio 0.20 not found in captured ratios: {captured_ratios}"
    )
