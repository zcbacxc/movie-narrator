"""Tests for Q-P7 reference video imitation.

Covers metrics dataclass, metrics-to-params mapping,
preset classification, report formatting, and the analyze
function with mocked dependencies.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from movie_narrator.imitate import (
    ReferenceMetrics,
    analyze_reference,
    metrics_to_params,
    metrics_to_preset_name,
    format_analysis_report,
    _get_video_duration,
    _count_scenes,
    _transcribe_reference,
)


# ── Fixtures ──────────────────────────────────────────────


def _fast_metrics() -> ReferenceMetrics:
    """Metrics resembling a douyin-fast style video."""
    return ReferenceMetrics(
        duration_sec=58.0,
        sentence_count=18,
        scene_count=25,
        sentences_per_minute=18.6,
        cuts_per_minute=25.9,
        avg_segment_duration=3.2,
        avg_scene_duration=2.3,
        transcript_segments=[
            {"start": 0.0, "end": 3.2, "text": "opening hook"},
            {"start": 3.2, "end": 6.4, "text": "second sentence"},
        ],
    )


def _slow_metrics() -> ReferenceMetrics:
    """Metrics resembling a bilibili-long style video."""
    return ReferenceMetrics(
        duration_sec=180.0,
        sentence_count=12,
        scene_count=8,
        sentences_per_minute=4.0,
        cuts_per_minute=2.7,
        avg_segment_duration=15.0,
        avg_scene_duration=22.5,
    )


def _medium_metrics() -> ReferenceMetrics:
    """Metrics resembling a mainstream-dry style video."""
    return ReferenceMetrics(
        duration_sec=90.0,
        sentence_count=15,
        scene_count=12,
        sentences_per_minute=10.0,
        cuts_per_minute=8.0,
        avg_segment_duration=6.0,
        avg_scene_duration=7.5,
    )


# ── ReferenceMetrics ──────────────────────────────────────


class TestReferenceMetrics:
    def test_summary_contains_key_metrics(self):
        """summary() includes duration, sentences, scenes."""
        m = _fast_metrics()
        s = m.summary()
        assert "duration" in s
        assert "sentences" in s
        assert "scenes" in s

    def test_default_values_are_zero(self):
        """Default ReferenceMetrics has all zeros."""
        m = ReferenceMetrics()
        assert m.duration_sec == 0.0
        assert m.sentence_count == 0
        assert m.scene_count == 0

    def test_transcript_segments_defaults_empty(self):
        """transcript_segments defaults to empty list."""
        m = ReferenceMetrics()
        assert m.transcript_segments == []


# ── metrics_to_params ─────────────────────────────────────


class TestMetricsToParams:
    def test_empty_metrics_returns_empty(self):
        """Empty metrics produce no params."""
        params = metrics_to_params(ReferenceMetrics())
        assert params == {}

    def test_fast_style_has_high_speed_clamp(self):
        """High cut density (>15/min) gives max speed clamp 1.30."""
        params = metrics_to_params(_fast_metrics())
        assert params["match_speed_clamp_max"] == 1.30

    def test_medium_style_has_medium_speed_clamp(self):
        """Medium cut density (8-15/min) gives speed clamp 1.20."""
        params = metrics_to_params(_medium_metrics())
        assert params["match_speed_clamp_max"] == 1.20

    def test_slow_style_has_low_speed_clamp(self):
        """Low cut density (<8/min) gives speed clamp 1.10."""
        params = metrics_to_params(_slow_metrics())
        assert params["match_speed_clamp_max"] == 1.10

    def test_sentence_count_maps_to_target_sentences(self):
        """Sentence count produces prompt_target_sentences."""
        params = metrics_to_params(_fast_metrics())
        assert "prompt_target_sentences" in params
        assert params["prompt_target_sentences"] >= 8
        assert params["prompt_target_sentences"] <= 30

    def test_target_sentences_clamped(self):
        """target_sentences is clamped to [8, 30]."""
        # Very high density
        m = ReferenceMetrics(
            duration_sec=30.0,
            sentence_count=50,
            sentences_per_minute=100.0,
            avg_segment_duration=0.6,
        )
        params = metrics_to_params(m)
        assert params["prompt_target_sentences"] <= 30

    def test_scene_ratio_affects_topk(self):
        """High scene/sentence ratio gives wider topk."""
        m = ReferenceMetrics(
            duration_sec=60.0,
            sentence_count=10,
            scene_count=50,  # ratio = 5.0 > 3.0
            sentences_per_minute=10.0,
            cuts_per_minute=50.0,
            avg_segment_duration=6.0,
            avg_scene_duration=1.2,
        )
        params = metrics_to_params(m)
        assert params["match_topk"] == 8

    def test_bgm_duck_deep_for_dense(self):
        """High sentence density gives deep BGM ducking."""
        params = metrics_to_params(_fast_metrics())
        assert params["bgm_duck_db"] == -10.0

    def test_bgm_duck_shallow_for_sparse(self):
        """Low sentence density gives shallow BGM ducking."""
        params = metrics_to_params(_slow_metrics())
        assert params["bgm_duck_db"] == -6.0

    def test_hook_seconds_from_first_segment(self):
        """Hook duration is derived from first transcript segment."""
        params = metrics_to_params(_fast_metrics())
        # First segment is 3.2s → hook = 3
        assert params["prompt_hook_seconds"] == 3

    def test_max_chars_per_sentence(self):
        """Max chars is based on avg segment duration."""
        params = metrics_to_params(_medium_metrics())
        # avg_segment_duration=6.0 → 6 * 3.8 = 22.8 → 22
        assert params["prompt_max_chars_per_sentence"] == 22


# ── metrics_to_preset_name ────────────────────────────────


class TestMetricsToPresetName:
    def test_fast_metrics_maps_to_douyin(self):
        """High sentence + cut density maps to douyin-fast."""
        assert metrics_to_preset_name(_fast_metrics()) == "douyin-fast"

    def test_slow_metrics_maps_to_bilibili(self):
        """Low sentence + cut density maps to bilibili-long."""
        assert metrics_to_preset_name(_slow_metrics()) == "bilibili-long"

    def test_medium_metrics_maps_to_mainstream(self):
        """Medium density maps to mainstream-dry."""
        assert metrics_to_preset_name(_medium_metrics()) == "mainstream-dry"

    def test_empty_metrics_returns_a_preset(self):
        """Empty metrics still returns a valid preset name."""
        name = metrics_to_preset_name(ReferenceMetrics())
        assert name in ("douyin-fast", "mainstream-dry", "bilibili-long")


# ── format_analysis_report ────────────────────────────────


class TestFormatAnalysisReport:
    def test_report_has_header(self):
        """Report contains the analysis header."""
        report = format_analysis_report(_fast_metrics())
        assert "Reference Video Analysis" in report

    def test_report_shows_duration(self):
        """Report shows the video duration."""
        report = format_analysis_report(_fast_metrics())
        assert "58.0" in report

    def test_report_shows_sentence_count(self):
        """Report shows the sentence count."""
        report = format_analysis_report(_fast_metrics())
        assert "18" in report

    def test_report_shows_preset(self):
        """Report shows the closest preset."""
        report = format_analysis_report(_fast_metrics())
        assert "douyin-fast" in report

    def test_report_shows_params(self):
        """Report shows generated parameters."""
        report = format_analysis_report(_fast_metrics())
        assert "Generated parameters" in report

    def test_empty_metrics_report(self):
        """Report handles empty metrics gracefully."""
        report = format_analysis_report(ReferenceMetrics())
        assert "Reference Video Analysis" in report


# ── _get_video_duration ───────────────────────────────────


class TestGetVideoDuration:
    def test_returns_float(self, tmp_path):
        """Duration is returned as a float."""
        # Create a dummy file (ffprobe will fail, fallback to 60.0)
        fake_video = tmp_path / "test.mp4"
        fake_video.write_bytes(b"fake")
        duration = _get_video_duration(str(fake_video))
        assert isinstance(duration, float)
        assert duration > 0


# ── _count_scenes ─────────────────────────────────────────


class TestCountScenes:
    def test_returns_tuple(self):
        """Returns (count, scene_list) tuple."""
        # Will fail without scenedetect, returning (0, [])
        count, scenes = _count_scenes("nonexistent.mp4")
        assert isinstance(count, int)
        assert isinstance(scenes, list)


# ── _transcribe_reference ─────────────────────────────────


class TestTranscribeReference:
    def test_returns_tuple(self):
        """Returns (count, segments) tuple."""
        count, segments = _transcribe_reference("nonexistent.mp4")
        assert isinstance(count, int)
        assert isinstance(segments, list)


# ── analyze_reference (integration with mocks) ────────────


class TestAnalyzeReference:
    def test_raises_on_missing_file(self):
        """Non-existent video raises FileNotFoundError."""
        with pytest.raises(FileNotFoundError):
            analyze_reference("nonexistent_video.mp4")

    @patch("movie_narrator.imitate._transcribe_reference")
    @patch("movie_narrator.imitate._count_scenes")
    @patch("movie_narrator.imitate._get_video_duration")
    def test_analyze_fast_style(self, mock_dur, mock_scenes, mock_trans, tmp_path):
        """Analyzing a fast-style reference produces correct metrics."""
        # Create dummy file
        video = tmp_path / "ref.mp4"
        video.write_bytes(b"fake video")

        mock_dur.return_value = 58.0
        mock_scenes.return_value = (
            25,
            [{"start": 0.0, "end": 2.3}, {"start": 2.3, "end": 4.6}],
        )
        mock_trans.return_value = (
            18,
            [{"start": 0.0, "end": 3.2, "text": "hook"}],
        )

        metrics = analyze_reference(str(video))

        assert metrics.duration_sec == 58.0
        assert metrics.sentence_count == 18
        assert metrics.scene_count == 25
        assert metrics.sentences_per_minute > 15  # fast
        assert metrics.cuts_per_minute > 15  # fast

    @patch("movie_narrator.imitate._transcribe_reference")
    @patch("movie_narrator.imitate._count_scenes")
    @patch("movie_narrator.imitate._get_video_duration")
    def test_analyze_saves_json(self, mock_dur, mock_scenes, mock_trans, tmp_path):
        """Analysis saves raw data to reference_analysis.json."""
        video = tmp_path / "ref.mp4"
        video.write_bytes(b"fake video")
        out_dir = tmp_path / "output"

        mock_dur.return_value = 90.0
        mock_scenes.return_value = (10, [{"start": 0.0, "end": 9.0}])
        mock_trans.return_value = (12, [{"start": 0.0, "end": 5.0, "text": "test"}])

        metrics = analyze_reference(str(video), output_dir=out_dir)

        json_path = out_dir / "reference_analysis.json"
        assert json_path.exists()
        data = json.loads(json_path.read_text(encoding="utf-8"))
        assert data["duration_sec"] == 90.0
        assert data["sentence_count"] == 12

    @patch("movie_narrator.imitate._transcribe_reference")
    @patch("movie_narrator.imitate._count_scenes")
    @patch("movie_narrator.imitate._get_video_duration")
    def test_zero_sentences_handled(self, mock_dur, mock_scenes, mock_trans, tmp_path):
        """Zero sentences doesn't crash — avg_segment_duration stays 0."""
        video = tmp_path / "ref.mp4"
        video.write_bytes(b"fake video")

        mock_dur.return_value = 60.0
        mock_scenes.return_value = (5, [])
        mock_trans.return_value = (0, [])

        metrics = analyze_reference(str(video))

        assert metrics.sentence_count == 0
        assert metrics.avg_segment_duration == 0.0
        assert metrics.sentences_per_minute == 0.0

    @patch("movie_narrator.imitate._transcribe_reference")
    @patch("movie_narrator.imitate._count_scenes")
    @patch("movie_narrator.imitate._get_video_duration")
    def test_zero_scenes_handled(self, mock_dur, mock_scenes, mock_trans, tmp_path):
        """Zero scenes doesn't crash — avg_scene_duration falls back to duration."""
        video = tmp_path / "ref.mp4"
        video.write_bytes(b"fake video")

        mock_dur.return_value = 60.0
        mock_scenes.return_value = (0, [])
        mock_trans.return_value = (10, [{"start": 0, "end": 6, "text": "t"}])

        metrics = analyze_reference(str(video))

        assert metrics.scene_count == 0
        assert metrics.avg_scene_duration == 60.0  # fallback to duration
