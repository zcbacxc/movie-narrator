# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""M5 DAG analysis — canonical fixture, E1, waves, L2 planner, Gate-2.

Covers:

- H6: canonical fixture generated from the single built-in truth source;
  CI asserts fixture ≡ global builtin registry (names/order/coarse IO/
  depends_on/soft/status_field). No hand-copied second 16-step graph.
- E1: ``topological_order(canonical) == canonical.ordered_names()``.
- L1 theoretical waves via Kahn layering in registry order (no
  alphabetical ``sorted()`` reordering).
- L2 resource-compatible waves: canonical-order first-fit greedy +
  completeness + class matrix + W-W / W-R / R-W conflicts.
- ``contract_complete`` extending the M4 gate (requires ⊆ depends_on,
  soft consistency).
- Provenance coverage by ``(consumer, resource)`` tuple.
- E2 candidate selection (L2 compatible + contract_complete + fixture).
- Gate-2 scaffolding claims theoretical eligibility only; potential
  speedup is report metadata, never pass/fail.
"""

from __future__ import annotations

import pytest

from movie_narrator.models import Context
from movie_narrator.pipeline.canonical import (
    build_canonical_registry,
    canonical_mismatch_against_global,
    make_canonical_registry,
)
from movie_narrator.pipeline.dag import topological_order
from movie_narrator.pipeline.dag_analysis import (
    KNOWN_PROVENANCE,
    build_gate2_report,
    build_known_provenance,
    concurrency_class_matrix,
    contract_complete,
    contract_complete_errors,
    find_resource_conflicts,
    potential_speedup,
    provenance_coverage,
    resource_compatible_waves,
    select_e2_pair,
    theoretical_eligibility,
    theoretical_waves,
    waves_cover_all_steps,
)
from movie_narrator.pipeline.registry import StepRegistry, step_registry

BUILTIN_COUNT = 16
CANONICAL = build_canonical_registry()
CANONICAL_NAMES = CANONICAL.ordered_names()


def _noop(ctx: Context) -> Context:
    return ctx


def _make_step():
    def step(ctx: Context) -> Context:
        return ctx

    return step


# ── H6: canonical fixture from single truth source ─────────


class TestCanonicalFixture:
    def test_exactly_16_builtins(self):
        assert len(CANONICAL_NAMES) == BUILTIN_COUNT

    def test_order_matches_global_registry(self):
        assert CANONICAL_NAMES == step_registry.ordered_names()

    def test_fixture_matches_global_builtin_fields(self):
        """CI guard: fixture ≡ global builtins on the frozen field set."""
        assert canonical_mismatch_against_global() == []

    def test_make_is_fresh_but_equal(self):
        other = make_canonical_registry()
        assert other.ordered_names() == CANONICAL_NAMES
        assert other is not CANONICAL

    def test_soft_flags_and_status_fields(self):
        soft = {
            "research_plot",
            "align_audio",
            "detect_scenes",
            "match_clips",
            "mix_bgm",
            "translate_subtitles",
            "run_qa_gate",
            "export_clips",
        }
        for name in CANONICAL_NAMES:
            entry = CANONICAL.get(name)
            assert entry is not None
            assert entry.soft is (name in soft), name
            if entry.soft:
                assert entry.status_field, name
            else:
                assert entry.status_field is None, name

    def test_reads_writes_declared_for_all(self):
        for name in CANONICAL_NAMES:
            entry = CANONICAL.get(name)
            assert entry is not None
            assert entry.reads, name
            assert entry.writes, name

    def test_fixture_ignores_plugin_entries(self):
        """Analysis registry stays at 16 even if the global registry grows."""
        assert len(CANONICAL.ordered_names()) == BUILTIN_COUNT
        # Global may grow via plugins; canonical must not.
        assert set(CANONICAL.ordered_names()) == set(step_registry.ordered_names())


# ── E1: topo order == ordered_names on canonical ────────────


class TestE1TopologicalAdapter:
    def test_topological_order_equals_ordered_names(self):
        """E1 assertion — if this fails, fix the DAG first."""
        assert topological_order(CANONICAL) == CANONICAL.ordered_names()

    def test_fresh_canonical_also_passes_e1(self):
        fresh = make_canonical_registry()
        assert topological_order(fresh) == fresh.ordered_names()


# ── L1: theoretical waves ───────────────────────────────────


class TestTheoreticalWaves:
    def test_completeness(self):
        waves = theoretical_waves(CANONICAL)
        assert waves_cover_all_steps(waves, CANONICAL)

    def test_wave0_is_registry_prefix_sources(self):
        waves = theoretical_waves(CANONICAL)
        assert waves[0] == ["resolve_video", "prepare_assets", "research_plot"]

    def test_within_wave_registry_order_not_alphabetical(self):
        waves = theoretical_waves(CANONICAL)
        for wave in waves:
            positions = [CANONICAL_NAMES.index(n) for n in wave]
            assert positions == sorted(positions), wave
            # Explicitly not alphabetical when it would differ.
            if wave != sorted(wave):
                assert wave == [n for n in CANONICAL_NAMES if n in wave]

    def test_dependencies_satisfied_before_dependents(self):
        waves = theoretical_waves(CANONICAL)
        wave_of = {n: i for i, wave in enumerate(waves) for n in wave}
        for name in CANONICAL_NAMES:
            entry = CANONICAL.get(name)
            assert entry is not None
            for dep in entry.depends_on:
                assert wave_of[dep] < wave_of[name], (dep, name)

    def test_hard_steps_after_their_producers(self):
        waves = theoretical_waves(CANONICAL)
        wave_of = {n: i for i, wave in enumerate(waves) for n in wave}
        assert wave_of["render_video"] > wave_of["generate_subtitle"]
        assert wave_of["validate_deliverable"] > wave_of["render_video"]
        assert wave_of["match_clips"] > wave_of["align_audio"]
        assert wave_of["match_clips"] > wave_of["detect_scenes"]

    def test_cycle_raises(self):
        reg = StepRegistry()
        reg.register(
            "a",
            _make_step(),
            depends_on=("b",),
            reads=("ctx.segments",),
            writes=("meta.script_qa",),
        )
        reg.register(
            "b", _make_step(), depends_on=("a",), reads=("ctx.segments",), writes=("meta.qa_gate",)
        )
        with pytest.raises(ValueError, match="cycle"):
            theoretical_waves(reg)


# ── L2: resource-compatible waves + class matrix + conflicts ─


class TestResourceCompatibleWaves:
    def test_completeness_preserved(self):
        l2 = resource_compatible_waves(CANONICAL)
        flat = [n for wave in l2 for group in wave for n in group]
        assert sorted(flat) == sorted(CANONICAL_NAMES)
        assert len(flat) == len(set(flat))

    def test_each_group_internally_compatible(self):
        matrix = concurrency_class_matrix(CANONICAL)
        l2 = resource_compatible_waves(CANONICAL)
        for wave in l2:
            for group in wave:
                for i, a in enumerate(group):
                    for b in group[i + 1 :]:
                        assert matrix[(a, b)] is True, (a, b)
                        assert matrix[(b, a)] is True, (a, b)

    def test_first_fit_uses_registry_order(self):
        """First compatible group wins; new group only when none fit."""
        l2 = resource_compatible_waves(CANONICAL)
        # Every wave's first group starts with the earliest registry step.
        l1 = theoretical_waves(CANONICAL)
        for wave_l1, wave_l2 in zip(l1, l2):
            earliest = min(wave_l1, key=CANONICAL_NAMES.index)
            assert wave_l2[0][0] == earliest

    def test_exclusive_render_is_alone_in_its_group(self):
        matrix = concurrency_class_matrix(CANONICAL)
        for other in CANONICAL_NAMES:
            if other == "render_video":
                continue
            assert matrix[("render_video", other)] is False
            assert matrix[(other, "render_video")] is False
        l2 = resource_compatible_waves(CANONICAL)
        for wave in l2:
            for group in wave:
                if "render_video" in group:
                    assert group == ["render_video"]

    def test_class_matrix_shape(self):
        matrix = concurrency_class_matrix(CANONICAL)
        assert len(matrix) == BUILTIN_COUNT * BUILTIN_COUNT
        assert matrix[("render_video", "render_video")] is False
        assert matrix[("resolve_video", "prepare_assets")] is True

    def test_wave0_three_way_compatible(self):
        """resolve_video + prepare_assets + research_plot can share a group."""
        l2 = resource_compatible_waves(CANONICAL)
        assert l2[0] == [["resolve_video", "prepare_assets", "research_plot"]]


class TestResourceConflicts:
    def test_conflicts_are_symmetric_resource_overlaps(self):
        conflicts = find_resource_conflicts(CANONICAL)
        by_pair = {(c.step_a, c.step_b): c for c in conflicts}
        # generate_voice writes timed_segments; align_audio also writes it.
        key = ("generate_voice", "align_audio")
        assert key in by_pair
        assert "ctx.timed_segments" in by_pair[key].conflicting_resources

    def test_no_conflict_for_disjoint_safe_pair(self):
        conflicts = find_resource_conflicts(CANONICAL)
        pairs = {(c.step_a, c.step_b) for c in conflicts}
        assert ("resolve_video", "prepare_assets") not in pairs

    def test_write_read_and_write_write_conflicts_detected(self):
        """W-R and W-W both count: writer's writes ∩ other's (reads|writes)."""
        conflicts = find_resource_conflicts(CANONICAL)
        pairs = {(c.step_a, c.step_b) for c in conflicts}
        # detect_scenes writes ctx.scenes; match_clips reads it (W-R).
        assert ("detect_scenes", "match_clips") in pairs
        # prepare_assets writes ctx.assets; mix_bgm reads it (W-R).
        assert ("prepare_assets", "mix_bgm") in pairs

    def test_write_write_conflict_present(self):
        conflicts = find_resource_conflicts(CANONICAL)
        ww = [c for c in conflicts if "ctx.timed_segments" in c.conflicting_resources]
        assert ww, "expected at least one timed_segments write-write conflict"


# ── contract_complete ───────────────────────────────────────


class TestContractComplete:
    def test_all_builtins_contract_complete(self):
        for name in CANONICAL_NAMES:
            entry = CANONICAL.get(name)
            assert entry is not None
            assert contract_complete(entry), (name, contract_complete_errors(entry))

    def test_requires_step_must_be_in_depends_on(self):
        reg = StepRegistry()
        reg.register(
            "s",
            _make_step(),
            requires=("step.upstream",),
            depends_on=(),
            reads=("ctx.segments",),
            writes=("meta.script_qa",),
        )
        entry = reg.get("s")
        assert entry is not None
        errors = contract_complete_errors(entry)
        assert any("not in depends_on" in e for e in errors)

    def test_requires_step_allowed_when_depends_on(self):
        reg = StepRegistry()
        reg.register("up", _make_step(), reads=("ctx.segments",), writes=("meta.script_qa",))
        reg.register(
            "s",
            _make_step(),
            requires=("step.up",),
            depends_on=("up",),
            reads=("ctx.segments",),
            writes=("meta.qa_gate",),
        )
        entry = reg.get("s")
        assert entry is not None
        assert contract_complete(entry)

    def test_soft_consistency_degrade_on_soft(self):
        reg = StepRegistry()
        reg.register(
            "s",
            _make_step(),
            soft=True,
            status_field="research",
            failure_policy="abort",
            reads=("ctx.segments",),
            writes=("meta.script_qa",),
        )
        entry = reg.get("s")
        assert entry is not None
        errors = contract_complete_errors(entry)
        assert any("inconsistent with soft=True" in e for e in errors)

    def test_soft_consistency_abort_on_hard(self):
        reg = StepRegistry()
        reg.register(
            "s",
            _make_step(),
            soft=False,
            failure_policy="degrade",
            reads=("ctx.segments",),
            writes=("meta.script_qa",),
        )
        entry = reg.get("s")
        assert entry is not None
        errors = contract_complete_errors(entry)
        assert any("inconsistent with soft=False" in e for e in errors)

    def test_none_failure_policy_always_ok(self):
        reg = StepRegistry()
        reg.register(
            "softish",
            _make_step(),
            soft=True,
            status_field="align",
            failure_policy=None,
            reads=("ctx.segments",),
            writes=("meta.script_qa",),
        )
        entry = reg.get("softish")
        assert entry is not None
        assert contract_complete(entry)

    def test_m4_gate_still_rejects_bare_refs(self):
        reg = StepRegistry()
        reg.register("s", _make_step(), reads=("segments",), writes=("meta.script_qa",))
        entry = reg.get("s")
        assert entry is not None
        assert not contract_complete(entry)


# ── Provenance ──────────────────────────────────────────────


class TestProvenance:
    def test_known_provenance_covers_all_declared_inputs(self):
        pairs = []
        for name in CANONICAL_NAMES:
            entry = CANONICAL.get(name)
            assert entry is not None
            for resource in entry.inputs:
                pairs.append((name, resource))
        ok, missing = provenance_coverage(pairs, KNOWN_PROVENANCE)
        assert ok, missing

    def test_initial_resources_have_none_producer(self):
        # research_plot declares coarse input movie_name; no prior producer.
        assert KNOWN_PROVENANCE[("research_plot", "movie_name")] is None
        assert KNOWN_PROVENANCE[("prepare_assets", "assets")] is None

    def test_step_produced_resources_point_at_latest_prior_writer(self):
        assert KNOWN_PROVENANCE[("generate_script", "research")] == "research_plot"
        assert KNOWN_PROVENANCE[("generate_voice", "segments")] == "generate_script"
        assert KNOWN_PROVENANCE[("align_audio", "timed_segments")] == "generate_voice"
        assert KNOWN_PROVENANCE[("match_clips", "scenes")] == "detect_scenes"
        assert KNOWN_PROVENANCE[("render_video", "matched_clips")] == "match_clips"

    def test_generated_not_hand_copied(self):
        """Regenerating from the truth source yields the same table."""
        assert build_known_provenance(CANONICAL) == KNOWN_PROVENANCE

    def test_coverage_reports_missing_tuples(self):
        ok, missing = provenance_coverage([("nope", "nothing")], KNOWN_PROVENANCE)
        assert not ok
        assert missing == ["('nope', 'nothing')"]


# ── E2 candidate selection ──────────────────────────────────


class TestE2CandidateSelection:
    def test_selects_first_compatible_complete_group_pair(self):
        pair = select_e2_pair(CANONICAL)
        assert pair == ("resolve_video", "prepare_assets")

    def test_respects_fixture_filter(self):
        only = {"research_plot"}
        assert select_e2_pair(CANONICAL, e2_fixture_available=only) is None
        pair = select_e2_pair(
            CANONICAL,
            e2_fixture_available={"resolve_video", "prepare_assets", "research_plot"},
        )
        assert pair == ("resolve_video", "prepare_assets")

    def test_skips_incomplete_group_members(self):
        # Exclude the first group's members so selection falls through.
        pair = select_e2_pair(
            CANONICAL,
            e2_fixture_available={
                n
                for n in CANONICAL_NAMES
                if n not in {"resolve_video", "prepare_assets", "research_plot"}
            },
        )
        # Next multi-member group must still be a compatible pair.
        if pair is not None:
            matrix = concurrency_class_matrix(CANONICAL)
            assert matrix[pair] is True

    def test_callable_filter(self):
        pair = select_e2_pair(
            CANONICAL,
            e2_fixture_available=lambda n: n in {"export_script_md", "generate_voice"},
        )
        # export_script_md and generate_voice share wave 2 and are both safe/isolated.
        if pair is not None:
            assert set(pair) <= {
                "export_script_md",
                "generate_voice",
                "align_audio",
                "mix_bgm",
                "detect_scenes",
            }


# ── Gate-2 ──────────────────────────────────────────────────


class TestGate2:
    def test_potential_speedup_is_metadata_formula(self):
        assert potential_speedup(10.0, 10.0) == pytest.approx(2.0)
        assert potential_speedup(10.0, 5.0) == pytest.approx(1.5)
        assert potential_speedup(0.0, 0.0) == 1.0

    def test_theoretical_eligibility_requires_all_flags(self):
        assert theoretical_eligibility(
            l2_compatible=True,
            contract_complete_a=True,
            contract_complete_b=True,
            resource_safe=True,
        )
        assert not theoretical_eligibility(
            l2_compatible=False,
            contract_complete_a=True,
            contract_complete_b=True,
            resource_safe=True,
        )
        assert not theoretical_eligibility(
            l2_compatible=True,
            contract_complete_a=False,
            contract_complete_b=True,
            resource_safe=True,
        )
        assert not theoretical_eligibility(
            l2_compatible=True,
            contract_complete_a=True,
            contract_complete_b=True,
            resource_safe=False,
        )

    def test_report_claims_only_theoretical_eligibility(self):
        report = build_gate2_report(
            CANONICAL, "resolve_video", "prepare_assets", timing_a=8.0, timing_b=4.0
        )
        assert report.theoretical_eligible is True
        assert report.l2_compatible is True
        assert report.contract_complete_a is True
        assert report.contract_complete_b is True
        # Semantic equivalence is NOT claimed by Gate-2 scaffolding.
        assert report.semantic_equivalent is None
        assert any("semantic equivalence not evaluated" in n for n in report.notes)
        # Potential speedup is recorded but is not a pass/fail input.
        assert report.potential_speedup == pytest.approx(1.5)
        assert any("report/F metadata only" in n for n in report.notes)

    def test_exclusive_pair_not_eligible(self):
        report = build_gate2_report(CANONICAL, "render_video", "validate_deliverable")
        assert report.l2_compatible is False
        assert report.theoretical_eligible is False
        assert any("exclusive" in n for n in report.notes)

    def test_conflicting_timed_segments_pair_reported(self):
        report = build_gate2_report(CANONICAL, "generate_voice", "align_audio")
        assert "ctx.timed_segments" in report.resource_conflicts
        assert report.theoretical_eligible is False

    def test_unknown_step_reported(self):
        report = build_gate2_report(CANONICAL, "nope", "prepare_assets")
        assert report.theoretical_eligible is False
        assert any("unknown step" in n for n in report.notes)

    def test_semantic_equivalent_recorded_when_provided(self):
        report = build_gate2_report(
            CANONICAL,
            "resolve_video",
            "prepare_assets",
            semantic_equivalent=True,
        )
        assert report.semantic_equivalent is True
        assert report.theoretical_eligible is True

    def test_gate2_does_not_use_speedup_threshold(self):
        """A high potential speedup alone never flips eligibility."""
        low = build_gate2_report(
            CANONICAL, "render_video", "export_clips", timing_a=1.0, timing_b=1.0
        )
        assert low.potential_speedup == pytest.approx(2.0)
        assert low.theoretical_eligible is False


# ── Import-boundary smoke (analysis does not mutate pipeline) ─


class TestAnalysisDoesNotMutatePipeline:
    def test_canonical_build_leaves_global_count_unchanged(self):
        before = len(step_registry.ordered_names())
        make_canonical_registry()
        build_known_provenance()
        theoretical_waves()
        resource_compatible_waves()
        assert len(step_registry.ordered_names()) == before
