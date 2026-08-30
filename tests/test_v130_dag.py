# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.3.0 Feature 2 — linear-compatible DAG contract.

Covers:
- All 16 built-in steps have StepSpecs with non-empty contracts.
- ``topological_order`` equals ``step_registry.ordered_names()`` for the
  built-in registry (the linear adapter guarantee).
- ``validate_linear_order`` flags a plugin step whose ``depends_on``
  points at a later-ordered step, an unregistered dependency, and a
  dependency cycle; empty list for the built-in registry.
- Cycle detection raises ``ValueError`` in ``topological_order``.
- Registry ``register()`` / ``@register_step`` accept the new
  keyword-only I/O arguments; ``info()`` exposes them.
- Contract exports are importable.
"""

from __future__ import annotations

import pytest

from movie_narrator.contract import (
    StepSpec,
    build_step_graph,
    topological_order,
    validate_linear_order,
)
from movie_narrator.models import Context
from movie_narrator.pipeline.dag import build_step_graph as _build_step_graph
from movie_narrator.pipeline.registry import StepRegistry, register_step, step_registry
from movie_narrator.pipeline.runner import STEPS

BUILTIN_NAMES = [s.__name__ for s in STEPS]

#: The 8 soft steps declared in runner.py's _BUILTIN_STEP_META.
SOFT_BUILTINS = {
    "research_plot",
    "align_audio",
    "detect_scenes",
    "match_clips",
    "mix_bgm",
    "translate_subtitles",
    "run_qa_gate",
    "export_clips",
}


def _noop(ctx: Context) -> Context:
    return ctx


def _make_step():
    """Return a DISTINCT no-op step function per call.

    StepRegistry keys entries by function identity, so each registered
    name needs its own callable object.
    """

    def step(ctx: Context) -> Context:
        return ctx

    return step


# ── Built-in graph ─────────────────────────────────────────


class TestBuiltInGraph:
    def test_all_16_builtins_have_specs(self):
        graph = build_step_graph()
        for name in BUILTIN_NAMES:
            assert name in graph, f"missing StepSpec for {name}"
        assert len(BUILTIN_NAMES) == 16

    def test_specs_shape(self):
        graph = build_step_graph()
        for name, spec in graph.items():
            assert isinstance(spec, StepSpec)
            assert spec.name == name
            assert isinstance(spec.inputs, tuple)
            assert isinstance(spec.outputs, tuple)
            assert isinstance(spec.depends_on, tuple)
            assert isinstance(spec.soft, bool)

    def test_soft_flags_match_registry(self):
        graph = build_step_graph()
        for name, spec in graph.items():
            assert spec.soft == (name in SOFT_BUILTINS)

    def test_builtins_declare_contracts(self):
        """Every built-in declares at least an output and the first step
        declares no dependencies."""
        graph = build_step_graph()
        assert graph["resolve_video"].outputs == ("source_video_path",)
        assert graph["resolve_video"].depends_on == ()
        for name in BUILTIN_NAMES[1:]:
            assert graph[name].outputs, f"{name} declares no outputs"

    def test_dependencies_point_backwards(self):
        """All built-in depends_on entries point earlier in linear order."""
        graph = build_step_graph()
        position = {name: i for i, name in enumerate(BUILTIN_NAMES)}
        for name, spec in graph.items():
            for dep in spec.depends_on:
                assert dep in position, f"{name} depends on unregistered {dep}"
                assert position[dep] < position[name], (
                    f"{name} depends on later-ordered {dep}"
                )

    def test_topological_order_equals_linear_order(self):
        """The linear adapter guarantee for the default 16 steps."""
        assert topological_order() == step_registry.ordered_names()

    def test_validation_clean_for_builtins(self):
        assert validate_linear_order() == []


# ── Plugin-step validation ─────────────────────────────────


class TestValidateLinearOrder:
    def _fresh_registry(self) -> StepRegistry:
        return StepRegistry()

    def test_flags_dependency_ordered_later(self):
        reg = self._fresh_registry()
        reg.register("early", _make_step(), depends_on=("late",))
        reg.register("late", _make_step())
        warnings = validate_linear_order(reg)
        assert any("'early'" in w and "'late'" in w for w in warnings)

    def test_flags_unregistered_dependency(self):
        reg = self._fresh_registry()
        reg.register("only", _make_step(), depends_on=("ghost",))
        warnings = validate_linear_order(reg)
        assert any("'ghost'" in w and "not a registered step" in w for w in warnings)

    def test_no_warning_for_backwards_dependency(self):
        reg = self._fresh_registry()
        reg.register("first", _make_step())
        reg.register("second", _make_step(), depends_on=("first",))
        assert validate_linear_order(reg) == []

    def test_cycle_reported_as_warning(self):
        reg = self._fresh_registry()
        reg.register("a", _make_step(), depends_on=("b",))
        reg.register("b", _make_step(), depends_on=("a",))
        warnings = validate_linear_order(reg)
        assert any("cycle" in w.lower() for w in warnings)


# ── Cycle detection ────────────────────────────────────────


class TestTopologicalOrderCycle:
    def test_two_node_cycle_raises(self):
        reg = StepRegistry()
        reg.register("a", _make_step(), depends_on=("b",))
        reg.register("b", _make_step(), depends_on=("a",))
        with pytest.raises(ValueError, match="cycle"):
            topological_order(reg)

    def test_self_dependency_raises(self):
        reg = StepRegistry()
        reg.register("solo", _make_step(), depends_on=("solo",))
        with pytest.raises(ValueError, match="cycle"):
            topological_order(reg)

    def test_diamond_resolves(self):
        reg = StepRegistry()
        reg.register("root", _make_step())
        reg.register("left", _make_step(), depends_on=("root",))
        reg.register("right", _make_step(), depends_on=("root",))
        reg.register("sink", _make_step(), depends_on=("left", "right"))
        order = topological_order(reg)
        assert order.index("root") < order.index("left")
        assert order.index("root") < order.index("right")
        assert order.index("left") < order.index("sink")
        assert order.index("right") < order.index("sink")


# ── Registry threading ─────────────────────────────────────


class TestRegistryThreading:
    def test_register_accepts_io_kwargs(self):
        reg = StepRegistry()
        reg.register(
            "io_step",
            _noop,
            inputs=("audio_path",),
            outputs=("final_audio_path", "bgm_transitions"),
            depends_on=("generate_voice",),
        )
        info = reg.info()[0]
        assert info["inputs"] == ["audio_path"]
        assert info["outputs"] == ["final_audio_path", "bgm_transitions"]
        assert info["depends_on"] == ["generate_voice"]

    def test_register_defaults_backward_compatible(self):
        reg = StepRegistry()
        reg.register("plain", _noop)
        info = reg.info()[0]
        assert info["inputs"] == []
        assert info["outputs"] == []
        assert info["depends_on"] == []

    def test_register_step_decorator_accepts_io_kwargs(self, monkeypatch):
        # register_step always targets the module-global registry — swap
        # in a fresh one for the duration of the test to avoid polluting
        # the real global step registry.
        reg = StepRegistry()
        monkeypatch.setattr("movie_narrator.pipeline.registry.step_registry", reg)
        decorator = register_step(
            "decorated",
            soft=True,
            status_field="scene",
            inputs=("source_video_path",),
            outputs=("scenes",),
            depends_on=("resolve_video",),
        )

        @decorator
        def decorated(ctx: Context) -> Context:
            return ctx

        entry = reg.get("decorated")
        assert entry is not None
        assert entry.inputs == ("source_video_path",)
        assert entry.outputs == ("scenes",)
        assert entry.depends_on == ("resolve_video",)


# ── Contract exports ───────────────────────────────────────


class TestContractExports:
    def test_build_step_graph_identity(self):
        from movie_narrator import contract

        assert contract.build_step_graph is _build_step_graph

    def test_importable_from_contract(self):
        from movie_narrator.contract import (
            build_step_graph as c_build,
            topological_order as c_topo,
            validate_linear_order as c_validate,
        )

        assert callable(c_build)
        assert callable(c_topo)
        assert callable(c_validate)
