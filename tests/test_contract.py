"""Tests for the contract layer (movie_narrator.contract).

Verifies that:
- All re-exported names are accessible from the contract module
- Re-exported objects are identical to their source (same object)
- PipelineResult protocol is satisfied by Context
- The contract __all__ matches the actual exports
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from movie_narrator import contract
from movie_narrator.contract import (
    BaseConsole,
    Console,
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
    """web_api can import everything it needs from contract alone."""

    def test_web_api_needs_only_contract(self):
        """The symbols that web_api uses are all in the contract module.

        This is a static check — it verifies that the set of names
        web_api previously imported from internal modules is a subset
        of what contract provides.
        """
        # Names web_api needs (from console.py, tasks.py, utils.py, form.py)
        web_api_needed = {
            "BaseConsole",
            "Console",
            "PipelineCancelled",
            "build_context",
            "run_pipeline",
            "sanitize_filename",
            "PARAM_WHITELIST",
        }
        contract_provided = set(contract.__all__)
        assert web_api_needed.issubset(contract_provided), (
            f"web_api needs {web_api_needed - contract_provided} "
            f"which are not in contract.__all__"
        )
