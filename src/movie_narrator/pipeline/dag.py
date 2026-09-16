# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Linear-compatible DAG contract for pipeline steps (v1.3.0).

This module turns the :class:`~movie_narrator.pipeline.registry.StepRegistry`
metadata (``inputs`` / ``outputs`` / ``depends_on`` declared at
registration time) into an explicit, inspectable step graph.

**The runner still executes linearly.** ``run_pipeline`` keeps its flat
for-loop over the registered order; nothing in this module schedules,
reorders, or parallelizes anything. Today the graph enables:

- **contract validation** — plugins can check that their declared
  dependencies are registered and ordered compatibly with the linear
  execution order (:func:`validate_linear_order`);
- **a linear adapter guarantee** — :func:`topological_order` reproduces
  the registry's linear order for any linear-compatible registry, so a
  future parallel scheduler can be introduced without changing the
  step graph semantics.

Nothing parallel runs today.

Declaration convention
----------------------

- ``inputs`` / ``outputs`` are **coarse names**: each entry is the
  *Context attribute name* (e.g. ``"audio_path"``, ``"matched_clips"``)
  or the *``ctx.metadata`` key name* (e.g. ``"match_summary"``,
  ``"qa_gate"``) that the step reads or writes. Names are not qualified
  by domain; a step that reads a value produced under another name does
  not declare it.
- ``depends_on`` lists the **upstream steps whose outputs the step
  reads** — direct data dependencies only, not transitive ones, and not
  necessarily every producer of every declared input. All entries of a
  linear-compatible registry point backwards in the linear order.
- Declarations are **advisory**: the runner never enforces them. They
  exist for validation (:func:`validate_linear_order`) and for future
  parallel scheduling.

Since v1.4.0 (M4), :class:`StepSpec` also mirrors the qualified
ResourceRef sets (``reads`` / ``writes``) and execution-semantics fields
(``idempotent``, ``concurrency_class``, ``requires``, ``optional_inputs``,
``failure_policy``). Those are validated by
``pipeline/step_contracts.validate_step_contracts`` — still advisory;
the runner stays linear and does not interpret them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .registry import StepRegistry, step_registry


@dataclass(frozen=True)
class StepSpec:
    """Resolved, immutable I/O contract for one pipeline step.

    Mirrors **only** the resolved same-name contract fields from
    :class:`~movie_narrator.pipeline.registry.StepEntry`. Explicitly
    **not** mirrored this phase:

    - ``resource_capacity`` (schema-only; M4/M5/F do not interpret it)
    - ``func`` / ``seq`` / ``insert_after`` / ``insert_before``
      (execution/ordering internals, not the contract surface)
    """

    name: str
    inputs: tuple
    outputs: tuple
    depends_on: tuple
    soft: bool
    status_field: str | None
    # M4 ResourceRef sets + execution-semantics (conservative defaults
    # when a step was registered without them).
    reads: tuple = ()
    writes: tuple = ()
    idempotent: bool = False
    concurrency_class: str = "isolated_only"
    requires: tuple = ()
    optional_inputs: tuple = ()
    failure_policy: str | None = None


def build_step_graph(registry: StepRegistry | None = None) -> dict[str, StepSpec]:
    """Build the step graph from a registry.

    Args:
        registry: The step registry to inspect. Defaults to the global
            :data:`~movie_narrator.pipeline.registry.step_registry`.

    Returns:
        A mapping of step name to :class:`StepSpec` covering every
        registered step (built-ins and plugins). Field equality holds
        for every resolved same-name contract field listed on
        :class:`StepSpec`.
    """
    reg = step_registry if registry is None else registry
    specs: dict[str, StepSpec] = {}
    for entry in reg.info():
        specs[entry["name"]] = StepSpec(
            name=entry["name"],
            inputs=tuple(entry["inputs"]),
            outputs=tuple(entry["outputs"]),
            depends_on=tuple(entry["depends_on"]),
            soft=bool(entry["soft"]),
            status_field=entry["status_field"],
            reads=tuple(entry["reads"]),
            writes=tuple(entry["writes"]),
            idempotent=bool(entry["idempotent"]),
            concurrency_class=entry["concurrency_class"],
            requires=tuple(entry["requires"]),
            optional_inputs=tuple(entry["optional_inputs"]),
            failure_policy=entry["failure_policy"],
        )
    return specs


def validate_linear_order(registry: StepRegistry | None = None) -> list[str]:
    """Check that a registry's declared dependencies are linear-compatible.

    Advises (returns warning strings, never raises) when:

    - a declared dependency names a step that is not registered;
    - a dependency is ordered AFTER the dependent step in the linear
      execution order (executing linearly would read data that does not
      exist yet);
    - the dependency graph contains a cycle.

    Args:
        registry: The step registry to validate. Defaults to the global
            registry.

    Returns:
        Advisory warnings. An empty list means the registry is
        linear-compatible.
    """
    reg = step_registry if registry is None else registry
    ordered = reg.ordered_names()
    position = {name: idx for idx, name in enumerate(ordered)}
    graph = build_step_graph(reg)

    warnings: list[str] = []
    for name, spec in graph.items():
        pos = position.get(name)
        for dep in spec.depends_on:
            if dep not in graph:
                warnings.append(
                    f"step '{name}' declares dependency '{dep}' which is not a registered step"
                )
            elif pos is not None and position.get(dep, pos) > pos:
                warnings.append(
                    f"step '{name}' (linear position {pos}) declares dependency "
                    f"'{dep}' ordered later (linear position {position.get(dep)}) — "
                    f"violates linear execution order"
                )

    try:
        topological_order(reg)
    except ValueError as exc:
        warnings.append(str(exc))
    return warnings


def topological_order(registry: StepRegistry | None = None) -> list[str]:
    """Kahn's algorithm over ``depends_on`` with linear tie-breaking.

    Among all nodes whose dependencies are satisfied, the one earliest in
    the registry's linear order is emitted first. This makes the
    function a **linear adapter**: whenever every declared dependency
    points backwards in the linear order (the linear-compatible case),
    the result is exactly the registry's linear order — for the 16
    built-in steps, :func:`topological_order` equals
    ``step_registry.ordered_names()``.

    Args:
        registry: The step registry to order. Defaults to the global
            registry.

    Returns:
        Step names in topological order.

    Raises:
        ValueError: if the dependency graph contains a cycle.
    """
    reg = step_registry if registry is None else registry
    ordered = reg.ordered_names()
    graph = build_step_graph(reg)

    # Nodes come from the graph (every registered entry). Positions come
    # from the linear order; nodes missing from it (possible when one
    # callable object was registered under several names) get stable
    # fallback positions after the ordered ones.
    position = {name: idx for idx, name in enumerate(ordered)}
    fallback = len(position)
    for name in graph:
        if name not in position:
            position[name] = fallback
            fallback += 1

    in_degree = {name: 0 for name in graph}
    dependents: dict[str, list[str]] = {name: [] for name in graph}
    for name in graph:
        for dep in graph[name].depends_on:
            # Self-dependencies are left in place on purpose: they never
            # resolve, so they are reported as cycles below.
            if dep in graph:
                in_degree[name] += 1
                dependents[dep].append(name)

    available = [name for name in graph if in_degree[name] == 0]
    result: list[str] = []
    while available:
        # Deterministic tie-break: earliest linear position first.
        pick = min(available, key=lambda n: position[n])
        available.remove(pick)
        result.append(pick)
        for dependent in dependents[pick]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                available.append(dependent)

    if len(result) != len(graph):
        resolved = set(result)
        stuck = [name for name in graph if name not in resolved]
        raise ValueError(
            f"Dependency cycle detected among pipeline steps: {', '.join(stuck)}"
        )
    return result
