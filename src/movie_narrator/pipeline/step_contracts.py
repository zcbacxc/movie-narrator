# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""ResourceRef model, catalog validation, and the M4 step-contract gate.

This module is **declarative only**. The runner still executes the 16
built-in steps linearly and does **not** interpret ``requires``,
``optional_inputs``, ``resource_capacity``, or ``concurrency_class``.
Those fields exist for authoring, static validation, and (later) M5
scheduling.

ResourceRef layers (R1–R4)
--------------------------

Every entry in ``reads`` / ``writes`` must be a *qualified* ResourceRef:

======  ==========  ==================================================
Prefix  Layer       Legal form
======  ==========  ==================================================
``ctx.``      R1    Selected ``Context`` data fields only. Forbidden:
                    whole ``metadata``, ``services``, ``cost_tracker``,
                    ``step_state``, ``status``.
``meta.``     R2    ``meta.<registered-key>`` — key must exist in
                    :class:`MetadataKeyRegistry` (canonical view over
                    ``MetadataDict``). No AST auto-promotion.
``artifact.`` R3    ``artifact.<logical-name>`` from the closed set of
                    path→artifact mappings. Arbitrary paths forbidden.
``external.`` R4    ``external.<enum-member>`` from
                    :data:`EXTERNAL_RESOURCES`. Free strings forbidden.
======  ==========  ==================================================

Bare (unqualified) names are **contract-illegal** in ``reads``/``writes``.
Legacy ``inputs``/``outputs`` keep coarse bare names and are mapped
through :func:`normalize_legacy_ref` only for subset checks.

Audit honesty (P4-3)
--------------------

- **AST layer**: static candidate write-sets over the 16 built-in step
  functions. Useful as a candidate set, **not** a semantic proof.
- **Manual layer**: filesystem / provider side effects.
- **Runtime layer**: only a selected L2 pair is observed (deferred to M5).

Never claim that AST analysis equals runtime behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

if TYPE_CHECKING:  # pragma: no cover
    from .registry import StepEntry, StepRegistry

# ── Errors ─────────────────────────────────────────────────


class ContractError(ValueError):
    """Raised when a step contract or ResourceRef is invalid."""


# ── ResourceRef prefixes ───────────────────────────────────

PREFIX_CTX = "ctx."
PREFIX_META = "meta."
PREFIX_ARTIFACT = "artifact."
PREFIX_EXTERNAL = "external."

RESOURCE_PREFIXES: Tuple[str, ...] = (
    PREFIX_CTX,
    PREFIX_META,
    PREFIX_ARTIFACT,
    PREFIX_EXTERNAL,
)

# ── External resource enum (R4) ────────────────────────────

#: Closed set of external dependencies. Always written as ``external.<name>``.
EXTERNAL_RESOURCES: FrozenSet[str] = frozenset(
    {
        "ffmpeg",
        "moviepy",
        "tts",
        "llm",
        "research",
        "gpu",
        "registry",
        "cache",
    }
)

# ── Path → artifact map (R3 auto-map; ONLY these) ──────────

#: Context path fields that auto-map to ``artifact.<logical-name>``.
#: Any other path field stays ``ctx.<name>``.
PATH_TO_ARTIFACT: Dict[str, str] = {
    "video_path": "output_video",
    "subtitle_path": "subtitle",
    "final_audio_path": "final_audio",
    "clips_dir": "clips",
    "audio_path": "narration_audio",
}

#: Closed set of legal ``artifact.*`` logical names (the values above).
ARTIFACT_LOGICAL_NAMES: FrozenSet[str] = frozenset(PATH_TO_ARTIFACT.values())

# ── R1 Context data fields ─────────────────────────────────

#: Context fields that may never appear as ``ctx.*`` resources.
FORBIDDEN_CTX_FIELDS: FrozenSet[str] = frozenset(
    {
        "metadata",  # whole metadata is not an R1 resource; use meta.*
        "services",
        "cost_tracker",
        "step_state",
        "status",
    }
)


def _context_data_field_names() -> FrozenSet[str]:
    """Selected Context data fields legal as ``ctx.*`` resources."""
    from ..models import Context

    names = set(Context.model_fields) - FORBIDDEN_CTX_FIELDS
    return frozenset(names)


#: Computed once at import. Context is a stable pydantic model.
CTX_DATA_FIELDS: FrozenSet[str] = _context_data_field_names()


# ── Metadata key registry (R2 truth source) ────────────────


class MetadataKeyRegistry:
    """Canonical, read-only view of registered ``ctx.metadata`` keys.

    Source of truth remains :class:`~movie_narrator.models.MetadataDict`
    (the TypedDict). This registry exposes those keys as a runtime set
    so the ResourceCatalog, the step-contract gate, and
    ``scripts/check_metadata_keys.py`` share one definition instead of
    each re-parsing ``models.py``.
    """

    def __init__(self, keys: Optional[Iterable[str]] = None) -> None:
        if keys is None:
            from ..models import MetadataDict

            keys = MetadataDict.__annotations__.keys()
        self._keys: FrozenSet[str] = frozenset(keys)

    def __contains__(self, key: object) -> bool:
        return key in self._keys

    def keys(self) -> FrozenSet[str]:
        return self._keys

    def __len__(self) -> int:
        return len(self._keys)

    def __iter__(self):
        return iter(self._keys)


#: Process-wide registry (derived from MetadataDict at import).
metadata_key_registry = MetadataKeyRegistry()


# ── ResourceRef validation ─────────────────────────────────


def split_resource_ref(ref: str) -> Tuple[str, str]:
    """Split a qualified ResourceRef into ``(prefix, name)``.

    Args:
        ref: A qualified reference such as ``"ctx.segments"``.

    Returns:
        ``(prefix_with_dot, bare_name)``.

    Raises:
        ContractError: if *ref* has no legal prefix.
    """
    for prefix in RESOURCE_PREFIXES:
        if ref.startswith(prefix) and len(ref) > len(prefix):
            return prefix, ref[len(prefix) :]
    raise ContractError(
        f"ResourceRef {ref!r} must be qualified with one of "
        f"{', '.join(RESOURCE_PREFIXES)} (bare names are contract-illegal)"
    )


def validate_resource_ref(
    ref: str,
    *,
    catalog: Optional["ResourceCatalog"] = None,
) -> str:
    """Validate one ResourceRef against the catalog.

    Returns:
        The same *ref* when valid.

    Raises:
        ContractError: on bad prefix, forbidden R1 field, unknown
            metadata key, unknown artifact name, or unknown external.
    """
    cat = catalog if catalog is not None else default_catalog()
    prefix, name = split_resource_ref(ref)
    if prefix == PREFIX_CTX:
        if name in FORBIDDEN_CTX_FIELDS:
            raise ContractError(
                f"ResourceRef {ref!r}: ctx.{name} is forbidden "
                f"(use meta.* for metadata keys; services/status/etc. are not resources)"
            )
        if name not in CTX_DATA_FIELDS:
            raise ContractError(f"ResourceRef {ref!r}: ctx.{name} is not a Context data field")
    elif prefix == PREFIX_META:
        if name not in cat.metadata_keys:
            raise ContractError(f"ResourceRef {ref!r}: meta.{name} is not in MetadataKeyRegistry")
    elif prefix == PREFIX_ARTIFACT:
        if name not in ARTIFACT_LOGICAL_NAMES:
            raise ContractError(
                f"ResourceRef {ref!r}: artifact.{name} is not a known logical artifact "
                f"(legal: {', '.join(sorted(ARTIFACT_LOGICAL_NAMES))})"
            )
    elif prefix == PREFIX_EXTERNAL:
        if name not in EXTERNAL_RESOURCES:
            raise ContractError(
                f"ResourceRef {ref!r}: external.{name} is not in the external enum "
                f"(legal: {', '.join(sorted(EXTERNAL_RESOURCES))})"
            )
    return ref


class ResourceCatalog:
    """Layered catalog used by the step-contract gate."""

    def __init__(
        self,
        *,
        ctx_fields: Optional[FrozenSet[str]] = None,
        metadata_keys: Optional[MetadataKeyRegistry] = None,
        artifacts: Optional[FrozenSet[str]] = None,
        externals: Optional[FrozenSet[str]] = None,
    ) -> None:
        self.ctx_fields: FrozenSet[str] = CTX_DATA_FIELDS if ctx_fields is None else ctx_fields
        self.metadata_keys: MetadataKeyRegistry = (
            metadata_key_registry if metadata_keys is None else metadata_keys
        )
        self.artifacts: FrozenSet[str] = (
            ARTIFACT_LOGICAL_NAMES if artifacts is None else artifacts
        )
        self.externals: FrozenSet[str] = EXTERNAL_RESOURCES if externals is None else externals

    def validate(self, ref: str) -> str:
        return validate_resource_ref(ref, catalog=self)


def default_catalog() -> ResourceCatalog:
    """Process-wide catalog bound to MetadataDict / Context / enums."""
    return ResourceCatalog()


# ── normalize_legacy_ref (P4-5 truth table) ────────────────


def normalize_legacy_ref(
    name: str,
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> str:
    """Map a legacy bare name (or already-qualified ref) to a ResourceRef.

    Truth table (P4-5), evaluated in order:

    1. Already qualified with a legal prefix → validate and return as-is.
    2. *name* in :data:`PATH_TO_ARTIFACT` → ``artifact.<mapped>``.
    3. *name* **only** in Context data → ``ctx.<name>``.
    4. *name* **only** in MetadataKeyRegistry → ``meta.<name>``.
    5. *name* in :data:`EXTERNAL_RESOURCES` → ``external.<name>``.
    6. *name* in **both** Context data and metadata →
       :class:`ContractError` ("ambiguous"; e.g. ``duration``).
    7. else → :class:`ContractError`.

    Used by the gate to check ``inputs ⊆ reads`` and ``outputs ⊆ writes``.
    """
    cat = catalog if catalog is not None else default_catalog()

    # 1. Already qualified.
    for prefix in RESOURCE_PREFIXES:
        if name.startswith(prefix):
            return validate_resource_ref(name, catalog=cat)

    # 2. Path → artifact map (only these auto-map).
    if name in PATH_TO_ARTIFACT:
        return PREFIX_ARTIFACT + PATH_TO_ARTIFACT[name]

    in_ctx = name in cat.ctx_fields
    in_meta = name in cat.metadata_keys

    # 5/6 order: ambiguity is checked before either single-membership rule
    # would claim the name. External enum names never collide with
    # Context/metadata keys in the frozen enums, but check ambiguity first
    # so "duration" cannot silently become ctx.* or meta.*.
    if in_ctx and in_meta:
        raise ContractError(
            f"legacy name {name!r} is ambiguous (present in both Context data "
            f"and MetadataDict); qualify explicitly as ctx.{name} or meta.{name}"
        )
    # 3. Context only.
    if in_ctx:
        return PREFIX_CTX + name
    # 4. Metadata only.
    if in_meta:
        return PREFIX_META + name
    # 5. External enum.
    if name in cat.externals:
        return PREFIX_EXTERNAL + name
    raise ContractError(
        f"legacy name {name!r} is not a Context data field, metadata key, "
        f"path→artifact entry, or external enum member"
    )


def normalize_legacy_refs(
    names: Sequence[str],
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> Tuple[str, ...]:
    """Normalize a sequence of legacy names / refs."""
    cat = catalog if catalog is not None else default_catalog()
    return tuple(normalize_legacy_ref(n, catalog=cat) for n in names)


# ── Failure policy (own failure only; NO "continue") ───────

FAILURE_POLICY_VALUES: FrozenSet[str] = frozenset({"degrade", "abort"})
#: ``"continue"`` is never legal — it would silently reinterpret hard
#: failures as skippable.
FAILURE_POLICY_FORBIDDEN: FrozenSet[str] = frozenset({"continue"})


def validate_failure_policy(value: Optional[str]) -> Optional[str]:
    """Validate a declared ``failure_policy`` (None is legal)."""
    if value is None:
        return None
    if value in FAILURE_POLICY_FORBIDDEN:
        raise ContractError(
            f"failure_policy {value!r} is never legal (hard steps cannot 'continue'); "
            f"use 'degrade', 'abort', or None"
        )
    if value not in FAILURE_POLICY_VALUES:
        raise ContractError(
            f"failure_policy {value!r} is invalid; legal: None, "
            f"{', '.join(sorted(FAILURE_POLICY_VALUES))}"
        )
    return value


def resolve_failure_policy(soft: bool, declared: Optional[str]) -> str:
    """Resolve the effective failure policy for *this step's own failure*.

    ``declared=None`` derives from the legacy soft flag:

    - ``soft=False`` → ``"abort"``
    - ``soft=True`` → ``"degrade"``

    There is no ``"continue"``. Explicit ``"degrade"`` / ``"abort"``
    values are returned unchanged (after validation).
    """
    validate_failure_policy(declared)
    if declared is not None:
        return declared
    return "degrade" if soft else "abort"


# ── Concurrency classes ────────────────────────────────────

CONCURRENCY_SAFE = "safe"
CONCURRENCY_ISOLATED = "isolated_only"
CONCURRENCY_EXCLUSIVE = "exclusive"

CONCURRENCY_CLASSES: FrozenSet[str] = frozenset(
    {CONCURRENCY_SAFE, CONCURRENCY_ISOLATED, CONCURRENCY_EXCLUSIVE}
)


def validate_concurrency_class(value: str) -> str:
    if value not in CONCURRENCY_CLASSES:
        raise ContractError(
            f"concurrency_class {value!r} is invalid; "
            f"legal: {', '.join(sorted(CONCURRENCY_CLASSES))}"
        )
    return value


def resources_conflict(
    reads_a: Iterable[str],
    writes_a: Iterable[str],
    reads_b: Iterable[str],
    writes_b: Iterable[str],
) -> bool:
    """True when two steps' declared resource sets interfere."""
    ra, wa = set(reads_a), set(writes_a)
    rb, wb = set(reads_b), set(writes_b)
    if wa & (rb | wb):
        return True
    if wb & (ra | wa):
        return True
    return False


def concurrency_compatible(
    class_a: str,
    reads_a: Iterable[str],
    writes_a: Iterable[str],
    class_b: str,
    reads_b: Iterable[str],
    writes_b: Iterable[str],
) -> bool:
    """Concurrency matrix (static, declarative — not runtime).

    - ``exclusive`` denies **all** pairs (with anything, including itself).
    - ``safe`` / ``isolated_only`` are mutually OK **iff** the declared
      resource sets do not conflict (see :func:`resources_conflict`).

    ``resource_capacity`` is intentionally **not** consulted here (M4/M5/F
    do not interpret it).
    """
    validate_concurrency_class(class_a)
    validate_concurrency_class(class_b)
    if class_a == CONCURRENCY_EXCLUSIVE or class_b == CONCURRENCY_EXCLUSIVE:
        return False
    return not resources_conflict(reads_a, writes_a, reads_b, writes_b)


# ── requires / optional_inputs namespaces ──────────────────

REQUIRES_ALLOWED_PREFIXES: Tuple[str, ...] = ("step.", "external.")
#: Declarative-only in M4. The Runner does **not** interpret requires.
REQUIRES_FORBIDDEN_PREFIXES: Tuple[str, ...] = ("condition.",)


def validate_requires(entries: Sequence[str]) -> Tuple[str, ...]:
    """Validate ``requires`` namespace (``step.*`` | ``external.*`` only)."""
    for item in entries:
        if any(item.startswith(p) for p in REQUIRES_FORBIDDEN_PREFIXES):
            raise ContractError(
                f"requires entry {item!r} uses a forbidden namespace "
                f"({'/'.join(REQUIRES_FORBIDDEN_PREFIXES)}); M4 is declarative-only "
                f"step.* / external.*"
            )
        if item.startswith("external."):
            validate_resource_ref(item)
            continue
        if item.startswith("step."):
            if len(item) <= len("step."):
                raise ContractError(f"requires entry {item!r} is an empty step name")
            continue
        raise ContractError(
            f"requires entry {item!r} must start with 'step.' or 'external.'"
        )
    return tuple(entries)


def validate_resource_capacity(value: Optional[int]) -> Optional[int]:
    """Schema-only capacity check: None or a positive int.

    M4/M5/F do **not** interpret capacity. The field is also **not**
    part of :class:`~movie_narrator.pipeline.dag.StepSpec` this phase.
    """
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ContractError(
            f"resource_capacity must be None or a positive int, got {value!r}"
        )
    return value


# ── Gate: validate a StepEntry / whole registry ────────────


def validate_step_entry(
    entry: "StepEntry",
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> List[str]:
    """Validate one step's contract fields. Returns a list of error strings."""
    cat = catalog if catalog is not None else default_catalog()
    errors: List[str] = []
    name = entry.name

    def _err(msg: str) -> None:
        errors.append(f"step '{name}': {msg}")

    # reads / writes: qualified ResourceRefs against the catalog.
    for kind, refs in (("reads", entry.reads), ("writes", entry.writes)):
        seen: set[str] = set()
        for ref in refs:
            try:
                validate_resource_ref(ref, catalog=cat)
            except ContractError as exc:
                _err(f"{kind}: {exc}")
                continue
            if ref in seen:
                _err(f"{kind}: duplicate ResourceRef {ref!r}")
            seen.add(ref)

    # inputs ⊆ reads / outputs ⊆ writes (legacy names normalized).
    for kind, declared, target_kind, target in (
        ("inputs", entry.inputs, "reads", set(entry.reads)),
        ("outputs", entry.outputs, "writes", set(entry.writes)),
    ):
        for legacy in declared:
            try:
                norm = normalize_legacy_ref(legacy, catalog=cat)
            except ContractError as exc:
                _err(f"{kind}: cannot normalize {legacy!r}: {exc}")
                continue
            if norm not in target:
                _err(
                    f"{kind}: {legacy!r} normalizes to {norm!r} "
                    f"which is not declared in {target_kind}"
                )

    # optional_inputs ⊆ reads (declarative-only; no runtime fallback).
    reads_set = set(entry.reads)
    for legacy in entry.optional_inputs:
        try:
            norm = normalize_legacy_ref(legacy, catalog=cat)
        except ContractError as exc:
            _err(f"optional_inputs: cannot normalize {legacy!r}: {exc}")
            continue
        if norm not in reads_set:
            _err(f"optional_inputs: {legacy!r} normalizes to {norm!r} which is not in reads")

    # requires namespace.
    try:
        validate_requires(entry.requires)
    except ContractError as exc:
        _err(f"requires: {exc}")

    # depends_on: unique.
    if len(set(entry.depends_on)) != len(entry.depends_on):
        _err(f"depends_on has duplicates: {entry.depends_on!r}")

    # concurrency_class.
    try:
        validate_concurrency_class(entry.concurrency_class)
    except ContractError as exc:
        _err(str(exc))

    # failure_policy enum (None | degrade | abort).
    try:
        validate_failure_policy(entry.failure_policy)
    except ContractError as exc:
        _err(str(exc))

    # capacity: schema-valid only.
    try:
        validate_resource_capacity(entry.resource_capacity)
    except ContractError as exc:
        _err(str(exc))

    return errors


def validate_step_contracts(
    registry: Optional["StepRegistry"] = None,
    *,
    catalog: Optional[ResourceCatalog] = None,
) -> List[str]:
    """Gate: validate every registered step's M4 contract fields.

    Checks (P4-5): prefix + Catalog, truth table via normalize_legacy_ref,
    inputs ⊆ reads, outputs ⊆ writes, optional ⊆ reads, requires
    namespace, depends_on unique, capacity schema, failure_policy enum.

    Returns:
        A list of human-readable error strings; empty means the registry
        passes the gate. Never raises for per-step contract mistakes.
    """
    from .registry import step_registry

    reg = step_registry if registry is None else registry
    cat = catalog if catalog is not None else default_catalog()
    errors: List[str] = []
    for entry in reg._entries.values():  # noqa: SLF001 — gate walks private store by design
        errors.extend(validate_step_entry(entry, catalog=cat))
    return errors
