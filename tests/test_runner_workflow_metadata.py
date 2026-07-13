from pathlib import Path
from unittest.mock import patch

from movie_narrator.models import Context
from movie_narrator.pipeline.runner import run_pipeline


def test_run_pipeline_writes_workflow_metadata(tmp_path):
    def _passthrough(ctx: Context) -> Context:
        return ctx

    fake_steps = [_passthrough] * 13
    for i, fn in enumerate(list(fake_steps)):
        fake_steps[i].__name__ = f"step_{i}"

    with patch("movie_narrator.pipeline.runner.STEPS", fake_steps):
        ctx = run_pipeline(
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

    with patch("movie_narrator.pipeline.runner.STEPS", fake_steps):
        ctx = run_pipeline(
            movie="M",
            style="S",
            duration=10,
            voice=None,
            format="16:9",
            output_dir=tmp_path,
        )
    assert "workflow_steps" not in ctx.metadata or ctx.metadata.get("workflow_steps") in (None, {})
    assert "scene_threshold" not in ctx.metadata
    assert "config_path" not in ctx.metadata
