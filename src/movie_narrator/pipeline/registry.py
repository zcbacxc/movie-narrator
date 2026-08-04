# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Step registry for the pipeline.

Provides a registration mechanism so that pipeline steps can be
discovered, ordered, and extended by external plugins.

Design:

- Built-in steps are registered at import time via :func:`register_step`
  or the :func:`step` decorator.
- External plugins call ``register_step("my_step", func, after="render_video")``
  to inject custom logic into the pipeline.
- The runner derives ``STEPS`` from the registry's ordered list,
  preserving backward compatibility with existing code that iterates
  ``STEPS``.

The registry tracks:

- **name**: unique step identifier (conventionally the function ``__name__``)
- **func**: the callable ``Context -> Context``
- **soft**: whether exceptions are caught (True) or re-raised (False)
- **status_field**: the ``PipelineStatus`` field name for soft steps
- **consequence**: human-readable degradation message for soft steps
- **insert_after** / **insert_before**: ordering hints for plugin steps

Ordering rules:

1. Built-in steps have a fixed relative order (the order they were
   registered in ``runner.py``).
2. Plugin steps with ``after="X"`` are placed immediately after step X.
3. Plugin steps with ``before="Y"`` are placed immediately before step Y.
4. Plugin steps with neither are appended to the end.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from ..models import Context

# Type alias for a step function: Context -> Context
StepFunc = Callable[[Context], Context]


@dataclass(frozen=True)
class StepEntry:
    """Metadata for a registered pipeline step."""

    name: str
    func: StepFunc
    soft: bool = False
    status_field: Optional[str] = None
    consequence: str = ""
    # Ordering: built-in steps use seq; plugin steps use after/before.
    seq: int = -1  # -1 means "unsequenced" (plugin step)
    insert_after: Optional[str] = None
    insert_before: Optional[str] = None


class StepRegistry:
    """Registry for pipeline steps.

    A single global instance :data:`step_registry` is shared across the
    process. Built-in steps are registered during module import;
    external plugins register via :func:`register_step`.
    """

    def __init__(self) -> None:
        self._entries: Dict[str, StepEntry] = {}
        self._seq_counter: int = 0

    # ── Registration ──────────────────────────────────────────

    def register(
        self,
        name: str,
        func: StepFunc,
        *,
        soft: bool = False,
        status_field: Optional[str] = None,
        consequence: str = "",
        after: Optional[str] = None,
        before: Optional[str] = None,
    ) -> StepFunc:
        """Register a step and return *func* (for decorator use).

        Args:
            name: Unique step identifier.
            func: The step callable ``Context -> Context``.
            soft: If True, exceptions from this step are caught and
                rendered as warnings instead of aborting the pipeline.
            status_field: For soft steps, the ``PipelineStatus`` field
                name to set on failure/skip.
            consequence: Human-readable message shown when the soft
                step degrades.
            after: Insert this step immediately after the named step
                (for plugin steps only).
            before: Insert this step immediately before the named step
                (for plugin steps only).

        Raises:
            ValueError: if *name* is already registered.
        """
        if name in self._entries:
            raise ValueError(
                f"Step '{name}' is already registered. Use a different name or unregister first."
            )

        is_builtin = after is None and before is None
        seq = self._seq_counter if is_builtin else -1
        if is_builtin:
            self._seq_counter += 1

        entry = StepEntry(
            name=name,
            func=func,
            soft=soft,
            status_field=status_field,
            consequence=consequence,
            seq=seq,
            insert_after=after,
            insert_before=before,
        )
        self._entries[name] = entry
        return func

    def unregister(self, name: str) -> None:
        """Remove a step from the registry.

        Mainly useful for testing. Built-in steps should not be
        unregistered in production code.
        """
        if name not in self._entries:
            raise KeyError(f"Step '{name}' is not registered.")
        del self._entries[name]

    # ── Lookup ────────────────────────────────────────────────

    def get(self, name: str) -> Optional[StepEntry]:
        """
        Returns:
            The entry for *name*, or None.
        """
        return self._entries.get(name)

    def get_func(self, name: str) -> Optional[StepFunc]:
        """
        Returns:
            The callable for *name*, or None.
        """
        entry = self._entries.get(name)
        return entry.func if entry else None

    def names(self) -> List[str]:
        """
        Returns:
            All registered step names (unordered).
        """
        return list(self._entries.keys())

    def contains(self, name: str) -> bool:
        """Check if a step name is registered."""
        return name in self._entries

    # ── Ordered list ──────────────────────────────────────────

    def ordered_steps(self) -> List[StepFunc]:
        """
        Returns:
            Step functions in execution order.

            Algorithm:

            1. Built-in steps (seq >= 0) sorted by seq.
            2. Plugin steps with ``after`` inserted right after the target.
            3. Plugin steps with ``before`` inserted right before the target.
            4. Plugin steps with neither appended to the end.

            If a plugin step's ``after``/``before`` target is not found,
            the step is appended to the end with a debug warning.
        """
        ordered: List[StepFunc] = []
        placed: set[str] = set()

        # Phase 1: built-in steps in seq order
        builtin = sorted(
            (e for e in self._entries.values() if e.seq >= 0),
            key=lambda e: e.seq,
        )
        for entry in builtin:
            ordered.append(entry.func)
            placed.add(entry.name)

            # Phase 2: insert "after" plugin steps right after this step
            for plugin in self._entries.values():
                if plugin.name in placed:
                    continue
                if plugin.insert_after == entry.name:
                    ordered.append(plugin.func)
                    placed.add(plugin.name)

            # Phase 3: insert "before" plugin steps — but "before" means
            # they go BEFORE the *next* built-in, so we handle them in
            # a separate pass below.

        # Phase 3 (deferred): "before" plugin steps
        # We need to insert them before their target. Since built-in
        # steps are already placed, we rebuild the list with inserts.
        if any(e.insert_before for e in self._entries.values() if e.name not in placed):
            new_ordered: List[StepFunc] = []
            for func in ordered:
                # Find the entry for this func to get its name
                func_entry = None
                for e in self._entries.values():
                    if e.func is func:
                        func_entry = e
                        break
                if func_entry:
                    # Insert any "before" plugins targeting this step
                    for plugin in self._entries.values():
                        if plugin.name in placed:
                            continue
                        if plugin.insert_before == func_entry.name:
                            new_ordered.append(plugin.func)
                            placed.add(plugin.name)
                new_ordered.append(func)
            ordered = new_ordered

        # Phase 4: append remaining unplaced plugin steps
        for entry in self._entries.values():
            if entry.name not in placed:
                ordered.append(entry.func)
                placed.add(entry.name)

        return ordered

    def ordered_names(self) -> List[str]:
        """
        Returns:
            Step names in execution order.
        """
        func_to_name = {}
        for entry in self._entries.values():
            # Use id() as key to handle cases where the same callable
            # might appear (shouldn't happen, but be safe)
            func_to_name[id(entry.func)] = entry.name

        result: List[str] = []
        for func in self.ordered_steps():
            name = func_to_name.get(id(func))
            if name:
                result.append(name)
        return result

    # ── Soft-step metadata ────────────────────────────────────

    def soft_step_names(self) -> set[str]:
        """
        Returns:
            The set of soft step names.
        """
        return {e.name for e in self._entries.values() if e.soft}

    def status_field_for(self, name: str) -> Optional[str]:
        """
        Returns:
            The status field for a soft step, or None.
        """
        entry = self._entries.get(name)
        return entry.status_field if entry else None

    def consequence_for(self, name: str) -> str:
        """
        Returns:
            The degradation message for a soft step.
        """
        entry = self._entries.get(name)
        return entry.consequence if entry else ""

    # ── Introspection ─────────────────────────────────────────

    def info(self) -> List[Dict[str, Any]]:
        """
        Returns:
            A list of dicts describing each registered step.
        """
        return [
            {
                "name": e.name,
                "soft": e.soft,
                "status_field": e.status_field,
                "seq": e.seq,
                "insert_after": e.insert_after,
                "insert_before": e.insert_before,
            }
            for e in self._entries.values()
        ]

    def clear(self) -> None:
        """Remove all registered steps. For testing only."""
        self._entries.clear()
        self._seq_counter = 0


# ── Global registry instance ──────────────────────────────

step_registry = StepRegistry()


# ── Decorator ─────────────────────────────────────────────


def register_step(
    name: str,
    *,
    soft: bool = False,
    status_field: Optional[str] = None,
    consequence: str = "",
    after: Optional[str] = None,
    before: Optional[str] = None,
) -> Callable[[StepFunc], StepFunc]:
    """Decorator to register a pipeline step.

    Usage (built-in step)::

        @register_step("detect_scenes", soft=True, status_field="scene")
        def detect_scenes(ctx: Context) -> Context:
            ...

    Usage (external plugin step)::

        from movie_narrator import register_step, Context

        @register_step("add_watermark", after="render_video")
        def add_watermark(ctx: Context) -> Context:
            ...
    """

    def decorator(func: StepFunc) -> StepFunc:
        """Register a decorator function."""
        return step_registry.register(
            name,
            func,
            soft=soft,
            status_field=status_field,
            consequence=consequence,
            after=after,
            before=before,
        )

    return decorator


# Backward-compat alias: ``step`` is a shorter name for the decorator.
step = register_step
