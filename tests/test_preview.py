"""Tests for v0.7.2 preview mode utilities (movie_narrator.utils.preview).

Verifies:
- truncate_segments_for_preview: filtering + boundary clamping
- truncate_clips_for_preview: filtering + narr_end clamping
- get_preview_duration: min(requested, total) clamped to [3, 60]
- should_skip_step_for_preview: only soft steps skipped, off by default
"""

from __future__ import annotations

import pytest

from movie_narrator.models import MatchedClip, TimedSegment
from movie_narrator.utils.preview import (
    get_preview_duration,
    should_skip_step_for_preview,
    truncate_clips_for_preview,
    truncate_segments_for_preview,
)


# ── truncate_segments_for_preview ─────────────────────────


class TestTruncateSegmentsForPreview:
    """truncate_segments_for_preview filters and clamps timed segments."""

    def test_segments_entirely_before_window_kept_unchanged(self):
        """Segments fully inside the window are reused by reference."""
        segs = [
            TimedSegment(text="A", start=0.0, end=3.0),
            TimedSegment(text="B", start=3.0, end=7.0),
        ]
        result = truncate_segments_for_preview(segs, 10.0)
        assert len(result) == 2
        assert result[0].end == 3.0
        assert result[1].end == 7.0

    def test_spanning_segment_clamped_to_boundary(self):
        """A segment that crosses the boundary is clamped to end at preview_sec."""
        segs = [
            TimedSegment(text="A", start=0.0, end=5.0),
            TimedSegment(text="B", start=5.0, end=15.0),
        ]
        result = truncate_segments_for_preview(segs, 10.0)
        assert len(result) == 2
        assert result[0].end == 5.0
        assert result[1].end == 10.0
        assert result[1].start == 5.0

    def test_segment_starting_at_boundary_dropped(self):
        """A segment starting exactly at preview_sec is dropped."""
        segs = [
            TimedSegment(text="A", start=0.0, end=10.0),
            TimedSegment(text="B", start=10.0, end=20.0),
        ]
        result = truncate_segments_for_preview(segs, 10.0)
        assert len(result) == 1
        assert result[0].text == "A"

    def test_segment_starting_after_boundary_dropped(self):
        """Segments starting after preview_sec are dropped."""
        segs = [
            TimedSegment(text="late", start=12.0, end=20.0),
        ]
        result = truncate_segments_for_preview(segs, 10.0)
        assert result == []

    def test_does_not_mutate_input(self):
        """The original segment list and objects are not mutated."""
        seg = TimedSegment(text="B", start=5.0, end=15.0)
        segs = [seg]
        result = truncate_segments_for_preview(segs, 10.0)
        assert result[0].end == 10.0
        # Original untouched
        assert seg.end == 15.0
        assert segs[0].end == 15.0

    def test_empty_list_returns_empty(self):
        assert truncate_segments_for_preview([], 10.0) == []

    def test_exact_boundary_segment_kept(self):
        """A segment ending exactly at preview_sec is kept unchanged."""
        seg = TimedSegment(text="A", start=0.0, end=10.0)
        result = truncate_segments_for_preview([seg], 10.0)
        assert len(result) == 1
        assert result[0].end == 10.0


# ── truncate_clips_for_preview ────────────────────────────


class TestTruncateClipsForPreview:
    """truncate_clips_for_preview filters and clamps matched clips."""

    def _make_clip(self, narr_start, narr_end, idx=0):
        return MatchedClip(
            segment_index=idx,
            text="x",
            narr_start=narr_start,
            narr_end=narr_end,
            src_start=0.0,
            src_end=narr_end - narr_start,
            score=0.5,
        )

    def test_clips_inside_window_kept(self):
        clips = [self._make_clip(0.0, 4.0, 0), self._make_clip(4.0, 8.0, 1)]
        result = truncate_clips_for_preview(clips, 10.0)
        assert len(result) == 2
        assert result[0].narr_end == 4.0
        assert result[1].narr_end == 8.0

    def test_spanning_clip_clamped(self):
        clips = [self._make_clip(6.0, 18.0, 0)]
        result = truncate_clips_for_preview(clips, 10.0)
        assert len(result) == 1
        assert result[0].narr_start == 6.0
        assert result[0].narr_end == 10.0

    def test_clip_at_boundary_dropped(self):
        clips = [self._make_clip(0.0, 5.0, 0), self._make_clip(10.0, 20.0, 1)]
        result = truncate_clips_for_preview(clips, 10.0)
        assert len(result) == 1
        assert result[0].segment_index == 0

    def test_clip_after_boundary_dropped(self):
        clips = [self._make_clip(15.0, 25.0, 0)]
        result = truncate_clips_for_preview(clips, 10.0)
        assert result == []

    def test_does_not_mutate_input(self):
        clip = self._make_clip(6.0, 18.0, 0)
        result = truncate_clips_for_preview([clip], 10.0)
        assert result[0].narr_end == 10.0
        assert clip.narr_end == 18.0

    def test_empty_list_returns_empty(self):
        assert truncate_clips_for_preview([], 10.0) == []


# ── get_preview_duration ──────────────────────────────────


class TestGetPreviewDuration:
    """get_preview_duration clamps min(requested, total) to [3, 60]."""

    def test_normal_request_within_range(self):
        assert get_preview_duration(10.0, 120.0) == 10.0

    def test_requested_exceeds_total(self):
        """When total is shorter than requested, total wins."""
        assert get_preview_duration(10.0, 5.0) == 5.0

    def test_clamp_lower_bound(self):
        """A request below 3s is clamped up to 3s."""
        assert get_preview_duration(2.0, 120.0) == 3.0

    def test_clamp_upper_bound(self):
        """A request above 60s is clamped down to 60s."""
        assert get_preview_duration(100.0, 120.0) == 60.0

    def test_clamp_upper_bound_with_short_total(self):
        """When total < 60 but requested > total, total wins (no upper clamp)."""
        assert get_preview_duration(100.0, 50.0) == 50.0

    def test_exact_lower_bound(self):
        assert get_preview_duration(3.0, 120.0) == 3.0

    def test_exact_upper_bound(self):
        assert get_preview_duration(60.0, 120.0) == 60.0

    def test_default_ten_seconds(self):
        """The conventional 10s preview is returned unchanged for normal sources."""
        assert get_preview_duration(10.0, 60.0) == 10.0

    def test_very_large_request(self):
        assert get_preview_duration(3600.0, 300.0) == 60.0

    def test_returns_float(self):
        result = get_preview_duration(10.0, 120.0)
        assert isinstance(result, float)


# ── should_skip_step_for_preview ──────────────────────────


class TestShouldSkipStepForPreview:
    """should_skip_step_for_preview only skips soft steps when preview is on."""

    @pytest.mark.parametrize(
        "step",
        [
            "research_plot",
            "translate_subtitles",
            "run_qa_gate",
            "export_clips",
        ],
    )
    def test_soft_steps_skipped_in_preview(self, step):
        assert should_skip_step_for_preview(step, True) is True

    @pytest.mark.parametrize(
        "step",
        [
            "resolve_video",
            "prepare_assets",
            "generate_script",
            "export_script_md",
            "generate_voice",
            "align_audio",
            "detect_scenes",
            "match_clips",
            "mix_bgm",
            "generate_subtitle",
            "render_video",
            "validate_deliverable",
        ],
    )
    def test_hard_steps_never_skipped(self, step):
        """Hard steps run even in preview mode."""
        assert should_skip_step_for_preview(step, True) is False

    @pytest.mark.parametrize(
        "step",
        [
            "research_plot",
            "translate_subtitles",
            "run_qa_gate",
            "export_clips",
            "generate_script",
            "render_video",
        ],
    )
    def test_nothing_skipped_when_preview_off(self, step):
        """Preview off = nothing skipped (backward compatible)."""
        assert should_skip_step_for_preview(step, False) is False

    def test_unknown_step_not_skipped(self):
        assert should_skip_step_for_preview("unknown_step", True) is False

    def test_empty_string_not_skipped(self):
        assert should_skip_step_for_preview("", True) is False
