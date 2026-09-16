# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""M5 E2 dual-baseline harness — reconstruct, disjoint diffs, identity.

Frozen E2 protocol (same S0; sequential; deterministic)::

    S0 = Context data + three independent run roots + frozen input fs
         + isolated cache + deterministic external fixtures
         + UsageLedger observational-only

    R  = execute(B, execute(A, S0))          # sequential baseline
    A' = execute(A, reconstruct(S0))         # independent branch
    B' = execute(B, reconstruct(S0))         # independent branch
    M  = apply_disjoint_diffs(base=S0, diff_A, diff_B)

    undeclared = branch_diff − declared_writes  → FAIL
    normalize(R) ≡ normalize(M)                → pass

``reconstruct`` is **not** ``deepcopy``: a new Context / CostTracker /
``step_state`` / ``status`` is created per branch (data fields are
value-copied so mutations cannot leak). Each branch receives an
independent services fixture (I3).

R3 identity
-----------

- small non-media file → content digest
- directory (clips)    → sorted ``(rel, size, sha256)`` manifest hash
- media deliverable    → :func:`normalize_deliverable_identity`
  (duration / codec / resolution / frame_count / subtitle from
  deliverable_manifest + media probe/QA facts; ``generated_at`` ignored)
- mtime                → diagnostic only, never identity

``StatePatch`` / adapters are intentionally **not** introduced here
(F phase).
"""

from __future__ import annotations

import copy
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Set, Tuple

from ..models import Context, Services
from ..utils.console import SilentConsole
from ..utils.cost_tracker import CostTracker
from .step_contracts import (
    PATH_TO_ARTIFACT,
    PREFIX_ARTIFACT,
    PREFIX_CTX,
    PREFIX_META,
    split_resource_ref,
)

__all__ = [
    "E2EquivalenceResult",
    "IDENTITY_MEDIA_FIELDS",
    "OBSERVATIONAL_META_KEYS",
    "R1_CONTROL_REFS",
    "VOLATILE_META_KEYS",
    "apply_disjoint_diffs",
    "apply_resource_diff",
    "compare_parallel_vs_sequential",
    "directory_identity",
    "diff_resources",
    "file_digest",
    "normalize_deliverable_identity",
    "normalize_diff_for_equivalence",
    "project_resources",
    "reconstruct_context_from_snapshot",
    "snapshot_context_data",
    "snapshot_resource_values",
    "strip_r1_control",
    "undeclared_resources",
]

_CHUNK = 65536

#: Artifact logical name → Context path field (reverse of PATH_TO_ARTIFACT).
_ARTIFACT_TO_PATH: Dict[str, str] = {v: k for k, v in PATH_TO_ARTIFACT.items()}

#: Control fields never copied into a branch snapshot / never identity.
_CONTROL_FIELDS: frozenset[str] = frozenset({"services", "cost_tracker", "step_state", "status"})

#: R1-control ResourceRefs stripped from raw diffs before equivalence.
R1_CONTROL_REFS: frozenset[str] = frozenset(
    {
        "ctx.status",
        "ctx.step_state",
        "ctx.services",
        "ctx.cost_tracker",
    }
)

#: Metadata keys that are timing / tracing / wall-clock — diagnostic only.
VOLATILE_META_KEYS: frozenset[str] = frozenset(
    {
        "generated_at",
        "elapsed_ms",
        "wall_time",
        "started_at",
        "finished_at",
        "trace_id",
        "span_id",
    }
)

#: Observational-only metadata (UsageLedger / cost) — excluded from
#: equivalence AND from undeclared-write detection.
OBSERVATIONAL_META_KEYS: frozenset[str] = frozenset({"usage"})

#: Stable media-identity fields for :func:`normalize_deliverable_identity`.
IDENTITY_MEDIA_FIELDS: Tuple[str, ...] = (
    "duration",
    "codec",
    "width",
    "height",
    "resolution",
    "frame_count",
    "subtitle",
    "audio_codec",
)


# ── Snapshot / reconstruct ──────────────────────────────────


def snapshot_context_data(ctx: Context) -> Dict[str, Any]:
    """Extract data-only snapshot (no services / cost / step_state / status).

    Values are deep-copied so later mutation of *ctx* cannot alias into
    the snapshot.
    """
    data: Dict[str, Any] = {}
    for name in Context.model_fields:
        if name in _CONTROL_FIELDS:
            continue
        data[name] = copy.deepcopy(getattr(ctx, name))
    return data


def reconstruct_context_from_snapshot(
    snapshot: Mapping[str, Any],
    *,
    services: Optional[Services] = None,
    output_dir: Optional[str] = None,
) -> Context:
    """Build a **new** Context from a data snapshot.

    Not ``deepcopy``: ``services``, ``cost_tracker``, ``step_state``, and
    ``status`` are always fresh instances. Data fields are value-copied
    (``copy.deepcopy``) so the three E2 branches cannot share mutable
    state.

    Args:
        snapshot: Output of :func:`snapshot_context_data` (or equivalent).
        services: Optional independent services fixture (I3). Defaults to
            a silent console.
        output_dir: Override ``output_dir`` (independent run root).

    Returns:
        A ready-to-execute Context.
    """
    payload: Dict[str, Any] = {}
    for key, value in snapshot.items():
        if key in _CONTROL_FIELDS:
            continue
        payload[key] = copy.deepcopy(value)
    if output_dir is not None:
        payload["output_dir"] = output_dir
    payload["services"] = services if services is not None else Services(console=SilentConsole())
    # Fresh control objects — never inherited from the snapshot.
    payload.pop("status", None)
    payload.pop("step_state", None)
    payload.pop("cost_tracker", None)
    ctx = Context(**payload)
    ctx.cost_tracker = CostTracker()
    return ctx


# ── Resource projection / diff ──────────────────────────────


def project_resources(ctx: Context, refs: Iterable[str]) -> Dict[str, Any]:
    """Project the given ResourceRefs from *ctx* into a plain dict."""
    out: Dict[str, Any] = {}
    for ref in refs:
        try:
            prefix, name = split_resource_ref(ref)
        except Exception:  # noqa: BLE001 — skip illegal refs defensively
            prefix, name = "", ""
        if not prefix:
            continue
        if prefix == PREFIX_CTX:
            out[ref] = copy.deepcopy(getattr(ctx, name, None))
        elif prefix == PREFIX_META:
            out[ref] = copy.deepcopy(ctx.metadata.get(name))
        elif prefix == PREFIX_ARTIFACT:
            path_field = _ARTIFACT_TO_PATH.get(name)
            out[ref] = getattr(ctx, path_field, None) if path_field else None
        # external.* carries no Context value — omitted on purpose.
    return out


def snapshot_resource_values(ctx: Context) -> Dict[str, Any]:
    """Project every Context-bearing ResourceRef (R1 data + R2 meta + R3).

    Path fields in :data:`PATH_TO_ARTIFACT` are emitted **only** as
    ``artifact.*`` (their M4 identity) so a write to ``ctx.audio_path``
    does not double-count as both ``ctx.audio_path`` and
    ``artifact.narration_audio``.

    Used as the raw_diff substrate so undeclared writes outside a step's
    declared set are still visible.
    """
    from .step_contracts import CTX_DATA_FIELDS

    artifact_fields = set(PATH_TO_ARTIFACT)
    out: Dict[str, Any] = {}
    for name in sorted(CTX_DATA_FIELDS):
        if name in artifact_fields:
            continue  # represented as artifact.* below
        out[f"{PREFIX_CTX}{name}"] = copy.deepcopy(getattr(ctx, name, None))
    for key in list(ctx.metadata.keys()):
        out[f"{PREFIX_META}{key}"] = copy.deepcopy(ctx.metadata[key])
    for path_field, logical in PATH_TO_ARTIFACT.items():
        out[f"{PREFIX_ARTIFACT}{logical}"] = getattr(ctx, path_field, None)
    return out


def diff_resources(before: Mapping[str, Any], after: Mapping[str, Any]) -> Dict[str, Any]:
    """Return ``{ref: after_value}`` for refs whose value changed.

    Keys present on only one side count as changed when the value is not
    ``None`` on that side (missing ≈ None).
    """
    keys = set(before) | set(after)
    changed: Dict[str, Any] = {}
    for ref in keys:
        b = before.get(ref)
        a = after.get(ref)
        if b != a:
            changed[ref] = a
    return changed


def strip_r1_control(
    diff: Mapping[str, Any],
    *,
    extra_volatile_meta: Iterable[str] = (),
) -> Dict[str, Any]:
    """Drop R1-control refs and observational/volatile metadata keys."""
    volatile = set(VOLATILE_META_KEYS) | set(OBSERVATIONAL_META_KEYS) | set(extra_volatile_meta)
    out: Dict[str, Any] = {}
    for ref, value in diff.items():
        if ref in R1_CONTROL_REFS:
            continue
        if ref.startswith(PREFIX_META):
            key = ref[len(PREFIX_META) :]
            if key in volatile:
                continue
        out[ref] = value
    return out


def undeclared_resources(
    branch_diff: Mapping[str, Any], declared_writes: Iterable[str]
) -> Set[str]:
    """``branch_diff − declared_writes`` (observational keys already stripped)."""
    return set(branch_diff) - set(declared_writes)


def apply_resource_diff(ctx: Context, diff: Mapping[str, Any]) -> Context:
    """Apply a ResourceRef → value diff onto *ctx* (mutates and returns it)."""
    for ref, value in diff.items():
        try:
            prefix, name = split_resource_ref(ref)
        except Exception:  # noqa: BLE001
            prefix, name = "", ""
        if not prefix:
            continue
        if prefix == PREFIX_CTX:
            setattr(ctx, name, value)
        elif prefix == PREFIX_META:
            ctx.metadata[name] = value
        elif prefix == PREFIX_ARTIFACT:
            path_field = _ARTIFACT_TO_PATH.get(name)
            if path_field:
                setattr(ctx, path_field, value)
    return ctx


@dataclass(frozen=True)
class DisjointMergeResult:
    """Outcome of resource-level disjoint merge."""

    context: Context
    conflicts: Tuple[str, ...] = ()


def apply_disjoint_diffs(
    base: Context,
    diff_a: Mapping[str, Any],
    diff_b: Mapping[str, Any],
) -> DisjointMergeResult:
    """Resource-level disjoint merge of two branch diffs onto *base*.

    Same logical ResourceRef written by both branches → **conflict**
    (even when the values happen to equal). No nested dict merge: keys
    are ResourceRefs, not path segments.

    Args:
        base: Fresh context (typically ``reconstruct(S0)``).
        diff_a / diff_b: ResourceRef → value maps from each branch.

    Returns:
        :class:`DisjointMergeResult` with conflicts listed and only the
        non-conflicting keys applied.
    """
    shared = sorted(set(diff_a) & set(diff_b))
    merged_ctx = reconstruct_context_from_snapshot(
        {
            k: copy.deepcopy(getattr(base, k))
            for k in Context.model_fields
            if k not in _CONTROL_FIELDS
        },
        output_dir=base.output_dir,
    )
    for ref, value in diff_a.items():
        if ref in shared:
            continue
        apply_resource_diff(merged_ctx, {ref: value})
    for ref, value in diff_b.items():
        if ref in shared:
            continue
        apply_resource_diff(merged_ctx, {ref: value})
    return DisjointMergeResult(context=merged_ctx, conflicts=tuple(shared))


# ── Deliverable / media identity (R3) ───────────────────────


def file_digest(path: str | Path) -> str:
    """SHA-256 content digest of a small non-media file."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def directory_identity(path: str | Path) -> Dict[str, Any]:
    """Stable identity for a directory artifact (e.g. clips/).

    Manifest is ``sorted(rel_path, size, sha256)``; the returned dict
    carries both the entries and their combined hash. mtime is **not**
    part of the identity.
    """
    root = Path(path)
    entries: List[Tuple[str, int, str]] = []
    if root.is_dir():
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            entries.append((rel, p.stat().st_size, file_digest(p)))
    blob = "\n".join(f"{rel}|{size}|{sha}" for rel, size, sha in entries)
    return {
        "kind": "directory",
        "entries": entries,
        "hash": hashlib.sha256(blob.encode("utf-8")).hexdigest(),
    }


def normalize_deliverable_identity(
    *,
    manifest: Optional[Mapping[str, Any]] = None,
    media_facts: Optional[Mapping[str, Any]] = None,
    qa_facts: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    """Stable media identity for E2 equivalence (E2 tool, not the manifest).

    Source priority (later wins on the same key):

    1. selected fields from *manifest* artifacts / qa blocks
    2. *qa_facts* (``video_qa`` metrics)
    3. *media_facts* (probe)

    Always ignores ``generated_at`` and mtime. Returns a dict containing
    only identity-relevant keys that were actually observed (missing
    fields are omitted rather than defaulted — absence is meaningful).
    """
    identity: Dict[str, Any] = {}

    def _absorb(src: Mapping[str, Any]) -> None:
        for key in IDENTITY_MEDIA_FIELDS:
            if key in src and src[key] is not None:
                identity[key] = src[key]
        # Common nested shapes: {"streams": {...}}, {"metrics": {...}}.
        for nest_key in ("metrics", "video", "streams"):
            nested = src.get(nest_key)
            if isinstance(nested, Mapping):
                for key in IDENTITY_MEDIA_FIELDS:
                    if key in nested and nested[key] is not None:
                        identity[key] = nested[key]
        res = src.get("resolution")
        if res is None and src.get("width") is not None and src.get("height") is not None:
            identity["resolution"] = f"{src['width']}x{src['height']}"

    if manifest:
        # DeliverableManifest.to_dict() → {"artifacts": [...], "qa": {...}, ...}
        qa_block = manifest.get("qa")
        if isinstance(qa_block, Mapping):
            for v in qa_block.values():
                if isinstance(v, Mapping):
                    _absorb(v)
        _absorb(manifest)
    if qa_facts:
        _absorb(qa_facts)
    if media_facts:
        _absorb(media_facts)

    if identity.get("resolution") is None and identity.get("width") is not None:
        h = identity.get("height")
        if h is not None:
            identity["resolution"] = f"{identity['width']}x{h}"

    identity.pop("generated_at", None)
    identity["kind"] = "media_identity"
    return identity


def normalize_diff_for_equivalence(
    diff: Mapping[str, Any],
    *,
    extra_volatile_meta: Iterable[str] = (),
) -> Dict[str, Any]:
    """Strip R1-control / observational / volatile keys from a branch diff.

    Path-valued artifact refs are reduced to their basename so two
    independent run roots compare equal when the logical artifact name
    matches. Callers that need media identity should feed
    :func:`normalize_deliverable_identity` results into the diff first.
    """
    stripped = strip_r1_control(diff, extra_volatile_meta=extra_volatile_meta)
    out: Dict[str, Any] = {}
    for ref, value in stripped.items():
        if ref.startswith(PREFIX_ARTIFACT) and isinstance(value, str) and value:
            out[ref] = Path(value).name
        elif (
            ref.startswith(PREFIX_CTX)
            and ref.endswith(("_path", "_dir"))
            and isinstance(value, str)
            and value
        ):
            out[ref] = Path(value).name
        else:
            out[ref] = value
    return out


# ── Dual-baseline comparison ────────────────────────────────


@dataclass
class E2EquivalenceResult:
    """Outcome of one dual-baseline comparison (unit-testable with mocks)."""

    equivalent: bool
    undeclared_a: Tuple[str, ...] = ()
    undeclared_b: Tuple[str, ...] = ()
    merge_conflicts: Tuple[str, ...] = ()
    branch_diff_a: Dict[str, Any] = field(default_factory=dict)
    branch_diff_b: Dict[str, Any] = field(default_factory=dict)
    sequential_diff: Dict[str, Any] = field(default_factory=dict)
    merged_diff: Dict[str, Any] = field(default_factory=dict)
    notes: Tuple[str, ...] = field(default_factory=tuple)


def compare_parallel_vs_sequential(
    s0: Context,
    *,
    execute_a: Callable[[Context], Context],
    execute_b: Callable[[Context], Context],
    writes_a: Iterable[str],
    writes_b: Iterable[str],
    make_branch_root: Optional[Callable[[str], str]] = None,
) -> E2EquivalenceResult:
    """Run the frozen E2 dual-baseline comparison with injectable executors.

    Designed for **unit tests with mocks/fakes** — no real video, LLM, or
    TTS required. Integration-style runs that exercise the real pipeline
    should be marked ``@pytest.mark.integration``.

    Protocol::

        R  = execute(B, execute(A, reconstruct(S0)))
        A' = execute(A, reconstruct(S0))
        B' = execute(B, reconstruct(S0))
        M  = apply_disjoint_diffs(reconstruct(S0), diff_A, diff_B)

    Pass when both branches declare all their writes (no undeclared), the
    merge has no dual-write conflicts, and
    ``normalize(R) == normalize(M)`` as ResourceRef diffs versus S0.
    """
    snap = snapshot_context_data(s0)

    if make_branch_root is None:
        base_root = Path(s0.output_dir)

        def make_branch_root(label: str) -> str:
            return str(base_root / f"e2_{label}")

    notes: List[str] = []

    # Sequential baseline R (three independent roots).
    r_base = reconstruct_context_from_snapshot(snap, output_dir=make_branch_root("seq"))
    r_before = snapshot_resource_values(r_base)
    r_ctx = execute_b(execute_a(r_base))
    r_after = snapshot_resource_values(r_ctx)
    raw_seq = diff_resources(r_before, r_after)
    seq_norm = normalize_diff_for_equivalence(raw_seq)

    # Independent branch A'.
    a_base = reconstruct_context_from_snapshot(snap, output_dir=make_branch_root("a"))
    a_before = snapshot_resource_values(a_base)
    a_ctx = execute_a(a_base)
    a_after = snapshot_resource_values(a_ctx)
    raw_a = diff_resources(a_before, a_after)
    a_stripped = strip_r1_control(raw_a)

    # Independent branch B'.
    b_base = reconstruct_context_from_snapshot(snap, output_dir=make_branch_root("b"))
    b_before = snapshot_resource_values(b_base)
    b_ctx = execute_b(b_base)
    b_after = snapshot_resource_values(b_ctx)
    raw_b = diff_resources(b_before, b_after)
    b_stripped = strip_r1_control(raw_b)

    undeclared_a = sorted(undeclared_resources(a_stripped, writes_a))
    undeclared_b = sorted(undeclared_resources(b_stripped, writes_b))
    if undeclared_a:
        notes.append(f"undeclared writes from A: {undeclared_a}")
    if undeclared_b:
        notes.append(f"undeclared writes from B: {undeclared_b}")

    m_base = reconstruct_context_from_snapshot(snap, output_dir=make_branch_root("merge"))
    merge = apply_disjoint_diffs(m_base, a_stripped, b_stripped)
    if merge.conflicts:
        notes.append(f"dual-write conflicts: {list(merge.conflicts)}")

    merged_raw = diff_resources(
        snapshot_resource_values(m_base), snapshot_resource_values(merge.context)
    )
    merged_norm = normalize_diff_for_equivalence(merged_raw)

    equivalent = (
        not undeclared_a and not undeclared_b and not merge.conflicts and seq_norm == merged_norm
    )
    if not equivalent and not notes:
        notes.append("normalize(R) ≠ normalize(M)")

    return E2EquivalenceResult(
        equivalent=equivalent,
        undeclared_a=tuple(undeclared_a),
        undeclared_b=tuple(undeclared_b),
        merge_conflicts=merge.conflicts,
        branch_diff_a=dict(a_stripped),
        branch_diff_b=dict(b_stripped),
        sequential_diff=seq_norm,
        merged_diff=merged_norm,
        notes=tuple(notes),
    )
