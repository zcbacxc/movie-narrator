from pathlib import Path
from unittest.mock import patch

from movie_narrator.models import Context, StepResult, StepState
from movie_narrator.pipeline.runner import build_context, run_pipeline


def test_run_pipeline_writes_workflow_metadata(tmp_path):
    def _passthrough(ctx: Context) -> Context:
        return ctx

    fake_steps = [_passthrough] * 13
    for i, fn in enumerate(list(fake_steps)):
        fake_steps[i].__name__ = f"step_{i}"

    ctx = build_context(
        movie="M",
        style="S",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=tmp_path,
        workflow_steps={"align_audio": False, "export_clips": False},
        params={"scene_threshold": 33.0, "match_min_score": 0.5, "research_provider": "llm"},
        config_path=str(tmp_path / "job.yaml"),
    )
    with patch("movie_narrator.pipeline.runner.STEPS", fake_steps):
        ctx = run_pipeline(ctx)
    assert ctx.metadata["workflow_steps"] == {"align_audio": False, "export_clips": False}
    assert ctx.metadata["scene_threshold"] == 33.0
    assert ctx.metadata["match_min_score"] == 0.5
    assert ctx.metadata["research_provider"] == "llm"
    assert ctx.metadata["config_path"] == str(tmp_path / "job.yaml")


def test_run_pipeline_omits_workflow_keys_when_empty(tmp_path):
    def _passthrough(ctx: Context) -> Context:
        return ctx

    fake_steps = [_passthrough]
    fake_steps[0].__name__ = "noop"

    ctx = build_context(
        movie="M",
        style="S",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=tmp_path,
    )
    with patch("movie_narrator.pipeline.runner.STEPS", fake_steps):
        ctx = run_pipeline(ctx)
    assert "workflow_steps" not in ctx.metadata or ctx.metadata.get("workflow_steps") in (None, {})
    assert "scene_threshold" not in ctx.metadata
    assert "config_path" not in ctx.metadata


# ── F3: runner surfaces soft-step degradation from non-exception paths ──


def test_run_pipeline_accumulates_degraded_steps_for_internal_fallback(tmp_path):
    """F3: soft step that internally sets status='failed'+WARNING (no raise)
    is accumulated into _degraded_steps by the runner.

    Reproduces the C1 align_fallback scenario: align_audio catches
    whisperx.align() exception internally, sets status.align='failed'
    + step_state.result=WARNING, and returns normally. Before F3, the
    runner's outer except block never fired, so _degraded_steps stayed
    empty and the degradation was invisible in the runner's summary.
    """
    def _passthrough(ctx: Context) -> Context:
        return ctx

    def _align_with_internal_fallback(ctx: Context) -> Context:
        """Simulate C1 align_fallback: catch + set failed + WARNING, no raise."""
        ctx.status.align = "failed"
        ctx.step_state = StepState(
            result=StepResult.WARNING,
            message="forced alignment failed: simulated OOM",
        )
        ctx.metadata["align_fallback"] = True
        ctx.metadata["align_degraded"] = True
        return ctx

    _align_with_internal_fallback.__name__ = "align_audio"
    fake_steps = [_passthrough, _align_with_internal_fallback]
    fake_steps[0].__name__ = "noop"

    ctx = build_context(
        movie="M",
        style="S",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=tmp_path,
    )
    with patch("movie_narrator.pipeline.runner.STEPS", fake_steps):
        ctx = run_pipeline(ctx)

    # F3: _degraded_steps should contain 'align_audio' even though the
    # step returned normally (no exception propagated to runner).
    degraded = ctx.metadata.get("_degraded_steps", [])
    assert "align_audio" in degraded, (
        f"F3: align_audio should be in _degraded_steps for internal fallback, "
        f"got {degraded}"
    )


def test_run_pipeline_does_not_duplicate_degraded_steps(tmp_path):
    """F3: if a soft step both raises AND sets status='failed', the runner
    should not add it to _degraded_steps twice (idempotent)."""
    def _passthrough(ctx: Context) -> Context:
        return ctx

    def _align_raises(ctx: Context) -> Context:
        """Simulate a step that sets status='failed' then raises."""
        ctx.status.align = "failed"
        ctx.step_state = StepState(
            result=StepResult.WARNING,
            message="pre-raise warning",
        )
        raise RuntimeError("align failed after setting status")

    _align_raises.__name__ = "align_audio"
    fake_steps = [_passthrough, _align_raises]
    fake_steps[0].__name__ = "noop"

    ctx = build_context(
        movie="M",
        style="S",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=tmp_path,
    )
    with patch("movie_narrator.pipeline.runner.STEPS", fake_steps):
        ctx = run_pipeline(ctx)

    degraded = ctx.metadata.get("_degraded_steps", [])
    # Should appear exactly once (exception path adds it, F3 dedupes)
    assert degraded.count("align_audio") == 1, (
        f"F3: align_audio should appear exactly once in _degraded_steps, "
        f"got {degraded}"
    )


def test_run_pipeline_no_degraded_when_step_succeeds(tmp_path):
    """F3: a step that returns normally with status='success' should NOT
    be added to _degraded_steps."""
    def _passthrough(ctx: Context) -> Context:
        return ctx

    def _align_success(ctx: Context) -> Context:
        ctx.status.align = "success"
        ctx.step_state = StepState()
        return ctx

    _align_success.__name__ = "align_audio"
    fake_steps = [_passthrough, _align_success]
    fake_steps[0].__name__ = "noop"

    ctx = build_context(
        movie="M",
        style="S",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=tmp_path,
    )
    with patch("movie_narrator.pipeline.runner.STEPS", fake_steps):
        ctx = run_pipeline(ctx)

    degraded = ctx.metadata.get("_degraded_steps", [])
    assert "align_audio" not in degraded, (
        f"F3: align_audio should NOT be in _degraded_steps when it succeeds, "
        f"got {degraded}"
    )
