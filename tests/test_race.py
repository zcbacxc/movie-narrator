"""Tests for multi-candidate horse race.

Covers candidate generation, scoring formula, report formatting,
and race orchestration with mocked pipeline.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.race import (
    CandidateConfig,
    CandidateResult,
    generate_candidates,
    score_candidate,
    format_race_report,
    save_race_report,
    run_race,
)


# ── Candidate generation ──────────────────────────────────


class TestGenerateCandidates:
    def test_default_three_candidates(self):
        """Default generates 3 candidates with distinct labels."""
        cands = generate_candidates()
        assert len(cands) == 3
        labels = [c.label for c in cands]
        assert labels == ["aggressive", "balanced", "conservative"]

    def test_custom_count(self):
        """n=2 generates 2 candidates."""
        cands = generate_candidates(n=2)
        assert len(cands) == 2

    def test_n_clamped_to_min(self):
        """n=0 or negative is clamped to 1."""
        cands = generate_candidates(n=0)
        assert len(cands) == 1

    def test_n_clamped_to_max(self):
        """n>6 is clamped to 6."""
        cands = generate_candidates(n=10)
        assert len(cands) == 3  # only 3 default candidates exist

    def test_custom_presets(self):
        """Custom presets generate candidates with those preset names."""
        cands = generate_candidates(
            n=2,
            presets=["douyin-fast", "mainstream-dry"],
        )
        assert len(cands) == 2
        assert cands[0].narration_preset == "douyin-fast"
        assert cands[1].narration_preset == "mainstream-dry"

    def test_custom_presets_labels(self):
        """Custom presets get numbered labels."""
        cands = generate_candidates(
            n=3,
            presets=["douyin-fast", "mainstream-dry", "bilibili-long"],
        )
        assert cands[0].label == "candidate-1"
        assert cands[1].label == "candidate-2"
        assert cands[2].label == "candidate-3"

    def test_default_candidates_have_distinct_params(self):
        """Each default candidate has a unique parameter combination."""
        cands = generate_candidates()
        combos = [
            (c.match_topk, c.match_topk_reuse_penalty, c.match_diversity_window)
            for c in cands
        ]
        assert len(set(combos)) == len(combos)

    def test_to_params_returns_dict(self):
        """to_params() returns a dict with match parameters."""
        cand = CandidateConfig(
            label="test",
            narration_preset="douyin-fast",
            match_topk=5,
            match_topk_reuse_penalty=0.15,
            match_diversity_window=3,
        )
        params = cand.to_params()
        assert params["match_topk"] == 5
        assert params["match_topk_reuse_penalty"] == 0.15
        assert params["match_diversity_window"] == 3

    def test_to_params_includes_extra_params(self):
        """to_params() merges extra_params into the output."""
        cand = CandidateConfig(
            label="test",
            narration_preset="douyin-fast",
            match_topk=5,
            match_topk_reuse_penalty=0.15,
            match_diversity_window=3,
            extra_params={"match_min_score": 0.3},
        )
        params = cand.to_params()
        assert params["match_min_score"] == 0.3


# ── Scoring ───────────────────────────────────────────────


class TestScoreCandidate:
    def test_empty_metadata_returns_zero(self):
        """Empty metadata produces a zero score."""
        score, breakdown = score_candidate({})
        assert score == 0.0
        assert breakdown["match_quality"] == 0.0

    def test_perfect_match_quality(self):
        """100% embedding ratio with avg_score=1.0 gives max match_quality."""
        metadata = {
            "match_summary": {
                "segments": 10,
                "embedding_ratio": 1.0,
                "score": {"avg": 1.0},
                "diversity": {"swaps": 2},
            },
            "duration_metrics": {
                "ratio": 1.0,
            },
        }
        score, breakdown = score_candidate(metadata)
        assert breakdown["match_quality"] == 1.0
        assert breakdown["duration_fit"] == 1.0
        assert score > 60.0  # at least match_quality * 40 + duration_fit * 25 = 65

    def test_all_heuristic_match(self):
        """0% embedding gives zero match_quality."""
        metadata = {
            "match_summary": {
                "segments": 10,
                "embedding_ratio": 0.0,
                "score": {"avg": 0.0},
                "diversity": {"swaps": 0},
            },
            "duration_metrics": {"ratio": 1.0},
        }
        score, breakdown = score_candidate(metadata)
        assert breakdown["match_quality"] == 0.0
        # scene_coverage falls back to embedding_ratio = 0
        assert breakdown["scene_coverage"] == 0.0

    def test_duration_fit_ideal(self):
        """ratio=1.0 gives duration_fit=1.0."""
        metadata = {
            "match_summary": {"segments": 10, "diversity": {"swaps": 2}},
            "duration_metrics": {"ratio": 1.0},
        }
        _, breakdown = score_candidate(metadata)
        assert breakdown["duration_fit"] == 1.0

    def test_duration_fit_off_target(self):
        """ratio=1.5 gives duration_fit=0.5."""
        metadata = {
            "match_summary": {"segments": 10, "diversity": {"swaps": 2}},
            "duration_metrics": {"ratio": 1.5},
        }
        _, breakdown = score_candidate(metadata)
        assert breakdown["duration_fit"] == 0.5

    def test_duration_fit_far_off(self):
        """ratio=2.0 gives duration_fit=0.0 (clamped)."""
        metadata = {
            "match_summary": {"segments": 10, "diversity": {"swaps": 2}},
            "duration_metrics": {"ratio": 2.0},
        }
        _, breakdown = score_candidate(metadata)
        assert breakdown["duration_fit"] == 0.0

    def test_diversity_ideal_zone(self):
        """swap_rate in [0.05, 0.30] gives diversity=1.0."""
        # 2 swaps / 10 segments = 0.2 (ideal zone)
        metadata = {
            "match_summary": {"segments": 10, "diversity": {"swaps": 2}},
            "duration_metrics": {"ratio": 1.0},
        }
        _, breakdown = score_candidate(metadata)
        assert breakdown["diversity"] == 1.0

    def test_diversity_too_low(self):
        """swap_rate < 0.05 gives reduced diversity."""
        # 0 swaps / 10 segments = 0.0
        metadata = {
            "match_summary": {"segments": 10, "diversity": {"swaps": 0}},
            "duration_metrics": {"ratio": 1.0},
        }
        _, breakdown = score_candidate(metadata)
        assert breakdown["diversity"] == 0.0

    def test_diversity_too_high(self):
        """swap_rate > 0.30 gives reduced diversity."""
        # 5 swaps / 10 segments = 0.5 (> 0.30)
        metadata = {
            "match_summary": {"segments": 10, "diversity": {"swaps": 5}},
            "duration_metrics": {"ratio": 1.0},
        }
        _, breakdown = score_candidate(metadata)
        assert breakdown["diversity"] < 1.0

    def test_scene_coverage_from_footage(self):
        """footage_coverage dict is used for scene_coverage."""
        metadata = {
            "match_summary": {"segments": 10, "diversity": {"swaps": 2}},
            "duration_metrics": {"ratio": 1.0},
            "footage_coverage": {
                "segments_with_footage": 8,
                "total_segments": 10,
            },
        }
        _, breakdown = score_candidate(metadata)
        assert breakdown["scene_coverage"] == 0.8

    def test_score_range_0_to_100(self):
        """Score is always in [0, 100]."""
        # Very bad
        score_bad, _ = score_candidate({})
        assert 0.0 <= score_bad <= 100.0

        # Very good
        metadata = {
            "match_summary": {
                "segments": 10,
                "embedding_ratio": 1.0,
                "score": {"avg": 0.95},
                "diversity": {"swaps": 2},
            },
            "duration_metrics": {"ratio": 1.0},
            "footage_coverage": {
                "segments_with_footage": 10,
                "total_segments": 10,
            },
        }
        score_good, _ = score_candidate(metadata)
        assert 0.0 <= score_good <= 100.0
        assert score_good > score_bad

    def test_score_is_deterministic(self):
        """Same metadata always produces the same score."""
        metadata = {
            "match_summary": {
                "segments": 15,
                "embedding_ratio": 0.8,
                "score": {"avg": 0.6},
                "diversity": {"swaps": 3},
            },
            "duration_metrics": {"ratio": 0.95},
        }
        s1, b1 = score_candidate(metadata)
        s2, b2 = score_candidate(metadata)
        assert s1 == s2
        assert b1 == b2


# ── Report formatting ─────────────────────────────────────


class TestFormatRaceReport:
    def test_empty_results(self):
        """Empty results list produces a message."""
        report = format_race_report([])
        assert "No candidates" in report

    def test_report_has_header(self):
        """Report contains the race header."""
        results = [
            CandidateResult(
                config=CandidateConfig(
                    label="test",
                    narration_preset="douyin-fast",
                    match_topk=5,
                    match_topk_reuse_penalty=0.15,
                    match_diversity_window=3,
                ),
                output_dir=Path("/tmp/test"),
                score=75.0,
                score_breakdown={
                    "match_quality": 0.8,
                    "duration_fit": 0.9,
                    "diversity": 1.0,
                    "scene_coverage": 0.7,
                },
            )
        ]
        report = format_race_report(results)
        assert "Multi-Candidate Race Results" in report

    def test_report_shows_scores(self):
        """Report displays the score for each candidate."""
        results = [
            CandidateResult(
                config=CandidateConfig(
                    label="winner",
                    narration_preset="douyin-fast",
                    match_topk=5,
                    match_topk_reuse_penalty=0.15,
                    match_diversity_window=3,
                ),
                output_dir=Path("/tmp/test"),
                score=85.5,
                score_breakdown={
                    "match_quality": 0.9,
                    "duration_fit": 0.95,
                    "diversity": 1.0,
                    "scene_coverage": 0.8,
                },
            )
        ]
        report = format_race_report(results)
        assert "85.5" in report
        assert "winner" in report

    def test_report_marks_best_with_star(self):
        """First (best) candidate is marked with *."""
        results = [
            CandidateResult(
                config=CandidateConfig(
                    label="best",
                    narration_preset="douyin-fast",
                    match_topk=5,
                    match_topk_reuse_penalty=0.15,
                    match_diversity_window=3,
                ),
                output_dir=Path("/tmp/best"),
                score=90.0,
                score_breakdown={
                    "match_quality": 0.9,
                    "duration_fit": 1.0,
                    "diversity": 1.0,
                    "scene_coverage": 0.9,
                },
            ),
            CandidateResult(
                config=CandidateConfig(
                    label="worst",
                    narration_preset="bilibili-long",
                    match_topk=3,
                    match_topk_reuse_penalty=0.10,
                    match_diversity_window=2,
                ),
                output_dir=Path("/tmp/worst"),
                score=50.0,
                score_breakdown={
                    "match_quality": 0.5,
                    "duration_fit": 0.7,
                    "diversity": 0.5,
                    "scene_coverage": 0.4,
                },
            ),
        ]
        report = format_race_report(results)
        assert "*" in report
        assert "best" in report

    def test_report_shows_error_for_failed(self):
        """Failed candidates show error status."""
        results = [
            CandidateResult(
                config=CandidateConfig(
                    label="failed",
                    narration_preset="douyin-fast",
                    match_topk=5,
                    match_topk_reuse_penalty=0.15,
                    match_diversity_window=3,
                ),
                output_dir=Path("/tmp/failed"),
                score=0.0,
                error="preflight: no video",
            )
        ]
        report = format_race_report(results)
        assert "ERR" in report

    def test_report_shows_winner_details(self):
        """Report includes detailed breakdown for the winner."""
        results = [
            CandidateResult(
                config=CandidateConfig(
                    label="winner",
                    narration_preset="mainstream-dry",
                    match_topk=5,
                    match_topk_reuse_penalty=0.15,
                    match_diversity_window=3,
                ),
                output_dir=Path("/tmp/winner"),
                score=88.0,
                video_path="/tmp/winner/output.mp4",
                match_summary={
                    "segments": 18,
                    "embedding_ratio": 0.9,
                    "heuristic_ratio": 0.1,
                    "score": {"avg": 0.75},
                },
                duration_metrics={
                    "actual_sec": 59.5,
                    "target_sec": 60,
                    "ratio": 0.99,
                },
                score_breakdown={
                    "match_quality": 0.9,
                    "duration_fit": 0.99,
                    "diversity": 1.0,
                    "scene_coverage": 0.85,
                },
            )
        ]
        report = format_race_report(results)
        assert "Winner" in report
        assert "mainstream-dry" in report
        assert "59.5" in report


# ── JSON report ───────────────────────────────────────────


class TestSaveRaceReport:
    def test_report_saved_as_json(self, tmp_path):
        """save_race_report writes valid JSON."""
        results = [
            CandidateResult(
                config=CandidateConfig(
                    label="test",
                    narration_preset="douyin-fast",
                    match_topk=5,
                    match_topk_reuse_penalty=0.15,
                    match_diversity_window=3,
                ),
                output_dir=tmp_path / "candidate-1-test",
                score=75.0,
                score_breakdown={
                    "match_quality": 0.8,
                    "duration_fit": 0.9,
                    "diversity": 1.0,
                    "scene_coverage": 0.7,
                },
            )
        ]
        report_path = tmp_path / "race_report.json"
        save_race_report(results, report_path)

        assert report_path.exists()
        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert len(data["candidates"]) == 1
        assert data["candidates"][0]["label"] == "test"
        assert data["candidates"][0]["score"] == 75.0

    def test_report_includes_rank(self, tmp_path):
        """Each candidate in the JSON report has a rank field."""
        results = [
            CandidateResult(
                config=CandidateConfig(
                    label="first",
                    narration_preset="douyin-fast",
                    match_topk=5,
                    match_topk_reuse_penalty=0.15,
                    match_diversity_window=3,
                ),
                output_dir=tmp_path / "c1",
                score=90.0,
            ),
            CandidateResult(
                config=CandidateConfig(
                    label="second",
                    narration_preset="mainstream-dry",
                    match_topk=3,
                    match_topk_reuse_penalty=0.10,
                    match_diversity_window=2,
                ),
                output_dir=tmp_path / "c2",
                score=70.0,
            ),
        ]
        report_path = tmp_path / "race_report.json"
        save_race_report(results, report_path)

        data = json.loads(report_path.read_text(encoding="utf-8"))
        assert data["candidates"][0]["rank"] == 1
        assert data["candidates"][1]["rank"] == 2


# ── Race orchestration (mocked) ───────────────────────────


class TestRunRace:
    """Tests for run_race with mocked build_context and run_pipeline."""

    @patch("movie_narrator.pipeline.runner.run_pipeline")
    @patch("movie_narrator.pipeline.runner.build_context")
    def test_runs_all_candidates(self, mock_build, mock_run, tmp_path):
        """run_race calls build_context + run_pipeline for each candidate."""
        from movie_narrator.models import Context, Services
        from movie_narrator.utils.console import SilentConsole

        def fake_ctx(cand_dir, preset):
            ctx = Context(
                movie_name="test",
                output_dir=str(cand_dir),
                source_video_path="/fake/video.mp4",
            )
            ctx.services = Services(console=SilentConsole())
            ctx.metadata["match_summary"] = {
                "segments": 10,
                "embedding_ratio": 0.8,
                "score": {"avg": 0.6},
                "diversity": {"swaps": 2},
            }
            ctx.metadata["duration_metrics"] = {"ratio": 0.95}
            ctx.video_path = "/fake/output.mp4"
            return ctx

        candidates = generate_candidates(n=2)

        # Wire up the mock to return different contexts per call
        call_count = [0]

        def mock_build_context(**kwargs):
            call_count[0] += 1
            ctx = fake_ctx(kwargs["output_dir"], kwargs.get("narration_preset"))
            return ctx

        mock_build.side_effect = mock_build_context
        mock_run.side_effect = lambda ctx, **kw: ctx

        results = run_race(
            candidates,
            movie="test",
            style="test",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
        )

        assert len(results) == 2
        assert mock_build.call_count == 2
        assert mock_run.call_count == 2
        # Results should be sorted by score descending
        assert results[0].score >= results[1].score

    @patch("movie_narrator.pipeline.runner.run_pipeline")
    @patch("movie_narrator.pipeline.runner.build_context")
    def test_failed_candidate_recorded(self, mock_build, mock_run, tmp_path):
        """A candidate that raises an exception is recorded with error."""
        from movie_narrator.models import Context, Services
        from movie_narrator.utils.console import SilentConsole

        candidates = generate_candidates(n=2)

        call_count = [0]

        def mock_build_context(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("simulated failure")
            ctx = Context(
                movie_name="test",
                output_dir=str(kwargs["output_dir"]),
            )
            ctx.services = Services(console=SilentConsole())
            ctx.metadata["match_summary"] = {
                "segments": 10,
                "embedding_ratio": 0.8,
                "score": {"avg": 0.6},
                "diversity": {"swaps": 2},
            }
            ctx.metadata["duration_metrics"] = {"ratio": 1.0}
            ctx.video_path = "/fake/output.mp4"
            return ctx

        mock_build.side_effect = mock_build_context
        mock_run.side_effect = lambda ctx, **kw: ctx

        results = run_race(
            candidates,
            movie="test",
            style="test",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
        )

        assert len(results) == 2
        # One should have an error
        errors = [r for r in results if r.error is not None]
        assert len(errors) == 1
        # The successful one should be ranked first
        assert results[0].error is None
        assert results[1].error is not None

    @patch("movie_narrator.pipeline.runner.run_pipeline")
    @patch("movie_narrator.pipeline.runner.build_context")
    def test_auto_pick_copies_best_video(self, mock_build, mock_run, tmp_path):
        """auto_pick=True copies the best candidate's video to output_base."""
        from movie_narrator.models import Context, Services
        from movie_narrator.utils.console import SilentConsole

        # Create a fake video file
        fake_video = tmp_path / "candidate-1-test" / "output.mp4"
        fake_video.parent.mkdir(parents=True, exist_ok=True)
        fake_video.write_bytes(b"fake video content")

        candidates = [CandidateConfig(
            label="test",
            narration_preset="douyin-fast",
            match_topk=5,
            match_topk_reuse_penalty=0.15,
            match_diversity_window=3,
        )]

        def mock_build_context(**kwargs):
            ctx = Context(
                movie_name="test",
                output_dir=str(kwargs["output_dir"]),
            )
            ctx.services = Services(console=SilentConsole())
            ctx.metadata["match_summary"] = {
                "segments": 10,
                "embedding_ratio": 0.9,
                "score": {"avg": 0.8},
                "diversity": {"swaps": 2},
            }
            ctx.metadata["duration_metrics"] = {"ratio": 1.0}
            ctx.video_path = str(fake_video)
            return ctx

        mock_build.side_effect = mock_build_context
        mock_run.side_effect = lambda ctx, **kw: ctx

        results = run_race(
            candidates,
            movie="test",
            style="test",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            auto_pick=True,
        )

        # The promoted video should exist in output_base
        promoted = tmp_path / "output.mp4"
        assert promoted.exists()
        assert results[0].video_path == str(fake_video)

    @patch("movie_narrator.pipeline.runner.run_pipeline")
    @patch("movie_narrator.pipeline.runner.build_context")
    def test_results_sorted_by_score(self, mock_build, mock_run, tmp_path):
        """Results are sorted by score descending."""
        from movie_narrator.models import Context, Services
        from movie_narrator.utils.console import SilentConsole

        candidates = generate_candidates(n=3)

        def mock_build_context(**kwargs):
            ctx = Context(
                movie_name="test",
                output_dir=str(kwargs["output_dir"]),
            )
            ctx.services = Services(console=SilentConsole())
            # Vary the score by preset name
            preset = kwargs.get("narration_preset", "")
            if preset == "douyin-fast":
                ctx.metadata["match_summary"] = {
                    "segments": 10, "embedding_ratio": 0.9,
                    "score": {"avg": 0.8}, "diversity": {"swaps": 2},
                }
            elif preset == "mainstream-dry":
                ctx.metadata["match_summary"] = {
                    "segments": 10, "embedding_ratio": 0.7,
                    "score": {"avg": 0.5}, "diversity": {"swaps": 2},
                }
            else:
                ctx.metadata["match_summary"] = {
                    "segments": 10, "embedding_ratio": 0.5,
                    "score": {"avg": 0.3}, "diversity": {"swaps": 2},
                }
            ctx.metadata["duration_metrics"] = {"ratio": 1.0}
            ctx.video_path = None
            return ctx

        mock_build.side_effect = mock_build_context
        mock_run.side_effect = lambda ctx, **kw: ctx

        results = run_race(
            candidates,
            movie="test",
            style="test",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
        )

        scores = [r.score for r in results]
        assert scores == sorted(scores, reverse=True)
