# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.2 Wave 3A observability.

Covers:
- Structured step logs (``extra=`` fields on step completion / failure / retry).
- Execution manifest (``execution_manifest.json``) written at pipeline end.
- Generation dry-run (``--dry-run`` reusing the ``workflow_steps`` mechanism).

No network or LLM is required — the runner's ``run_preflight`` is patched out
and steps are replaced with pure passthrough functions.
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from movie_narrator import __version__
from movie_narrator.cli import app
from movie_narrator.contract import CONTRACT_VERSION
from movie_narrator.models import Context
from movie_narrator.pipeline.errors import StepAction
from movie_narrator.pipeline import runner as runner_mod
from movie_narrator.pipeline.runner import (
    DRY_RUN_DISABLED_STEPS,
    apply_dry_run_steps,
    run_pipeline,
)

cli_runner = CliRunner()


def _minimal_ctx(tmp_path: Path) -> Context:
    """Build a Context with a SilentConsole, ready for run_pipeline."""
    ctx = Context(movie_name="m", output_dir=str(tmp_path))
    ctx.metadata["run_id"] = "test-run"
    return ctx


def _step_records(caplog):
    """Filter caplog records down to the structured ``event=step`` lines."""
    return [r for r in caplog.records if getattr(r, "event", None) == "step"]


# ── Structured step logs ───────────────────────────────────


def test_step_log_success_and_failure(tmp_path, caplog, monkeypatch):
    """Each step emits a structured record; failures carry error_class."""
    caplog.set_level(logging.DEBUG)

    def _ok(ctx: Context) -> Context:
        return ctx

    _ok.__name__ = "step_ok"

    def _fail(ctx: Context) -> Context:
        raise ValueError("boom")

    _fail.__name__ = "step_fail"

    ctx = _minimal_ctx(tmp_path)
    monkeypatch.setattr(runner_mod, "STEPS", [_ok, _fail])
    monkeypatch.setattr(runner_mod, "run_preflight", lambda c: None)

    with pytest.raises(ValueError):
        run_pipeline(ctx)

    records = _step_records(caplog)
    assert records, "expected structured step records"

    ok_record = next(r for r in records if r.step == "step_ok")
    assert ok_record.result == "success"
    assert ok_record.attempt == 1
    assert ok_record.duration_s >= 0
    assert not hasattr(ok_record, "error_class")
    assert ok_record.task_id == "test-run"

    fail_record = next(r for r in records if r.step == "step_fail")
    assert fail_record.result == "failed"
    assert fail_record.error_class == "ValueError"
    assert fail_record.attempt == 1


def test_step_log_records_attempt_on_retry(tmp_path, caplog, monkeypatch):
    """A hard step that fails once then succeeds records the final attempt."""
    caplog.set_level(logging.DEBUG)

    state = {"calls": 0}

    def _flaky(ctx: Context) -> Context:
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("transient")
        return ctx

    _flaky.__name__ = "step_flaky"

    ctx = _minimal_ctx(tmp_path)
    controller = MagicMock()
    controller.is_cancelled.return_value = False
    controller.on_step_error.return_value = StepAction.RETRY

    monkeypatch.setattr(runner_mod, "STEPS", [_flaky])
    monkeypatch.setattr(runner_mod, "run_preflight", lambda c: None)

    run_pipeline(ctx, controller=controller)

    records = _step_records(caplog)
    final = records[-1]
    assert final.step == "step_flaky"
    assert final.attempt == 2
    assert final.result == "success"


def test_step_log_marks_disabled_step(tmp_path, caplog, monkeypatch):
    """A workflow_steps-disabled step records result='disabled' without running."""
    caplog.set_level(logging.DEBUG)

    ctx = _minimal_ctx(tmp_path)
    ctx.metadata["workflow_steps"] = {"align_audio": False}

    def _align(ctx: Context) -> Context:
        raise AssertionError("should not run")

    _align.__name__ = "align_audio"

    monkeypatch.setattr(runner_mod, "STEPS", [_align])
    monkeypatch.setattr(runner_mod, "run_preflight", lambda c: None)

    run_pipeline(ctx)

    records = _step_records(caplog)
    assert records[0].step == "align_audio"
    assert records[0].result == "disabled"
    assert records[0].attempt == 0
    assert records[0].duration_s == 0.0


def test_step_log_extra_fields_survive_json_serialization():
    """The emitted extra fields are JSON-serialisable by JsonFormatter."""
    from movie_narrator.utils.logging_config import JsonFormatter

    ctx = _minimal_ctx(Path("."))
    entry = runner_mod._emit_step_log(
        ctx, "generate_voice", 1, 1.23456, "success", error_class=None
    )
    record = logging.LogRecord(
        "movie_narrator.pipeline.runner",
        logging.DEBUG,
        __file__,
        1,
        "pipeline_step",
        (),
        None,
    )
    for key, value in entry.items():
        setattr(record, key, value)
    setattr(record, "event", "step")
    setattr(record, "pid", 123)
    setattr(record, "task_id", "test-run")

    payload = json.loads(JsonFormatter().format(record))
    assert payload["step"] == "generate_voice"
    assert payload["attempt"] == 1
    assert payload["duration_s"] == 1.2346
    assert payload["result"] == "success"


# ── Execution manifest ─────────────────────────────────────


def test_execution_manifest_written_after_pipeline(tmp_path):
    """A completed run (with video) writes execution_manifest.json."""
    video = tmp_path / "final.mp4"
    video.write_bytes(b"fake-video-bytes")

    ctx = Context(movie_name="M", style="S", duration=12, output_dir=str(tmp_path))
    ctx.video_path = str(video)
    ctx.metadata["run_id"] = "run-xyz"
    ctx.metadata["research_provider"] = "llm"
    ctx.metadata["vision_captioner"] = "stub"
    ctx.metadata["qa_report"] = {"duration_ok": True}
    ctx.metadata["workflow_steps"] = {"align_audio": False}

    def _step(ctx: Context) -> Context:
        return ctx

    _step.__name__ = "step_x"

    with patch("movie_narrator.pipeline.runner.STEPS", [_step]), patch(
        "movie_narrator.pipeline.runner.run_preflight", lambda c: None
    ):
        run_pipeline(ctx)

    manifest_path = tmp_path / "execution_manifest.json"
    assert manifest_path.exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert manifest["version"] == __version__
    assert manifest["contract_version"] == ".".join(str(v) for v in CONTRACT_VERSION)
    assert manifest["run_id"] == "run-xyz"
    assert manifest["input"]["movie"] == "M"
    assert manifest["input"]["duration"] == 12
    assert manifest["providers"]["research_provider"] == "llm"
    assert manifest["providers"]["vision_captioner"] == "stub"
    assert manifest["workflow_steps"] == {"align_audio": False}
    assert manifest["checksum"]["sha256"] == hashlib.sha256(b"fake-video-bytes").hexdigest()
    assert manifest["checksum"]["path"] == str(video)
    assert manifest["qa"]["qa_report"] == {"duration_ok": True}
    assert manifest["dry_run"] is False
    assert manifest["steps"][0]["step"] == "step_x"
    assert manifest["steps"][0]["result"] == "success"


def test_execution_manifest_no_video_no_file(tmp_path):
    """Without a video path (e.g. dry-run), no manifest is emitted."""
    ctx = _minimal_ctx(tmp_path)

    def _step(ctx: Context) -> Context:
        return ctx

    _step.__name__ = "step_x"

    with patch("movie_narrator.pipeline.runner.STEPS", [_step]), patch(
        "movie_narrator.pipeline.runner.run_preflight", lambda c: None
    ):
        run_pipeline(ctx)

    assert not (tmp_path / "execution_manifest.json").exists()


def test_manifest_write_failure_does_not_block(tmp_path, monkeypatch):
    """A failed manifest write degrades silently and returns the context."""
    video = tmp_path / "final.mp4"
    video.write_bytes(b"x")
    ctx = _minimal_ctx(tmp_path)
    ctx.video_path = str(video)

    def _step(ctx: Context) -> Context:
        return ctx

    _step.__name__ = "step_x"

    def _boom(self, *args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", _boom)
    monkeypatch.setattr(runner_mod, "STEPS", [_step])
    monkeypatch.setattr(runner_mod, "run_preflight", lambda c: None)

    result = run_pipeline(ctx)
    assert result is ctx


# ── Generation dry-run ─────────────────────────────────────


def test_apply_dry_run_steps_disables_heavy_steps():
    """Dry-run disables every heavy step and preserves non-heavy flags."""
    merged = apply_dry_run_steps({"align": False, "generate_voice": True})
    assert merged["align"] is False
    # Authoritative overrides: an explicit True for a heavy step is ignored.
    assert merged["generate_voice"] is False
    for step in DRY_RUN_DISABLED_STEPS:
        assert merged[step] is False


def test_apply_dry_run_steps_keeps_non_heavy_flags():
    """Dry-run leaves planning-step flags (research) untouched."""
    merged = apply_dry_run_steps({"research": False})
    assert merged["research"] is False
    assert "research_plot" not in merged


def test_dry_run_keeps_planning_steps_enabled():
    """Dry-run never disables the generated planning steps."""
    merged = apply_dry_run_steps(None)
    for keep in ("resolve_video", "prepare_assets", "research_plot", "generate_script",
                 "export_script_md"):
        assert keep not in merged


def test_cli_dry_run_disables_heavy_steps_and_prints_notice(tmp_path, monkeypatch):
    """--dry-run merges heavy-step disables into workflow_steps and prints a note."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("movie_narrator.cli._EXAMPLE_YAML", tmp_path / "nonexistent.yaml")
    ctx = Context(movie_name="M", output_dir=str(tmp_path))
    bc = MagicMock(return_value=ctx)
    rp = MagicMock(return_value=ctx)
    with (
        patch("movie_narrator.cli.build_context", bc),
        patch("movie_narrator.cli.run_pipeline", rp),
    ):
        result = cli_runner.invoke(app, ["create", "--movie", "M", "--dry-run"])

    assert result.exit_code == 0, result.output
    kwargs = bc.call_args.kwargs
    assert kwargs["workflow_steps"]["render_video"] is False
    assert kwargs["workflow_steps"]["generate_voice"] is False
    assert kwargs["workflow_steps"]["export_clips"] is False
    assert "research_plot" not in kwargs["workflow_steps"]
    assert "Dry-run mode" in result.output
    assert "No final.mp4 will be produced" in result.output


def test_cli_without_dry_run_unchanged(tmp_path, monkeypatch):
    """Without --dry-run the workflow_steps pass through untouched."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("movie_narrator.cli._EXAMPLE_YAML", tmp_path / "nonexistent.yaml")
    ctx = Context(movie_name="M", output_dir=str(tmp_path))
    bc = MagicMock(return_value=ctx)
    rp = MagicMock(return_value=ctx)
    with (
        patch("movie_narrator.cli.build_context", bc),
        patch("movie_narrator.cli.run_pipeline", rp),
    ):
        result = cli_runner.invoke(app, ["create", "--movie", "M"])

    assert result.exit_code == 0, result.output
    kwargs = bc.call_args.kwargs
    assert not kwargs.get("workflow_steps")
    assert "Dry-run mode" not in result.output
