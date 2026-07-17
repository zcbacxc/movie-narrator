"""Tests for pipeline/qa.py — validate_deliverable step gating."""

from unittest.mock import patch

import pytest

from movie_narrator.models import Context
from movie_narrator.pipeline.qa import validate_deliverable
from movie_narrator.utils.deliverable_qa import QAReport, QAIssue


def _ctx(tmp_path, **metadata):
    ctx = Context(movie_name="m", output_dir=str(tmp_path), audio_path=str(tmp_path / "a.mp3"))
    ctx.video_path = str(tmp_path / "final.mp4")
    ctx.metadata.update(metadata)
    return ctx


def test_qa_skipped_in_ci_by_default(tmp_path):
    """In CI with qa_enabled unset, the step is a no-op (skipped)."""
    ctx = _ctx(tmp_path)
    with patch("movie_narrator.pipeline.qa.is_ci", return_value=True):
        result = validate_deliverable(ctx)
    assert result is ctx
    assert "qa_report" not in ctx.metadata


def test_qa_runs_in_ci_when_explicitly_enabled(tmp_path):
    """qa_enabled=True forces QA even in CI."""
    ctx = _ctx(tmp_path, qa_enabled=True)
    report = QAReport(ok=True, issues=[], metrics={"duration": 10.0, "mean_volume": -14.0})
    with patch("movie_narrator.pipeline.qa.is_ci", return_value=True), \
         patch("movie_narrator.pipeline.qa.evaluate_deliverable", return_value=report):
        validate_deliverable(ctx)
    assert ctx.metadata["qa_report"]["ok"] is True


def test_qa_skipped_when_explicitly_disabled(tmp_path):
    """qa_enabled=False skips QA even outside CI."""
    ctx = _ctx(tmp_path, qa_enabled=False)
    with patch("movie_narrator.pipeline.qa.is_ci", return_value=False):
        result = validate_deliverable(ctx)
    assert result is ctx
    assert "qa_report" not in ctx.metadata


def test_qa_runs_locally_by_default(tmp_path):
    """Outside CI with qa_enabled unset, QA runs."""
    ctx = _ctx(tmp_path)
    report = QAReport(ok=True, issues=[], metrics={"duration": 10.0, "mean_volume": -14.0})
    with patch("movie_narrator.pipeline.qa.is_ci", return_value=False), \
         patch("movie_narrator.pipeline.qa.evaluate_deliverable", return_value=report):
        validate_deliverable(ctx)
    assert ctx.metadata["qa_report"]["ok"] is True


def test_qa_raises_on_failure(tmp_path):
    """A failing report raises RuntimeError with issue codes."""
    ctx = _ctx(tmp_path)
    report = QAReport(
        ok=False,
        issues=[QAIssue("silent_audio", "mean volume -60dB too low")],
        metrics={"duration": 10.0, "mean_volume": -60.0},
    )
    with patch("movie_narrator.pipeline.qa.is_ci", return_value=False), \
         patch("movie_narrator.pipeline.qa.evaluate_deliverable", return_value=report):
        with pytest.raises(RuntimeError, match="silent_audio"):
            validate_deliverable(ctx)


def test_qa_raises_when_no_video_path(tmp_path):
    """Missing video_path raises RuntimeError before probing."""
    ctx = _ctx(tmp_path)
    ctx.video_path = None
    with patch("movie_narrator.pipeline.qa.is_ci", return_value=False):
        with pytest.raises(RuntimeError, match="no video_path"):
            validate_deliverable(ctx)


def test_qa_stores_report_in_metadata(tmp_path):
    """The full report (issues + metrics) is stored on ctx.metadata."""
    ctx = _ctx(tmp_path)
    report = QAReport(
        ok=False,
        issues=[QAIssue("too_short", "5s < 85% of 10s")],
        metrics={"duration": 5.0, "mean_volume": -14.0},
    )
    with patch("movie_narrator.pipeline.qa.is_ci", return_value=False), \
         patch("movie_narrator.pipeline.qa.evaluate_deliverable", return_value=report):
        with pytest.raises(RuntimeError):
            validate_deliverable(ctx)
    stored = ctx.metadata["qa_report"]
    assert stored["ok"] is False
    assert stored["issues"][0]["code"] == "too_short"
    assert stored["metrics"]["duration"] == 5.0
