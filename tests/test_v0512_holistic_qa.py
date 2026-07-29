"""Tests for v0.5.12 Holistic QA & Quality Dashboard features.

Covers:
- Video encoding QA: probing, codec/resolution/bitrate/fps checks, report structure
- Quality dashboard: per-dimension extraction, weighted scoring, regression comparison
- QA report: dict generation, text formatting, file export
- QA gate: intermediate product validation, CI skip, strict mode
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from movie_narrator.models import Context, Services, StepResult, TimedSegment
from movie_narrator.utils.console import SilentConsole
from movie_narrator.utils.video_qa import (
    VideoEncodingMetrics,
    VideoQAReport,
    probe_video_encoding,
    check_encoding_quality,
    evaluate_video_quality,
    _ACCEPTABLE_CODECS,
    _ACCEPTABLE_AUDIO_CODECS,
    _MIN_WIDTH,
    _MIN_HEIGHT,
    _MIN_BITRATE_KBPS,
    _MIN_FPS,
    _MAX_FPS,
)
from movie_narrator.utils.quality_dashboard import (
    QualityDimension,
    RegressionDelta,
    QualityDashboard,
    _DEFAULT_WEIGHTS,
    _GOOD_THRESHOLD,
    _ACCEPTABLE_THRESHOLD,
    _POOR_THRESHOLD,
    _extract_script_score,
    _extract_audio_score,
    _extract_alignment_score,
    _extract_match_score,
    _extract_subtitle_score,
    _extract_translation_score,
    _extract_deliverable_score,
    _extract_video_encoding_score,
    collect_quality_dimensions,
    compute_overall_score,
    build_quality_dashboard,
    _compare_with_baseline,
)
from movie_narrator.utils.qa_report import (
    generate_qa_report_dict,
    format_qa_report_text,
    export_qa_report,
    _summarize_audio,
    _summarize_subtitle,
)
from movie_narrator.pipeline.qa_gate import (
    run_qa_gate,
    _MAX_SCRIPT_ISSUES,
    _MAX_CLIPPING_RATIO,
    _MAX_CPS_MULTIPLIER,
)


# ── Fixtures ────────────────────────────────────────────────


def _make_ctx(tmp_path, **overrides) -> Context:
    """Create a minimal Context with SilentConsole for QA testing."""
    ctx = Context(
        movie_name="TestMovie",
        output_dir=str(tmp_path),
        services=Services(console=SilentConsole()),
    )
    ctx.step_state.result = StepResult.SUCCESS
    ctx.step_state.message = ""
    for k, v in overrides.items():
        ctx.metadata[k] = v
    return ctx


def _good_video_metrics() -> VideoEncodingMetrics:
    """Return metrics that pass all encoding checks."""
    return VideoEncodingMetrics(
        codec="h264",
        profile="High",
        width=1920,
        height=1080,
        fps=30.0,
        bitrate_kbps=5000,
        pixel_format="yuv420p",
        audio_codec="aac",
        audio_bitrate_kbps=128,
        audio_channels=2,
        audio_sample_rate=48000,
    )


# ════════════════════════════════════════════════════════════
#  Video QA Tests
# ════════════════════════════════════════════════════════════


class TestVideoEncodingMetrics:
    """VideoEncodingMetrics dataclass and serialization."""

    def test_defaults(self):
        m = VideoEncodingMetrics()
        assert m.codec == ""
        assert m.width == 0
        assert m.height == 0
        assert m.fps == 0.0
        assert m.bitrate_kbps == 0

    def test_to_dict(self):
        m = _good_video_metrics()
        d = m.to_dict()
        assert d["codec"] == "h264"
        assert d["width"] == 1920
        assert d["height"] == 1080
        assert d["fps"] == 30.0
        assert d["bitrate_kbps"] == 5000
        assert d["audio_codec"] == "aac"
        assert d["audio_channels"] == 2

    def test_fps_rounding(self):
        m = VideoEncodingMetrics(fps=29.97002997)
        d = m.to_dict()
        assert d["fps"] == 29.97


class TestVideoQAReport:
    """VideoQAReport dataclass and serialization."""

    def test_defaults(self):
        r = VideoQAReport()
        assert r.ok is True
        assert r.issues == []
        assert r.recommendations == []

    def test_to_dict(self):
        r = VideoQAReport(
            ok=False,
            metrics=_good_video_metrics(),
            issues=["bad codec"],
            recommendations=["re-encode"],
        )
        d = r.to_dict()
        assert d["ok"] is False
        assert "bad codec" in d["issues"]
        assert "re-encode" in d["recommendations"]
        assert d["metrics"]["codec"] == "h264"


class TestCheckEncodingQuality:
    """check_encoding_quality validation logic."""

    def test_all_pass(self):
        metrics = _good_video_metrics()
        report = check_encoding_quality(metrics)
        assert report.ok is True
        assert report.issues == []
        assert report.recommendations == []

    def test_bad_codec(self):
        metrics = _good_video_metrics()
        metrics.codec = "mpeg2video"
        report = check_encoding_quality(metrics)
        assert report.ok is False
        assert any("codec" in i for i in report.issues)
        assert any("H.264" in r for r in report.recommendations)

    def test_low_resolution(self):
        metrics = _good_video_metrics()
        metrics.width = 640
        metrics.height = 360
        report = check_encoding_quality(metrics)
        assert report.ok is False
        assert any("resolution" in i for i in report.issues)

    def test_low_bitrate(self):
        metrics = _good_video_metrics()
        metrics.bitrate_kbps = 500
        report = check_encoding_quality(metrics)
        assert report.ok is False
        assert any("bitrate" in i for i in report.issues)

    def test_low_fps(self):
        metrics = _good_video_metrics()
        metrics.fps = 15.0
        report = check_encoding_quality(metrics)
        assert report.ok is False
        assert any("frame rate" in i.lower() for i in report.issues)

    def test_high_fps(self):
        metrics = _good_video_metrics()
        metrics.fps = 60.0
        report = check_encoding_quality(metrics)
        assert report.ok is False
        assert any("frame rate" in i.lower() for i in report.issues)

    def test_bad_audio_codec(self):
        metrics = _good_video_metrics()
        metrics.audio_codec = "pcm_s16le"
        report = check_encoding_quality(metrics)
        assert report.ok is False
        assert any("audio codec" in i for i in report.issues)

    def test_bad_pixel_format(self):
        metrics = _good_video_metrics()
        metrics.pixel_format = "yuv422p10le"
        report = check_encoding_quality(metrics)
        assert report.ok is False
        assert any("pixel format" in i for i in report.issues)

    def test_non_standard_aspect_ratio(self):
        metrics = _good_video_metrics()
        metrics.width = 1440
        metrics.height = 1080  # 4:3, not 16:9 or 9:16
        report = check_encoding_quality(metrics)
        assert report.ok is False
        assert any("aspect ratio" in i for i in report.issues)

    def test_portrait_aspect_ratio_ok(self):
        metrics = _good_video_metrics()
        metrics.width = 1080
        metrics.height = 1920  # 9:16 portrait
        report = check_encoding_quality(metrics)
        # 9:16 is acceptable, so no aspect ratio issue
        aspect_issues = [i for i in report.issues if "aspect ratio" in i]
        assert aspect_issues == []

    def test_empty_codec_skipped(self):
        """When codec is empty (probe failed), codec check is skipped."""
        metrics = VideoEncodingMetrics(width=1920, height=1080)
        report = check_encoding_quality(metrics)
        # No codec issue since codec is empty
        codec_issues = [i for i in report.issues if "codec" in i]
        assert codec_issues == []

    def test_zero_resolution_skipped(self):
        """When width/height are 0, resolution check is skipped."""
        metrics = VideoEncodingMetrics(codec="h264")
        report = check_encoding_quality(metrics)
        res_issues = [i for i in report.issues if "resolution" in i]
        assert res_issues == []

    def test_custom_thresholds(self):
        metrics = _good_video_metrics()
        metrics.bitrate_kbps = 3000
        # Default min is 1500, so 3000 passes
        report = check_encoding_quality(metrics)
        assert report.ok is True
        # With custom min of 4000, it fails
        report = check_encoding_quality(metrics, min_bitrate_kbps=4000)
        assert report.ok is False

    def test_multiple_issues(self):
        metrics = VideoEncodingMetrics(
            codec="mpeg2video",
            width=480,
            height=360,
            fps=15.0,
            bitrate_kbps=300,
            pixel_format="yuv422p10le",
            audio_codec="pcm_s16le",
        )
        report = check_encoding_quality(metrics)
        assert report.ok is False
        assert len(report.issues) >= 6

    def test_hevc_codec_accepted(self):
        metrics = _good_video_metrics()
        metrics.codec = "hevc"
        report = check_encoding_quality(metrics)
        codec_issues = [i for i in report.issues if "codec" in i]
        assert codec_issues == []


class TestProbeVideoEncoding:
    """probe_video_encoding with ffprobe mocking."""

    def test_file_not_found(self, tmp_path):
        metrics = probe_video_encoding(str(tmp_path / "nonexistent.mp4"))
        assert metrics.codec == ""
        assert metrics.width == 0

    def test_probe_success(self, tmp_path):
        """Mock ffprobe to return valid JSON."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 10)

        mock_data = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "profile": "High",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "30/1",
                    "pix_fmt": "yuv420p",
                    "bit_rate": "5000000",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "bit_rate": "128000",
                    "channels": 2,
                    "sample_rate": "48000",
                },
            ],
            "format": {"bit_rate": "5128000"},
        }

        with patch("movie_narrator.utils.video_qa._run_ffprobe", return_value=mock_data):
            metrics = probe_video_encoding(str(video_file))

        assert metrics.codec == "h264"
        assert metrics.profile == "High"
        assert metrics.width == 1920
        assert metrics.height == 1080
        assert metrics.fps == 30.0
        assert metrics.bitrate_kbps == 5000
        assert metrics.pixel_format == "yuv420p"
        assert metrics.audio_codec == "aac"
        assert metrics.audio_bitrate_kbps == 128
        assert metrics.audio_channels == 2
        assert metrics.audio_sample_rate == 48000

    def test_probe_fractional_fps(self, tmp_path):
        """Test fps parsing with fractional rates like 30000/1001 (29.97)."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 10)

        mock_data = {
            "streams": [{
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30000/1001",
                "pix_fmt": "yuv420p",
            }],
            "format": {},
        }

        with patch("movie_narrator.utils.video_qa._run_ffprobe", return_value=mock_data):
            metrics = probe_video_encoding(str(video_file))

        assert abs(metrics.fps - 29.97) < 0.1

    def test_probe_format_bitrate_fallback(self, tmp_path):
        """When stream bitrate is missing, fall back to format bitrate."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 10)

        mock_data = {
            "streams": [{
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
            }],
            "format": {"bit_rate": "4000000"},
        }

        with patch("movie_narrator.utils.video_qa._run_ffprobe", return_value=mock_data):
            metrics = probe_video_encoding(str(video_file))

        assert metrics.bitrate_kbps == 4000

    def test_probe_no_ffprobe(self, tmp_path):
        """When ffprobe is unavailable, return empty metrics."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 10)

        with patch("movie_narrator.utils.video_qa._run_ffprobe", return_value=None):
            metrics = probe_video_encoding(str(video_file))

        assert metrics.codec == ""
        assert metrics.width == 0

    def test_probe_invalid_fps(self, tmp_path):
        """Test fps parsing with invalid fps string."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 10)

        mock_data = {
            "streams": [{
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "invalid",
                "pix_fmt": "yuv420p",
            }],
            "format": {},
        }

        with patch("movie_narrator.utils.video_qa._run_ffprobe", return_value=mock_data):
            metrics = probe_video_encoding(str(video_file))

        assert metrics.fps == 0.0

    def test_probe_zero_denominator_fps(self, tmp_path):
        """Test fps parsing with zero denominator."""
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 10)

        mock_data = {
            "streams": [{
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/0",
                "pix_fmt": "yuv420p",
            }],
            "format": {},
        }

        with patch("movie_narrator.utils.video_qa._run_ffprobe", return_value=mock_data):
            metrics = probe_video_encoding(str(video_file))

        assert metrics.fps == 0.0


class TestEvaluateVideoQuality:
    """evaluate_video_quality end-to-end wrapper."""

    def test_file_not_found(self, tmp_path):
        report = evaluate_video_quality(str(tmp_path / "nope.mp4"))
        assert report.ok is False
        assert any("file not found" in i for i in report.issues)

    def test_existing_file_with_mocked_probe(self, tmp_path):
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 10)

        mock_data = {
            "streams": [{
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1920,
                "height": 1080,
                "r_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
                "bit_rate": "5000000",
            }, {
                "codec_type": "audio",
                "codec_name": "aac",
                "channels": 2,
                "sample_rate": "48000",
            }],
            "format": {},
        }

        with patch("movie_narrator.utils.video_qa._run_ffprobe", return_value=mock_data):
            report = evaluate_video_quality(str(video_file))

        assert report.ok is True
        assert report.metrics.codec == "h264"

    def test_custom_thresholds(self, tmp_path):
        video_file = tmp_path / "test.mp4"
        video_file.write_bytes(b"\x00" * 10)

        mock_data = {
            "streams": [{
                "codec_type": "video",
                "codec_name": "h264",
                "width": 1280,
                "height": 720,
                "r_frame_rate": "30/1",
                "pix_fmt": "yuv420p",
                "bit_rate": "1500000",
            }],
            "format": {},
        }

        with patch("movie_narrator.utils.video_qa._run_ffprobe", return_value=mock_data):
            # Passes with default min 1280x720
            report = evaluate_video_quality(str(video_file))
            assert report.ok is True

            # Fails with higher min
            report = evaluate_video_quality(str(video_file), min_width=1920)
            assert report.ok is False


# ════════════════════════════════════════════════════════════
#  Quality Dashboard Tests
# ════════════════════════════════════════════════════════════


class TestQualityDimension:
    """QualityDimension dataclass and label logic."""

    def test_good_label(self):
        d = QualityDimension(name="test", score=0.9, weight=0.1)
        assert d.label == "good"

    def test_acceptable_label(self):
        d = QualityDimension(name="test", score=0.6, weight=0.1)
        assert d.label == "acceptable"

    def test_poor_label(self):
        d = QualityDimension(name="test", score=0.4, weight=0.1)
        assert d.label == "poor"

    def test_critical_label(self):
        d = QualityDimension(name="test", score=0.1, weight=0.1)
        assert d.label == "critical"

    def test_to_dict(self):
        d = QualityDimension(name="script", score=0.85, weight=0.1, issues_count=2)
        dct = d.to_dict()
        assert dct["name"] == "script"
        assert dct["score"] == 0.85
        assert dct["label"] == "good"
        assert dct["issues_count"] == 2


class TestRegressionDelta:
    """RegressionDelta direction logic."""

    def test_improved(self):
        d = RegressionDelta(name="audio", current=0.9, baseline=0.7, delta=0.2)
        assert d.direction == "improved"

    def test_regressed(self):
        d = RegressionDelta(name="audio", current=0.5, baseline=0.8, delta=-0.3)
        assert d.direction == "regressed"

    def test_stable(self):
        d = RegressionDelta(name="audio", current=0.75, baseline=0.755, delta=-0.005)
        assert d.direction == "stable"

    def test_to_dict(self):
        d = RegressionDelta(name="audio", current=0.9, baseline=0.7, delta=0.2)
        dct = d.to_dict()
        assert dct["direction"] == "improved"
        assert dct["delta"] == 0.2


class TestQualityDashboard:
    """QualityDashboard dataclass."""

    def test_defaults(self):
        d = QualityDashboard()
        assert d.overall_score == 0.0
        assert d.dimensions == []
        assert d.regression_summary == "no_baseline"

    def test_label_good(self):
        d = QualityDashboard(overall_score=0.85)
        assert d.label == "good"

    def test_label_critical(self):
        d = QualityDashboard(overall_score=0.1)
        assert d.label == "critical"

    def test_to_dict(self):
        d = QualityDashboard(
            overall_score=0.8,
            dimensions=[QualityDimension(name="script", score=0.9, weight=0.1)],
            total_issues=3,
        )
        dct = d.to_dict()
        assert dct["overall_score"] == 0.8
        assert dct["label"] == "good"
        assert len(dct["dimensions"]) == 1
        assert dct["total_issues"] == 3


class TestDimensionExtractors:
    """Per-dimension score extraction from metadata."""

    def test_extract_script_score(self):
        meta = {"script_qa": {"total_issues": 3}}
        result = _extract_script_score(meta)
        assert result is not None
        score, issues, details = result
        assert score == 0.7  # 1.0 - 3 * 0.1
        assert issues == 3

    def test_extract_script_score_no_data(self):
        assert _extract_script_score({}) is None

    def test_extract_script_score_zero_issues(self):
        meta = {"script_qa": {"total_issues": 0}}
        score, _, _ = _extract_script_score(meta)
        assert score == 1.0

    def test_extract_script_score_many_issues(self):
        meta = {"script_qa": {"total_issues": 15}}
        score, _, _ = _extract_script_score(meta)
        assert score == 0.0  # max(0, 1.0 - 15*0.1) = 0.0... wait: 1.0 - 1.5 = -0.5, max(0, -0.5) = 0.0

    def test_extract_audio_score(self):
        meta = {
            "audio_quality": {
                "summary": {"avg_snr_db": 20.0, "avg_clipping_ratio": 0.001},
                "segments": [
                    {"issues": []},
                    {"issues": ["clipping"]},
                    {"issues": []},
                ],
            }
        }
        result = _extract_audio_score(meta)
        assert result is not None
        score, issues, details = result
        assert score == pytest.approx(2 / 3)
        assert issues == 1
        assert details["total_segments"] == 3

    def test_extract_audio_score_no_segments(self):
        meta = {"audio_quality": {"summary": {}, "segments": []}}
        assert _extract_audio_score(meta) is None

    def test_extract_audio_score_no_data(self):
        assert _extract_audio_score({}) is None

    def test_extract_alignment_score(self):
        meta = {"alignment_qa": {"total_segments": 10, "low_confidence_count": 2}}
        result = _extract_alignment_score(meta)
        assert result is not None
        score, issues, _ = result
        assert score == 0.8  # (10-2)/10

    def test_extract_alignment_score_zero_total(self):
        meta = {"alignment_qa": {"total_segments": 0, "low_confidence_count": 0}}
        assert _extract_alignment_score(meta) is None

    def test_extract_match_score(self):
        meta = {"match_quality": {"total_clips": 5, "low_quality_count": 1, "avg_composite": 0.82}}
        result = _extract_match_score(meta)
        assert result is not None
        score, issues, details = result
        assert score == 0.82
        assert details["avg_composite"] == 0.82

    def test_extract_match_score_zero_clips(self):
        meta = {"match_quality": {"total_clips": 0}}
        assert _extract_match_score(meta) is None

    def test_extract_subtitle_score(self):
        meta = {
            "subtitle_qa": {
                "original": {"total_cues": 10, "issues_count": 2},
                "translated": {"total_cues": 10, "issues_count": 1},
            }
        }
        result = _extract_subtitle_score(meta)
        assert result is not None
        score, issues, _ = result
        assert score == pytest.approx(17 / 20)  # (20-3)/20
        assert issues == 3

    def test_extract_subtitle_score_no_cues(self):
        meta = {"subtitle_qa": {"original": {"total_cues": 0, "issues_count": 0}}}
        assert _extract_subtitle_score(meta) is None

    def test_extract_translation_score(self):
        meta = {
            "translation_glossary": {"inconsistent_count": 2},
            "untranslated_indices": [1, 3],
        }
        result = _extract_translation_score(meta)
        assert result is not None
        score, issues, details = result
        assert score == 0.8  # 1.0 - 4 * 0.05
        assert issues == 4
        assert details["inconsistent_terms"] == 2
        assert details["untranslated_lines"] == 2

    def test_extract_translation_score_no_data(self):
        assert _extract_translation_score({}) is None

    def test_extract_deliverable_score_ok(self):
        meta = {"qa_report": {"ok": True, "issues": []}}
        result = _extract_deliverable_score(meta)
        assert result is not None
        score, _, _ = result
        assert score == 1.0

    def test_extract_deliverable_score_failed(self):
        meta = {"qa_report": {"ok": False, "issues": [{"code": "D001"}, {"code": "D002"}]}}
        result = _extract_deliverable_score(meta)
        assert result is not None
        score, issues, _ = result
        assert score == 0.6  # 1.0 - 2 * 0.2

    def test_extract_video_encoding_score_ok(self):
        meta = {"video_qa": {"ok": True, "issues": [], "metrics": {"codec": "h264", "width": 1920, "height": 1080}}}
        result = _extract_video_encoding_score(meta)
        assert result is not None
        score, _, details = result
        assert score == 1.0
        assert details["codec"] == "h264"

    def test_extract_video_encoding_score_issues(self):
        meta = {"video_qa": {"ok": False, "issues": ["bad codec", "low bitrate"], "metrics": {}}}
        result = _extract_video_encoding_score(meta)
        assert result is not None
        score, issues, _ = result
        assert score == 0.7  # 1.0 - 2 * 0.15


class TestCollectQualityDimensions:
    """collect_quality_dimensions aggregation."""

    def test_empty_metadata(self):
        dims = collect_quality_dimensions({})
        assert dims == []

    def test_all_dimensions(self):
        meta = {
            "script_qa": {"total_issues": 1},
            "audio_quality": {"summary": {}, "segments": [{"issues": []}]},
            "alignment_qa": {"total_segments": 5, "low_confidence_count": 1},
            "match_quality": {"total_clips": 3, "low_quality_count": 0, "avg_composite": 0.9},
            "subtitle_qa": {"original": {"total_cues": 5, "issues_count": 0}},
            "translation_glossary": {"inconsistent_count": 0},
            "qa_report": {"ok": True, "issues": []},
            "video_qa": {"ok": True, "issues": [], "metrics": {}},
        }
        dims = collect_quality_dimensions(meta)
        assert len(dims) == 8
        names = [d.name for d in dims]
        assert "script" in names
        assert "video_encoding" in names

    def test_custom_weights(self):
        meta = {"script_qa": {"total_issues": 0}}
        weights = {"script": 0.5}
        dims = collect_quality_dimensions(meta, weights=weights)
        assert dims[0].weight == 0.5

    def test_default_weights_applied(self):
        meta = {"script_qa": {"total_issues": 0}}
        dims = collect_quality_dimensions(meta)
        assert dims[0].weight == _DEFAULT_WEIGHTS["script"]


class TestComputeOverallScore:
    """compute_overall_score weighted average."""

    def test_empty_dimensions(self):
        assert compute_overall_score([]) == 0.0

    def test_single_dimension(self):
        d = QualityDimension(name="script", score=0.8, weight=0.1)
        assert compute_overall_score([d]) == pytest.approx(0.8)

    def test_weighted_average(self):
        dims = [
            QualityDimension(name="a", score=1.0, weight=0.3),
            QualityDimension(name="b", score=0.5, weight=0.7),
        ]
        # (1.0*0.3 + 0.5*0.7) / (0.3+0.7) = 0.65
        assert compute_overall_score(dims) == pytest.approx(0.65)

    def test_zero_weight(self):
        dims = [QualityDimension(name="a", score=1.0, weight=0.0)]
        assert compute_overall_score(dims) == 0.0

    def test_weight_redistribution(self):
        """Missing dimensions' weights are redistributed to present ones."""
        dims = [
            QualityDimension(name="a", score=1.0, weight=0.3),
            QualityDimension(name="b", score=0.0, weight=0.3),
        ]
        # Only 2 of 8 dimensions present; total_weight = 0.6
        # weighted_sum = 1.0*0.3 + 0.0*0.3 = 0.3
        # score = 0.3 / 0.6 = 0.5
        assert compute_overall_score(dims) == pytest.approx(0.5)


class TestBuildQualityDashboard:
    """build_quality_dashboard end-to-end."""

    def test_empty_metadata(self):
        d = build_quality_dashboard({})
        assert d.overall_score == 0.0
        assert d.dimensions == []
        assert d.total_issues == 0
        assert d.regression_summary == "no_baseline"

    def test_with_data(self):
        meta = {
            "script_qa": {"total_issues": 0},
            "qa_report": {"ok": True, "issues": []},
        }
        d = build_quality_dashboard(meta)
        assert len(d.dimensions) == 2
        assert d.overall_score == 1.0
        assert d.total_issues == 0

    def test_with_baseline(self, tmp_path):
        # Create baseline metadata.json
        baseline = {
            "quality_dashboard": {
                "dimensions": [
                    {"name": "script", "score": 0.8},
                ],
            },
        }
        baseline_path = tmp_path / "baseline_metadata.json"
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

        meta = {"script_qa": {"total_issues": 1}}  # score = 0.9
        d = build_quality_dashboard(meta, baseline_path=str(baseline_path))
        assert len(d.regression_deltas) == 1
        delta = d.regression_deltas[0]
        assert delta.name == "script"
        assert delta.direction == "improved"
        assert d.regression_summary == "improved"

    def test_baseline_regression(self, tmp_path):
        baseline = {
            "quality_dashboard": {
                "dimensions": [
                    {"name": "script", "score": 0.95},
                ],
            },
        }
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

        meta = {"script_qa": {"total_issues": 5}}  # score = 0.5
        d = build_quality_dashboard(meta, baseline_path=str(baseline_path))
        delta = d.regression_deltas[0]
        assert delta.direction == "regressed"
        assert d.regression_summary == "regressed"

    def test_baseline_stable(self, tmp_path):
        baseline = {
            "quality_dashboard": {
                "dimensions": [
                    {"name": "script", "score": 0.9},
                ],
            },
        }
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

        meta = {"script_qa": {"total_issues": 1}}  # score = 0.9
        d = build_quality_dashboard(meta, baseline_path=str(baseline_path))
        delta = d.regression_deltas[0]
        assert delta.direction == "stable"
        assert d.regression_summary == "stable"

    def test_baseline_not_found(self, tmp_path):
        meta = {"script_qa": {"total_issues": 0}}
        d = build_quality_dashboard(meta, baseline_path=str(tmp_path / "nonexistent.json"))
        assert d.regression_deltas == []
        assert d.regression_summary == "no_baseline"

    def test_custom_weights(self):
        meta = {"script_qa": {"total_issues": 0}}
        weights = {"script": 1.0}
        d = build_quality_dashboard(meta, weights=weights)
        assert d.dimensions[0].weight == 1.0


class TestCompareWithBaseline:
    """_compare_with_baseline internal function."""

    def test_matching_dimensions(self, tmp_path):
        baseline = {
            "quality_dashboard": {
                "dimensions": [
                    {"name": "script", "score": 0.7},
                    {"name": "audio", "score": 0.8},
                ],
            },
        }
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(baseline), encoding="utf-8")

        current_dims = [
            QualityDimension(name="script", score=0.9, weight=0.1),
            QualityDimension(name="audio", score=0.6, weight=0.15),
        ]
        deltas = _compare_with_baseline(current_dims, str(path))
        assert len(deltas) == 2
        script_delta = next(d for d in deltas if d.name == "script")
        assert script_delta.direction == "improved"
        audio_delta = next(d for d in deltas if d.name == "audio")
        assert audio_delta.direction == "regressed"

    def test_no_matching_dimensions(self, tmp_path):
        baseline = {
            "quality_dashboard": {
                "dimensions": [{"name": "old_dim", "score": 0.5}],
            },
        }
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(baseline), encoding="utf-8")

        current_dims = [QualityDimension(name="script", score=0.9, weight=0.1)]
        deltas = _compare_with_baseline(current_dims, str(path))
        assert deltas == []

    def test_invalid_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("not json", encoding="utf-8")
        current_dims = [QualityDimension(name="script", score=0.9, weight=0.1)]
        deltas = _compare_with_baseline(current_dims, str(path))
        assert deltas == []


# ════════════════════════════════════════════════════════════
#  QA Report Tests
# ════════════════════════════════════════════════════════════


class TestGenerateQaReportDict:
    """generate_qa_report_dict structure."""

    def test_empty_metadata(self):
        report = generate_qa_report_dict({})
        assert report["report_version"] == "1.0"
        assert report["overall"]["score"] == 0.0
        assert report["overall"]["label"] == "critical"
        assert report["dimensions"] == []
        assert report["regression"]["summary"] == "no_baseline"
        assert report["recommendations"] == []

    def test_with_data(self):
        meta = {
            "script_qa": {"total_issues": 0},
            "qa_report": {"ok": True, "issues": []},
            "video_qa": {
                "ok": True,
                "issues": [],
                "recommendations": ["Use H.264"],
                "metrics": {"codec": "h264"},
            },
        }
        report = generate_qa_report_dict(meta, movie_name="Inception")
        assert report["movie_name"] == "Inception"
        assert report["overall"]["score"] == 1.0
        assert len(report["dimensions"]) == 3
        assert "Use H.264" in report["recommendations"]

    def test_issue_summary(self):
        meta = {
            "script_qa": {"total_issues": 3},
        }
        report = generate_qa_report_dict(meta)
        assert len(report["issue_summary"]) == 1
        assert report["issue_summary"][0]["dimension"] == "script"
        assert report["issue_summary"][0]["issues_count"] == 3

    def test_raw_reports_included(self):
        meta = {
            "script_qa": {"total_issues": 0},
            "alignment_qa": {"total_segments": 5, "low_confidence_count": 1},
        }
        report = generate_qa_report_dict(meta)
        assert report["raw_reports"]["script_qa"] is not None
        assert report["raw_reports"]["alignment_qa"] is not None

    def test_generated_at_present(self):
        report = generate_qa_report_dict({})
        assert "generated_at" in report
        assert report["generated_at"].endswith("Z")

    def test_tool_version_present(self):
        report = generate_qa_report_dict({})
        assert "tool_version" in report
        assert report["tool_version"]  # non-empty


class TestSummarizeAudio:
    """_summarize_audio helper."""

    def test_none(self):
        assert _summarize_audio(None) is None

    def test_summary(self):
        aq = {
            "summary": {"avg_snr_db": 25.0},
            "segments": [{}, {}, {}],
            "prosody": {"emotion": "happy"},
        }
        result = _summarize_audio(aq)
        assert result["summary"]["avg_snr_db"] == 25.0
        assert result["segment_count"] == 3
        assert result["prosody"]["emotion"] == "happy"


class TestSummarizeSubtitle:
    """_summarize_subtitle helper."""

    def test_none(self):
        assert _summarize_subtitle(None) is None

    def test_with_tracks(self):
        sq = {
            "original": {"total_cues": 10, "issues_count": 1},
            "translated": {"total_cues": 10, "issues_count": 2},
        }
        result = _summarize_subtitle(sq)
        assert result["original"]["total_cues"] == 10
        assert result["translated"]["issues_count"] == 2

    def test_with_display_fit_issues(self):
        sq = {
            "original": {"total_cues": 5, "issues_count": 0},
            "display_fit_issues": [{"issue": "too_long"}, {"issue": "too_long"}],
        }
        result = _summarize_subtitle(sq)
        assert result["display_fit_issues_count"] == 2

    def test_empty_dict(self):
        assert _summarize_subtitle({}) is None


class TestFormatQaReportText:
    """format_qa_report_text human-readable output."""

    def test_basic_formatting(self):
        report = generate_qa_report_dict({"script_qa": {"total_issues": 0}})
        text = format_qa_report_text(report)
        assert "QUALITY ASSURANCE REPORT" in text
        assert "OVERALL QUALITY" in text
        assert "PER-DIMENSION BREAKDOWN" in text
        assert "END OF REPORT" in text

    def test_empty_report(self):
        report = generate_qa_report_dict({})
        text = format_qa_report_text(report)
        assert "QUALITY ASSURANCE REPORT" in text
        assert "no quality dimensions available" in text

    def test_regression_section(self, tmp_path):
        baseline = {
            "quality_dashboard": {
                "dimensions": [{"name": "script", "score": 0.8}],
            },
        }
        path = tmp_path / "baseline.json"
        path.write_text(json.dumps(baseline), encoding="utf-8")

        report = generate_qa_report_dict(
            {"script_qa": {"total_issues": 1}},
            baseline_path=str(path),
        )
        text = format_qa_report_text(report)
        assert "REGRESSION ANALYSIS" in text

    def test_recommendations_section(self):
        meta = {
            "video_qa": {
                "ok": False,
                "issues": ["bad codec"],
                "recommendations": ["Re-encode with H.264"],
                "metrics": {},
            },
        }
        report = generate_qa_report_dict(meta)
        text = format_qa_report_text(report)
        assert "RECOMMENDATIONS" in text
        assert "Re-encode with H.264" in text

    def test_issue_summary_section(self):
        meta = {"script_qa": {"total_issues": 5}}
        report = generate_qa_report_dict(meta)
        text = format_qa_report_text(report)
        assert "ISSUE SUMMARY" in text

    def test_movie_name_in_header(self):
        report = generate_qa_report_dict({}, movie_name="MyMovie")
        text = format_qa_report_text(report)
        assert "MyMovie" in text


class TestExportQaReport:
    """export_qa_report file output."""

    def test_export_creates_files(self, tmp_path):
        meta = {"script_qa": {"total_issues": 0}}
        report = export_qa_report(meta, tmp_path, movie_name="Test")
        json_path = tmp_path / "qa_report.json"
        txt_path = tmp_path / "qa_report.txt"
        assert json_path.exists()
        assert txt_path.exists()

        # Verify JSON is valid
        loaded = json.loads(json_path.read_text(encoding="utf-8"))
        assert loaded["movie_name"] == "Test"

        # Verify text is readable
        text = txt_path.read_text(encoding="utf-8")
        assert "QUALITY ASSURANCE REPORT" in text

    def test_export_creates_output_dir(self, tmp_path):
        output_dir = tmp_path / "nested" / "qa"
        export_qa_report({}, output_dir)
        assert (output_dir / "qa_report.json").exists()

    def test_export_with_baseline(self, tmp_path):
        baseline = {
            "quality_dashboard": {
                "dimensions": [{"name": "script", "score": 0.5}],
            },
        }
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(json.dumps(baseline), encoding="utf-8")

        report = export_qa_report(
            {"script_qa": {"total_issues": 0}},  # score = 1.0
            tmp_path,
            baseline_path=str(baseline_path),
        )
        assert report["regression"]["summary"] == "improved"

    def test_return_value_matches_json(self, tmp_path):
        meta = {"script_qa": {"total_issues": 1}}
        report = export_qa_report(meta, tmp_path)
        json_path = tmp_path / "qa_report.json"
        loaded = json.loads(json_path.read_text(encoding="utf-8"))
        assert loaded == report


# ════════════════════════════════════════════════════════════
#  QA Gate Tests
# ════════════════════════════════════════════════════════════


class TestRunQaGate:
    """run_qa_gate intermediate product validation."""

    def test_skip_in_ci(self, tmp_path):
        """Gate is skipped in CI unless qa_enabled is True."""
        ctx = _make_ctx(tmp_path)
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=True):
            run_qa_gate(ctx)
        assert ctx.step_state.result == StepResult.SKIPPED
        assert ctx.metadata.get("qa_gate") is None

    def test_ci_with_qa_enabled(self, tmp_path):
        """Gate runs in CI when qa_enabled=True."""
        ctx = _make_ctx(tmp_path, qa_enabled=True)
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=True):
            run_qa_gate(ctx)
        assert ctx.metadata.get("qa_gate") is not None
        assert ctx.metadata["qa_gate"]["passed"] is True

    def test_no_ci_no_data(self, tmp_path):
        """Gate passes when no QA data is present (nothing to validate)."""
        ctx = _make_ctx(tmp_path)
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is True
        assert ctx.step_state.result == StepResult.SUCCESS

    def test_script_qa_pass(self, tmp_path):
        ctx = _make_ctx(tmp_path, script_qa={"total_issues": 3})
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is True
        assert len(ctx.metadata["qa_gate"]["warnings"]) == 1

    def test_script_qa_critical(self, tmp_path):
        ctx = _make_ctx(tmp_path, script_qa={"total_issues": _MAX_SCRIPT_ISSUES + 1})
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is False
        assert any("Script QA" in i for i in ctx.metadata["qa_gate"]["issues"])
        assert ctx.step_state.result == StepResult.WARNING

    def test_audio_qa_clipping(self, tmp_path):
        ctx = _make_ctx(tmp_path, audio_quality={
            "segments": [
                {"clipping_ratio": 0.005},  # ok
                {"clipping_ratio": 0.02},   # critical
            ],
        })
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is False
        assert any("Audio QA" in i for i in ctx.metadata["qa_gate"]["issues"])

    def test_audio_qa_ok(self, tmp_path):
        ctx = _make_ctx(tmp_path, audio_quality={
            "segments": [{"clipping_ratio": 0.001}],
        })
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is True

    def test_subtitle_qa_too_many_issues(self, tmp_path):
        ctx = _make_ctx(tmp_path, subtitle_qa={
            "original": {"total_cues": 10, "issues_count": 6},  # 60% > 50%
        })
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is False
        assert any("Subtitle QA" in i for i in ctx.metadata["qa_gate"]["issues"])

    def test_subtitle_qa_ok(self, tmp_path):
        ctx = _make_ctx(tmp_path, subtitle_qa={
            "original": {"total_cues": 10, "issues_count": 3},  # 30% < 50%
        })
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is True

    def test_alignment_qa_low_confidence(self, tmp_path):
        ctx = _make_ctx(tmp_path, alignment_qa={
            "total_segments": 10,
            "low_confidence_count": 6,  # 60% > 50%
        })
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is False
        assert any("Alignment QA" in i for i in ctx.metadata["qa_gate"]["issues"])

    def test_alignment_qa_ok(self, tmp_path):
        ctx = _make_ctx(tmp_path, alignment_qa={
            "total_segments": 10,
            "low_confidence_count": 3,  # 30% < 50%
        })
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is True

    def test_translation_too_many_untranslated(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.timed_segments = [TimedSegment(text=f"seg{i}", start=float(i), end=float(i + 1)) for i in range(10)]
        ctx.metadata["untranslated_indices"] = [1, 2, 3, 4]  # 40% > 30%
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is False
        assert any("Translation" in i for i in ctx.metadata["qa_gate"]["issues"])

    def test_translation_ok(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        ctx.timed_segments = [TimedSegment(text=f"seg{i}", start=float(i), end=float(i + 1)) for i in range(10)]
        ctx.metadata["untranslated_indices"] = [1, 2]  # 20% < 30%
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is True

    def test_strict_mode_raises(self, tmp_path):
        ctx = _make_ctx(tmp_path, strict=True, script_qa={"total_issues": _MAX_SCRIPT_ISSUES + 1})
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            with pytest.raises(RuntimeError, match="QA gate failed"):
                run_qa_gate(ctx)

    def test_non_strict_mode_no_raise(self, tmp_path):
        ctx = _make_ctx(tmp_path, script_qa={"total_issues": _MAX_SCRIPT_ISSUES + 1})
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            # Should not raise
            run_qa_gate(ctx)
        assert ctx.step_state.result == StepResult.WARNING

    def test_multiple_issues_collected(self, tmp_path):
        ctx = _make_ctx(
            tmp_path,
            script_qa={"total_issues": _MAX_SCRIPT_ISSUES + 1},
            audio_quality={"segments": [{"clipping_ratio": 0.05}]},
        )
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert len(ctx.metadata["qa_gate"]["issues"]) >= 2

    def test_gate_result_structure(self, tmp_path):
        ctx = _make_ctx(tmp_path, script_qa={"total_issues": 2})
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        gate = ctx.metadata["qa_gate"]
        assert "issues" in gate
        assert "warnings" in gate
        assert "passed" in gate
        assert isinstance(gate["issues"], list)
        assert isinstance(gate["warnings"], list)
        assert isinstance(gate["passed"], bool)

    def test_warnings_not_issues(self, tmp_path):
        """Minor script issues go to warnings, not issues."""
        ctx = _make_ctx(tmp_path, script_qa={"total_issues": 1})
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is True
        assert len(ctx.metadata["qa_gate"]["issues"]) == 0
        assert len(ctx.metadata["qa_gate"]["warnings"]) == 1

    def test_zero_untranslated_no_issue(self, tmp_path):
        """Empty untranslated_indices should not trigger a gate issue."""
        ctx = _make_ctx(tmp_path)
        ctx.timed_segments = [TimedSegment(text="seg", start=0.0, end=1.0)]
        ctx.metadata["untranslated_indices"] = []
        with patch("movie_narrator.pipeline.qa_gate.is_ci", return_value=False):
            run_qa_gate(ctx)
        assert ctx.metadata["qa_gate"]["passed"] is True
