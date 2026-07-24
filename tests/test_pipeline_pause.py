"""Tests for EP9 pipeline pause/resume functionality.

Verifies:
- PipelinePaused exception carries completed_step attribute
- _save_pipeline_state writes a valid JSON state file
- _load_pipeline_state reconstructs Context from the saved state
- _next_step_after returns the correct next step name
- run_pipeline with start_step skips already-completed steps
- run_pipeline raises PipelinePaused when pause_at matches a step
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from movie_narrator.models import Context, Services
from movie_narrator.pipeline.errors import PipelinePaused
from movie_narrator.pipeline.runner import (
    STEPS,
    _load_pipeline_state,
    _next_step_after,
    _save_pipeline_state,
)


# ── Fixtures ───────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> Context:
    """Build a minimal Context for testing."""
    return Context(
        movie_name="test-movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )


# ── PipelinePaused exception ───────────────────────────────


class TestPipelinePaused:
    def test_is_runtime_error(self):
        """PipelinePaused is a RuntimeError subclass."""
        err = PipelinePaused("match_clips")
        assert isinstance(err, RuntimeError)

    def test_completed_step_attribute(self):
        """completed_step is accessible on the exception."""
        err = PipelinePaused("generate_script")
        assert err.completed_step == "generate_script"

    def test_message_contains_step_name(self):
        """Exception message includes the step name."""
        err = PipelinePaused("match_clips")
        assert "match_clips" in str(err)


# ── _next_step_after ───────────────────────────────────────


class TestNextStepAfter:
    def test_returns_next_step_for_first_step(self):
        """First step (resolve_video) → next is prepare_assets."""
        result = _next_step_after("resolve_video")
        assert result == "prepare_assets"

    def test_returns_next_step_for_middle_step(self):
        """Middle step → correct next step."""
        result = _next_step_after("generate_script")
        assert result == "export_script_md"

    def test_returns_none_for_last_step(self):
        """Last step → None (no step after)."""
        last_step_name = STEPS[-1].__name__
        result = _next_step_after(last_step_name)
        assert result is None

    def test_returns_none_for_unknown_step(self):
        """Unknown step name → None."""
        result = _next_step_after("nonexistent_step")
        assert result is None

    def test_all_steps_have_valid_next_except_last(self):
        """Every step except the last has a valid next step."""
        for i, step in enumerate(STEPS[:-1]):
            next_name = _next_step_after(step.__name__)
            assert next_name == STEPS[i + 1].__name__


# ── _save_pipeline_state ───────────────────────────────────


class TestSavePipelineState:
    def test_creates_state_file(self, tmp_path: Path):
        """State file is created at output_dir/pipeline_state.json."""
        ctx = _make_ctx(tmp_path)
        state_path = _save_pipeline_state(ctx, "match_clips")
        assert state_path.exists()
        assert state_path.name == "pipeline_state.json"
        assert state_path.parent == tmp_path

    def test_state_file_contains_completed_step(self, tmp_path: Path):
        """State JSON contains the completed_step field."""
        ctx = _make_ctx(tmp_path)
        _save_pipeline_state(ctx, "generate_script")
        data = json.loads(
            (tmp_path / "pipeline_state.json").read_text(encoding="utf-8")
        )
        assert data["completed_step"] == "generate_script"

    def test_state_file_contains_context(self, tmp_path: Path):
        """State JSON contains a context field with movie_name."""
        ctx = _make_ctx(tmp_path)
        _save_pipeline_state(ctx, "match_clips")
        data = json.loads(
            (tmp_path / "pipeline_state.json").read_text(encoding="utf-8")
        )
        assert "context" in data
        assert data["context"]["movie_name"] == "test-movie"

    def test_state_excludes_services(self, tmp_path: Path):
        """Services field is excluded from serialization (non-serializable)."""
        ctx = _make_ctx(tmp_path)
        _save_pipeline_state(ctx, "match_clips")
        data = json.loads(
            (tmp_path / "pipeline_state.json").read_text(encoding="utf-8")
        )
        # services should not be in the serialized context
        assert "services" not in data["context"]

    def test_preserves_metadata(self, tmp_path: Path):
        """Context metadata survives serialization round-trip."""
        ctx = _make_ctx(tmp_path)
        ctx.metadata["pause_at"] = "render_video"
        ctx.metadata["custom_key"] = "custom_value"
        _save_pipeline_state(ctx, "match_clips")
        data = json.loads(
            (tmp_path / "pipeline_state.json").read_text(encoding="utf-8")
        )
        assert data["context"]["metadata"]["pause_at"] == "render_video"
        assert data["context"]["metadata"]["custom_key"] == "custom_value"


# ── _load_pipeline_state ───────────────────────────────────


class TestLoadPipelineState:
    def test_loads_completed_step(self, tmp_path: Path):
        """Loading returns the correct completed_step."""
        ctx = _make_ctx(tmp_path)
        _save_pipeline_state(ctx, "generate_voice")
        state_path = tmp_path / "pipeline_state.json"
        loaded_ctx, step = _load_pipeline_state(state_path)
        assert step == "generate_voice"

    def test_loads_context_movie_name(self, tmp_path: Path):
        """Loaded context preserves movie_name."""
        ctx = _make_ctx(tmp_path)
        _save_pipeline_state(ctx, "match_clips")
        state_path = tmp_path / "pipeline_state.json"
        loaded_ctx, _ = _load_pipeline_state(state_path)
        assert loaded_ctx.movie_name == "test-movie"

    def test_loaded_context_has_services(self, tmp_path: Path):
        """Loaded context auto-injects SilentConsole via model_validator."""
        ctx = _make_ctx(tmp_path)
        _save_pipeline_state(ctx, "match_clips")
        state_path = tmp_path / "pipeline_state.json"
        loaded_ctx, _ = _load_pipeline_state(state_path)
        # services should exist (auto-filled by model_validator)
        assert loaded_ctx.services is not None

    def test_round_trip_preserves_metadata(self, tmp_path: Path):
        """Metadata survives save → load round-trip."""
        ctx = _make_ctx(tmp_path)
        ctx.metadata["test_key"] = "test_value"
        _save_pipeline_state(ctx, "match_clips")
        state_path = tmp_path / "pipeline_state.json"
        loaded_ctx, _ = _load_pipeline_state(state_path)
        assert loaded_ctx.metadata.get("test_key") == "test_value"

    def test_nonexistent_file_raises(self, tmp_path: Path):
        """Loading a non-existent file raises an error."""
        with pytest.raises((FileNotFoundError, json.JSONDecodeError)):
            _load_pipeline_state(tmp_path / "nonexistent.json")


# ── run_pipeline with start_step (EP9 resume) ──────────────


class TestRunPipelineStartStep:
    def test_start_step_skips_preceding_steps(self, tmp_path: Path, monkeypatch):
        """When start_step is set, steps before it are skipped."""
        ctx = _make_ctx(tmp_path)
        ctx.metadata["research_enabled"] = False

        # Track which steps actually run
        executed: list[str] = []
        original_steps = list(STEPS)

        # Patch each step to record execution and return ctx
        for step in original_steps:
            def make_wrapper(s):
                def wrapper(c):
                    executed.append(s.__name__)
                    return c
                wrapper.__name__ = s.__name__
                return wrapper

        # We need to patch STEPS in the runner module
        import movie_narrator.pipeline.runner as runner_mod

        patched_steps = []
        for step in original_steps:
            name = step.__name__
            def make_mock(n):
                def mock_step(c):
                    executed.append(n)
                    return c
                mock_step.__name__ = n
                return mock_step
            patched_steps.append(make_mock(name))

        monkeypatch.setattr(runner_mod, "STEPS", patched_steps)

        # Mock preflight to pass
        monkeypatch.setattr(runner_mod, "run_preflight", lambda ctx: None)

        # Start from generate_voice (skip resolve, prepare, research, script, export_script_md)
        runner_mod.run_pipeline(ctx, start_step="generate_voice")

        # Steps before generate_voice should NOT be in executed
        assert "resolve_video" not in executed
        assert "prepare_assets" not in executed
        assert "research_plot" not in executed
        assert "generate_script" not in executed
        assert "export_script_md" not in executed
        # generate_voice and later should be in executed
        assert "generate_voice" in executed

    def test_start_step_none_runs_all_steps(self, tmp_path: Path, monkeypatch):
        """When start_step is None (normal run), all steps run."""
        ctx = _make_ctx(tmp_path)

        executed: list[str] = []
        import movie_narrator.pipeline.runner as runner_mod

        original_steps = list(runner_mod.STEPS)
        patched_steps = []
        for step in original_steps:
            name = step.__name__
            def make_mock(n):
                def mock_step(c):
                    executed.append(n)
                    return c
                mock_step.__name__ = n
                return mock_step
            patched_steps.append(make_mock(name))

        monkeypatch.setattr(runner_mod, "STEPS", patched_steps)
        monkeypatch.setattr(runner_mod, "run_preflight", lambda ctx: None)

        runner_mod.run_pipeline(ctx, start_step=None)
        assert "resolve_video" in executed
        assert executed[0] == "resolve_video"


# ── run_pipeline with pause_at ─────────────────────────────


class TestRunPipelinePauseAt:
    def test_pause_at_raises_pipeline_paused(self, tmp_path: Path, monkeypatch):
        """When pause_at matches a step, PipelinePaused is raised."""
        ctx = _make_ctx(tmp_path)
        ctx.metadata["pause_at"] = "resolve_video"

        import movie_narrator.pipeline.runner as runner_mod

        # Mock all steps to be no-ops
        original_steps = list(runner_mod.STEPS)
        patched_steps = []
        for step in original_steps:
            name = step.__name__
            def make_mock(n):
                def mock_step(c):
                    return c
                mock_step.__name__ = n
                return mock_step
            patched_steps.append(make_mock(name))

        monkeypatch.setattr(runner_mod, "STEPS", patched_steps)
        monkeypatch.setattr(runner_mod, "run_preflight", lambda ctx: None)

        with pytest.raises(PipelinePaused) as exc_info:
            runner_mod.run_pipeline(ctx)

        assert exc_info.value.completed_step == "resolve_video"

    def test_pause_at_creates_state_file(self, tmp_path: Path, monkeypatch):
        """PipelinePaused saves pipeline_state.json before raising."""
        ctx = _make_ctx(tmp_path)
        ctx.metadata["pause_at"] = "resolve_video"

        import movie_narrator.pipeline.runner as runner_mod

        original_steps = list(runner_mod.STEPS)
        patched_steps = []
        for step in original_steps:
            name = step.__name__
            def make_mock(n):
                def mock_step(c):
                    return c
                mock_step.__name__ = n
                return mock_step
            patched_steps.append(make_mock(name))

        monkeypatch.setattr(runner_mod, "STEPS", patched_steps)
        monkeypatch.setattr(runner_mod, "run_preflight", lambda ctx: None)

        with pytest.raises(PipelinePaused):
            runner_mod.run_pipeline(ctx)

        state_file = tmp_path / "pipeline_state.json"
        assert state_file.exists()
        data = json.loads(state_file.read_text(encoding="utf-8"))
        assert data["completed_step"] == "resolve_video"

    def test_pause_at_none_completes_pipeline(self, tmp_path: Path, monkeypatch):
        """Without pause_at, the pipeline runs to completion normally."""
        ctx = _make_ctx(tmp_path)

        import movie_narrator.pipeline.runner as runner_mod

        original_steps = list(runner_mod.STEPS)
        patched_steps = []
        for step in original_steps:
            name = step.__name__
            def make_mock(n):
                def mock_step(c):
                    return c
                mock_step.__name__ = n
                return mock_step
            patched_steps.append(make_mock(name))

        monkeypatch.setattr(runner_mod, "STEPS", patched_steps)
        monkeypatch.setattr(runner_mod, "run_preflight", lambda ctx: None)

        # Should NOT raise
        result = runner_mod.run_pipeline(ctx)
        assert result is not None

    def test_pause_at_not_matching_completes_pipeline(self, tmp_path: Path, monkeypatch):
        """When pause_at doesn't match any step name, pipeline completes."""
        ctx = _make_ctx(tmp_path)
        ctx.metadata["pause_at"] = "nonexistent_step"

        import movie_narrator.pipeline.runner as runner_mod

        original_steps = list(runner_mod.STEPS)
        patched_steps = []
        for step in original_steps:
            name = step.__name__
            def make_mock(n):
                def mock_step(c):
                    return c
                mock_step.__name__ = n
                return mock_step
            patched_steps.append(make_mock(name))

        monkeypatch.setattr(runner_mod, "STEPS", patched_steps)
        monkeypatch.setattr(runner_mod, "run_preflight", lambda ctx: None)

        # Should NOT raise — pause_at never matches
        result = runner_mod.run_pipeline(ctx)
        assert result is not None
