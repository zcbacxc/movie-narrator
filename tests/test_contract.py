"""Tests for the contract layer (movie_narrator.contract).

Verifies that:
- All re-exported names are accessible from the contract module
- Re-exported objects are identical to their source (same object)
- PipelineResult protocol is satisfied by Context
- The contract __all__ matches the actual exports
- CONTRACT_VERSION is correct (v0.5+)
- M1/M2 SDK symbols are re-exported correctly (v0.5+)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from movie_narrator import contract
from movie_narrator.contract import (
    BaseConsole,
    Console,
    CONTRACT_VERSION,
    PARAM_WHITELIST,
    PipelineCancelled,
    PipelineResult,
    PipelineStrictError,
    RunController,
    SilentConsole,
    StepAction,
    build_context,
    check_cancelled,
    run_pipeline,
    sanitize_filename,
)
from movie_narrator.models import Context, Services
from movie_narrator.pipeline.errors import (
    PipelineCancelled as _PipelineCancelled,
    PipelineStrictError as _PipelineStrictError,
    RunController as _RunController,
    StepAction as _StepAction,
    check_cancelled as _check_cancelled,
)
from movie_narrator.pipeline.runner import (
    PARAM_WHITELIST as _PARAM_WHITELIST,
    build_context as _build_context,
    run_pipeline as _run_pipeline,
)
from movie_narrator.utils.console import (
    BaseConsole as _BaseConsole,
    Console as _Console,
    SilentConsole as _SilentConsole,
)
from movie_narrator.utils.sanitize import sanitize_filename as _sanitize_filename


# ── Re-export identity ─────────────────────────────────────


class TestReExportIdentity:
    """Contract symbols are the same objects as their source module."""

    def test_base_console_identity(self):
        assert BaseConsole is _BaseConsole

    def test_console_identity(self):
        assert Console is _Console

    def test_silent_console_identity(self):
        assert SilentConsole is _SilentConsole

    def test_param_whitelist_identity(self):
        assert PARAM_WHITELIST is _PARAM_WHITELIST

    def test_build_context_identity(self):
        assert build_context is _build_context

    def test_run_pipeline_identity(self):
        assert run_pipeline is _run_pipeline

    def test_sanitize_filename_identity(self):
        assert sanitize_filename is _sanitize_filename

    def test_pipeline_cancelled_identity(self):
        assert PipelineCancelled is _PipelineCancelled

    def test_pipeline_strict_error_identity(self):
        assert PipelineStrictError is _PipelineStrictError

    def test_run_controller_identity(self):
        assert RunController is _RunController

    def test_step_action_identity(self):
        assert StepAction is _StepAction

    def test_check_cancelled_identity(self):
        assert check_cancelled is _check_cancelled


# ── __all__ completeness ──────────────────────────────────


class TestAllCompleteness:
    def test_all_names_exported(self):
        """Every name in __all__ is accessible on the contract module."""
        for name in contract.__all__:
            assert hasattr(contract, name), f"{name!r} in __all__ but not on module"

    def test_all_names_in_all(self):
        """Key public names are in __all__."""
        expected = {
            "BaseConsole", "Console", "SilentConsole",
            "PipelineCancelled", "PipelineStrictError",
            "RunController", "StepAction", "check_cancelled",
            "PARAM_WHITELIST", "build_context", "run_pipeline",
            "sanitize_filename", "PipelineResult",
            # M1/M2 symbols (v0.5+)
            "CONTRACT_VERSION",
            "StepRegistry", "step_registry",
            "ProviderRegistry", "tts_registry", "vision_registry",
            "llm_registry", "research_registry",
            "register_step", "step",
            "register_tts", "register_vision",
            "register_llm", "register_research",
            "Plugin", "PluginContext",
            "load_plugin", "discover_plugins", "list_available_plugins",
            "Step",
            "list_presets", "get_preset",
        }
        assert expected.issubset(set(contract.__all__))


# ── PipelineResult protocol ───────────────────────────────


class TestPipelineResult:
    def test_context_satisfies_protocol(self, tmp_path: Path):
        """Context satisfies the PipelineResult protocol structurally."""
        ctx = Context(
            movie_name="test",
            output_dir=str(tmp_path),
            services=Services(console=MagicMock()),
        )
        # runtime_checkable Protocol — isinstance check works
        assert isinstance(ctx, PipelineResult)

    def test_protocol_has_video_path(self):
        """PipelineResult declares video_path."""
        assert hasattr(PipelineResult, "video_path")

    def test_protocol_has_audio_path(self):
        """PipelineResult declares audio_path."""
        assert hasattr(PipelineResult, "audio_path")

    def test_protocol_has_clips_dir(self):
        """PipelineResult declares clips_dir."""
        assert hasattr(PipelineResult, "clips_dir")

    def test_protocol_has_output_dir(self):
        """PipelineResult declares output_dir."""
        assert hasattr(PipelineResult, "output_dir")

    def test_protocol_has_subtitle_paths(self):
        """PipelineResult declares subtitle_paths."""
        assert hasattr(PipelineResult, "subtitle_paths")

    def test_non_matching_object_fails_protocol(self):
        """A plain dict does not satisfy PipelineResult."""

        class NotAResult:
            pass

        assert not isinstance(NotAResult(), PipelineResult)


# ── Contract import isolation ─────────────────────────────


class TestContractIsolation:
    """External consumers (movie-narrator-web) can import everything they need from contract alone."""

    def test_web_package_needs_only_contract(self):
        """The symbols that movie-narrator-web uses are all in the contract module.

        This is a static check — it verifies that the set of names
        the web package imports from internal modules is a subset
        of what contract provides.
        """
        # Names the web package needs (console.py, tasks.py, utils.py, form.py)
        web_needed = {
            "BaseConsole",
            "Console",
            "PipelineCancelled",
            "build_context",
            "run_pipeline",
            "sanitize_filename",
            "PARAM_WHITELIST",
        }
        contract_provided = set(contract.__all__)
        assert web_needed.issubset(contract_provided), (
            f"web package needs {web_needed - contract_provided} "
            f"which are not in contract.__all__"
        )


# ── CONTRACT_VERSION (v0.5+) ──────────────────────────────


class TestContractVersion:
    """CONTRACT_VERSION is the stable API boundary for external consumers."""

    def test_contract_version_value(self):
        """CONTRACT_VERSION is (0, 9, 5) — i18n & voice-map exports (v0.9.6).

        v0.9.1 added reliability exports, v0.9.2 added task checkpointing and
        graceful-shutdown exports, v0.9.3 added batch and scheduled-job types,
        v0.9.4 adds dead-letter queue and distributed-rendering types,
        v0.9.6 adds i18n / localized voice-mapping exports; each bumps the
        contract version per the semver policy in contract.py.
        """
        assert CONTRACT_VERSION == (0, 9, 5)

    def test_contract_version_is_tuple(self):
        """CONTRACT_VERSION is a 3-tuple of ints (semver)."""
        assert isinstance(CONTRACT_VERSION, tuple)
        assert len(CONTRACT_VERSION) == 3
        assert all(isinstance(v, int) for v in CONTRACT_VERSION)

    def test_contract_version_in_all(self):
        """CONTRACT_VERSION is in __all__."""
        assert "CONTRACT_VERSION" in contract.__all__


# ── M1/M2 SDK symbol re-exports (v0.5+) ───────────────────


class TestSDKSymbolExports:
    """M1/M2 SDK symbols are accessible from the contract module."""

    @pytest.mark.parametrize("name", [
        "StepRegistry", "step_registry",
        "ProviderRegistry", "tts_registry", "vision_registry",
        "llm_registry", "research_registry",
        "register_step", "step",
        "register_tts", "register_vision",
        "register_llm", "register_research",
        "Plugin", "PluginContext",
        "load_plugin", "discover_plugins", "list_available_plugins",
        "Step",
        "list_presets", "get_preset",
    ])
    def test_symbol_accessible(self, name):
        """Each M1/M2 symbol is accessible on the contract module."""
        assert hasattr(contract, name), f"{name!r} not accessible from contract module"

    def test_step_registry_is_global_instance(self):
        """step_registry is the global StepRegistry instance."""
        from movie_narrator.pipeline.registry import step_registry as _step_registry
        assert contract.step_registry is _step_registry

    def test_tts_registry_is_global_instance(self):
        """tts_registry is the global ProviderRegistry instance for TTS."""
        from movie_narrator.providers.registry import tts_registry as _tts_registry
        assert contract.tts_registry is _tts_registry

    def test_vision_registry_is_global_instance(self):
        """vision_registry is the global ProviderRegistry instance for vision."""
        from movie_narrator.providers.registry import vision_registry as _vision_registry
        assert contract.vision_registry is _vision_registry

    def test_register_step_identity(self):
        """register_step is the same function as in pipeline.registry."""
        from movie_narrator.pipeline.registry import register_step as _register_step
        assert contract.register_step is _register_step

    def test_register_tts_identity(self):
        """register_tts is the same function as in providers.registry."""
        from movie_narrator.providers.registry import register_tts as _register_tts
        assert contract.register_tts is _register_tts

    def test_register_vision_identity(self):
        """register_vision is the same function as in providers.registry."""
        from movie_narrator.providers.registry import register_vision as _register_vision
        assert contract.register_vision is _register_vision

    def test_llm_registry_is_global_instance(self):
        """llm_registry is the global ProviderRegistry instance for LLM."""
        from movie_narrator.providers.registry import llm_registry as _llm_registry
        assert contract.llm_registry is _llm_registry

    def test_research_registry_is_global_instance(self):
        """research_registry is the global ProviderRegistry instance for research."""
        from movie_narrator.providers.registry import research_registry as _research_registry
        assert contract.research_registry is _research_registry

    def test_register_llm_identity(self):
        """register_llm is the same function as in providers.registry."""
        from movie_narrator.providers.registry import register_llm as _register_llm
        assert contract.register_llm is _register_llm

    def test_register_research_identity(self):
        """register_research is the same function as in providers.registry."""
        from movie_narrator.providers.registry import register_research as _register_research
        assert contract.register_research is _register_research

    def test_load_plugin_identity(self):
        """load_plugin is the same function as in plugin_loader."""
        from movie_narrator.plugin_loader import load_plugin as _load_plugin
        assert contract.load_plugin is _load_plugin

    def test_discover_plugins_identity(self):
        """discover_plugins is the same function as in plugin_loader."""
        from movie_narrator.plugin_loader import discover_plugins as _discover_plugins
        assert contract.discover_plugins is _discover_plugins

    def test_list_presets_callable(self):
        """list_presets is callable from contract."""
        assert callable(contract.list_presets)

    def test_get_preset_callable(self):
        """get_preset is callable from contract."""
        assert callable(contract.get_preset)
