"""Tests for scene filtering — intro skip, dark frame drop, highlight window.

All tests use mocks for ffmpeg/PIL so they run in CI without media extras.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Scene, Services, SilentConsole
from movie_narrator.pipeline.scene_filter import (
    apply_source_window,
    filter_dark_scenes,
    filter_intro_scenes,
)


# ── Fixtures ───────────────────────────────────────────────


def _scenes() -> list[Scene]:
    """8 scenes spanning 0–80s (10s each)."""
    return [Scene(index=i, start=i * 10.0, end=(i + 1) * 10.0) for i in range(8)]


# ── 9.1 Intro skip ─────────────────────────────────────────


class TestFilterIntroScenes:
    def test_no_skip_returns_unchanged(self):
        """skip_intro_sec=0 → no filtering."""
        scenes = _scenes()
        result, dropped = filter_intro_scenes(scenes, 0.0)
        assert result is scenes
        assert dropped == 0

    def test_negative_skip_returns_unchanged(self):
        scenes = _scenes()
        result, dropped = filter_intro_scenes(scenes, -5.0)
        assert result is scenes
        assert dropped == 0

    def test_skip_drops_early_scenes(self):
        """skip_intro_sec=30 drops scenes ending at 10, 20, 30."""
        scenes = _scenes()
        result, dropped = filter_intro_scenes(scenes, 30.0)
        assert dropped == 3  # scenes 0,1,2 end at 10,20,30
        assert len(result) == 5
        # Re-indexed from 0
        assert result[0].index == 0
        assert result[0].start == 30.0
        assert result[-1].end == 80.0

    def test_skip_all_scenes_returns_original(self):
        """If skip would remove all scenes, return original list."""
        scenes = _scenes()
        result, dropped = filter_intro_scenes(scenes, 999.0)
        assert result is scenes
        assert dropped == 0

    def test_empty_scenes_returns_empty(self):
        result, dropped = filter_intro_scenes([], 30.0)
        assert result == []
        assert dropped == 0

    def test_re_index_after_drop(self):
        """Scene indices are sequential after filtering."""
        scenes = _scenes()
        result, _ = filter_intro_scenes(scenes, 25.0)
        for i, s in enumerate(result):
            assert s.index == i


# ── 9.2 Dark frame drop ────────────────────────────────────


class TestFilterDarkScenes:
    def test_no_threshold_returns_unchanged(self):
        """luma_threshold=0 → no filtering."""
        scenes = _scenes()
        result, dropped = filter_dark_scenes(scenes, "video.mp4", 0.0)
        assert result is scenes
        assert dropped == 0

    def test_no_video_path_returns_unchanged(self):
        scenes = _scenes()
        result, dropped = filter_dark_scenes(scenes, None, 20.0)
        assert result is scenes
        assert dropped == 0

    def test_no_ffmpeg_returns_unchanged(self):
        """When ffmpeg is not available, skip filtering."""
        scenes = _scenes()
        with patch("movie_narrator.pipeline.scene_filter.shutil.which", return_value=None):
            result, dropped = filter_dark_scenes(scenes, "video.mp4", 20.0)
        assert result is scenes
        assert dropped == 0

    def test_no_pil_returns_unchanged(self):
        """When PIL is not available, skip filtering."""
        scenes = _scenes()
        with (
            patch(
                "movie_narrator.pipeline.scene_filter.shutil.which", return_value="/usr/bin/ffmpeg"
            ),
            patch.dict(sys.modules, {"PIL": None, "PIL.Image": None}),
        ):
            result, dropped = filter_dark_scenes(scenes, "video.mp4", 20.0)
        assert result is scenes
        assert dropped == 0

    def test_drops_dark_scenes(self, tmp_path):
        """Scenes with luma below threshold are dropped."""
        scenes = _scenes()
        # Create a dummy video file so _video_hash can stat it
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fake_video")

        # Mock ffmpeg extraction to create fake frame files
        # Mock PIL to return low luma for scenes 0 and 3
        fake_frames = {}

        def fake_extract(video_path, timestamp, output_path):
            output_path.write_bytes(b"fake_jpeg")
            fake_frames[timestamp] = output_path
            return True

        class FakeImage:
            def __init__(self, luma_val):
                self._luma = luma_val
                self.width = 64

            def convert(self, mode):
                return self

            def resize(self, size):
                return self

            def getdata(self):
                return [self._luma] * (64 * 64)

        luma_map = {
            5.0: 5.0,  # scene 0 mid → dark
            15.0: 50.0,  # scene 1 mid → bright
            25.0: 80.0,  # scene 2 mid → bright
            35.0: 3.0,  # scene 3 mid → dark
            45.0: 60.0,  # scene 4 mid → bright
            55.0: 70.0,  # scene 5 mid → bright
            65.0: 40.0,  # scene 6 mid → bright
            75.0: 90.0,  # scene 7 mid → bright
        }

        def fake_open(path):
            # Find the matching timestamp
            for ts, fp in fake_frames.items():
                if str(fp) == str(path):
                    return FakeImage(luma_map.get(ts, 50.0))
            return FakeImage(50.0)

        fake_pil = MagicMock()
        fake_pil.Image.open = fake_open
        fake_pil.Image.__name__ = "Image"

        with (
            patch(
                "movie_narrator.pipeline.scene_filter.shutil.which", return_value="/usr/bin/ffmpeg"
            ),
            patch(
                "movie_narrator.pipeline.scene_filter._extract_mid_frame", side_effect=fake_extract
            ),
            patch.dict(sys.modules, {"PIL": fake_pil, "PIL.Image": fake_pil.Image}),
        ):
            result, dropped = filter_dark_scenes(
                scenes,
                str(video_path),
                20.0,
                cache_dir=tmp_path / "cache",
            )

        assert dropped == 2  # scenes 0 and 3
        assert len(result) == 6
        for i, s in enumerate(result):
            assert s.index == i

    def test_extraction_failure_keeps_scene(self, tmp_path):
        """When frame extraction fails, the scene is kept (not dropped)."""
        scenes = _scenes()
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fake_video")

        with (
            patch(
                "movie_narrator.pipeline.scene_filter.shutil.which", return_value="/usr/bin/ffmpeg"
            ),
            patch("movie_narrator.pipeline.scene_filter._extract_mid_frame", return_value=False),
        ):
            result, dropped = filter_dark_scenes(
                scenes,
                str(video_path),
                20.0,
                cache_dir=tmp_path / "cache",
            )

        assert dropped == 0
        assert len(result) == 8  # all kept

    def test_all_dark_returns_original(self, tmp_path):
        """If all scenes would be dropped, return original list."""
        scenes = _scenes()
        video_path = tmp_path / "video.mp4"
        video_path.write_bytes(b"fake_video")

        with (
            patch(
                "movie_narrator.pipeline.scene_filter.shutil.which", return_value="/usr/bin/ffmpeg"
            ),
            patch("movie_narrator.pipeline.scene_filter._extract_mid_frame", return_value=True),
            patch("movie_narrator.pipeline.scene_filter._compute_mean_luma", return_value=5.0),
        ):
            result, dropped = filter_dark_scenes(
                scenes,
                str(video_path),
                20.0,
                cache_dir=tmp_path / "cache",
            )

        # All dark → return original (don't nuke everything)
        assert result is scenes
        assert dropped == 0


# ── 9.3 Highlight window ───────────────────────────────────


class TestApplySourceWindow:
    def test_no_window_returns_unchanged(self):
        scenes = _scenes()
        result, dropped = apply_source_window(scenes, None)
        assert result is scenes
        assert dropped == 0

    def test_full_span_window_returns_unchanged(self):
        """[0.0, 1.0] is a no-op."""
        scenes = _scenes()
        result, dropped = apply_source_window(scenes, [0.0, 1.0])
        assert result is scenes
        assert dropped == 0

    def test_window_drops_outside_scenes(self):
        """[0.15, 0.85] drops scenes entirely outside the window."""
        scenes = _scenes()
        # Span is 0-80s; window is 12-68s
        # Scene 0 (0-10) entirely before 12 → drop
        # Scene 1 (10-20) partially overlaps → clipped to 12-20
        # Scenes 2-6 fully inside
        # Scene 7 (70-80) entirely after 68 → drop
        result, dropped = apply_source_window(scenes, [0.15, 0.85])
        assert dropped == 2
        assert len(result) == 6
        # First remaining scene is clipped scene 1
        assert result[0].start == 12.0
        assert result[0].end == 20.0
        # Last remaining scene is scene 6
        assert result[-1].start == 60.0
        assert result[-1].end == 68.0  # clipped to window end

    def test_window_clips_partial_overlap(self):
        """Scenes partially overlapping the window are clipped."""
        scenes = _scenes()
        # Window 15-65 (span 0-80)
        result, dropped = apply_source_window(scenes, [0.1875, 0.8125])
        # Scene 0 (0-10) entirely before → dropped
        # Scene 7 (70-80) entirely after → dropped
        # Scene 1 (10-20) partially overlaps → clipped to 15-20
        # Scene 6 (60-70) partially overlaps → clipped to 60-65
        assert dropped == 2
        assert len(result) == 6
        # First remaining scene clipped
        assert result[0].start == 15.0
        assert result[0].end == 20.0
        # Last remaining scene clipped
        assert result[-1].start == 60.0
        assert result[-1].end == 65.0

    def test_empty_scenes_returns_empty(self):
        result, dropped = apply_source_window([], [0.2, 0.8])
        assert result == []
        assert dropped == 0

    def test_invalid_window_returns_unchanged(self):
        scenes = _scenes()
        result, dropped = apply_source_window(scenes, [0.2])  # only 1 element
        assert result is scenes
        assert dropped == 0

    def test_re_index_after_drop(self):
        scenes = _scenes()
        result, _ = apply_source_window(scenes, [0.5, 1.0])
        for i, s in enumerate(result):
            assert s.index == i

    def test_all_outside_returns_original(self):
        """If window would drop all scenes, return original."""
        scenes = [Scene(index=0, start=0.0, end=10.0)]
        result, dropped = apply_source_window(scenes, [0.9, 1.0])
        # Window is 9-10; scene 0-10 partially overlaps → clipped, not dropped
        # So this should return 1 clipped scene
        assert len(result) == 1
        assert result[0].start == 9.0
        assert result[0].end == 10.0


# ── Integration: match.py scene filter params ───────────────────────


class TestMatchWP6Integration:
    """Verify that scene filter params flow through match_clips correctly."""

    def _make_ctx(self, tmp_path, scenes, metadata=None):
        from movie_narrator.models import Context, TimedSegment

        ctx = Context(
            movie_name="test",
            output_dir=str(tmp_path),
            source_video_path=str(tmp_path / "video.mp4"),
            services=Services(console=SilentConsole()),
        )
        ctx.scenes = scenes
        ctx.status.scene = "success"  # match_clips requires scene status != "disabled"
        ctx.timed_segments = [
            TimedSegment(text=f"segment {i}", start=float(i * 5), end=float(i * 5 + 4))
            for i in range(4)
        ]
        if metadata:
            ctx.metadata.update(metadata)
        return ctx

    def test_skip_intro_applied_in_match(self, tmp_path):
        """match_skip_intro_sec filters scenes before matching."""
        scenes = [Scene(index=i, start=i * 10.0, end=(i + 1) * 10.0) for i in range(8)]
        ctx = self._make_ctx(tmp_path, scenes, {"match_skip_intro_sec": 30.0})

        # Mock embedding to unavailable so we test heuristic path
        with patch("movie_narrator.pipeline.match.probe", return_value=(False, "no st")):
            from movie_narrator.pipeline.match import match_clips

            match_clips(ctx)

        assert ctx.status.match == "success"
        assert ctx.metadata.get("wp6_intro_dropped") == 3
        # Matched clips should reference scenes starting from 30s+
        for mc in ctx.matched_clips:
            assert mc.src_start >= 30.0

    def test_source_window_applied_in_match(self, tmp_path):
        """match_source_window restricts scenes to the window."""
        scenes = [Scene(index=i, start=i * 10.0, end=(i + 1) * 10.0) for i in range(8)]
        ctx = self._make_ctx(tmp_path, scenes, {"match_source_window": [0.15, 0.85]})

        with patch("movie_narrator.pipeline.match.probe", return_value=(False, "no st")):
            from movie_narrator.pipeline.match import match_clips

            match_clips(ctx)

        assert ctx.status.match == "success"
        assert ctx.metadata.get("wp6_window_dropped") == 2
        # All matched clips should be within 12-68s range (window of 0-80 span)
        for mc in ctx.matched_clips:
            assert mc.src_start >= 10.0  # first kept scene starts at 10 (clipped to 12)
            assert mc.src_end <= 70.0  # last kept scene ends at 70 (clipped to 68)

    def test_no_wp6_params_no_change(self, tmp_path):
        """Without scene filter params, behavior is unchanged."""
        scenes = [Scene(index=i, start=i * 10.0, end=(i + 1) * 10.0) for i in range(8)]
        ctx = self._make_ctx(tmp_path, scenes)

        with patch("movie_narrator.pipeline.match.probe", return_value=(False, "no st")):
            from movie_narrator.pipeline.match import match_clips

            match_clips(ctx)

        assert ctx.status.match == "success"
        assert "wp6_intro_dropped" not in ctx.metadata
        assert "wp6_dark_dropped" not in ctx.metadata
        assert "wp6_window_dropped" not in ctx.metadata
