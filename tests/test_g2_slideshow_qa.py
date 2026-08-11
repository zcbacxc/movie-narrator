# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for G2 — slideshow-degradation & near-black frame QA checks.

Covers the pure scoring logic in ``check_slideshow_risk`` (via mocked luma
samples) and the opt-in wiring in ``evaluate_deliverable`` (via mocked
``check_slideshow_risk``), so no real ffmpeg/PIL is required.
"""

from unittest.mock import patch

import pytest

from movie_narrator.utils.deliverable_qa import evaluate_deliverable
from movie_narrator.utils.video_qa import SlideshowRisk, check_slideshow_risk


# ── Pure scoring logic (bypass frame extraction) ────────────


def test_slideshow_risk_static_video_is_high(monkeypatch, tmp_path):
    """Near-constant luma samples → high risk, high static ratio."""
    f = tmp_path / "static.mp4"
    f.write_bytes(b"x" * 100)
    # All frames identical luma → zero motion → carousel-like.
    monkeypatch.setattr(
        "movie_narrator.utils.video_qa._extract_luma_frames",
        lambda *a, **k: [50.0, 50.0, 50.0, 50.0, 50.0],
    )
    result = check_slideshow_risk(str(f))
    assert result.probed is True
    assert result.static_ratio == 1.0
    assert result.avg_motion == 0.0
    assert result.risk > 0.9


def test_slideshow_risk_moving_video_is_low(monkeypatch, tmp_path):
    """Varying luma samples → low motion risk."""
    f = tmp_path / "moving.mp4"
    f.write_bytes(b"x" * 100)
    monkeypatch.setattr(
        "movie_narrator.utils.video_qa._extract_luma_frames",
        lambda *a, **k: [10.0, 90.0, 20.0, 80.0, 30.0, 70.0],
    )
    result = check_slideshow_risk(str(f))
    assert result.probed is True
    assert result.avg_motion > 10.0
    assert result.risk < 0.5


def test_slideshow_risk_black_frames_add_to_risk(monkeypatch, tmp_path):
    """High black-frame ratio pushes risk up even with some motion."""
    f = tmp_path / "mixed.mp4"
    f.write_bytes(b"x" * 100)
    # One bright frame among many black → high black_ratio.
    monkeypatch.setattr(
        "movie_narrator.utils.video_qa._extract_luma_frames",
        lambda *a, **k: [0.0, 0.0, 0.0, 0.0, 200.0, 0.0],
    )
    result = check_slideshow_risk(str(f))
    assert result.probed is True
    assert result.black_ratio > 0.8
    assert result.risk >= 0.4  # black_ratio * 0.5 floor


def test_slideshow_risk_unavailable_returns_probed_false(monkeypatch, tmp_path):
    """Too few samples → probed=False (graceful degradation)."""
    f = tmp_path / "short.mp4"
    f.write_bytes(b"x" * 100)
    monkeypatch.setattr(
        "movie_narrator.utils.video_qa._extract_luma_frames",
        lambda *a, **k: [],
    )
    result = check_slideshow_risk(str(f))
    assert result.probed is False
    assert result.samples == 0


# ── Opt-in wiring in evaluate_deliverable ───────────────────


def _base_metrics():
    return {
        "duration": 10.0,
        "has_video": True,
        "has_audio": True,
        "width": 1920,
        "height": 1080,
        "size_bytes": 50000,
        "mean_volume": -14.0,
    }


def test_g2_opt_out_no_extra_checks(tmp_path):
    """Without max_* params, no slideshow probe runs and no G2 issues."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 50000)
    with (
        patch("movie_narrator.utils.deliverable_qa.probe_media", return_value=_base_metrics()),
        patch(
            "movie_narrator.utils.deliverable_qa.check_slideshow_risk",
            wraps=lambda *a, **k: SlideshowRisk(),  # must not be called
        ) as spy,
    ):
        report = evaluate_deliverable(str(f), expected_duration=10.0)
    spy.assert_not_called()
    assert report.ok is True


def test_g2_slideshow_issue_raised(tmp_path):
    """Risk above threshold → slideshow_degraded issue, report not ok."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 50000)
    high_risk = SlideshowRisk(risk=0.85, probed=True)
    with (
        patch("movie_narrator.utils.deliverable_qa.probe_media", return_value=_base_metrics()),
        patch(
            "movie_narrator.utils.deliverable_qa.check_slideshow_risk",
            return_value=high_risk,
        ),
    ):
        report = evaluate_deliverable(
            str(f),
            expected_duration=10.0,
            max_slideshow_risk=0.5,
        )
    assert any(i.code == "slideshow_degraded" for i in report.issues)
    assert report.ok is False
    assert report.metrics["slideshow_risk"] == 0.85


def test_g2_black_issue_raised(tmp_path):
    """Black ratio above threshold → excessive_black_frames issue."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 50000)
    high_black = SlideshowRisk(black_ratio=0.6, probed=True)
    with (
        patch("movie_narrator.utils.deliverable_qa.probe_media", return_value=_base_metrics()),
        patch(
            "movie_narrator.utils.deliverable_qa.check_slideshow_risk",
            return_value=high_black,
        ),
    ):
        report = evaluate_deliverable(
            str(f),
            expected_duration=10.0,
            max_black_ratio=0.3,
        )
    assert any(i.code == "excessive_black_frames" for i in report.issues)
    assert report.ok is False


def test_g2_under_threshold_passes(tmp_path):
    """Risk and black ratio under thresholds → no G2 issues."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 50000)
    low = SlideshowRisk(risk=0.1, black_ratio=0.05, probed=True)
    with (
        patch("movie_narrator.utils.deliverable_qa.probe_media", return_value=_base_metrics()),
        patch("movie_narrator.utils.deliverable_qa.check_slideshow_risk", return_value=low),
    ):
        report = evaluate_deliverable(
            str(f),
            expected_duration=10.0,
            max_slideshow_risk=0.5,
            max_black_ratio=0.3,
        )
    assert not any(i.code in ("slideshow_degraded", "excessive_black_frames") for i in report.issues)
    assert report.ok is True


def test_g2_unprobed_does_not_fail(tmp_path):
    """Probe unavailable (probed=False) → no G2 issues even if thresholds set."""
    f = tmp_path / "v.mp4"
    f.write_bytes(b"x" * 50000)
    unprobed = SlideshowRisk()  # probed=False
    with (
        patch("movie_narrator.utils.deliverable_qa.probe_media", return_value=_base_metrics()),
        patch("movie_narrator.utils.deliverable_qa.check_slideshow_risk", return_value=unprobed),
    ):
        report = evaluate_deliverable(
            str(f),
            expected_duration=10.0,
            max_slideshow_risk=0.5,
            max_black_ratio=0.3,
        )
    assert not any(i.code in ("slideshow_degraded", "excessive_black_frames") for i in report.issues)
    assert report.ok is True