"""Tests for step-level retry mechanism in the pipeline runner."""

from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.config import TTSProviderType
from movie_narrator.models import Context, Services, StepResult
from movie_narrator.pipeline.errors import StepAction
from movie_narrator.pipeline.runner import _handle_step_error


def _make_ctx(tmp_path):
    return Context(
        movie_name="test",
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )


# ── _handle_step_error helper ──────────────────────────────


def test_handle_step_error_no_controller(tmp_path):
    """No controller → ABORT (existing fail-fast behavior)."""
    ctx = _make_ctx(tmp_path)
    action = _handle_step_error(None, "generate_voice", RuntimeError("timeout"), 1, ctx.services.console)
    assert action is StepAction.ABORT


def test_handle_step_error_no_handler_method(tmp_path):
    """Controller without on_step_error → ABORT."""
    ctx = _make_ctx(tmp_path)
    controller = MagicMock()
    del controller.on_step_error  # ensure attribute doesn't exist
    action = _handle_step_error(controller, "generate_voice", RuntimeError("timeout"), 1, ctx.services.console)
    assert action is StepAction.ABORT


def test_handle_step_error_retry(tmp_path):
    """Controller returns RETRY → StepAction.RETRY."""
    ctx = _make_ctx(tmp_path)
    controller = MagicMock()
    controller.on_step_error.return_value = StepAction.RETRY
    action = _handle_step_error(controller, "generate_voice", RuntimeError("timeout"), 1, ctx.services.console)
    assert action is StepAction.RETRY
    controller.on_step_error.assert_called_once_with("generate_voice", controller.on_step_error.call_args[0][1], 1)


def test_handle_step_error_skip(tmp_path):
    """Controller returns SKIP → StepAction.SKIP."""
    ctx = _make_ctx(tmp_path)
    controller = MagicMock()
    controller.on_step_error.return_value = StepAction.SKIP
    action = _handle_step_error(controller, "generate_voice", RuntimeError("timeout"), 1, ctx.services.console)
    assert action is StepAction.SKIP


def test_handle_step_error_abort(tmp_path):
    """Controller returns ABORT → StepAction.ABORT."""
    ctx = _make_ctx(tmp_path)
    controller = MagicMock()
    controller.on_step_error.return_value = StepAction.ABORT
    action = _handle_step_error(controller, "generate_voice", RuntimeError("timeout"), 1, ctx.services.console)
    assert action is StepAction.ABORT


# ── StepAction enum ────────────────────────────────────────


def test_step_action_values():
    """StepAction has exactly RETRY, SKIP, ABORT."""
    assert StepAction.RETRY.value == "retry"
    assert StepAction.SKIP.value == "skip"
    assert StepAction.ABORT.value == "abort"
    assert len(StepAction) == 3


# ── InteractiveCLIController ───────────────────────────────


def test_interactive_controller_retry(tmp_path, monkeypatch):
    """InteractiveCLIController parses 'r' as RETRY."""
    monkeypatch.setattr("builtins.input", lambda _: "r")
    from movie_narrator.cli import InteractiveCLIController

    controller = InteractiveCLIController()
    action = controller.on_step_error("generate_voice", RuntimeError("timeout"), 1)
    assert action is StepAction.RETRY


def test_interactive_controller_skip(tmp_path, monkeypatch):
    """InteractiveCLIController parses 's' as SKIP."""
    monkeypatch.setattr("builtins.input", lambda _: "s")
    from movie_narrator.cli import InteractiveCLIController

    controller = InteractiveCLIController()
    action = controller.on_step_error("generate_voice", RuntimeError("timeout"), 1)
    assert action is StepAction.SKIP


def test_interactive_controller_abort_default(tmp_path, monkeypatch):
    """InteractiveCLIController defaults to ABORT on unknown input."""
    monkeypatch.setattr("builtins.input", lambda _: "x")
    from movie_narrator.cli import InteractiveCLIController

    controller = InteractiveCLIController()
    action = controller.on_step_error("generate_voice", RuntimeError("timeout"), 1)
    assert action is StepAction.ABORT


def test_interactive_controller_eof_aborts(tmp_path, monkeypatch):
    """InteractiveCLIController aborts on EOFError (no stdin)."""
    def raise_eof(_):
        raise EOFError()

    monkeypatch.setattr("builtins.input", raise_eof)
    from movie_narrator.cli import InteractiveCLIController

    controller = InteractiveCLIController()
    action = controller.on_step_error("generate_voice", RuntimeError("timeout"), 1)
    assert action is StepAction.ABORT


def test_interactive_controller_is_cancelled_default():
    """InteractiveCLIController.is_cancelled() defaults to False."""
    from movie_narrator.cli import InteractiveCLIController

    controller = InteractiveCLIController()
    assert controller.is_cancelled() is False
