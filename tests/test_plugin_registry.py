"""Tests for the v0.5 plugin registry infrastructure.

Covers:
- StepRegistry: registration, ordering, soft-step metadata
- ProviderRegistry: registration, lookup, creation
- Plugin protocol and load_plugin
- UnifiedParamSchema (PARAM_WHITELIST derived from JobParams)
- SDK surface exports
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from movie_narrator.pipeline.registry import (
    StepRegistry,
    StepEntry,
    step_registry,
    register_step,
)
from movie_narrator.providers import (
    ProviderRegistry,
    tts_registry,
    vision_registry,
    llm_registry,
    research_registry,
    register_tts,
    register_vision,
    register_llm,
    register_research,
)
from movie_narrator.contract import (
    Plugin,
    PluginContext,
    Step,
    load_plugin,
)
from movie_narrator.models import Context, Assets, Services
from movie_narrator.utils.console import SilentConsole


# ── Fixtures ───────────────────────────────────────────────


def _make_ctx() -> Context:
    """Create a minimal Context for step function testing."""
    return Context(
        movie_name="test",
        style="test",
        duration=30,
        output_dir="/tmp/test",
        assets=Assets(),
        services=Services(console=SilentConsole()),
    )


# ── StepRegistry tests ────────────────────────────────────


class TestStepRegistry:
    """StepRegistry core functionality."""

    def test_register_and_get(self):
        reg = StepRegistry()

        def my_step(ctx):
            return ctx

        reg.register("my_step", my_step)
        assert reg.contains("my_step")
        assert reg.get("my_step").func is my_step
        assert reg.get_func("my_step") is my_step

    def test_register_returns_func(self):
        reg = StepRegistry()

        def my_step(ctx):
            return ctx

        returned = reg.register("my_step", my_step)
        assert returned is my_step

    def test_duplicate_name_raises(self):
        reg = StepRegistry()

        def step_a(ctx):
            return ctx

        def step_b(ctx):
            return ctx

        reg.register("dup", step_a)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("dup", step_b)

    def test_unregister(self):
        reg = StepRegistry()

        def my_step(ctx):
            return ctx

        reg.register("my_step", my_step)
        reg.unregister("my_step")
        assert not reg.contains("my_step")
        assert reg.get("my_step") is None

    def test_unregister_unknown_raises(self):
        reg = StepRegistry()
        with pytest.raises(KeyError):
            reg.unregister("nonexistent")

    def test_ordered_steps_builtin_only(self):
        """Built-in steps preserve registration order."""
        reg = StepRegistry()

        def step_a(ctx):
            return ctx

        def step_b(ctx):
            return ctx

        def step_c(ctx):
            return ctx

        reg.register("a", step_a)
        reg.register("b", step_b)
        reg.register("c", step_c)

        ordered = reg.ordered_steps()
        assert ordered == [step_a, step_b, step_c]

    def test_ordered_steps_with_after(self):
        """Plugin step with after= is inserted right after target."""
        reg = StepRegistry()

        def step_a(ctx):
            return ctx

        def step_b(ctx):
            return ctx

        def plugin_step(ctx):
            return ctx

        reg.register("a", step_a)
        reg.register("b", step_b)
        reg.register("plugin", plugin_step, after="a")

        ordered = reg.ordered_steps()
        assert ordered == [step_a, plugin_step, step_b]

    def test_ordered_steps_with_before(self):
        """Plugin step with before= is inserted right before target."""
        reg = StepRegistry()

        def step_a(ctx):
            return ctx

        def step_b(ctx):
            return ctx

        def plugin_step(ctx):
            return ctx

        reg.register("a", step_a)
        reg.register("b", step_b)
        reg.register("plugin", plugin_step, before="b")

        ordered = reg.ordered_steps()
        assert ordered == [step_a, plugin_step, step_b]

    def test_ordered_steps_unsequenced_appended(self):
        """Plugin step with neither after nor before is appended."""
        reg = StepRegistry()

        def step_a(ctx):
            return ctx

        def plugin_step(ctx):
            return ctx

        reg.register("a", step_a)
        reg.register("plugin", plugin_step, after=None, before=None)

        ordered = reg.ordered_steps()
        assert ordered == [step_a, plugin_step]

    def test_ordered_names(self):
        reg = StepRegistry()

        def step_a(ctx):
            return ctx

        def step_b(ctx):
            return ctx

        reg.register("a", step_a)
        reg.register("b", step_b)

        assert reg.ordered_names() == ["a", "b"]

    def test_soft_step_metadata(self):
        reg = StepRegistry()

        def soft_step(ctx):
            return ctx

        def hard_step(ctx):
            return ctx

        reg.register(
            "soft", soft_step, soft=True, status_field="soft_field", consequence="soft step failed"
        )
        reg.register("hard", hard_step)

        assert reg.soft_step_names() == {"soft"}
        assert reg.status_field_for("soft") == "soft_field"
        assert reg.consequence_for("soft") == "soft step failed"
        assert reg.status_field_for("hard") is None
        assert reg.consequence_for("hard") == ""

    def test_info(self):
        reg = StepRegistry()

        def my_step(ctx):
            return ctx

        reg.register("my_step", my_step, soft=True, status_field="my")
        info = reg.info()
        assert len(info) == 1
        assert info[0]["name"] == "my_step"
        assert info[0]["soft"] is True
        assert info[0]["status_field"] == "my"

    def test_clear(self):
        reg = StepRegistry()

        def my_step(ctx):
            return ctx

        reg.register("my_step", my_step)
        reg.clear()
        assert not reg.contains("my_step")
        assert reg.names() == []


class TestStepRegistryDecorator:
    """The @register_step decorator pattern."""

    def test_decorator_registers_in_local_registry(self):
        """Verify the decorator pattern works with a local registry."""
        reg = StepRegistry()

        # Simulate the decorator factory bound to this registry
        def local_register(name, **kwargs):
            def decorator(func):
                return reg.register(name, func, **kwargs)

            return decorator

        @local_register("decorated", soft=True)
        def my_step(ctx):
            return ctx

        assert reg.contains("decorated")
        assert reg.get("decorated").func is my_step
        assert reg.get("decorated").soft is True

    def test_global_register_step_is_callable(self):
        """The global register_step decorator is callable and returns a decorator."""
        assert callable(register_step)
        decorator = register_step("test_unique_step_name")
        assert callable(decorator)


# ── Global step_registry tests ────────────────────────────


class TestGlobalStepRegistry:
    """The global step_registry instance with built-in steps."""

    def test_has_16_builtin_steps(self):
        assert len(step_registry.names()) == 16

    def test_ordered_names_match_pipeline(self):
        from movie_narrator.pipeline.runner import STEPS

        assert len(step_registry.ordered_steps()) == len(STEPS)

    def test_first_step_is_resolve_video(self):
        names = step_registry.ordered_names()
        assert names[0] == "resolve_video"

    def test_last_step_is_export_clips(self):
        names = step_registry.ordered_names()
        assert names[-1] == "export_clips"

    def test_soft_steps_correct(self):
        expected = {
            "research_plot",
            "align_audio",
            "detect_scenes",
            "match_clips",
            "mix_bgm",
            "export_clips",
            "translate_subtitles",
            "run_qa_gate",
        }
        assert step_registry.soft_step_names() == expected

    def test_status_fields_correct(self):
        assert step_registry.status_field_for("research_plot") == "research"
        assert step_registry.status_field_for("align_audio") == "align"
        assert step_registry.status_field_for("detect_scenes") == "scene"
        assert step_registry.status_field_for("match_clips") == "match"
        assert step_registry.status_field_for("mix_bgm") == "bgm"
        assert step_registry.status_field_for("export_clips") == "export"
        assert step_registry.status_field_for("translate_subtitles") == "translate"
        assert step_registry.status_field_for("run_qa_gate") == "qa_gate"

    def test_consequence_messages_exist(self):
        for name in step_registry.soft_step_names():
            assert step_registry.consequence_for(name), f"Missing consequence for {name}"


# ── ProviderRegistry tests ────────────────────────────────


class TestProviderRegistry:
    """ProviderRegistry core functionality."""

    def test_register_and_create(self):
        reg = ProviderRegistry("test")

        class FakeProvider:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        def factory(**kwargs):
            return FakeProvider(**kwargs)

        reg.register("fake", factory)
        assert reg.contains("fake")
        provider = reg.create("fake", key="value")
        assert isinstance(provider, FakeProvider)
        assert provider.kwargs == {"key": "value"}

    def test_register_returns_factory(self):
        reg = ProviderRegistry("test")

        def factory(**kwargs):
            return None

        returned = reg.register("fake", factory)
        assert returned is factory

    def test_duplicate_name_raises(self):
        reg = ProviderRegistry("test")

        def factory1(**kwargs):
            return None

        def factory2(**kwargs):
            return None

        reg.register("dup", factory1)
        with pytest.raises(ValueError, match="already registered"):
            reg.register("dup", factory2)

    def test_create_unknown_raises(self):
        reg = ProviderRegistry("test")
        with pytest.raises(ValueError, match="Unknown test provider"):
            reg.create("nonexistent")

    def test_unregister(self):
        reg = ProviderRegistry("test")

        def factory(**kwargs):
            return None

        reg.register("temp", factory)
        reg.unregister("temp")
        assert not reg.contains("temp")

    def test_names(self):
        reg = ProviderRegistry("test")

        def f1(**kw):
            pass

        def f2(**kw):
            pass

        reg.register("a", f1)
        reg.register("b", f2)
        assert set(reg.names()) == {"a", "b"}

    def test_clear(self):
        reg = ProviderRegistry("test")

        def factory(**kwargs):
            return None

        reg.register("temp", factory)
        reg.clear()
        assert reg.names() == []


class TestGlobalTtsRegistry:
    """The global tts_registry with built-in providers."""

    def test_has_edge_openai_mimo(self):
        names = set(tts_registry.names())
        assert "edge" in names
        assert "openai" in names
        assert "mimo" in names

    def test_factory_returns_provider(self):
        from movie_narrator.config import Settings, TTSProviderType

        settings = Settings(tts_provider=TTSProviderType.EDGE)
        from movie_narrator.tts.factory import get_tts_provider

        provider = get_tts_provider(settings)
        assert provider is not None

    def test_unknown_provider_raises_config_error(self):
        from movie_narrator.config import Settings, TTSProviderType
        from movie_narrator.utils.errors import ConfigError
        from movie_narrator.tts.factory import get_tts_provider

        # Create a mock settings with unsupported provider
        settings = Settings()
        settings.tts_provider = "unsupported"
        with pytest.raises(ConfigError, match="Unsupported TTS provider"):
            get_tts_provider(settings)


class TestGlobalVisionRegistry:
    """The global vision_registry."""

    def test_stub_registered_after_import(self):
        # Import the factory to trigger registration
        import movie_narrator.vision.factory  # noqa: F401

        assert "stub" in vision_registry.names()

    def test_create_stub(self):
        import movie_narrator.vision.factory  # noqa: F401
        from movie_narrator.vision.protocol import VisionCaptioner

        provider = vision_registry.create("stub")
        assert isinstance(provider, VisionCaptioner)


class TestGlobalLlmRegistry:
    """The global llm_registry (M4)."""

    def test_openai_registered_after_import(self):
        # Import the module to trigger registration
        import movie_narrator.utils.llm  # noqa: F401

        assert "openai" in llm_registry.names()

    def test_registry_category(self):
        assert llm_registry._category == "llm"

    def test_registry_no_protocol(self):
        """LLM registry does not set a protocol (factories return context managers)."""
        assert llm_registry._protocol is None


class TestGlobalResearchRegistry:
    """The global research_registry (M4)."""

    def test_llm_registered_after_import(self):
        # Import the module to trigger registration
        import movie_narrator.pipeline.research  # noqa: F401

        assert "llm" in research_registry.names()

    def test_registry_category(self):
        assert research_registry._category == "research"

    def test_registry_no_protocol(self):
        """Research registry does not set a protocol (factories return ResearchInfo)."""
        assert research_registry._protocol is None


# ── Plugin system tests ───────────────────────────────────


class TestPluginProtocol:
    """Plugin protocol and load_plugin function."""

    def test_plugin_protocol_is_runtime_checkable(self):
        class MyPlugin:
            name = "test"

            def register(self, ctx: PluginContext) -> None:
                pass

        assert isinstance(MyPlugin(), Plugin)

    def test_non_plugin_fails_check(self):
        class NotAPlugin:
            pass

        assert not isinstance(NotAPlugin(), Plugin)

    def test_load_plugin_calls_register(self):
        class TestPlugin:
            name = "test"
            called = False

            def register(self, ctx: PluginContext) -> None:
                self.__class__.called = True

        plugin = TestPlugin()
        load_plugin(plugin)
        assert TestPlugin.called is True

    def test_load_plugin_rejects_non_plugin(self):
        with pytest.raises(TypeError, match="does not implement the Plugin protocol"):
            load_plugin("not a plugin")

    def test_plugin_context_default(self):
        ctx = PluginContext.default()
        assert ctx.steps is step_registry
        assert ctx.tts is tts_registry
        assert ctx.vision is vision_registry
        assert ctx.llm is llm_registry
        assert ctx.research is research_registry


class TestPluginStepInsertion:
    """Plugin steps inserted via after=/before= appear in correct position."""

    def test_plugin_step_after_render(self):
        """A plugin step registered with after='render_video' should
        appear immediately after render_video in the ordered list."""
        # Use a fresh registry to avoid polluting the global one
        reg = StepRegistry()

        def step_a(ctx):
            return ctx

        def step_b(ctx):
            return ctx

        def plugin_step(ctx):
            return ctx

        reg.register("a", step_a)
        reg.register("b", step_b)
        reg.register("plugin", plugin_step, after="a")

        names = reg.ordered_names()
        idx_a = names.index("a")
        idx_plugin = names.index("plugin")
        assert idx_plugin == idx_a + 1

    def test_plugin_step_before_last(self):
        """A plugin step with before= on the last built-in step."""
        reg = StepRegistry()

        def step_a(ctx):
            return ctx

        def step_b(ctx):
            return ctx

        def plugin_step(ctx):
            return ctx

        reg.register("a", step_a)
        reg.register("b", step_b)
        reg.register("plugin", plugin_step, before="b")

        names = reg.ordered_names()
        idx_b = names.index("b")
        idx_plugin = names.index("plugin")
        assert idx_plugin == idx_b - 1

    def test_multiple_plugin_steps_after_same_target(self):
        """Multiple plugin steps after the same target are appended in
        registration order."""
        reg = StepRegistry()

        def base(ctx):
            return ctx

        def p1(ctx):
            return ctx

        def p2(ctx):
            return ctx

        reg.register("base", base)
        reg.register("p1", p1, after="base")
        reg.register("p2", p2, after="base")

        names = reg.ordered_names()
        assert names == ["base", "p1", "p2"]


# ── UnifiedParamSchema tests ──────────────────────────────


class TestUnifiedParamSchema:
    """PARAM_WHITELIST is derived from JobParams.model_fields."""

    def test_param_whitelist_matches_job_params(self):
        from movie_narrator.pipeline.runner import PARAM_WHITELIST
        from movie_narrator.workflow.schema import JobParams

        derived = frozenset(JobParams.model_fields.keys())
        assert PARAM_WHITELIST == derived

    def test_param_whitelist_is_frozenset(self):
        from movie_narrator.pipeline.runner import PARAM_WHITELIST

        assert isinstance(PARAM_WHITELIST, frozenset)

    def test_param_whitelist_has_vision_captioner(self):
        """vision_captioner was missing from JobParams before v0.5."""
        from movie_narrator.pipeline.runner import PARAM_WHITELIST

        assert "vision_captioner" in PARAM_WHITELIST

    def test_job_params_has_vision_captioner(self):
        from movie_narrator.workflow.schema import JobParams

        assert "vision_captioner" in JobParams.model_fields

    def test_allowed_param_keys_subset_of_whitelist(self):
        """ALLOWED_PARAM_KEYS must be a subset of PARAM_WHITELIST."""
        from movie_narrator.pipeline.runner import PARAM_WHITELIST
        from movie_narrator.presets.base import ALLOWED_PARAM_KEYS

        assert ALLOWED_PARAM_KEYS <= PARAM_WHITELIST


# ── SDK surface tests ─────────────────────────────────────


class TestSDKSurface:
    """Public SDK exports from movie_narrator package."""

    def test_register_step_exported(self):
        from movie_narrator import register_step

        assert callable(register_step)

    def test_register_tts_exported(self):
        from movie_narrator import register_tts

        assert callable(register_tts)

    def test_register_vision_exported(self):
        from movie_narrator import register_vision

        assert callable(register_vision)

    def test_register_llm_exported(self):
        from movie_narrator import register_llm

        assert callable(register_llm)

    def test_register_research_exported(self):
        from movie_narrator import register_research

        assert callable(register_research)

    def test_llm_registry_exported(self):
        from movie_narrator import llm_registry as exported
        from movie_narrator.providers import llm_registry

        assert exported is llm_registry

    def test_research_registry_exported(self):
        from movie_narrator import research_registry as exported
        from movie_narrator.providers import research_registry

        assert exported is research_registry

    def test_context_exported(self):
        from movie_narrator import Context as ExportedContext
        from movie_narrator.models import Context

        assert ExportedContext is Context

    def test_step_registry_exported(self):
        from movie_narrator import step_registry as exported
        from movie_narrator.pipeline.registry import step_registry

        assert exported is step_registry

    def test_tts_registry_exported(self):
        from movie_narrator import tts_registry as exported
        from movie_narrator.providers import tts_registry

        assert exported is tts_registry

    def test_vision_registry_exported(self):
        from movie_narrator import vision_registry as exported
        from movie_narrator.providers import vision_registry

        assert exported is vision_registry

    def test_plugin_types_exported(self):
        from movie_narrator import Plugin, PluginContext, load_plugin

        assert Plugin is not None
        assert PluginContext is not None
        assert callable(load_plugin)

    def test_step_type_alias_exported(self):
        from movie_narrator import Step

        assert Step is not None

    def test_build_context_and_run_pipeline_exported(self):
        from movie_narrator import build_context, run_pipeline

        assert callable(build_context)
        assert callable(run_pipeline)


# ── Integration: plugin step actually runs ────────────────


class TestPluginStepExecution:
    """A plugin step registered via the decorator actually runs in the pipeline."""

    def test_plugin_step_executes(self):
        """Register a no-op plugin step and verify it appears in ordered_steps."""
        # We can't easily run the full pipeline in a unit test,
        # but we can verify the registry integration works.
        reg = StepRegistry()

        executed = []

        def base_step(ctx):
            executed.append("base")
            return ctx

        def plugin_step(ctx):
            executed.append("plugin")
            return ctx

        reg.register("base", base_step)
        reg.register("plugin", plugin_step, after="base")

        ctx = _make_ctx()
        for func in reg.ordered_steps():
            ctx = func(ctx)

        assert executed == ["base", "plugin"]
