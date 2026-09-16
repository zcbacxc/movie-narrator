# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for M3 content quality (independent of quality_dashboard).

Covers:
* overall arithmetic mean (available subset; all unavailable → None)
* pacing unavailable on single segment
* hook from script_qa / script_judge
* schema keys present
* default disabled (pipeline STEPS count still 16)
* metadata key gate (check_metadata_keys.py)
* quality_dashboard schema not mutated
* race does not consume content_quality by default
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from movie_narrator.content_quality import (
    CONTENT_QUALITY_SCHEMA_VERSION,
    DIMENSION_NAMES,
    DimensionResult,
    apply_content_quality,
    arithmetic_mean_available,
    build_content_quality_dict,
    evaluate_content_quality,
    evaluate_coherence,
    evaluate_hook,
    evaluate_pacing,
    evaluate_semantic_visual_relevance,
)
from movie_narrator.models import Context, MetadataDict, TimedSegment
from movie_narrator.race import score_candidate
from movie_narrator.utils.quality_dashboard import (
    QualityDashboard,
    build_quality_dashboard,
)


# ── Fixtures ───────────────────────────────────────────────


def _seg(start: float, end: float, text: str = "") -> TimedSegment:
    return TimedSegment(text=text, start=start, end=end)


@pytest.fixture
def multi_segments() -> list[TimedSegment]:
    """Four timed segments with modest pauses and varied durations."""
    return [
        _seg(0.0, 2.0, "开场钩子：你敢信吗？"),
        _seg(2.2, 5.0, "主角刚出场就被围堵。"),
        _seg(5.3, 7.5, "他反手一拳打穿了墙。"),
        _seg(7.8, 12.0, "全城都记住了这个名字。"),
    ]


@pytest.fixture
def rich_metadata(multi_segments) -> dict:
    return {
        "script_qa": {"hook_strength": 8, "total_issues": 0},
        "duration_metrics": {"narration_sec": 12.0},
        "match_visual_features_samples": [
            {"scene_index": 0, "luma": 110.0, "hist_rgb": [0.2, 0.3, 0.1, 0.15, 0.1, 0.05, 0.05, 0.05]},
            {"scene_index": 1, "luma": 95.0, "hist_rgb": [0.15, 0.25, 0.2, 0.1, 0.1, 0.1, 0.05, 0.05]},
        ],
    }


@pytest.fixture
def rich_ctx(tmp_path, multi_segments, rich_metadata) -> Context:
    ctx = Context(movie_name="M", output_dir=str(tmp_path))
    ctx.timed_segments = multi_segments
    ctx.segments = [
        SimpleNamespace(text=s.text) for s in multi_segments
    ]  # type: ignore[assignment]
    ctx.metadata.update(rich_metadata)
    return ctx


# ── Schema / overall mean ──────────────────────────────────


class TestOverallMean:
    def test_arithmetic_mean_available_subset(self):
        assert arithmetic_mean_available([0.5, 1.0]) == pytest.approx(0.75)
        assert arithmetic_mean_available([0.2]) == pytest.approx(0.2)
        assert arithmetic_mean_available([0.0, 0.0, 1.0]) == pytest.approx(1.0 / 3.0)

    def test_empty_available_is_none(self):
        assert arithmetic_mean_available([]) is None

    def test_overall_excludes_unavailable(self):
        results = [
            DimensionResult("pacing", 0.8, "measured"),
            DimensionResult("coherence", None, "unavailable"),
            DimensionResult("hook", 0.6, "measured"),
            DimensionResult("semantic_visual_relevance", None, "unavailable"),
        ]
        d = build_content_quality_dict(results, provenance={"t": 1})
        assert d["overall"] == pytest.approx(0.7)
        assert d["dimension_status"]["coherence"] == "unavailable"
        assert d["dimensions"]["coherence"] is None

    def test_all_unavailable_overall_none(self):
        results = [
            DimensionResult(n, None, "unavailable") for n in DIMENSION_NAMES
        ]
        d = build_content_quality_dict(results, provenance={})
        assert d["overall"] is None

    def test_proxy_counts_as_available(self):
        results = [
            DimensionResult("pacing", 0.4, "measured"),
            DimensionResult("semantic_visual_relevance", 0.8, "proxy"),
            DimensionResult("coherence", None, "unavailable"),
            DimensionResult("hook", None, "unavailable"),
        ]
        d = build_content_quality_dict(results, provenance={})
        assert d["overall"] == pytest.approx(0.6)

    def test_error_not_in_denominator(self):
        results = [
            DimensionResult("pacing", 0.5, "measured"),
            DimensionResult("hook", None, "error"),
        ]
        d = build_content_quality_dict(results, provenance={})
        assert d["overall"] == pytest.approx(0.5)


class TestSchemaKeys:
    def test_frozen_top_level_keys(self, rich_ctx):
        result = evaluate_content_quality(rich_ctx)
        assert set(result.keys()) == {
            "schema_version",
            "dimensions",
            "dimension_status",
            "overall",
            "provenance",
        }
        assert result["schema_version"] == CONTENT_QUALITY_SCHEMA_VERSION

    def test_all_four_dimensions_present(self, rich_ctx):
        result = evaluate_content_quality(rich_ctx)
        for name in DIMENSION_NAMES:
            assert name in result["dimensions"]
            assert name in result["dimension_status"]

    def test_overall_is_mean_of_available(self, rich_ctx):
        result = evaluate_content_quality(rich_ctx)
        available = [
            result["dimensions"][n]
            for n in DIMENSION_NAMES
            if result["dimension_status"][n] in ("measured", "proxy")
            and result["dimensions"][n] is not None
        ]
        if available:
            assert result["overall"] == pytest.approx(sum(available) / len(available), abs=1e-3)


# ── Pacing ─────────────────────────────────────────────────


class TestPacing:
    def test_single_segment_unavailable(self):
        r = evaluate_pacing([_seg(0.0, 3.0, "only")], 3.0)
        assert r.status == "unavailable"
        assert r.score is None
        assert r.details["reason"] == "n_segments<2"

    def test_zero_duration_unavailable(self):
        segs = [_seg(0.0, 1.0), _seg(1.0, 2.0)]
        r = evaluate_pacing(segs, 0.0)
        assert r.status == "unavailable"

    def test_measured_with_fixture_bins(self, multi_segments):
        r = evaluate_pacing(multi_segments, 12.0, bins=5, lo=0.5, hi=8.0)
        assert r.status == "measured"
        assert r.score is not None
        assert 0.0 <= r.score <= 1.0
        assert r.details["bins"] == 5
        assert r.details["lo"] == 0.5
        assert r.details["hi"] == 8.0
        assert r.details["n_segments"] == 4
        # Neighbor gaps: 0.2, 0.3, 0.3 → density = 0.8/12
        assert r.details["pause_density"] == pytest.approx(0.8 / 12.0, abs=1e-3)
        assert 0.0 <= r.details["duration_entropy_h_norm"] <= 1.0

    def test_uniform_durations_low_entropy(self):
        segs = [_seg(i * 3.0, i * 3.0 + 2.0, f"s{i}") for i in range(6)]
        r = evaluate_pacing(segs, 18.0, bins=5, lo=0.5, hi=8.0)
        # All durations = 2.0 → single bin → H_norm ≈ 0
        assert r.details["duration_entropy_h_norm"] == pytest.approx(0.0, abs=1e-6)

    def test_zero_gaps_density_zero(self):
        segs = [_seg(0.0, 2.0), _seg(2.0, 4.0), _seg(4.0, 6.0)]
        r = evaluate_pacing(segs, 6.0)
        assert r.details["pause_density"] == pytest.approx(0.0)


# ── Coherence ──────────────────────────────────────────────


class TestCoherence:
    def test_precomputed_vectors_measured(self):
        # Three nearly identical unit vectors → high coherence.
        v = [1.0, 0.0, 0.0]
        r = evaluate_coherence(
            ["a", "b", "c"],
            precomputed_vectors=[v, v, v],
        )
        assert r.status == "measured"
        assert r.score == pytest.approx(1.0)
        assert r.details["window"] == 3
        assert "effective_model_id" in r.details
        assert "effective_revision" in r.details

    def test_orthogonal_vectors_low(self):
        r = evaluate_coherence(
            ["a", "b"],
            precomputed_vectors=[[1.0, 0.0], [0.0, 1.0]],
        )
        assert r.score == pytest.approx(0.0)

    def test_injectable_embedder(self):
        def emb(texts):
            # Deterministic: first char of each text selects a basis.
            out = []
            for t in texts:
                if "A" in t:
                    out.append([1.0, 0.0])
                else:
                    out.append([0.0, 1.0])
            return out

        r = evaluate_coherence(["A1", "A2", "A3"], embedder=emb)
        assert r.status == "measured"
        assert r.score == pytest.approx(1.0)
        assert r.details["source"] == "injected_embedder"

    def test_single_segment_unavailable(self):
        r = evaluate_coherence(["only one"])
        assert r.status == "unavailable"
        assert r.score is None

    def test_does_not_read_match_override(self, tmp_path):
        """Frozen identity — match embedding_model_name is irrelevant."""
        ctx = Context(movie_name="M", output_dir=str(tmp_path))
        ctx.metadata["embedding_model_name"] = "some-other-match-model"
        ctx.segments = [SimpleNamespace(text="一"), SimpleNamespace(text="二")]
        result = evaluate_content_quality(
            ctx, precomputed_vectors=[[1.0, 0.0], [1.0, 0.0]]
        )
        mid = result["provenance"]["coherence_effective_model_id"]
        assert "some-other-match-model" not in str(mid)
        assert "content-quality" in str(mid)

    def test_lexical_proxy_when_no_st(self, multi_segments):
        texts = [s.text for s in multi_segments]
        r = evaluate_coherence(texts)
        # Without sentence-transformers this should be proxy (CI-safe).
        assert r.status in ("proxy", "measured")
        assert r.score is not None


# ── Hook ───────────────────────────────────────────────────


class TestHook:
    def test_from_script_qa(self):
        r = evaluate_hook({"script_qa": {"hook_strength": 8}})
        assert r.status == "measured"
        assert r.score == pytest.approx(0.8)
        assert r.details["source"] == "script_qa"

    def test_from_script_judge_when_qa_missing(self):
        r = evaluate_hook({"script_judge": {"hook_strength": 5}})
        assert r.status == "measured"
        assert r.score == pytest.approx(0.5)
        assert r.details["source"] == "script_judge"

    def test_script_qa_wins_over_judge(self):
        r = evaluate_hook(
            {
                "script_qa": {"hook_strength": 10},
                "script_judge": {"hook_strength": 2},
            }
        )
        assert r.score == pytest.approx(1.0)
        assert r.details["source"] == "script_qa"

    def test_missing_unavailable(self):
        r = evaluate_hook({})
        assert r.status == "unavailable"
        assert r.score is None

    def test_clamp_out_of_range(self):
        r = evaluate_hook({"script_qa": {"hook_strength": 15}})
        assert r.score == pytest.approx(1.0)
        r2 = evaluate_hook({"script_qa": {"hook_strength": -3}})
        assert r2.score == pytest.approx(0.0)

    def test_independent_judge_fallback_only_when_missing(self):
        calls = []

        def judge(meta):
            calls.append(meta)
            return 7.0

        r = evaluate_hook({}, independent_judge=judge)
        assert r.score == pytest.approx(0.7)
        assert r.details["source"] == "independent_judge"
        assert len(calls) == 1

        calls.clear()
        r2 = evaluate_hook(
            {"script_qa": {"hook_strength": 9}}, independent_judge=judge
        )
        assert r2.score == pytest.approx(0.9)
        assert len(calls) == 0


# ── Semantic visual relevance ──────────────────────────────


class TestSemanticVisualRelevance:
    def test_proxy_from_luma_hist(self, rich_metadata):
        r = evaluate_semantic_visual_relevance(
            rich_metadata["match_visual_features_samples"]
        )
        assert r.status == "proxy"
        assert r.score is not None
        assert 0.0 <= r.score <= 1.0
        assert "motion" not in r.details.get("note", "").lower() or "no motion" in r.details["note"]
        assert "no motion" in r.details["note"]

    def test_empty_unavailable(self):
        assert evaluate_semantic_visual_relevance(None).status == "unavailable"
        assert evaluate_semantic_visual_relevance([]).status == "unavailable"

    def test_dark_frames_score_lower_than_mid(self):
        dark = [{"luma": 10.0, "hist_rgb": [1.0, 0, 0, 0, 0, 0, 0, 0]}]
        mid = [{"luma": 120.0, "hist_rgb": [1 / 8] * 8}]
        d = evaluate_semantic_visual_relevance(dark)
        m = evaluate_semantic_visual_relevance(mid)
        assert d.score < m.score


# ── Integration / defaults ─────────────────────────────────


class TestDefaultsAndGates:
    def test_steps_registry_still_16(self):
        from movie_narrator.pipeline.registry import step_registry

        assert len(step_registry.ordered_names()) == 16

    def test_content_quality_not_in_steps(self):
        from movie_narrator.pipeline.registry import step_registry

        assert "content_quality" not in step_registry.ordered_names()
        assert "evaluate_content_quality" not in step_registry.ordered_names()

    def test_apply_skipped_when_disabled(self, rich_ctx):
        assert "content_quality_enabled" not in rich_ctx.metadata or not rich_ctx.metadata.get(
            "content_quality_enabled"
        )
        out = apply_content_quality(rich_ctx)
        assert out == {}
        assert "content_quality" not in rich_ctx.metadata

    def test_apply_writes_when_enabled(self, rich_ctx):
        rich_ctx.metadata["content_quality_enabled"] = True
        out = apply_content_quality(rich_ctx)
        assert out
        assert "content_quality" in rich_ctx.metadata
        assert rich_ctx.metadata["content_quality"]["schema_version"] == (
            CONTENT_QUALITY_SCHEMA_VERSION
        )

    def test_apply_force_ignores_flag(self, rich_ctx):
        out = apply_content_quality(rich_ctx, force=True)
        assert out
        assert rich_ctx.metadata["content_quality"]["overall"] is not None

    def test_job_params_defaults_off(self):
        from movie_narrator.workflow.schema import JobParams

        p = JobParams()
        assert p.content_quality_enabled is None
        assert p.content_quality_pacing_bins is None

    def test_job_params_accept_fields(self):
        from movie_narrator.workflow.schema import JobParams

        p = JobParams(
            content_quality_enabled=True,
            content_quality_pacing_bins=4,
            content_quality_pacing_lo=0.4,
            content_quality_pacing_hi=6.0,
        )
        assert p.content_quality_enabled is True
        assert p.content_quality_pacing_bins == 4

    def test_metadata_dict_declares_key(self):
        # Static analysis type; presence is enough for the checker.
        assert "content_quality" in MetadataDict.__annotations__

    def test_metadata_keys_gate(self):
        import subprocess
        import sys
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent
        proc = subprocess.run(
            [sys.executable, str(root / "scripts" / "check_metadata_keys.py")],
            capture_output=True,
            text=True,
            cwd=str(root),
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr


class TestQualityDashboardUntouched:
    def test_dashboard_shape_unchanged(self, rich_ctx):
        # Seed a couple of dashboard inputs and rebuild — must still expose
        # the historical 8-dim engineering surface.
        rich_ctx.metadata["script_qa"] = {"total_issues": 0}
        dash = build_quality_dashboard(dict(rich_ctx.metadata))
        assert isinstance(dash, QualityDashboard)
        d = dash.to_dict()
        assert "overall_score" in d
        assert "dimensions" in d
        # content_quality must not leak into the dashboard schema.
        assert "content_quality" not in d

    def test_content_quality_does_not_write_dashboard_key(self, rich_ctx):
        rich_ctx.metadata["content_quality_enabled"] = True
        apply_content_quality(rich_ctx)
        # Dashboard key remains absent unless the QA step itself wrote it.
        assert "quality_dashboard" not in rich_ctx.metadata or isinstance(
            rich_ctx.metadata.get("quality_dashboard"), dict
        )
        # The content_quality payload is independent.
        cq = rich_ctx.metadata["content_quality"]
        assert "overall_score" not in cq  # dashboard field name
        assert "overall" in cq


class TestRaceConsumption:
    def _base_meta(self) -> dict:
        return {
            "match_summary": {
                "segments": 4,
                "embedding_ratio": 1.0,
                "score": {"avg": 0.8},
                "diversity": {"swaps": 1},
            },
            "duration_metrics": {"ratio": 1.0},
            "footage_coverage": {"segments_with_footage": 4, "total_segments": 4},
        }

    def test_default_score_ignores_content_quality(self):
        meta = self._base_meta()
        baseline, _ = score_candidate(meta)
        meta["content_quality"] = {
            "schema_version": "1",
            "dimensions": {"pacing": 0.0},
            "dimension_status": {"pacing": "measured"},
            "overall": 0.0,
            "provenance": {},
        }
        after, breakdown = score_candidate(meta)
        assert after == baseline
        assert "content_quality" not in breakdown

    def test_opt_in_blends_content_quality(self):
        meta = self._base_meta()
        baseline, _ = score_candidate(meta)
        meta["content_quality"] = {
            "schema_version": "1",
            "dimensions": {},
            "dimension_status": {},
            "overall": 0.5,
            "provenance": {},
        }
        blended, breakdown = score_candidate(meta, use_content_quality=True)
        assert "content_quality" in breakdown
        assert breakdown["content_quality"] == pytest.approx(0.5)
        expected = baseline * 0.9 + 50.0 * 0.1
        assert blended == pytest.approx(expected, abs=0.02)

    def test_candidate_result_field_defaults_none(self):
        from pathlib import Path

        from movie_narrator.race import CandidateConfig, CandidateResult

        r = CandidateResult(
            config=CandidateConfig("x", "douyin-fast", 5, 0.15, 3),
            output_dir=Path("out"),
        )
        assert r.content_quality is None
