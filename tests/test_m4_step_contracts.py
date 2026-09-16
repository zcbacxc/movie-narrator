# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""M4 step contracts — ResourceRef, registry fields, gate, concurrency.

Covers:

- ``normalize_legacy_ref`` truth table (P4-5), including ambiguous ``duration``.
- ResourceRef catalog validation (prefix + layer).
- ``StepEntry`` conservative defaults and full register → info → StepSpec
  field equality for the resolved same-name contract fields.
- Failure-policy resolution (None derives from soft; no ``continue``).
- Concurrency matrix (exclusive denies all; safe/isolated OK when
  resources do not conflict).
- The P4-5 gate over the canonical 16 built-in registry.
- ``resource_capacity`` is schema-only and **not** in StepSpec.
- ``CONTRACT_VERSION == (1, 4, 0)``.
"""

from __future__ import annotations

import pytest

from movie_narrator.contract import CONTRACT_VERSION
from movie_narrator.models import Context
from movie_narrator.pipeline.builtin_contracts import BUILTIN_STEP_CONTRACTS
from movie_narrator.pipeline.dag import StepSpec, build_step_graph
from movie_narrator.pipeline.registry import StepRegistry
from movie_narrator.pipeline.runner import STEPS
from movie_narrator.pipeline.step_contracts import (
    ARTIFACT_LOGICAL_NAMES,
    CONCURRENCY_CLASSES,
    EXTERNAL_RESOURCES,
    PATH_TO_ARTIFACT,
    ContractError,
    MetadataKeyRegistry,
    ResourceCatalog,
    concurrency_compatible,
    metadata_key_registry,
    normalize_legacy_ref,
    resolve_failure_policy,
    split_resource_ref,
    validate_failure_policy,
    validate_resource_capacity,
    validate_resource_ref,
    validate_requires,
    validate_step_contracts,
    validate_step_entry,
)

BUILTIN_NAMES = [s.__name__ for s in STEPS]

#: Same-name contract fields mirrored from StepEntry → info() → StepSpec.
MIRRORED_FIELDS = (
    "name",
    "inputs",
    "outputs",
    "depends_on",
    "soft",
    "status_field",
    "reads",
    "writes",
    "idempotent",
    "concurrency_class",
    "requires",
    "optional_inputs",
    "failure_policy",
)


def _noop(ctx: Context) -> Context:
    return ctx


def _make_step():
    def step(ctx: Context) -> Context:
        return ctx

    return step


# ── Version ────────────────────────────────────────────────


class TestContractVersion:
    def test_version_is_1_4_0(self):
        assert CONTRACT_VERSION == (1, 4, 0)

    def test_m4_symbols_exported_from_contract(self):
        from movie_narrator import contract

        for name in (
            "ContractError",
            "MetadataKeyRegistry",
            "normalize_legacy_ref",
            "resolve_failure_policy",
            "validate_step_contracts",
            "concurrency_compatible",
        ):
            assert name in contract.__all__, name
            assert getattr(contract, name) is not None, name

    def test_contract_error_is_valueerror(self):
        from movie_narrator.contract import ContractError as PublicContractError

        assert issubclass(PublicContractError, ValueError)
        assert PublicContractError is ContractError


# ── normalize_legacy_ref truth table ───────────────────────


class TestNormalizeLegacyRef:
    def test_path_to_artifact_map(self):
        for path_name, logical in PATH_TO_ARTIFACT.items():
            assert normalize_legacy_ref(path_name) == f"artifact.{logical}"

    def test_only_mapped_paths_auto_artifact(self):
        # script_md_path is a path but NOT in the auto-map → stays ctx.*
        assert normalize_legacy_ref("script_md_path") == "ctx.script_md_path"
        assert normalize_legacy_ref("source_video_path") == "ctx.source_video_path"

    def test_context_only_field(self):
        assert normalize_legacy_ref("segments") == "ctx.segments"
        assert normalize_legacy_ref("movie_name") == "ctx.movie_name"
        assert normalize_legacy_ref("scenes") == "ctx.scenes"

    def test_metadata_only_key(self):
        assert normalize_legacy_ref("movie_card") == "meta.movie_card"
        assert normalize_legacy_ref("match_summary") == "meta.match_summary"
        assert normalize_legacy_ref("script_qa") == "meta.script_qa"

    def test_external_enum(self):
        assert normalize_legacy_ref("ffmpeg") == "external.ffmpeg"
        assert normalize_legacy_ref("tts") == "external.tts"

    def test_ambiguous_duration_raises(self):
        with pytest.raises(ContractError, match="ambiguous"):
            normalize_legacy_ref("duration")

    def test_unknown_name_raises(self):
        with pytest.raises(ContractError, match="not a Context data field"):
            normalize_legacy_ref("not_a_real_resource_xyz")

    def test_already_qualified_passes_through(self):
        assert normalize_legacy_ref("ctx.segments") == "ctx.segments"
        assert normalize_legacy_ref("meta.movie_card") == "meta.movie_card"
        assert normalize_legacy_ref("artifact.output_video") == "artifact.output_video"
        assert normalize_legacy_ref("external.llm") == "external.llm"

    def test_qualified_forbidden_ctx_field_raises(self):
        with pytest.raises(ContractError, match="forbidden"):
            normalize_legacy_ref("ctx.services")
        with pytest.raises(ContractError, match="forbidden"):
            normalize_legacy_ref("ctx.metadata")
        with pytest.raises(ContractError, match="forbidden"):
            normalize_legacy_ref("ctx.status")


# ── ResourceRef catalog ────────────────────────────────────


class TestResourceRefCatalog:
    def test_split_requires_prefix(self):
        assert split_resource_ref("ctx.segments") == ("ctx.", "segments")
        with pytest.raises(ContractError, match="qualified"):
            split_resource_ref("segments")

    def test_unknown_meta_key_rejected(self):
        with pytest.raises(ContractError, match="MetadataKeyRegistry"):
            validate_resource_ref("meta.definitely_not_registered_key")

    def test_unknown_artifact_rejected(self):
        with pytest.raises(ContractError, match="logical artifact"):
            validate_resource_ref("artifact.arbitrary_path")

    def test_unknown_external_rejected(self):
        with pytest.raises(ContractError, match="external enum"):
            validate_resource_ref("external.free_string")

    def test_external_enum_closed(self):
        assert EXTERNAL_RESOURCES == frozenset(
            {"ffmpeg", "moviepy", "tts", "llm", "research", "gpu", "registry", "cache"}
        )

    def test_metadata_registry_tracks_metadatadict(self):
        from movie_narrator.models import MetadataDict

        assert metadata_key_registry.keys() == frozenset(MetadataDict.__annotations__)
        assert "movie_card" in metadata_key_registry
        assert len(metadata_key_registry) == len(MetadataDict.__annotations__)

    def test_custom_catalog_rejects_unknown_meta(self):
        cat = ResourceCatalog(metadata_keys=MetadataKeyRegistry(keys=["only_one"]))
        validate_resource_ref("meta.only_one", catalog=cat)
        with pytest.raises(ContractError):
            validate_resource_ref("meta.movie_card", catalog=cat)


# ── failure_policy ─────────────────────────────────────────


class TestFailurePolicy:
    def test_continue_is_never_legal(self):
        with pytest.raises(ContractError, match="never legal"):
            validate_failure_policy("continue")

    def test_none_and_enum_ok(self):
        assert validate_failure_policy(None) is None
        assert validate_failure_policy("degrade") == "degrade"
        assert validate_failure_policy("abort") == "abort"

    def test_unknown_value_rejected(self):
        with pytest.raises(ContractError, match="invalid"):
            validate_failure_policy("retry")

    def test_resolve_from_soft(self):
        assert resolve_failure_policy(soft=False, declared=None) == "abort"
        assert resolve_failure_policy(soft=True, declared=None) == "degrade"

    def test_explicit_declaration_wins(self):
        assert resolve_failure_policy(soft=False, declared="degrade") == "degrade"
        assert resolve_failure_policy(soft=True, declared="abort") == "abort"

    def test_builtins_soft_alignment(self):
        """Steps that declare failure_policy must match their soft flag."""
        graph = build_step_graph()
        for name, spec in graph.items():
            if spec.failure_policy is None:
                continue
            expected = "degrade" if spec.soft else "abort"
            assert spec.failure_policy == expected, name


# ── Concurrency matrix ─────────────────────────────────────


class TestConcurrencyMatrix:
    def test_classes_frozen(self):
        assert CONCURRENCY_CLASSES == frozenset({"safe", "isolated_only", "exclusive"})

    def test_exclusive_denies_all_pairs(self):
        assert not concurrency_compatible(
            "exclusive", set(), {"ctx.segments"}, "safe", set(), set()
        )
        assert not concurrency_compatible(
            "exclusive", set(), set(), "exclusive", set(), set()
        )
        assert not concurrency_compatible(
            "exclusive", set(), set(), "isolated_only", set(), set()
        )

    def test_safe_and_isolated_ok_when_no_conflict(self):
        assert concurrency_compatible(
            "safe",
            {"ctx.movie_name"},
            {"ctx.research"},
            "isolated_only",
            {"ctx.segments"},
            {"meta.script_qa"},
        )

    def test_conflicting_writes_deny(self):
        assert not concurrency_compatible(
            "safe",
            set(),
            {"ctx.timed_segments"},
            "isolated_only",
            {"ctx.timed_segments"},
            set(),
        )

    def test_write_read_conflict_deny(self):
        assert not concurrency_compatible(
            "isolated_only",
            set(),
            {"meta.match_summary"},
            "safe",
            {"meta.match_summary"},
            set(),
        )

    def test_disjoint_writes_ok(self):
        assert concurrency_compatible(
            "isolated_only",
            set(),
            {"ctx.scenes"},
            "isolated_only",
            set(),
            {"ctx.matched_clips"},
        )


# ── requires / capacity ────────────────────────────────────


class TestRequiresAndCapacity:
    def test_requires_allows_step_and_external(self):
        assert validate_requires(("step.generate_script", "external.llm")) == (
            "step.generate_script",
            "external.llm",
        )

    def test_requires_forbids_condition(self):
        with pytest.raises(ContractError, match="forbidden"):
            validate_requires(("condition.something",))

    def test_requires_rejects_bare_and_unknown_namespace(self):
        with pytest.raises(ContractError, match="must start with"):
            validate_requires(("generate_script",))
        with pytest.raises(ContractError, match="must start with"):
            validate_requires(("meta.foo",))

    def test_capacity_none_or_positive(self):
        assert validate_resource_capacity(None) is None
        assert validate_resource_capacity(1) == 1
        assert validate_resource_capacity(8) == 8

    def test_capacity_rejects_non_positive_and_non_int(self):
        with pytest.raises(ContractError, match="positive int"):
            validate_resource_capacity(0)
        with pytest.raises(ContractError, match="positive int"):
            validate_resource_capacity(-1)
        with pytest.raises(ContractError, match="positive int"):
            validate_resource_capacity(True)  # bool is not a capacity
        with pytest.raises(ContractError, match="positive int"):
            validate_resource_capacity("2")  # type: ignore[arg-type]


# ── StepEntry defaults + StepSpec mirror ───────────────────


class TestStepEntryDefaults:
    def test_conservative_defaults(self):
        reg = StepRegistry()
        reg.register("plain", _noop)
        entry = reg.get("plain")
        assert entry is not None
        assert entry.reads == ()
        assert entry.writes == ()
        assert entry.idempotent is False
        assert entry.concurrency_class == "isolated_only"
        assert entry.requires == ()
        assert entry.optional_inputs == ()
        assert entry.failure_policy is None
        assert entry.resource_capacity is None

    def test_register_accepts_m4_kwargs(self):
        reg = StepRegistry()
        reg.register(
            "m4_step",
            _noop,
            reads=("ctx.segments", "external.llm"),
            writes=("meta.script_qa",),
            idempotent=True,
            concurrency_class="safe",
            requires=("external.llm",),
            optional_inputs=("ctx.segments",),
            failure_policy="degrade",
            resource_capacity=4,
        )
        entry = reg.get("m4_step")
        assert entry is not None
        assert entry.reads == ("ctx.segments", "external.llm")
        assert entry.writes == ("meta.script_qa",)
        assert entry.idempotent is True
        assert entry.concurrency_class == "safe"
        assert entry.requires == ("external.llm",)
        assert entry.optional_inputs == ("ctx.segments",)
        assert entry.failure_policy == "degrade"
        assert entry.resource_capacity == 4

    def test_info_exposes_m4_but_not_capacity(self):
        reg = StepRegistry()
        reg.register("cap", _noop, resource_capacity=2)
        info = reg.info()[0]
        assert "reads" in info and "writes" in info
        assert "idempotent" in info and "concurrency_class" in info
        assert "requires" in info and "optional_inputs" in info
        assert "failure_policy" in info
        assert "resource_capacity" not in info

    def test_stepspec_field_equality_for_builtin(self):
        reg = StepRegistry()
        reg.register(
            "mirrored",
            _noop,
            inputs=("segments",),
            outputs=("script_qa",),
            depends_on=(),
            soft=True,
            status_field="research",
            reads=("ctx.segments",),
            writes=("meta.script_qa",),
            idempotent=True,
            concurrency_class="safe",
            requires=("external.llm",),
            optional_inputs=("ctx.segments",),
            failure_policy="degrade",
            resource_capacity=3,
        )
        graph = build_step_graph(reg)
        spec = graph["mirrored"]
        entry = reg.get("mirrored")
        assert entry is not None
        assert isinstance(spec, StepSpec)
        for field in MIRRORED_FIELDS:
            ev = getattr(entry, field)
            sv = getattr(spec, field)
            if isinstance(ev, tuple):
                assert sv == ev, field
            else:
                assert sv == ev, field

    def test_stepspec_excludes_capacity_and_internals(self):
        spec_fields = set(StepSpec.__dataclass_fields__)
        assert "resource_capacity" not in spec_fields
        assert "func" not in spec_fields
        assert "seq" not in spec_fields
        assert "insert_after" not in spec_fields
        assert "insert_before" not in spec_fields

    def test_builtin_graph_mirrors_contract_fields(self):
        graph = build_step_graph()
        for name in BUILTIN_NAMES:
            assert name in graph
            spec = graph[name]
            declared = BUILTIN_STEP_CONTRACTS.get(name, {})
            assert spec.reads == tuple(declared.get("reads", ()))
            assert spec.writes == tuple(declared.get("writes", ()))
            assert spec.concurrency_class == declared.get(
                "concurrency_class", "isolated_only"
            )


# ── P4-5 gate over the canonical registry ──────────────────


class TestGateOnBuiltins:
    def test_all_16_declared(self):
        assert len(BUILTIN_NAMES) == 16
        assert set(BUILTIN_STEP_CONTRACTS) == set(BUILTIN_NAMES)

    def test_canonical_registry_passes_gate(self):
        assert validate_step_contracts() == []

    def test_every_builtin_has_reads_and_writes(self):
        graph = build_step_graph()
        for name in BUILTIN_NAMES:
            assert graph[name].reads, name
            assert graph[name].writes, name

    def test_inputs_subset_reads_outputs_subset_writes(self):
        """Gate-relevant invariant, checked explicitly for clarity."""
        graph = build_step_graph()
        for name, spec in graph.items():
            for legacy in spec.inputs:
                assert normalize_legacy_ref(legacy) in set(spec.reads), (
                    f"{name}: input {legacy!r} not covered by reads"
                )
            for legacy in spec.outputs:
                assert normalize_legacy_ref(legacy) in set(spec.writes), (
                    f"{name}: output {legacy!r} not covered by writes"
                )


class TestGateRejects:
    def _reg_with(self, **kwargs) -> StepRegistry:
        reg = StepRegistry()
        defaults = dict(
            reads=("ctx.segments",),
            writes=("meta.script_qa",),
            inputs=("segments",),
            outputs=("script_qa",),
        )
        defaults.update(kwargs)
        reg.register("s", _make_step(), **defaults)
        return reg

    def test_bare_name_in_reads_rejected(self):
        reg = StepRegistry()
        reg.register("s", _make_step(), reads=("segments",), writes=("meta.script_qa",))
        errors = validate_step_contracts(reg)
        assert any("qualified" in e for e in errors)

    def test_input_not_in_reads_rejected(self):
        reg = self._reg_with(inputs=("segments", "movie_name"))
        errors = validate_step_contracts(reg)
        assert any("not declared in reads" in e for e in errors)

    def test_output_not_in_writes_rejected(self):
        reg = self._reg_with(outputs=("script_qa", "qa_gate"))
        errors = validate_step_contracts(reg)
        assert any("not declared in writes" in e for e in errors)

    def test_optional_not_in_reads_rejected(self):
        reg = self._reg_with(optional_inputs=("movie_name",))
        errors = validate_step_contracts(reg)
        assert any("optional_inputs" in e for e in errors)

    def test_condition_requires_rejected(self):
        reg = self._reg_with(requires=("condition.x",))
        errors = validate_step_contracts(reg)
        assert any("forbidden" in e for e in errors)

    def test_duplicate_depends_on_rejected(self):
        reg = StepRegistry()
        reg.register("a", _make_step())
        reg.register(
            "b",
            _make_step(),
            depends_on=("a", "a"),
            reads=("ctx.segments",),
            writes=("meta.script_qa",),
        )
        errors = validate_step_contracts(reg)
        assert any("duplicates" in e for e in errors)

    def test_bad_capacity_rejected(self):
        reg = StepRegistry()
        reg.register(
            "s",
            _make_step(),
            reads=("ctx.segments",),
            writes=("meta.script_qa",),
            resource_capacity=0,
        )
        errors = validate_step_contracts(reg)
        assert any("resource_capacity" in e for e in errors)

    def test_continue_failure_policy_rejected(self):
        reg = StepRegistry()
        reg.register(
            "s",
            _make_step(),
            reads=("ctx.segments",),
            writes=("meta.script_qa",),
            failure_policy="continue",
        )
        errors = validate_step_contracts(reg)
        assert any("never legal" in e for e in errors)

    def test_unknown_meta_write_rejected(self):
        reg = StepRegistry()
        reg.register(
            "s",
            _make_step(),
            reads=("ctx.segments",),
            writes=("meta.no_such_key_zzz",),
        )
        errors = validate_step_contracts(reg)
        assert any("MetadataKeyRegistry" in e for e in errors)

    def test_validate_step_entry_collects_multiple(self):
        reg = StepRegistry()
        reg.register(
            "s",
            _make_step(),
            reads=("bare", "ctx.services"),
            writes=(),
            failure_policy="continue",
            requires=("condition.x",),
            resource_capacity=-3,
        )
        entry = reg.get("s")
        assert entry is not None
        errors = validate_step_entry(entry)
        assert len(errors) >= 4


# ── Artifact logical names ─────────────────────────────────


class TestArtifactNames:
    def test_closed_set_matches_path_map_values(self):
        assert ARTIFACT_LOGICAL_NAMES == frozenset(PATH_TO_ARTIFACT.values())
        assert ARTIFACT_LOGICAL_NAMES == frozenset(
            {"output_video", "subtitle", "final_audio", "clips", "narration_audio"}
        )
