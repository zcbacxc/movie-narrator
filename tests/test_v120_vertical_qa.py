# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""v1.2 Wave 1B — portrait (vertical) resolution QA fix regression tests.

Ensures ``check_encoding_quality`` selects resolution minimums by
orientation: a 9:16 1080x1920 video must pass, while sub-threshold
landscape/portrait videos still produce a resolution issue.
"""

from movie_narrator.utils.video_qa import VideoEncodingMetrics, check_encoding_quality


def _vertical_metrics(width: int, height: int) -> VideoEncodingMetrics:
    """Build otherwise-clean metrics with the given dimensions."""
    return VideoEncodingMetrics(
        codec="h264",
        profile="High",
        width=width,
        height=height,
        fps=30.0,
        bitrate_kbps=5000,
        pixel_format="yuv420p",
        audio_codec="aac",
        audio_bitrate_kbps=128,
        audio_channels=2,
        audio_sample_rate=48000,
    )


def test_vertical_1080x1920_no_resolution_issue():
    """A compliant 9:16 1080x1920 vertical video must not raise a resolution issue."""
    report = check_encoding_quality(_vertical_metrics(1080, 1920))
    assert not any("resolution" in i for i in report.issues)
    assert report.ok is True


def test_vertical_720x1280_passes_minimum():
    """720x1280 is the portrait minimum and must pass cleanly."""
    report = check_encoding_quality(_vertical_metrics(720, 1280))
    assert not any("resolution" in i for i in report.issues)
    assert report.ok is True


def test_vertical_below_720x1280_raises_issue():
    """A portrait video below 720x1280 (540x960) must raise a resolution issue."""
    report = check_encoding_quality(_vertical_metrics(540, 960))
    assert any("resolution" in i for i in report.issues)
    assert any("720x1280" in i for i in report.issues)


def test_landscape_below_1280x720_still_raises_issue():
    """Landscape minimums are unchanged: 640x360 must still fail."""
    report = check_encoding_quality(_vertical_metrics(640, 360))
    assert any("resolution" in i for i in report.issues)
    assert any("1280x720" in i for i in report.issues)


def test_landscape_1920x1080_passes():
    report = check_encoding_quality(_vertical_metrics(1920, 1080))
    assert not any("resolution" in i for i in report.issues)
    assert report.ok is True


def test_vertical_aspect_ratio_still_standard():
    """1080/1920 ~= 0.5625 ~= 9/16, so no aspect ratio issue remains."""
    report = check_encoding_quality(_vertical_metrics(1080, 1920))
    assert not any("aspect ratio" in i for i in report.issues)
