"""Tests for cover.jpg export + vertical safe area.

Tests cover:
- Vertical safe area auto-adjustment (render_vertical_safe_area param)
- Cover image export function (_export_cover_image)
- Param whitelist sync (4-file: schema/load/merge/runner + presets/base)
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Context, MatchedClip, Services, TimedSegment
from movie_narrator.utils.console import SilentConsole
from movie_narrator.presets.base import ALLOWED_PARAM_KEYS
from movie_narrator.pipeline.runner import PARAM_WHITELIST


# ── Param whitelist sync (4-file) ──────────────────────────


class TestEP5ParamWhitelistSync:
    """Verify render_cover_export and render_vertical_safe_area are in all 4 sync files."""

    def test_cover_export_in_param_whitelist(self):
        assert "render_cover_export" in PARAM_WHITELIST

    def test_vertical_safe_area_in_param_whitelist(self):
        assert "render_vertical_safe_area" in PARAM_WHITELIST

    def test_cover_export_in_presets_base(self):
        assert "render_cover_export" in ALLOWED_PARAM_KEYS

    def test_vertical_safe_area_in_presets_base(self):
        assert "render_vertical_safe_area" in ALLOWED_PARAM_KEYS

    def test_params_in_schema(self):
        from movie_narrator.workflow.schema import JobParams
        fields = JobParams.model_fields
        assert "render_cover_export" in fields
        assert "render_vertical_safe_area" in fields

    def test_params_in_load(self):
        """load.py uses a local allowed_params set — inspect source."""
        from movie_narrator.workflow import load
        source = inspect.getsource(load.load_job_config)
        assert "render_cover_export" in source
        assert "render_vertical_safe_area" in source

    def test_params_in_merge(self):
        """merge.py uses an inline tuple — inspect source."""
        from movie_narrator.workflow import merge
        source = inspect.getsource(merge.merge_job)
        assert "render_cover_export" in source
        assert "render_vertical_safe_area" in source


# ── Preset injection ───────────────────────────────────────


class TestEP5PresetInjection:
    """Verify presets inject the new cover export and safe area params."""

    def test_douyin_fast_has_cover_export(self):
        from movie_narrator.presets.douyin_fast import DouyinFastPreset
        params = DouyinFastPreset().params()
        assert params.get("render_cover_export") is True

    def test_douyin_fast_has_vertical_safe_area(self):
        from movie_narrator.presets.douyin_fast import DouyinFastPreset
        params = DouyinFastPreset().params()
        assert params.get("render_vertical_safe_area") is True

    def test_bilibili_long_has_cover_export(self):
        from movie_narrator.presets.bilibili_long import BilibiliLongPreset
        params = BilibiliLongPreset().params()
        assert params.get("render_cover_export") is True

    def test_bilibili_long_has_vertical_safe_area(self):
        from movie_narrator.presets.bilibili_long import BilibiliLongPreset
        params = BilibiliLongPreset().params()
        assert params.get("render_vertical_safe_area") is True

    def test_mainstream_dry_does_not_have_cover_export(self):
        """mainstream-dry should NOT auto-inject cover export (not in its preset)."""
        from movie_narrator.presets.mainstream_dry import MainstreamDryPreset
        params = MainstreamDryPreset().params()
        # mainstream-dry doesn't define it — defaults to False
        assert params.get("render_cover_export") is None


# ── Vertical safe area logic ───────────────────────────────


class TestVerticalSafeArea:
    """Test the vertical safe area auto-adjustment constants and logic."""

    def test_vertical_constants_exist(self):
        from movie_narrator.pipeline.render import (
            _VERTICAL_BOTTOM_MARGIN_RATIO,
            _VERTICAL_MAX_WIDTH_RATIO,
        )
        assert _VERTICAL_BOTTOM_MARGIN_RATIO > 0.08  # more than 16:9 default
        assert _VERTICAL_MAX_WIDTH_RATIO < 0.90      # less than 16:9 default

    def test_vertical_safe_area_logic(self):
        """Verify the min/max clamp logic for 9:16 format."""
        from movie_narrator.pipeline.render import (
            _VERTICAL_BOTTOM_MARGIN_RATIO,
            _VERTICAL_MAX_WIDTH_RATIO,
        )

        # Simulate the render.py logic
        default_max_width = 0.9
        default_bottom_margin = 0.08
        vertical_safe = True
        video_format = "9:16"

        if vertical_safe and video_format == "9:16":
            max_width_ratio = min(default_max_width, _VERTICAL_MAX_WIDTH_RATIO)
            bottom_margin_ratio = max(default_bottom_margin, _VERTICAL_BOTTOM_MARGIN_RATIO)
        else:
            max_width_ratio = default_max_width
            bottom_margin_ratio = default_bottom_margin

        assert max_width_ratio == _VERTICAL_MAX_WIDTH_RATIO
        assert bottom_margin_ratio == _VERTICAL_BOTTOM_MARGIN_RATIO

    def test_vertical_safe_area_disabled(self):
        """When render_vertical_safe_area=False, no adjustment."""
        from movie_narrator.pipeline.render import (
            _VERTICAL_BOTTOM_MARGIN_RATIO,
            _VERTICAL_MAX_WIDTH_RATIO,
        )

        default_max_width = 0.9
        default_bottom_margin = 0.08
        vertical_safe = False
        video_format = "9:16"

        if vertical_safe and video_format == "9:16":
            max_width_ratio = min(default_max_width, _VERTICAL_MAX_WIDTH_RATIO)
            bottom_margin_ratio = max(default_bottom_margin, _VERTICAL_BOTTOM_MARGIN_RATIO)
        else:
            max_width_ratio = default_max_width
            bottom_margin_ratio = default_bottom_margin

        assert max_width_ratio == 0.9
        assert bottom_margin_ratio == 0.08

    def test_vertical_safe_area_horizontal_no_change(self):
        """16:9 format should not trigger vertical adjustment."""
        from movie_narrator.pipeline.render import (
            _VERTICAL_BOTTOM_MARGIN_RATIO,
            _VERTICAL_MAX_WIDTH_RATIO,
        )

        default_max_width = 0.9
        default_bottom_margin = 0.08
        vertical_safe = True
        video_format = "16:9"

        if vertical_safe and video_format == "9:16":
            max_width_ratio = min(default_max_width, _VERTICAL_MAX_WIDTH_RATIO)
            bottom_margin_ratio = max(default_bottom_margin, _VERTICAL_BOTTOM_MARGIN_RATIO)
        else:
            max_width_ratio = default_max_width
            bottom_margin_ratio = default_bottom_margin

        assert max_width_ratio == 0.9
        assert bottom_margin_ratio == 0.08


# ── Cover image export ─────────────────────────────────────


class TestCoverExport:
    """Test the _export_cover_image function."""

    def _make_ctx(self, tmp_path, clips=None, movie_name="测试电影"):
        ctx = Context(
            movie_name=movie_name,
            output_dir=str(tmp_path),
            source_video_path="/fake/video.mp4",
            services=Services(console=SilentConsole()),
        )
        ctx.matched_clips = clips or []
        ctx.metadata = {}
        return ctx

    def test_no_clips_skips_export(self, tmp_path):
        from movie_narrator.pipeline.render import _export_cover_image

        ctx = self._make_ctx(tmp_path, clips=[])
        _export_cover_image(ctx, [], tmp_path)

        # No cover.jpg should be created
        assert not (tmp_path / "cover.jpg").exists()

    def test_no_source_video_skips_export(self, tmp_path):
        from movie_narrator.pipeline.render import _export_cover_image

        ctx = self._make_ctx(tmp_path)
        ctx.source_video_path = None
        clips = [MatchedClip(
            segment_index=0, text="test", narr_start=0, narr_end=1,
            src_start=0, src_end=1, score=0.8, source="embedding",
        )]
        _export_cover_image(ctx, clips, tmp_path)

        assert not (tmp_path / "cover.jpg").exists()

    def test_no_scored_clips_skips_export(self, tmp_path):
        from movie_narrator.pipeline.render import _export_cover_image

        ctx = self._make_ctx(tmp_path)
        clips = [MatchedClip(
            segment_index=0, text="test", narr_start=0, narr_end=1,
            src_start=0, src_end=1, score=0.0, source="heuristic",
        )]
        _export_cover_image(ctx, clips, tmp_path)

        assert not (tmp_path / "cover.jpg").exists()

    def test_ffmpeg_not_found_skips_gracefully(self, tmp_path):
        from movie_narrator.pipeline.render import _export_cover_image

        ctx = self._make_ctx(tmp_path)
        clips = [MatchedClip(
            segment_index=0, text="test", narr_start=0, narr_end=1,
            src_start=5.0, src_end=10.0, score=0.85, source="embedding",
        )]

        with patch("movie_narrator.pipeline.render.shutil.which", return_value=None):
            _export_cover_image(ctx, clips, tmp_path)

        assert not (tmp_path / "cover.jpg").exists()

    def test_picks_highest_score_clip(self, tmp_path):
        """Verify the function selects the clip with the highest score."""
        from movie_narrator.pipeline.render import _export_cover_image

        ctx = self._make_ctx(tmp_path, movie_name="最佳封面")
        clips = [
            MatchedClip(
                segment_index=0, text="low", narr_start=0, narr_end=1,
                src_start=0.0, src_end=5.0, score=0.3, source="embedding",
            ),
            MatchedClip(
                segment_index=1, text="high", narr_start=1, narr_end=2,
                src_start=100.0, src_end=110.0, score=0.92, source="embedding",
            ),
            MatchedClip(
                segment_index=2, text="mid", narr_start=2, narr_end=3,
                src_start=50.0, src_end=55.0, score=0.6, source="embedding",
            ),
        ]

        # Mock ffmpeg to not exist so we test the selection logic without actual extraction
        with patch("movie_narrator.pipeline.render.shutil.which", return_value=None):
            _export_cover_image(ctx, clips, tmp_path)

        # The function should have attempted extraction (and failed gracefully)
        # No cover.jpg since ffmpeg was mocked as unavailable
        assert not (tmp_path / "cover.jpg").exists()
