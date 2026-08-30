# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.3.0 Feature 1 — selective rerun (``mn rerun``).

Covers:
- ``prepare_rerun``: downstream invalidation list, soft-step status reset
  to the "not yet run" defaults, metadata ``rerun`` block, structured
  audit log record, unknown-step validation.
- ``run_pipeline`` starting AT an arbitrary named step (fake steps).
- CLI wiring via the typer CliRunner: happy path, invalid ``--from``,
  ``--list-steps``, missing state file.
- ``--from`` after the saved ``completed_step`` degenerates to resume
  behavior (no upstream re-execution).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from typer.testing import CliRunner

import movie_narrator.pipeline.runner as runner_mod
from movie_narrator.cli import app
from movie_narrator.models import Context, PipelineStatus, Services
from movie_narrator.pipeline.runner import (
    STEPS,
    _save_pipeline_state,
    ordered_step_names,
    prepare_rerun,
    run_pipeline,
)

runner = CliRunner()


# ── Fixtures / helpers ─────────────────────────────────────


def _make_ctx(tmp_path: Path) -> Context:
    """Build a minimal Context for testing."""
    return Context(
        movie_name="test-movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )


def _mark_all_success(ctx: Context) -> None:
    """Set every soft status field to a stale 'success' value."""
    for field in PipelineStatus.model_fields:
        setattr(ctx.status, field, "success")


def _patch_fake_steps(monkeypatch, executed: list) -> None:
    """Replace runner STEPS with recording no-op mocks."""
    patched = []
    for step in list(STEPS):
        name = step.__name__

        def make_mock(n):
            def mock_step(c):
                executed.append(n)
                return c

            mock_step.__name__ = n
            return mock_step

        patched.append(make_mock(name))
    monkeypatch.setattr(runner_mod, "STEPS", patched)
    monkeypatch.setattr(runner_mod, "run_preflight", lambda ctx: None)


# ── prepare_rerun: invalidation semantics ──────────────────


class TestPrepareRerunInvalidation:
    def test_invalidated_list_from_middle_step(self, tmp_path):
        """--from match_clips invalidates match_clips and everything after."""
        ctx = _make_ctx(tmp_path)
        invalidated = prepare_rerun(ctx, "generate_voice", "match_clips")
        assert invalidated == [
            "match_clips",
            "mix_bgm",
            "translate_subtitles",
            "generate_subtitle",
            "run_qa_gate",
            "render_video",
            "validate_deliverable",
            "export_clips",
        ]

    def test_invalidated_list_from_first_step_is_all_steps(self, tmp_path):
        """--from on the first step invalidates every step."""
        ctx = _make_ctx(tmp_path)
        invalidated = prepare_rerun(ctx, "resolve_video", "resolve_video")
        assert invalidated == ordered_step_names()

    def test_soft_status_fields_reset_to_not_yet_run(self, tmp_path):
        """Invalidated soft steps get their status field reset to the
        PipelineStatus 'not yet run' default; upstream fields untouched."""
        ctx = _make_ctx(tmp_path)
        _mark_all_success(ctx)
        prepare_rerun(ctx, "generate_voice", "match_clips")
        # Invalidated soft steps reset (translate defaults to "skipped",
        # all other soft fields default to "disabled").
        assert ctx.status.match == "disabled"  # match_clips
        assert ctx.status.bgm == "disabled"  # mix_bgm
        assert ctx.status.translate == "skipped"  # translate_subtitles
        assert ctx.status.qa_gate == "disabled"  # run_qa_gate
        assert ctx.status.export == "disabled"  # export_clips
        # Upstream (reusable) fields untouched.
        assert ctx.status.research == "success"
        assert ctx.status.align == "success"
        assert ctx.status.scene == "success"  # detect_scenes is upstream

    def test_hard_steps_have_no_status_field(self, tmp_path):
        """Hard steps in the invalidated range rerun unconditionally —
        no status field exists, so nothing to reset and no error."""
        ctx = _make_ctx(tmp_path)
        _mark_all_success(ctx)
        invalidated = prepare_rerun(ctx, "generate_voice", "render_video")
        assert "render_video" in invalidated
        assert "validate_deliverable" in invalidated
        # No non-soft status field was clobbered.
        assert ctx.status.research == "success"

    def test_metadata_rerun_block(self, tmp_path):
        """ctx.metadata["rerun"] records from/completed/invalidated/timestamp."""
        ctx = _make_ctx(tmp_path)
        prepare_rerun(ctx, "generate_voice", "match_clips")
        block = ctx.metadata["rerun"]
        assert block["from_step"] == "match_clips"
        assert block["state_completed_step"] == "generate_voice"
        assert block["invalidated_steps"][0] == "match_clips"
        assert len(block["invalidated_steps"]) == 8
        # ISO-8601 UTC timestamp.
        ts = datetime.fromisoformat(block["timestamp"].replace("Z", "+00:00"))
        assert ts.utcoffset() == timedelta(0)

    def test_unknown_step_raises_value_error_listing_valid(self, tmp_path):
        """Unknown --from step raises ValueError listing valid steps."""
        ctx = _make_ctx(tmp_path)
        with pytest.raises(ValueError, match="render_video"):
            prepare_rerun(ctx, "generate_voice", "not_a_step")

    def test_from_after_completed_degenerates_to_resume(self, tmp_path):
        """--from later than completed_step: only downstream steps are
        invalidated and the run starts at --from (resume-like)."""
        ctx = _make_ctx(tmp_path)
        _mark_all_success(ctx)
        invalidated = prepare_rerun(ctx, "generate_voice", "mix_bgm")
        assert invalidated[0] == "mix_bgm"
        assert "generate_voice" not in invalidated
        assert "match_clips" not in invalidated
        # Upstream soft fields keep their saved state.
        assert ctx.status.align == "success"
        assert ctx.status.scene == "success"

    def test_structured_log_record_emitted(self, tmp_path, caplog):
        """One structured 'pipeline_rerun' record is emitted for audit."""
        ctx = _make_ctx(tmp_path)
        with caplog.at_level(logging.INFO, logger="movie_narrator.pipeline.runner"):
            prepare_rerun(ctx, "generate_voice", "match_clips")
        records = [r for r in caplog.records if r.getMessage() == "pipeline_rerun"]
        assert len(records) == 1
        rec = records[0]
        assert getattr(rec, "event", None) == "rerun"
        assert getattr(rec, "from_step", None) == "match_clips"
        assert getattr(rec, "state_completed_step", None) == "generate_voice"
        assert getattr(rec, "invalidated_steps", None)[0] == "match_clips"
        assert getattr(rec, "timestamp", None)


# ── Execution starts AT the target step ────────────────────


class TestRerunStartsAtTarget:
    def test_run_pipeline_start_step_begins_at_target(self, tmp_path, monkeypatch):
        """run_pipeline(start_step=...) executes the target step FIRST."""
        ctx = _make_ctx(tmp_path)
        executed: list[str] = []
        _patch_fake_steps(monkeypatch, executed)
        run_pipeline(ctx, start_step="match_clips")
        assert executed[0] == "match_clips"
        assert executed[-1] == "export_clips"

    def test_prepare_rerun_then_run_end_to_end(self, tmp_path, monkeypatch):
        """prepare_rerun + run_pipeline: statuses invalidated, run starts
        at the target step, upstream steps never execute."""
        ctx = _make_ctx(tmp_path)
        _mark_all_success(ctx)
        executed: list[str] = []
        _patch_fake_steps(monkeypatch, executed)

        prepare_rerun(ctx, "generate_voice", "match_clips")
        run_pipeline(ctx, start_step="match_clips")

        assert executed[0] == "match_clips"
        for upstream in ("resolve_video", "research_plot", "generate_voice"):
            assert upstream not in executed
        # Downstream stale success was reset before execution (fake steps
        # don't set statuses, so the reset value survives the run).
        assert ctx.status.match == "disabled"
        assert ctx.status.translate == "skipped"


# ── CLI wiring ─────────────────────────────────────────────


class TestRerunCli:
    def test_rerun_invokes_run_pipeline_from_step(self, tmp_path, monkeypatch):
        """Happy path: state loaded, prepare_rerun applied, run_pipeline
        called with start_step=--from."""
        ctx = _make_ctx(tmp_path)
        state_path = _save_pipeline_state(ctx, "generate_voice")

        calls: dict = {}

        def fake_run_pipeline(c, *, controller=None, start_step=None):
            calls["ctx"] = c
            calls["start_step"] = start_step
            return c

        monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)
        result = runner.invoke(
            app, ["rerun", str(state_path), "--from", "match_clips"]
        )
        assert result.exit_code == 0, result.output
        assert calls["start_step"] == "match_clips"
        rerun_block = calls["ctx"].metadata["rerun"]
        assert rerun_block["from_step"] == "match_clips"
        assert rerun_block["state_completed_step"] == "generate_voice"

    def test_rerun_from_after_completed_behaves_like_resume(
        self, tmp_path, monkeypatch
    ):
        """--from later than completed_step: run starts at --from with no
        upstream invalidation beyond the documented degenerate case."""
        ctx = _make_ctx(tmp_path)
        state_path = _save_pipeline_state(ctx, "resolve_video")

        calls: dict = {}

        def fake_run_pipeline(c, *, controller=None, start_step=None):
            calls["start_step"] = start_step
            return c

        monkeypatch.setattr(runner_mod, "run_pipeline", fake_run_pipeline)
        result = runner.invoke(
            app, ["rerun", str(state_path), "--from", "generate_voice"]
        )
        assert result.exit_code == 0, result.output
        assert calls["start_step"] == "generate_voice"

    def test_rerun_unknown_step_lists_valid_steps(self, tmp_path):
        """Unknown --from step → BadParameter listing valid step names."""
        state_path = tmp_path / "pipeline_state.json"
        state_path.write_text("{}", encoding="utf-8")
        result = runner.invoke(
            app, ["rerun", str(state_path), "--from", "no_such_step"]
        )
        assert result.exit_code != 0
        assert "no_such_step" in result.output
        assert "render_video" in result.output  # a valid step is listed

    def test_rerun_list_steps(self, tmp_path):
        """--list-steps prints ordered names, marking soft steps, and
        exits without touching the state file."""
        result = runner.invoke(
            app, ["rerun", str(tmp_path / "unused.json"), "--list-steps"]
        )
        assert result.exit_code == 0, result.output
        lines = result.output.strip().splitlines()
        assert lines[0] == "resolve_video"
        assert "research_plot (soft)" in lines
        assert "align_audio (soft)" in lines
        assert "render_video" in lines
        # No "(soft)" marker on hard steps.
        assert "generate_script (soft)" not in lines

    def test_rerun_missing_state_file(self, tmp_path):
        """Missing state file → exit code 1 with a clear message."""
        result = runner.invoke(
            app, ["rerun", str(tmp_path / "missing.json"), "--from", "render_video"]
        )
        assert result.exit_code == 1
        assert "State file not found" in result.output
