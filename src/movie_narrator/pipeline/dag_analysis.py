# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""M5 DAG run analysis — waves, L2 planner, provenance, Gate-2 scaffolding.

Naming layers (C3)
------------------

======  =====================================  ==========================
Layer   Name                                   Consumed this phase
======  =====================================  ==========================
L1      ``theoretical_wave``                   yes (Kahn layering)
L2      ``resource_compatible_wave``           yes (first-fit greedy)
L3      ``capacity_aware_executable_wave``     **no** (F phase; capacity
                                               is schema-only in M4)
======  =====================================  ==========================

Analysis always uses the **canonical fixture** from
``pipeline.canonical.build_canonical_registry`` — never the process-global
``step_registry`` (plugins would pollute the built-in graph).

Gate-2 honesty
--------------

Gate-2 reports **theoretical eligibility** only
(``l2_compatible ∧ contract_complete ∧ resource-safe``). Semantic
equivalence is filled in by the E2 harness; real speedup ≥ 1.10 is an
**F-phase bench** criterion. ``potential_speedup = (tA+tB)/max(tA,tB)``
is report / F-eligibility metadata and **never** a pass/fail input here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    AbstractSet,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
)

from .canonical import build_canonical_registry
from .dag import StepSpec, build_step_graph
from .registry import StepRegistry
from .step_contracts import (
    CONCURRENCY_EXCLUSIVE,
    concurrency_compatible,
    resources_conflict,
    validate_step_entry,
)

__all__ = [
    "KNOWN_PROVENANCE",
    "Gate2Report",
    "ResourceConflict",
    "build_known_provenance",
    "build_gate2_report",
    "concurrency_class_matrix",
    "contract_complete",
    "contract_complete_errors",
    "find_resource_conflicts",
    "potential_speedup",
    "provenance_coverage",
    "resource_compatible_waves",
    "select_e2_pair",
    "theoretical_eligibility",
    "theoretical_waves",
    "waves_cover_all_steps",
]


# ── L1: theoretical waves (Kahn layering, registry order) ──


def theoretical_waves(registry: Optional[StepRegistry] = None) -> List[List[str]]:
    """L1 Kahn layering over ``depends_on``.

    Within each wave, steps appear in **canonical registry order**
    (never alphabetically sorted). The entire current layer is emitted
    before any dependent is considered for the next layer.

    Args:
        registry: Defaults to the canonical built-in fixture.

    Returns:
        ``waves[w][i]`` — step name at wave *w*, registry-order index *i*.

    Raises:
        ValueError: if the dependency graph contains a cycle.
    """
    reg = registry if registry is not None else build_canonical_registry()
    graph = build_step_graph(reg)
    ordered = reg.ordered_names()
    position: Dict[str, int] = {name: idx for idx, name in enumerate(ordered)}
    fallback = len(position)
    for name in graph:
        if name not in position:
            position[name] = fallback
            fallback += 1
    # Registry-order iteration list covering every graph node.
    order_list = list(ordered) + [n for n in graph if n not in position]

    in_degree: Dict[str, int] = {name: 0 for name in graph}
    dependents: Dict[str, List[str]] = {name: [] for name in graph}
    for name, spec in graph.items():
        for dep in spec.depends_on:
            if dep in graph:
                in_degree[name] += 1
                dependents[dep].append(name)

    remaining: Set[str] = set(graph)
    waves: List[List[str]] = []
    while remaining:
        # Registry order — do NOT alphabetical-sort names.
        wave = [n for n in order_list if n in remaining and in_degree[n] == 0]
        if not wave:
            stuck = sorted(remaining)
            raise ValueError(f"Dependency cycle detected among pipeline steps: {', '.join(stuck)}")
        waves.append(wave)
        for n in wave:
            remaining.discard(n)
            for dependent in dependents[n]:
                in_degree[dependent] -= 1
    return waves


def waves_cover_all_steps(
    waves: Sequence[Sequence[str]], registry: Optional[StepRegistry] = None
) -> bool:
    """True when every registered step appears exactly once across *waves*."""
    reg = registry if registry is not None else build_canonical_registry()
    names = reg.ordered_names()
    flat = [n for wave in waves for n in wave]
    return sorted(flat) == sorted(names) and len(flat) == len(set(flat))


# ── L2: resource-compatible waves (first-fit greedy) ────────


def _spec_map(registry: StepRegistry) -> Dict[str, StepSpec]:
    return build_step_graph(registry)


def _compatible_pair(specs: Mapping[str, StepSpec], a: str, b: str) -> bool:
    sa, sb = specs[a], specs[b]
    return concurrency_compatible(
        sa.concurrency_class,
        sa.reads,
        sa.writes,
        sb.concurrency_class,
        sb.reads,
        sb.writes,
    )


def resource_compatible_waves(
    registry: Optional[StepRegistry] = None,
    *,
    theoretical: Optional[Sequence[Sequence[str]]] = None,
) -> List[List[List[str]]]:
    """L2: split each L1 wave into resource-compatible groups.

    **Canonical-order first-fit greedy**: walk the wave's steps in
    registry order; place each step into the first existing group with
    which it is L2-compatible with **every** member; otherwise open a
    new group.

    Args:
        registry: Defaults to the canonical built-in fixture.
        theoretical: Optional precomputed L1 waves.

    Returns:
        ``result[w][g][i]`` — wave, group, step name. Completeness
        holds: every theoretical step appears exactly once.
    """
    reg = registry if registry is not None else build_canonical_registry()
    waves = list(theoretical) if theoretical is not None else theoretical_waves(reg)
    specs = _spec_map(reg)

    out: List[List[List[str]]] = []
    for wave in waves:
        groups: List[List[str]] = []
        for name in wave:
            placed = False
            for group in groups:
                if all(_compatible_pair(specs, name, other) for other in group):
                    group.append(name)
                    placed = True
                    break
            if not placed:
                groups.append([name])
        out.append(groups)
    return out


# ── Class matrix + resource conflicts ───────────────────────


def concurrency_class_matrix(
    registry: Optional[StepRegistry] = None,
) -> Dict[Tuple[str, str], bool]:
    """Pairwise L2 compatibility for every ordered pair of steps.

    Keys are ``(a, b)`` in registry order; ``matrix[(a, a)]`` is the
    self-compatibility of a step (``exclusive`` is never self-compatible).
    """
    reg = registry if registry is not None else build_canonical_registry()
    specs = _spec_map(reg)
    names = reg.ordered_names()
    matrix: Dict[Tuple[str, str], bool] = {}
    for a in names:
        for b in names:
            matrix[(a, b)] = _compatible_pair(specs, a, b)
    return matrix


@dataclass(frozen=True)
class ResourceConflict:
    """Two steps whose declared resource sets interfere."""

    step_a: str
    step_b: str
    conflicting_resources: Tuple[str, ...]


def find_resource_conflicts(
    registry: Optional[StepRegistry] = None,
) -> List[ResourceConflict]:
    """All unordered pairs with a write/read or write/write conflict.

    Uses M4 ``resources_conflict`` semantics on the qualified ResourceRef
    sets. ``exclusive`` steps are reported only for actual resource
    overlap here; class-level denial is the class matrix's job.
    """
    reg = registry if registry is not None else build_canonical_registry()
    specs = _spec_map(reg)
    names = reg.ordered_names()
    conflicts: List[ResourceConflict] = []
    for i, a in enumerate(names):
        sa = specs[a]
        for b in names[i + 1 :]:
            sb = specs[b]
            if not resources_conflict(sa.reads, sa.writes, sb.reads, sb.writes):
                continue
            wa, ra = set(sa.writes), set(sa.reads)
            wb, rb = set(sb.writes), set(sb.reads)
            shared = (wa & (rb | wb)) | (wb & (ra | wa))
            conflicts.append(
                ResourceConflict(
                    step_a=a,
                    step_b=b,
                    conflicting_resources=tuple(sorted(shared)),
                )
            )
    return conflicts


# ── contract_complete (extends the M4 gate) ─────────────────


def contract_complete_errors(entry) -> List[str]:
    """Full contract-complete check for one :class:`StepEntry`.

    M4 ``validate_step_entry`` plus M5 extras:

    - every ``requires`` ``step.*`` entry must be in ``depends_on``;
    - declared ``failure_policy`` must be consistent with ``soft``
      (``soft=True → degrade``, ``soft=False → abort``; ``None`` is legal
      and derives from soft).
    """
    errors = list(validate_step_entry(entry))
    name = entry.name

    deps = set(entry.depends_on)
    for req in entry.requires:
        if req.startswith("step."):
            step_name = req[len("step.") :]
            if step_name not in deps:
                errors.append(
                    f"step '{name}': requires {req!r} is not in depends_on {sorted(deps)!r}"
                )

    if entry.failure_policy is not None:
        expected = "degrade" if entry.soft else "abort"
        if entry.failure_policy != expected:
            errors.append(
                f"step '{name}': failure_policy {entry.failure_policy!r} "
                f"inconsistent with soft={entry.soft} (expected {expected!r} or None)"
            )
    return errors


def contract_complete(entry) -> bool:
    """True when *entry* passes :func:`contract_complete_errors`."""
    return not contract_complete_errors(entry)


# ── Provenance (C4) ─────────────────────────────────────────


def build_known_provenance(
    registry: Optional[StepRegistry] = None,
) -> Dict[Tuple[str, str], Optional[str]]:
    """Derive ``KNOWN_PROVENANCE[(consumer, resource)]`` from the truth source.

    For each declared coarse input of each step, the **latest preceding**
    step (registry order) that declares the same name in ``outputs`` is
    the producer. ``None`` means initial / external (no producing step).

    Generated — never a hand-copied second graph.
    """
    reg = registry if registry is not None else build_canonical_registry()
    ordered = reg.ordered_names()
    outputs_of: Dict[str, Set[str]] = {}
    for name in ordered:
        entry = reg.get(name)
        outputs_of[name] = set(entry.outputs) if entry else set()

    provenance: Dict[Tuple[str, str], Optional[str]] = {}
    for consumer in ordered:
        entry = reg.get(consumer)
        if entry is None:
            continue
        for resource in entry.inputs:
            producer: Optional[str] = None
            for prior in ordered:
                if prior == consumer:
                    break
                if resource in outputs_of[prior]:
                    producer = prior
            provenance[(consumer, resource)] = producer
    return provenance


#: Process-wide provenance map for the canonical built-ins.
KNOWN_PROVENANCE: Dict[Tuple[str, str], Optional[str]] = build_known_provenance()


def provenance_coverage(
    pairs: Iterable[Tuple[str, str]],
    provenance: Optional[Mapping[Tuple[str, str], Optional[str]]] = None,
) -> Tuple[bool, List[str]]:
    """Check that every ``(consumer, resource)`` tuple has a provenance entry.

    Coverage is **by tuple** — a missing key is a coverage hole even when
    the resource appears under another consumer.
    """
    table = KNOWN_PROVENANCE if provenance is None else provenance
    missing = [f"({c!r}, {r!r})" for c, r in pairs if (c, r) not in table]
    return (not missing), missing


# ── E2 candidate selection ──────────────────────────────────


def _e2_available_fn(
    e2_fixture_available: Optional[AbstractSet[str] | Callable[[str], bool]],
) -> Callable[[str], bool]:
    if e2_fixture_available is None:
        return lambda _name: True
    if callable(e2_fixture_available):
        return e2_fixture_available  # type: ignore[return-value]
    allowed = set(e2_fixture_available)
    return lambda name: name in allowed


def select_e2_pair(
    registry: Optional[StepRegistry] = None,
    *,
    e2_fixture_available: Optional[AbstractSet[str] | Callable[[str], bool]] = None,
    l2_waves: Optional[Sequence[Sequence[Sequence[str]]]] = None,
) -> Optional[Tuple[str, str]]:
    """First L2 group eligible as an E2 race pair.

    Eligibility of a group:

    1. every member is L2-compatible with every other (true by
       construction of :func:`resource_compatible_waves`);
    2. every member is :func:`contract_complete`;
    3. every member has an E2 fixture available (``e2_fixture_available``).

    Returns the **first two** steps of the first eligible group in
    registry order, or ``None``.
    """
    reg = registry if registry is not None else build_canonical_registry()
    waves = list(l2_waves) if l2_waves is not None else resource_compatible_waves(reg)
    available = _e2_available_fn(e2_fixture_available)

    for wave in waves:
        for group in wave:
            if len(group) < 2:
                continue
            if not all(available(n) for n in group):
                continue
            complete = True
            for n in group:
                entry = reg.get(n)
                if entry is None or not contract_complete(entry):
                    complete = False
                    break
            if not complete:
                continue
            return group[0], group[1]
    return None


# ── Gate-2 report ───────────────────────────────────────────


def potential_speedup(t_a: float, t_b: float) -> float:
    """``(tA+tB)/max(tA,tB)`` — **report / F-eligibility metadata only**.

    Never feed this into Gate-2 pass/fail. Real speedup ≥ 1.10 is an
    F-phase bench criterion (median + p95, ≥3 repeats).
    """
    peak = max(t_a, t_b)
    if peak <= 0:
        return 1.0
    return (t_a + t_b) / peak


def theoretical_eligibility(
    *,
    l2_compatible: bool,
    contract_complete_a: bool,
    contract_complete_b: bool,
    resource_safe: bool,
) -> bool:
    """Gate-2 theoretical eligibility — **not** a semantic-equivalence claim."""
    return bool(l2_compatible and contract_complete_a and contract_complete_b and resource_safe)


@dataclass(frozen=True)
class Gate2Report:
    """Gate-2 scaffolding report for one candidate pair.

    ``semantic_equivalent`` stays ``None`` until the E2 harness runs;
    this module never claims it. ``potential_speedup`` is metadata for
    the F bench, not a Gate-2 criterion.
    """

    step_a: str
    step_b: str
    class_a: str
    class_b: str
    l2_compatible: bool
    contract_complete_a: bool
    contract_complete_b: bool
    resource_conflicts: Tuple[str, ...] = ()
    theoretical_eligible: bool = False
    semantic_equivalent: Optional[bool] = None
    timing_a: Optional[float] = None
    timing_b: Optional[float] = None
    potential_speedup: Optional[float] = None
    notes: Tuple[str, ...] = field(default_factory=tuple)


def build_gate2_report(
    registry: Optional[StepRegistry] = None,
    step_a: str = "",
    step_b: str = "",
    *,
    timing_a: Optional[float] = None,
    timing_b: Optional[float] = None,
    e2_fixture_available: Optional[AbstractSet[str] | Callable[[str], bool]] = None,
    semantic_equivalent: Optional[bool] = None,
) -> Gate2Report:
    """Build a Gate-2 report claiming **theoretical eligibility only**.

    Args:
        registry: Defaults to the canonical fixture.
        step_a / step_b: Candidate pair (registry order preferred).
        timing_a / timing_b: Optional baseline seconds; used solely to
            populate ``potential_speedup`` metadata.
        e2_fixture_available: Fixture availability filter (informational).
        semantic_equivalent: Optional E2 outcome to record; ``None`` means
            "not yet run" — never inferred here.
    """
    reg = registry if registry is not None else build_canonical_registry()
    specs = _spec_map(reg)
    notes: List[str] = []

    if step_a not in specs or step_b not in specs:
        missing = [n for n in (step_a, step_b) if n not in specs]
        return Gate2Report(
            step_a=step_a,
            step_b=step_b,
            class_a="",
            class_b="",
            l2_compatible=False,
            contract_complete_a=False,
            contract_complete_b=False,
            notes=(f"unknown step(s): {', '.join(missing)}",),
        )

    sa, sb = specs[step_a], specs[step_b]
    l2 = _compatible_pair(specs, step_a, step_b)
    entry_a, entry_b = reg.get(step_a), reg.get(step_b)
    assert entry_a is not None and entry_b is not None
    cc_a = contract_complete(entry_a)
    cc_b = contract_complete(entry_b)

    conflicting: Tuple[str, ...] = ()
    if resources_conflict(sa.reads, sa.writes, sb.reads, sb.writes):
        wa, ra = set(sa.writes), set(sa.reads)
        wb, rb = set(sb.writes), set(sb.reads)
        shared = (wa & (rb | wb)) | (wb & (ra | wa))
        conflicting = tuple(sorted(shared))
        notes.append(f"resource conflict on {', '.join(conflicting)}")
    if (
        sa.concurrency_class == CONCURRENCY_EXCLUSIVE
        or sb.concurrency_class == CONCURRENCY_EXCLUSIVE
    ):
        notes.append("exclusive concurrency class denies pairing")

    eligible = theoretical_eligibility(
        l2_compatible=l2,
        contract_complete_a=cc_a,
        contract_complete_b=cc_b,
        resource_safe=not conflicting,
    )
    if not cc_a:
        notes.append(f"{step_a} is not contract_complete")
    if not cc_b:
        notes.append(f"{step_b} is not contract_complete")

    available = _e2_available_fn(e2_fixture_available)
    if not (available(step_a) and available(step_b)):
        notes.append("E2 fixture missing for at least one step")

    pot: Optional[float] = None
    if timing_a is not None and timing_b is not None:
        pot = potential_speedup(float(timing_a), float(timing_b))
        notes.append(
            f"potential_speedup={pot:.4f} is report/F metadata only "
            f"(Gate-2 does not pass/fail on it)"
        )

    if semantic_equivalent is None:
        notes.append("semantic equivalence not evaluated (run E2 harness)")

    return Gate2Report(
        step_a=step_a,
        step_b=step_b,
        class_a=sa.concurrency_class,
        class_b=sb.concurrency_class,
        l2_compatible=l2,
        contract_complete_a=cc_a,
        contract_complete_b=cc_b,
        resource_conflicts=conflicting,
        theoretical_eligible=eligible,
        semantic_equivalent=semantic_equivalent,
        timing_a=timing_a,
        timing_b=timing_b,
        potential_speedup=pot,
        notes=tuple(notes),
    )
