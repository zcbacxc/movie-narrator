# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""v1.5.0 — expectations-aware video QA (4K size + pixel plan).

Extends ``utils/video_qa.py`` with checks driven by the run's own
configuration/metadata:

- a 4K-class requested size (>= 3840 wide or >= 2160 tall, either
  orientation) must be reproduced exactly by the output;
- the ``render_pixel`` plan (Feature 1) is cross-checked against the
  probed ``pix_fmt`` / ``color_transfer`` (10-bit = yuv420p10le,
  hdr10 = smpte2084, sdr = bt709);
- yuv420p10le is a first-class output pix_fmt (no compat warning).

The minimum-resolution gate (1280x720 with portrait transposition) is
untouched — see tests/test_v120_vertical_qa.py.
"""

from unittest.mock import patch

from movie_narrator.models import Context
from movie_narrator.pipeline.qa import validate_deliverable
from movie_narrator.utils.deliverable_qa import QAReport
from movie_narrator.utils.video_qa import (
    VideoEncodingMetrics,
    VideoQAReport,
    check_encoding_quality,
    evaluate_video_quality,
    extract_render_expectations,
)


def _metrics(
    width: int = 1920,
    height: int = 1080,
    *,
    pix_fmt: str = "yuv420p",
    color_primaries: str = "",
    color_transfer: str = "",
    color_space: str = "",
) -> VideoEncodingMetrics:
    """Build otherwise-clean metrics with the given pixel attributes."""
    return VideoEncodingMetrics(
        codec="h264",
        profile="High 10" if pix_fmt == "yuv420p10le" else "High",
        width=width,
        height=height,
        fps=30.0,
        bitrate_kbps=8000,
        pixel_format=pix_fmt,
        color_primaries=color_primaries,
        color_transfer=color_transfer,
        color_space=color_space,
        audio_codec="aac",
        audio_bitrate_kbps=128,
        audio_channels=2,
        audio_sample_rate=48000,
    )


def _plan(bit_depth: int, color_space: str) -> dict:
    """Build a render_pixel block shaped exactly like the render step's."""
    tags = (
        {"color_primaries": "bt709", "color_trc": "bt709", "colorspace": "bt709"}
        if color_space == "sdr"
        else {"color_primaries": "bt2020", "color_trc": "smpte2084", "colorspace": "bt2020nc"}
    )
    return {
        "bit_depth": bit_depth,
        "pix_fmt": "yuv420p10le" if bit_depth == 10 else "yuv420p",
        "color_space": color_space,
        "color_tags": tags,
        "encoder_path": "cpu",
    }


# ── 1. 4K size expectations ──────────────────────────────


class TestFourKSizeExpectations:
    def test_4k_landscape_exact_match_passes(self):
        report = check_encoding_quality(_metrics(3840, 2160), expected_size=(3840, 2160))
        assert report.ok is True, report.issues

    def test_4k_landscape_mismatch_flagged(self):
        report = check_encoding_quality(_metrics(1920, 1080), expected_size=(3840, 2160))
        assert any("4K-class resolution mismatch" in i for i in report.issues)
        assert any("3840x2160" in i for i in report.issues)
        assert report.ok is False
        assert any("video_sizes" in r for r in report.recommendations)

    def test_4k_portrait_exact_match_passes(self):
        report = check_encoding_quality(_metrics(2160, 3840), expected_size=(2160, 3840))
        assert report.ok is True, report.issues

    def test_4k_portrait_mismatch_flagged(self):
        """A 1080x1920 output against a 2160x3840 request passes the
        portrait 720p floor (no regression) but fails the exact match."""
        report = check_encoding_quality(_metrics(1080, 1920), expected_size=(2160, 3840))
        assert any("4K-class resolution mismatch" in i for i in report.issues)
        assert not any("below" in i for i in report.issues)
        assert any("2160x3840" in i for i in report.issues)

    def test_sub4k_request_keeps_floor_only(self):
        """Below 4K the request size is not enforced — only the 720p floor."""
        report = check_encoding_quality(_metrics(1280, 720), expected_size=(2560, 1440))
        assert report.ok is True, report.issues
        assert not any("mismatch" in i for i in report.issues)

    def test_4k_width_triggers_even_when_short(self):
        """3840x720 is 4K-class via width (>= 3840) — exact match enforced."""
        report = check_encoding_quality(_metrics(1920, 720), expected_size=(3840, 720))
        assert any("4K-class resolution mismatch" in i for i in report.issues)

    def test_4k_height_triggers_even_when_narrow(self):
        """1280x2160 is 4K-class via height (>= 2160) — exact match enforced."""
        report = check_encoding_quality(_metrics(1280, 1080), expected_size=(1280, 2160))
        assert any("4K-class resolution mismatch" in i for i in report.issues)

    def test_minimum_gate_not_regressed_by_4k_check(self):
        """A sub-floor output against a 4K request raises both findings."""
        report = check_encoding_quality(_metrics(640, 360), expected_size=(3840, 2160))
        assert any("below" in i for i in report.issues)
        assert any("4K-class resolution mismatch" in i for i in report.issues)


# ── 2. Pixel plan expectations ───────────────────────────


class TestPixelExpectations:
    def test_10bit_plan_matches_yuv420p10le(self):
        report = check_encoding_quality(
            _metrics(pix_fmt="yuv420p10le", color_transfer="bt709"),
            expected_pixel=_plan(10, "sdr"),
        )
        assert report.ok is True, report.issues

    def test_10bit_plan_8bit_output_flagged(self):
        report = check_encoding_quality(
            _metrics(pix_fmt="yuv420p", color_transfer="bt709"),
            expected_pixel=_plan(10, "sdr"),
        )
        assert any("does not match the render plan" in i for i in report.issues)
        assert any("yuv420p10le" in i for i in report.issues)
        assert report.ok is False
        assert any("render_bit_depth" in r for r in report.recommendations)

    def test_8bit_plan_matches_yuv420p(self):
        report = check_encoding_quality(
            _metrics(pix_fmt="yuv420p", color_transfer="bt709"),
            expected_pixel=_plan(8, "sdr"),
        )
        assert report.ok is True, report.issues

    def test_8bit_plan_10bit_output_flagged(self):
        report = check_encoding_quality(
            _metrics(pix_fmt="yuv420p10le", color_transfer="bt709"),
            expected_pixel=_plan(8, "sdr"),
        )
        assert any("yuv420p" in i and "render plan" in i for i in report.issues)

    def test_hdr10_plan_matches_smpte2084(self):
        report = check_encoding_quality(
            _metrics(pix_fmt="yuv420p10le", color_transfer="smpte2084"),
            expected_pixel=_plan(10, "hdr10"),
        )
        assert report.ok is True, report.issues

    def test_hdr10_plan_bt709_output_flagged(self):
        report = check_encoding_quality(
            _metrics(pix_fmt="yuv420p10le", color_transfer="bt709"),
            expected_pixel=_plan(10, "hdr10"),
        )
        assert any("color transfer 'bt709'" in i for i in report.issues)
        assert any("smpte2084" in i for i in report.issues)
        assert report.ok is False

    def test_sdr_plan_smpte2084_output_flagged(self):
        report = check_encoding_quality(
            _metrics(color_transfer="smpte2084"),
            expected_pixel=_plan(8, "sdr"),
        )
        assert any("color transfer 'smpte2084'" in i for i in report.issues)
        assert any("bt709" in i for i in report.issues)

    def test_recorded_pix_fmt_used_when_depth_missing(self):
        """A plan without bit_depth falls back to its recorded pix_fmt."""
        plan = {"pix_fmt": "yuv420p10le", "color_space": "sdr"}
        ok = check_encoding_quality(_metrics(pix_fmt="yuv420p10le"), expected_pixel=plan)
        bad = check_encoding_quality(_metrics(pix_fmt="yuv420p"), expected_pixel=plan)
        assert ok.ok is True
        assert any("render plan" in i for i in bad.issues)

    def test_color_tags_used_when_space_missing(self):
        """A plan without color_space falls back to its recorded trc tag."""
        plan = {"bit_depth": 10, "color_tags": {"color_trc": "smpte2084"}}
        ok = check_encoding_quality(
            _metrics(pix_fmt="yuv420p10le", color_transfer="smpte2084"),
            expected_pixel=plan,
        )
        bad = check_encoding_quality(
            _metrics(pix_fmt="yuv420p10le", color_transfer="bt709"),
            expected_pixel=plan,
        )
        assert ok.ok is True
        assert any("color transfer" in i for i in bad.issues)

    def test_untagged_probe_does_not_false_positive(self):
        """Mirror the module convention: only flag what ffprobe detected."""
        report = check_encoding_quality(
            _metrics(pix_fmt="", color_transfer=""),
            expected_pixel=_plan(10, "hdr10"),
        )
        assert not any("render plan" in i for i in report.issues)

    def test_10bit_pix_fmt_no_compatibility_warning(self):
        """yuv420p10le is a first-class publishable pix_fmt in v1.5.0."""
        report = check_encoding_quality(_metrics(pix_fmt="yuv420p10le"))
        assert not any("compatibility issues" in i for i in report.issues)
        assert report.ok is True

    def test_no_expectations_keeps_prior_behaviour(self):
        """expected_pixel=None + expected_size=None == the v0.5.12 checks."""
        report = check_encoding_quality(_metrics())
        assert report.ok is True, report.issues
        assert report.issues == []


# ── 3. extract_render_expectations ───────────────────────


class TestExtractRenderExpectations:
    def test_full_metadata(self):
        meta = {
            "video_sizes": {"16:9": [3840, 2160], "9:16": [2160, 3840]},
            "video_format": "9:16",
            "render_pixel": _plan(10, "hdr10"),
        }
        size, pixel = extract_render_expectations(meta)
        assert size == (2160, 3840)
        assert pixel == meta["render_pixel"]

    def test_default_format_is_16_9(self):
        meta = {"video_sizes": {"16:9": [3840, 2160]}}
        size, _ = extract_render_expectations(meta)
        assert size == (3840, 2160)

    def test_missing_sizes_gives_none(self):
        size, pixel = extract_render_expectations({"render_pixel": _plan(10, "sdr")})
        assert size is None
        assert pixel is not None

    def test_malformed_sizes_give_none(self):
        for bad in ([3840], [3840, 2160, 0], "big", [None, 2160], ["3840x", 2160]):
            size, _ = extract_render_expectations({"video_sizes": {"16:9": bad}})
            assert size is None, bad

    def test_missing_or_non_dict_render_pixel_gives_none(self):
        for bad in ({}, "", None, ["yuv420p10le"]):
            _, pixel = extract_render_expectations({"render_pixel": bad})
            assert pixel is None, bad

    def test_empty_metadata(self):
        assert extract_render_expectations({}) == (None, None)


# ── 4. Probe captures color metadata ─────────────────────


class TestProbeColorMetadata:
    def _probe(self, monkeypatch, stream):
        import movie_narrator.utils.video_qa as vqa

        monkeypatch.setattr(vqa, "_run_ffprobe", lambda p, timeout=30: {"streams": [stream]})
        return vqa.probe_video_encoding("fake.mp4")

    def test_probe_captures_color_tags(self, monkeypatch):
        metrics = self._probe(
            monkeypatch,
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 3840,
                "height": 2160,
                "pix_fmt": "yuv420p10le",
                "color_primaries": "bt2020",
                "color_transfer": "smpte2084",
                "color_space": "bt2020nc",
            },
        )
        assert metrics.color_primaries == "bt2020"
        assert metrics.color_transfer == "smpte2084"
        assert metrics.color_space == "bt2020nc"
        assert metrics.pixel_format == "yuv420p10le"

    def test_probe_untagged_stream_leaves_color_empty(self, monkeypatch):
        metrics = self._probe(monkeypatch, {"codec_type": "video", "codec_name": "h264"})
        assert metrics.color_primaries == ""
        assert metrics.color_transfer == ""
        assert metrics.color_space == ""

    def test_to_dict_includes_color_fields(self):
        d = _metrics(pix_fmt="yuv420p10le", color_transfer="smpte2084").to_dict()
        assert d["color_transfer"] == "smpte2084"
        assert d["color_primaries"] == ""
        assert d["color_space"] == ""


# ── 5. evaluate_video_quality forwarding ─────────────────


class TestEvaluateVideoQualityForwarding:
    def _file(self, tmp_path):
        f = tmp_path / "final.mp4"
        f.write_bytes(b"\x00\x00\x00\x00")
        return str(f)

    def test_expectations_forwarded_and_enforced(self, tmp_path, monkeypatch):
        import movie_narrator.utils.video_qa as vqa

        stream = {
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "pix_fmt": "yuv420p",
            "color_transfer": "bt709",
        }
        monkeypatch.setattr(vqa, "_run_ffprobe", lambda p, timeout=30: {"streams": [stream]})
        report = evaluate_video_quality(
            self._file(tmp_path),
            expected_size=(3840, 2160),
            expected_pixel=_plan(10, "sdr"),
        )
        assert any("4K-class resolution mismatch" in i for i in report.issues)
        assert any("render plan" in i for i in report.issues)

    def test_probe_failure_with_expectations_stays_clean(self, tmp_path, monkeypatch):
        """An empty probe (ffprobe unavailable) produces no expectation noise."""
        import movie_narrator.utils.video_qa as vqa

        monkeypatch.setattr(vqa, "_run_ffprobe", lambda p, timeout=30: None)
        report = evaluate_video_quality(
            self._file(tmp_path),
            expected_size=(3840, 2160),
            expected_pixel=_plan(10, "hdr10"),
        )
        assert report.issues == []


# ── 6. validate_deliverable wiring ───────────────────────


class TestValidateDeliverableWiring:
    def test_step_passes_run_expectations_to_video_qa(self, tmp_path):
        """The hard QA step derives expectations from the run's own
        video_sizes / video_format / render_pixel metadata."""
        ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(tmp_path / "a.mp3"))
        ctx.video_path = str(tmp_path / "final.mp4")
        ctx.metadata.update(
            {
                "qa_enabled": True,
                "video_sizes": {"16:9": [3840, 2160], "9:16": [2160, 3840]},
                "video_format": "9:16",
                "render_pixel": _plan(10, "hdr10"),
            }
        )
        report = QAReport(ok=True, issues=[], metrics={"duration": 10.0, "mean_volume": -14.0})
        with (
            patch("movie_narrator.pipeline.qa.is_ci", return_value=True),
            patch("movie_narrator.pipeline.qa.evaluate_deliverable", return_value=report),
            patch(
                "movie_narrator.pipeline.qa.evaluate_video_quality",
                return_value=VideoQAReport(),
            ) as vq,
        ):
            validate_deliverable(ctx)
        assert vq.call_count == 1
        kwargs = vq.call_args.kwargs
        assert kwargs["expected_size"] == (2160, 3840)
        assert kwargs["expected_pixel"] == ctx.metadata["render_pixel"]

    def test_step_without_metadata_passes_none(self, tmp_path):
        ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(tmp_path / "a.mp3"))
        ctx.video_path = str(tmp_path / "final.mp4")
        ctx.metadata["qa_enabled"] = True
        report = QAReport(ok=True, issues=[], metrics={"duration": 10.0, "mean_volume": -14.0})
        with (
            patch("movie_narrator.pipeline.qa.is_ci", return_value=True),
            patch("movie_narrator.pipeline.qa.evaluate_deliverable", return_value=report),
            patch(
                "movie_narrator.pipeline.qa.evaluate_video_quality",
                return_value=VideoQAReport(),
            ) as vq,
        ):
            validate_deliverable(ctx)
        kwargs = vq.call_args.kwargs
        assert kwargs["expected_size"] is None
        assert kwargs["expected_pixel"] is None
