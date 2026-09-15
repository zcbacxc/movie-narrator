# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Canonical built-in step registry fixture (M5 / H6).

Single truth source
-------------------

The 16 built-in steps are **not** hand-copied here. This module
generates a fresh :class:`~movie_narrator.pipeline.registry.StepRegistry`
from the same three dicts that populate the process-global registry at
import time in ``pipeline/runner.py``:

1. ``runner._BUILTIN_STEP_META`` — name → (func, soft, status_field, consequence)
2. ``runner._BUILTIN_STEP_IO``   — coarse inputs / outputs / depends_on
3. ``builtin_contracts.BUILTIN_STEP_CONTRACTS`` — M4 ResourceRef + semantics

Analysis code (waves, L2 planning, Gate-2, E2) must use
:func:`build_canonical_registry` rather than the global
``step_registry``, so plugin registrations cannot pollute the built-in
graph. CI asserts the generated fixture matches the global registry's
built-in entries (same names/order/coarse IO/depends_on/soft/status_field).
"""

from __future__ import annotations

from functools import lru_cache

from .registry import StepRegistry


@lru_cache(maxsize=1)
def build_canonical_registry() -> StepRegistry:
    """Build a fresh registry containing **only** the 16 built-in steps.

    Generated from the single built-in truth source. The returned
    registry is process-cached but never mutated by analysis code;
    callers that need a disposable copy should construct another via
    :func:`make_canonical_registry`.

    Returns:
        A :class:`StepRegistry` whose ``ordered_names()`` is the frozen
        built-in linear order.
    """
    return make_canonical_registry()


def make_canonical_registry() -> StepRegistry:
    """Build a fresh (uncached) canonical registry from the truth source."""
    # Imported lazily so importing this module stays light and avoids a
    # cycle: runner does not import canonical.
    from .builtin_contracts import BUILTIN_STEP_CONTRACTS
    from .runner import _BUILTIN_STEP_IO, _BUILTIN_STEP_META

    reg = StepRegistry()
    for name, (func, soft, field, consequence) in _BUILTIN_STEP_META.items():
        if reg.contains(name):
            continue
        io = _BUILTIN_STEP_IO.get(name, {})
        m4 = BUILTIN_STEP_CONTRACTS.get(name, {})
        reg.register(
            name,
            func,
            soft=soft,
            status_field=field,
            consequence=consequence,
            inputs=io.get("inputs", ()),
            outputs=io.get("outputs", ()),
            depends_on=io.get("depends_on", ()),
            reads=m4.get("reads", ()),
            writes=m4.get("writes", ()),
            idempotent=m4.get("idempotent", False),
            concurrency_class=m4.get("concurrency_class", "isolated_only"),
            requires=m4.get("requires", ()),
            optional_inputs=m4.get("optional_inputs", ()),
            failure_policy=m4.get("failure_policy", None),
            resource_capacity=m4.get("resource_capacity", None),
        )
    return reg


#: Contract fields compared when asserting fixture ≡ global builtin registry.
CANONICAL_MATCH_FIELDS: tuple[str, ...] = (
    "name",
    "soft",
    "status_field",
    "inputs",
    "outputs",
    "depends_on",
    "reads",
    "writes",
    "idempotent",
    "concurrency_class",
    "requires",
    "optional_inputs",
    "failure_policy",
)


def canonical_mismatch_against_global() -> list[str]:
    """Compare the canonical fixture to the global registry built-ins.

    Only the 16 built-in names are compared; plugin entries in the global
    registry are ignored (analysis never reads them).

    Returns:
        Human-readable mismatch strings; empty means the fixture matches.
    """
    from .registry import step_registry

    canonical = build_canonical_registry()
    errors: list[str] = []
    c_names = canonical.ordered_names()
    g_names = step_registry.ordered_names()
    if c_names != g_names:
        errors.append(f"ordered_names mismatch: canonical={c_names!r} global={g_names!r}")
    for name in c_names:
        ce = canonical.get(name)
        ge = step_registry.get(name)
        if ge is None:
            errors.append(f"step {name!r} missing from global registry")
            continue
        assert ce is not None
        for field in CANONICAL_MATCH_FIELDS:
            cv = getattr(ce, field)
            gv = getattr(ge, field)
            if cv != gv:
                errors.append(f"step {name!r} field {field!r}: canonical={cv!r} global={gv!r}")
    return errors
