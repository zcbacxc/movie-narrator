# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Coverage-boost tests for ``movie_narrator.cli``.

These tests focus on the CLI-command layer: entry functions, argument
validation, error branches, and return codes.  All heavyweight
dependencies (pipeline runner, LLM, TTS, FFmpeg, scene detection, cloud
daemon) are mocked so no network or real media is required.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

import movie_narrator.cli as cli
from movie_narrator.config import ServerOpsSettings
from movie_narrator.cli import (
    InteractiveCLIController,
    _format_degradation_hints,
    _format_match_summary,
    app,
)
from movie_narrator.cloud.models import (
    Task,
    TaskProgress,
    TaskRequest,
    TaskResult,
    TaskStatus,
)
from movie_narrator.models import Context
from movie_narrator.pipeline.errors import PipelinePaused, StepAction

runner = CliRunner()


# ── Shared helpers ─────────────────────────────────────────


def fake_ctx(tmp_path, **overrides):
    """A bare Context with a video_path set, plus optional metadata."""
    ctx = Context(movie_name="M", output_dir=str(tmp_path))
    ctx.video_path = str(tmp_path / "final.mp4")
    ctx.metadata = {}
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


def patch_create_pipeline(tmp_path, ctx=None):
    """Patch module-level build_context + run_pipeline.

    Returns (bc, rp). ``run_pipeline`` returns ``ctx``.
    """
    ctx = ctx or fake_ctx(tmp_path)
    bc = MagicMock(return_value=ctx)
    rp = MagicMock(return_value=ctx)
    return bc, rp


def resolved_job(**overrides):
    """A SimpleNamespace standing in for the object returned by merge_job."""
    base = dict(
        movie="TestMovie",
        style="热血搞笑",
        duration=60,
        voice=None,
        video_format="16:9",
        keep_cache=False,
        video=None,
        library_dir=None,
        research=None,
        bgm=None,
        no_bgm=False,
        no_clips=False,
        strict=False,
        workflow_steps=None,
        params=None,
        config_path=None,
        subtitle_lang=None,
        subtitle_mode=None,
        narration_preset=None,
        lang="zh",
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def make_task(**overrides):
    req = TaskRequest(movie_name="TestMovie")
    task = Task(request=req)
    task.id = "task123"
    task.status = TaskStatus.COMPLETED
    task.created_at = "2026-01-01T00:00:00+00:00"
    task.started_at = "2026-01-01T00:00:01+00:00"
    task.completed_at = "2026-01-01T00:01:00+00:00"
    task.retries = 1
    task.progress = TaskProgress(
        current_step="render_video",
        current_step_index=5,
        total_steps=16,
        percentage=31.0,
        elapsed_seconds=59.0,
        steps_completed=["resolve_video", "script"],
        steps_skipped=["align_audio"],
        steps_failed=["match_clips"],
    )
    task.result = TaskResult(
        video_path="/out/final.mp4",
        audio_path="/out/narration.mp3",
        subtitle_path="/out/subtitle.srt",
        output_dir="/out",
    )
    for k, v in overrides.items():
        setattr(task, k, v)
    return task


# ── _format_match_summary / _format_degradation_hints ──────


def test_format_match_summary_none_when_missing(tmp_path):
    ctx = fake_ctx(tmp_path)
    assert _format_match_summary(ctx) is None


def test_format_match_summary_with_all_sources(tmp_path):
    ctx = fake_ctx(tmp_path)
    ctx.metadata["match_summary"] = {
        "segments": 10,
        "source_counts": {"embedding": 6, "heuristic": 3, "fallback": 1, "scene": 2},
        "score": {"avg": 0.857},
        "degraded_reason": "all_heuristic",
    }
    line = _format_match_summary(ctx)
    assert line.startswith("match: 10 segs")
    assert "emb 6(60%)" in line
    assert "heur 3(30%)" in line
    assert "fb 1" in line
    assert "scene 2" in line
    assert "avg 0.86" in line
    assert "degraded: all_heuristic" in line


def test_format_match_summary_zero_segments_no_crash(tmp_path):
    ctx = fake_ctx(tmp_path)
    ctx.metadata["match_summary"] = {
        "segments": 0,
        "source_counts": {"embedding": 0, "heuristic": 0},
        "score": {"avg": 0.5},
    }
    line = _format_match_summary(ctx)
    assert "match: 0 segs" in line


def test_format_degradation_hints_fake_captions(tmp_path):
    ctx = fake_ctx(tmp_path)
    ctx.metadata["match_summary"] = {"degraded_reason": "fake_captions"}
    hints = _format_degradation_hints(ctx)
    assert any("fake captions" in h for h in hints)


def test_format_degradation_hints_all_heuristic(tmp_path):
    ctx = fake_ctx(tmp_path)
    ctx.metadata["match_summary"] = {"degraded_reason": "all_heuristic"}
    hints = _format_degradation_hints(ctx)
    assert any("all segments fell back" in h for h in hints)


def test_format_degradation_hints_other_reason_and_steps(tmp_path):
    ctx = fake_ctx(tmp_path)
    ctx.metadata["match_summary"] = {"degraded_reason": "something_else"}
    ctx.metadata["_degraded_steps"] = ["match_clips", "align_audio"]
    hints = _format_degradation_hints(ctx)
    assert any("degraded: something_else" in h for h in hints)
    assert any("match_clips, align_audio" in h for h in hints)


def test_format_degradation_hints_empty(tmp_path):
    ctx = fake_ctx(tmp_path)
    assert _format_degradation_hints(ctx) == []


# ── InteractiveCLIController ───────────────────────────────


def test_interactive_controller_cancelled_state():
    ctrl = InteractiveCLIController()
    assert ctrl.is_cancelled() is False


def test_interactive_controller_retry(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "r")
    ctrl = InteractiveCLIController()
    assert ctrl.on_step_error("script", RuntimeError("boom"), 1) is StepAction.RETRY


def test_interactive_controller_skip(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "s")
    ctrl = InteractiveCLIController()
    assert ctrl.on_step_error("script", RuntimeError("boom"), 1) is StepAction.SKIP


def test_interactive_controller_abort(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt: "x")
    ctrl = InteractiveCLIController()
    assert ctrl.on_step_error("script", RuntimeError("boom"), 1) is StepAction.ABORT


def test_interactive_controller_eof(monkeypatch):
    def raise_eof(prompt):
        raise EOFError()

    monkeypatch.setattr("builtins.input", raise_eof)
    ctrl = InteractiveCLIController()
    assert ctrl.on_step_error("script", RuntimeError("boom"), 1) is StepAction.ABORT


# ── create command ─────────────────────────────────────────


def test_create_pipeline_paused_exits_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_EXAMPLE_YAML", tmp_path / "nonexistent.yaml")
    bc, rp = patch_create_pipeline(tmp_path)
    rp.side_effect = PipelinePaused("script")
    with (
        patch("movie_narrator.cli.build_context", bc),
        patch("movie_narrator.cli.run_pipeline", rp),
        patch("movie_narrator.workflow.merge_job", return_value=resolved_job()),
        patch("movie_narrator.config.get_settings", return_value=MagicMock()),
    ):
        result = runner.invoke(app, ["create", "--movie", "M"])
    assert result.exit_code == 0
    assert "Resume with: mn resume" in result.output


def test_create_preflight_error_exits_one(tmp_path, monkeypatch):
    from movie_narrator.pipeline.preflight import PreflightError

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_EXAMPLE_YAML", tmp_path / "nonexistent.yaml")
    bc, rp = patch_create_pipeline(tmp_path)
    rp.side_effect = PreflightError("no ffmpeg")
    with (
        patch("movie_narrator.cli.build_context", bc),
        patch("movie_narrator.cli.run_pipeline", rp),
        patch("movie_narrator.workflow.merge_job", return_value=resolved_job()),
        patch("movie_narrator.config.get_settings", return_value=MagicMock()),
    ):
        result = runner.invoke(app, ["create", "--movie", "M"])
    assert result.exit_code == 1
    assert "no ffmpeg" in result.output


def test_create_generic_error_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_EXAMPLE_YAML", tmp_path / "nonexistent.yaml")
    bc, rp = patch_create_pipeline(tmp_path)
    rp.side_effect = RuntimeError("boom")
    with (
        patch("movie_narrator.cli.build_context", bc),
        patch("movie_narrator.cli.run_pipeline", rp),
        patch("movie_narrator.workflow.merge_job", return_value=resolved_job()),
        patch("movie_narrator.config.get_settings", return_value=MagicMock()),
    ):
        result = runner.invoke(app, ["create", "--movie", "M"])
    assert result.exit_code == 1


def test_create_script_degraded_and_match_output(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_EXAMPLE_YAML", tmp_path / "nonexistent.yaml")
    ctx = fake_ctx(tmp_path)
    ctx.metadata["script_degraded"] = True
    ctx.metadata["match_summary"] = {"segments": 5, "source_counts": {"embedding": 5}}
    ctx.metadata["_degraded_steps"] = ["match_clips"]
    bc, rp = patch_create_pipeline(tmp_path, ctx)
    with (
        patch("movie_narrator.cli.build_context", bc),
        patch("movie_narrator.cli.run_pipeline", rp),
        patch("movie_narrator.workflow.merge_job", return_value=resolved_job()),
        patch("movie_narrator.config.get_settings", return_value=MagicMock()),
    ):
        result = runner.invoke(app, ["create", "--movie", "M"])
    assert result.exit_code == 0
    assert "旁白为占位" in result.output
    assert "match: 5 segs" in result.output
    assert str(ctx.video_path) in result.output


def test_create_video_not_found_badparameter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_EXAMPLE_YAML", tmp_path / "nonexistent.yaml")
    with (
        patch("movie_narrator.workflow.merge_job", return_value=resolved_job(video=str(tmp_path / "nope.mp4"))),
        patch("movie_narrator.config.get_settings", return_value=MagicMock()),
    ):
        result = runner.invoke(app, ["create", "--movie", "M"])
    assert result.exit_code != 0
    assert "video not found" in (result.output or str(result.exception))


def test_create_config_not_found_badparameter(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["create", "--config", str(tmp_path / "missing.yaml")])
    assert result.exit_code != 0
    assert "config not found" in (result.output or str(result.exception))


def test_create_pause_at_sets_metadata(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_EXAMPLE_YAML", tmp_path / "nonexistent.yaml")
    ctx = fake_ctx(tmp_path)
    bc, rp = patch_create_pipeline(tmp_path, ctx)
    with (
        patch("movie_narrator.cli.build_context", bc),
        patch("movie_narrator.cli.run_pipeline", rp),
        patch("movie_narrator.workflow.merge_job", return_value=resolved_job()),
        patch("movie_narrator.config.get_settings", return_value=MagicMock()),
    ):
        result = runner.invoke(app, ["create", "--movie", "M", "--pause-at", "script"])
    assert result.exit_code == 0
    assert ctx.metadata.get("pause_at") == "script"


def test_create_retry_passes_controller(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_EXAMPLE_YAML", tmp_path / "nonexistent.yaml")
    ctx = fake_ctx(tmp_path)
    bc, rp = patch_create_pipeline(tmp_path, ctx)
    with (
        patch("movie_narrator.cli.build_context", bc),
        patch("movie_narrator.cli.run_pipeline", rp),
        patch("movie_narrator.workflow.merge_job", return_value=resolved_job()),
        patch("movie_narrator.config.get_settings", return_value=MagicMock()),
    ):
        result = runner.invoke(app, ["create", "--movie", "M", "--retry"])
    assert result.exit_code == 0
    controller = rp.call_args.kwargs.get("controller")
    assert isinstance(controller, InteractiveCLIController)


def test_create_requires_movie_or_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_EXAMPLE_YAML", tmp_path / "nonexistent.yaml")
    result = runner.invoke(app, ["create"])
    assert result.exit_code != 0


# ── race command ───────────────────────────────────────────


def test_race_happy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result_obj = SimpleNamespace(error=None, video_path="/out/v.mp4", config=SimpleNamespace(label="preset-a"))
    with (
        patch(
            "movie_narrator.race.generate_candidates",
            return_value=[{"preset": "a"}, {"preset": "b"}],
        ),
        patch("movie_narrator.race.run_race", return_value=[result_obj, result_obj]),
        patch("movie_narrator.race.format_race_report", return_value="REPORT"),
        patch("movie_narrator.race.save_race_report"),
    ):
        result = runner.invoke(
            app, ["race", "--movie", "M", "--presets", "a,b", "--output-dir", str(tmp_path / "out")]
        )
    assert result.exit_code == 0
    assert "Starting race with 2 candidates" in result.output
    assert "REPORT" in result.output
    assert "Best candidate: preset-a" in result.output


def test_race_requires_movie_or_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["race"])
    assert result.exit_code != 0


def test_race_config_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["race", "--config", str(tmp_path / "nope.yaml")])
    assert result.exit_code != 0
    assert "config not found" in (result.output or str(result.exception))


# ── imitate command ────────────────────────────────────────


def test_imitate_reference_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["imitate", "--reference", str(tmp_path / "nope.mp4"), "--movie", "M"])
    assert result.exit_code != 0
    assert "reference video not found" in (result.output or str(result.exception))


def test_imitate_requires_movie_unless_analyze_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ref = tmp_path / "ref.mp4"
    ref.write_bytes(b"00")
    result = runner.invoke(app, ["imitate", "--reference", str(ref)])
    assert result.exit_code != 0
    assert "movie is required" in (result.output or str(result.exception))


def test_imitate_analyze_only(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ref = tmp_path / "ref.mp4"
    ref.write_bytes(b"00")
    metrics = {"sentence_density": 1.0}
    out = tmp_path / "out"
    with (
        patch("movie_narrator.imitate.analyze_reference", return_value=metrics),
        patch("movie_narrator.imitate.format_analysis_report", return_value="REPORT"),
    ):
        result = runner.invoke(
            app, ["imitate", "--reference", str(ref), "--analyze-only", "--output-dir", str(out)]
        )
    assert result.exit_code == 0
    assert "REPORT" in result.output
    assert "reference_analysis.json" in result.output


def test_imitate_full_generation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ref = tmp_path / "ref.mp4"
    ref.write_bytes(b"00")
    ctx = fake_ctx(tmp_path)
    bc = MagicMock(return_value=ctx)
    rp = MagicMock(return_value=ctx)
    with (
        patch("movie_narrator.imitate.analyze_reference", return_value={"density": 2.0}),
        patch("movie_narrator.imitate.metrics_to_params", return_value={"p": 1}),
        patch("movie_narrator.imitate.metrics_to_preset_name", return_value="preset-x"),
        patch("movie_narrator.imitate.format_analysis_report", return_value="REPORT"),
        patch("movie_narrator.pipeline.runner.build_context", bc),
        patch("movie_narrator.pipeline.runner.run_pipeline", rp),
    ):
        result = runner.invoke(
            app,
            ["imitate", "--reference", str(ref), "--movie", "M", "--output-dir", str(tmp_path / "out")],
        )
    assert result.exit_code == 0
    assert "Using preset: preset-x" in result.output
    assert str(ctx.video_path) in result.output


def test_imitate_pipeline_paused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ref = tmp_path / "ref.mp4"
    ref.write_bytes(b"00")
    ctx = fake_ctx(tmp_path)
    bc = MagicMock(return_value=ctx)
    rp = MagicMock(side_effect=PipelinePaused("script"))
    with (
        patch("movie_narrator.imitate.analyze_reference", return_value={"density": 2.0}),
        patch("movie_narrator.imitate.metrics_to_params", return_value={"p": 1}),
        patch("movie_narrator.imitate.metrics_to_preset_name", return_value="preset-x"),
        patch("movie_narrator.imitate.format_analysis_report", return_value="REPORT"),
        patch("movie_narrator.pipeline.runner.build_context", bc),
        patch("movie_narrator.pipeline.runner.run_pipeline", rp),
    ):
        result = runner.invoke(
            app,
            ["imitate", "--reference", str(ref), "--movie", "M", "--output-dir", str(tmp_path / "out")],
        )
    assert result.exit_code == 0
    assert "Resume with: mn resume" in result.output


# ── resume command ─────────────────────────────────────────


def test_resume_state_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["resume", "--state", str(tmp_path / "nope.json")])
    assert result.exit_code == 1
    assert "State file not found" in result.output


def test_resume_already_completed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"completed_step": "x", "context": {"movie_name": "M", "output_dir": str(tmp_path)}}), encoding="utf-8")
    ctx = fake_ctx(tmp_path)
    with (
        patch("movie_narrator.pipeline.runner._load_pipeline_state", return_value=(ctx, "last_step")),
        patch("movie_narrator.pipeline.runner._next_step_after", return_value=None),
        patch("movie_narrator.utils.console.build_console", return_value=MagicMock()),
    ):
        result = runner.invoke(app, ["resume", "--state", str(state)])
    assert result.exit_code == 0
    assert "Nothing to resume" in result.output


def test_resume_happy_path(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "s.json"
    state.write_text("{}", encoding="utf-8")
    ctx = fake_ctx(tmp_path)
    rp = MagicMock(return_value=ctx)
    console = MagicMock()
    with (
        patch("movie_narrator.pipeline.runner._load_pipeline_state", return_value=(ctx, "script")),
        patch("movie_narrator.pipeline.runner._next_step_after", return_value="tts"),
        patch("movie_narrator.utils.console.build_console", return_value=console),
        patch("movie_narrator.pipeline.runner.run_pipeline", rp),
    ):
        result = runner.invoke(app, ["resume", "--state", str(state)])
    assert result.exit_code == 0
    assert rp.call_args.kwargs["start_step"] == "tts"
    assert str(ctx.video_path) in result.output


def test_resume_pipeline_paused(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "s.json"
    state.write_text("{}", encoding="utf-8")
    ctx = fake_ctx(tmp_path)
    rp = MagicMock(side_effect=PipelinePaused("tts"))
    with (
        patch("movie_narrator.pipeline.runner._load_pipeline_state", return_value=(ctx, "script")),
        patch("movie_narrator.pipeline.runner._next_step_after", return_value="tts"),
        patch("movie_narrator.utils.console.build_console", return_value=MagicMock()),
        patch("movie_narrator.pipeline.runner.run_pipeline", rp),
    ):
        result = runner.invoke(app, ["resume", "--state", str(state)])
    assert result.exit_code == 0
    assert "Resume with: mn resume" in result.output


# ── resolve / research / debug commands ────────────────────


def test_resolve_json_matched(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = Context(movie_name="M", output_dir=str(tmp_path))
    ctx.source_video_path = "/lib/M.mp4"
    with patch("movie_narrator.cli.resolve_video") as rv:
        result = runner.invoke(
            app, ["resolve", "--movie", "M", "--library-dir", str(tmp_path), "--json"]
        )
    rv.assert_called_once()
    parsed = json.loads(result.output.strip())
    assert parsed["matched"] is False  # ctx in mock not linked
    assert "path" in parsed


def test_research_writes_when_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ctx = Context(movie_name="M", output_dir=str(tmp_path))
    ctx.status.research = "success"
    with patch("movie_narrator.cli.research_plot") as rp:
        result = runner.invoke(app, ["research", "--movie", "M", "--output-dir", str(tmp_path / "out")])
    rp.assert_called_once()
    assert result.exit_code == 0
    assert "Research completed." in result.output


def test_research_failed_exits_one(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)

    def mark_failed(c):
        c.status.research = "failed"

    with patch("movie_narrator.cli.research_plot", side_effect=mark_failed):
        result = runner.invoke(app, ["research", "--movie", "M", "--output-dir", str(tmp_path / "out")])
    assert result.exit_code == 1


def test_research_writes_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    research_path = tmp_path / "out" / "research.json"
    research_path.parent.mkdir(parents=True, exist_ok=True)
    research_path.write_text("{}", encoding="utf-8")
    ctx = Context(movie_name="M", output_dir=str(tmp_path))
    ctx.status.research = "success"
    with patch("movie_narrator.cli.research_plot") as rp:
        result = runner.invoke(app, ["research", "--movie", "M", "--output-dir", str(tmp_path / "out")])
    assert result.exit_code == 0
    assert "Research written to:" in result.output


def test_scenes_success(tmp_path, monkeypatch):
    fake_video = tmp_path / "v.mp4"
    fake_video.write_bytes(b"00")

    def mark_success(c):
        c.status.scene = "success"

    with patch("movie_narrator.pipeline.scenes.detect_scenes", side_effect=mark_success):
        result = runner.invoke(
            app, ["scenes", "--video", str(fake_video), "--output", str(tmp_path / "out")]
        )
    assert result.exit_code == 0
    assert "Scenes: 0" in result.output


def test_scenes_disabled_exits_one(tmp_path, monkeypatch):
    fake_video = tmp_path / "v.mp4"
    fake_video.write_bytes(b"00")
    ctx = Context(movie_name="debug", output_dir=str(tmp_path / "out"))
    ctx.status.scene = "disabled"
    with patch("movie_narrator.pipeline.scenes.detect_scenes", return_value=ctx):
        result = runner.invoke(
            app, ["scenes", "--video", str(fake_video), "--output", str(tmp_path / "out")]
        )
    assert result.exit_code == 1
    assert "[media]" in result.output


def test_align_success_with_script(tmp_path, monkeypatch):
    fake_audio = tmp_path / "a.wav"
    fake_audio.write_bytes(b"00")
    script = tmp_path / "s.txt"
    script.write_text("line one\nline two\n", encoding="utf-8")

    def mark_success(c):
        c.status.align = "success"

    with patch("movie_narrator.pipeline.align.align_audio", side_effect=mark_success):
        result = runner.invoke(
            app, ["align", "--audio", str(fake_audio), "--script", str(script), "--output", str(tmp_path / "out")]
        )
    assert result.exit_code == 0
    assert "Segments: 2" in result.output


def test_align_disabled_exits_one(tmp_path, monkeypatch):
    fake_audio = tmp_path / "a.wav"
    fake_audio.write_bytes(b"00")
    ctx = Context(movie_name="align_debug", output_dir=str(tmp_path / "out"))
    ctx.status.align = "disabled"
    with patch("movie_narrator.pipeline.align.align_audio", return_value=ctx):
        result = runner.invoke(
            app, ["align", "--audio", str(fake_audio), "--output", str(tmp_path / "out")]
        )
    assert result.exit_code == 1
    assert "[ml]" in result.output


def test_clips_success(tmp_path, monkeypatch):
    fake_video = tmp_path / "v.mp4"
    fake_video.write_bytes(b"00")
    scenes_json = tmp_path / "scenes.json"
    scenes_json.write_text(json.dumps([{"index": 0, "start": 0.0, "end": 1.0}]), encoding="utf-8")

    def mark_success(c):
        c.status.export = "success"
        c.clips_dir = str(tmp_path / "out" / "clips")

    with patch("movie_narrator.pipeline.export_clips.export_clips", side_effect=mark_success):
        result = runner.invoke(
            app,
            ["clips", "--video", str(fake_video), "--scenes", str(scenes_json), "--output", str(tmp_path / "out")],
        )
    assert result.exit_code == 0
    assert "Export status: success" in result.output


def test_clips_disabled_exits_one(tmp_path, monkeypatch):
    fake_video = tmp_path / "v.mp4"
    fake_video.write_bytes(b"00")
    scenes_json = tmp_path / "scenes.json"
    scenes_json.write_text(json.dumps([{"index": 0, "start": 0.0, "end": 1.0}]), encoding="utf-8")
    ctx = Context(movie_name="clips_debug", output_dir=str(tmp_path / "out"))
    ctx.status.export = "disabled"
    with patch("movie_narrator.pipeline.export_clips.export_clips", return_value=ctx):
        result = runner.invoke(
            app,
            ["clips", "--video", str(fake_video), "--scenes", str(scenes_json), "--output", str(tmp_path / "out")],
        )
    assert result.exit_code == 1
    assert "[media]" in result.output


# ── plugin command ─────────────────────────────────────────


def test_plugin_list_empty(tmp_path):
    with patch("movie_narrator.plugin_loader.list_available_plugins", return_value=[]):
        result = runner.invoke(app, ["plugin", "list"])
    assert result.exit_code == 0
    assert "No plugins found" in result.output


def test_plugin_list_with_plugins(tmp_path):
    with patch("movie_narrator.plugin_loader.list_available_plugins", return_value=["alpha", "beta"]):
        result = runner.invoke(app, ["plugin", "list"])
    assert result.exit_code == 0
    assert "alpha" in result.output
    assert "beta" in result.output


def test_plugin_discover_empty(tmp_path):
    with patch("movie_narrator.plugin_loader.discover_plugins", return_value=[]):
        result = runner.invoke(app, ["plugin", "discover"])
    assert result.exit_code == 0
    assert "No plugins found" in result.output


def test_plugin_discover_with_results(tmp_path):
    ok = SimpleNamespace(success=True, name="p1", error=None)
    fail = SimpleNamespace(success=False, name="p2", error="boom")
    with patch("movie_narrator.plugin_loader.discover_plugins", return_value=[ok, fail]):
        result = runner.invoke(app, ["plugin", "discover"])
    assert result.exit_code == 0
    assert "1 succeeded, 1 failed" in result.output
    assert "[OK] p1" in result.output
    assert "[FAIL] p2: boom" in result.output


def test_plugin_registries(tmp_path):
    step_info = MagicMock()
    step_info.info.return_value = [
        {"name": "resolve_video", "soft": False, "insert_after": "x", "insert_before": None}
    ]
    reg_info = MagicMock()
    reg_info.info.return_value = [{"name": "edge", "protocol_validated": True}]
    with (
        patch("movie_narrator.pipeline.registry.step_registry", step_info),
        patch("movie_narrator.providers.tts_registry", reg_info),
        patch("movie_narrator.providers.vision_registry", reg_info),
        patch("movie_narrator.providers.llm_registry", reg_info),
        patch("movie_narrator.providers.research_registry", reg_info),
    ):
        result = runner.invoke(app, ["plugin", "registries"])
    assert result.exit_code == 0
    assert "Step Registry" in result.output
    assert "TTS Registry" in result.output
    assert "Vision Registry" in result.output
    assert "LLM Registry" in result.output
    assert "Research Registry" in result.output


def test_plugin_version(tmp_path):
    result = runner.invoke(app, ["plugin", "version"])
    assert result.exit_code == 0
    assert "CONTRACT_VERSION" in result.output


def test_plugin_unknown_action(tmp_path):
    result = runner.invoke(app, ["plugin", "bogus"])
    assert result.exit_code != 0
    assert "Unknown action" in (result.output or str(result.exception))


# ── version / doctor / preset ──────────────────────────────


def test_version_command(tmp_path):
    result = runner.invoke(app, ["version"])
    assert result.exit_code == 0
    assert "movie-narrator v" in result.output


def test_doctor_healthy(tmp_path):
    report = SimpleNamespace(healthy=True)
    with (
        patch("movie_narrator.doctor.run_doctor", return_value=report),
        patch("movie_narrator.doctor.render_report", return_value="REPORT"),
    ):
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "REPORT" in result.output


def test_doctor_unhealthy_exits_one(tmp_path):
    report = SimpleNamespace(healthy=False)
    with (
        patch("movie_narrator.doctor.run_doctor", return_value=report),
        patch("movie_narrator.doctor.render_report", return_value="REPORT"),
    ):
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1


def test_preset_list_mode(tmp_path):
    with patch("movie_narrator.presets.list_presets", return_value={"a": "desc a", "b": "desc b"}):
        result = runner.invoke(app, ["preset"])
    assert result.exit_code == 0
    assert "a" in result.output
    assert "b" in result.output


def test_preset_list_empty(tmp_path):
    with patch("movie_narrator.presets.list_presets", return_value={}):
        result = runner.invoke(app, ["preset"])
    assert result.exit_code == 0
    assert "No narration presets" in result.output


def test_preset_show_mode(tmp_path):
    p = SimpleNamespace(name="douyin", desc="desc", param_dict={"duration": 60}, tag_dict={"tag": "x"})
    with patch("movie_narrator.presets.get_preset", return_value=p):
        result = runner.invoke(app, ["preset", "douyin"])
    assert result.exit_code == 0
    assert "Preset: douyin" in result.output
    assert "duration" in result.output


def test_preset_show_not_found(tmp_path):
    with patch("movie_narrator.presets.get_preset", side_effect=KeyError("nope")):
        result = runner.invoke(app, ["preset", "nope"])
    assert result.exit_code == 1
    assert "nope" in result.output


# ── submit / status / tasks / cancel / wait / cleanup ──────


def _fake_queue(**attrs):
    q = MagicMock()
    for k, v in attrs.items():
        setattr(q, k, v)
    return q


def test_submit_no_wait(tmp_path):
    q = _fake_queue(submit=MagicMock(return_value="task123"), shutdown=MagicMock())
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["submit", "--movie", "M"])
    assert result.exit_code == 0
    assert "Task submitted: task123" in result.output
    q.shutdown.assert_called_once()


def test_submit_wait_success(tmp_path):
    q = _fake_queue(submit=MagicMock(return_value="task123"), shutdown=MagicMock())
    q.wait = MagicMock(return_value=TaskResult(video_path="/out/final.mp4"))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["submit", "--movie", "M", "--wait"])
    assert result.exit_code == 0
    assert "Task completed" in result.output


def test_submit_wait_timeout(tmp_path):
    q = _fake_queue(submit=MagicMock(return_value="task123"), shutdown=MagicMock())
    q.wait = MagicMock(return_value=None)
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["submit", "--movie", "M", "--wait"])
    assert result.exit_code == 1
    assert "did not complete" in result.output


def test_submit_wait_failed(tmp_path):
    q = _fake_queue(submit=MagicMock(return_value="task123"), shutdown=MagicMock())
    q.wait = MagicMock(return_value=TaskResult(error="boom"))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["submit", "--movie", "M", "--wait"])
    assert result.exit_code == 1
    assert "Task failed" in result.output


def test_submit_remote(tmp_path):
    q = _fake_queue(submit=MagicMock(return_value="task123"), shutdown=MagicMock())
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["submit", "--movie", "M", "--remote", "http://x:1"])
    assert result.exit_code == 0
    assert "Remote: http://x:1" in result.output


def test_status_not_found(tmp_path):
    q = _fake_queue(get_task=MagicMock(return_value=None))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["status", "task123"])
    assert result.exit_code == 1
    assert "Task not found" in result.output


def test_status_full_output(tmp_path):
    task = make_task()
    q = _fake_queue(get_task=MagicMock(return_value=task))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["status", "task123"])
    assert result.exit_code == 0
    assert "TestMovie" in result.output
    assert "completed" in result.output
    assert "Retries: 1" in result.output
    assert "Progress: 5/16" in result.output
    assert "render_video" in result.output
    assert "resolve_video, script" in result.output
    assert "align_audio" in result.output
    assert "match_clips" in result.output
    assert "final.mp4" in result.output
    assert "narration.mp3" in result.output
    assert "subtitle.srt" in result.output


def test_status_failed_result(tmp_path):
    task = make_task(result=TaskResult(error="boom", error_type="RuntimeError"))
    q = _fake_queue(get_task=MagicMock(return_value=task))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["status", "task123"])
    assert result.exit_code == 0
    assert "Error: boom" in result.output
    assert "RuntimeError" in result.output


def test_tasks_invalid_status(tmp_path):
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=_fake_queue()),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=_fake_queue()),
    ):
        result = runner.invoke(app, ["tasks", "--status", "bogus"])
    assert result.exit_code == 1
    assert "Invalid status" in result.output


def test_tasks_empty(tmp_path):
    q = _fake_queue(list_tasks=MagicMock(return_value=[]))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["tasks"])
    assert result.exit_code == 0
    assert "No tasks found." in result.output


def test_tasks_with_rows(tmp_path):
    task = make_task()
    task.id = "abcdefghijkl"
    task.created_at = "2026-01-01T00:00:00+00:00"
    q = _fake_queue(list_tasks=MagicMock(return_value=[task]))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["tasks"])
    assert result.exit_code == 0
    assert "TestMovie" in result.output
    assert "31%" in result.output


def test_cancel_success(tmp_path):
    q = _fake_queue(cancel=MagicMock(return_value=True))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["cancel", "task123"])
    assert result.exit_code == 0
    assert "cancellation requested" in result.output


def test_cancel_failure(tmp_path):
    q = _fake_queue(cancel=MagicMock(return_value=False))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["cancel", "task123"])
    assert result.exit_code == 1
    assert "could not cancel" in result.output


def test_wait_success(tmp_path):
    q = _fake_queue(wait=MagicMock(return_value=TaskResult(video_path="/out/final.mp4")))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["wait", "task123"])
    assert result.exit_code == 0
    assert "completed" in result.output


def test_wait_timeout(tmp_path):
    q = _fake_queue(wait=MagicMock(return_value=None))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["wait", "task123"])
    assert result.exit_code == 1
    assert "did not complete" in result.output


def test_wait_failed(tmp_path):
    q = _fake_queue(wait=MagicMock(return_value=TaskResult(error="boom")))
    with (
        patch("movie_narrator.cloud.LocalTaskQueue", return_value=q),
        patch("movie_narrator.cloud.RemoteTaskQueue", return_value=q),
    ):
        result = runner.invoke(app, ["wait", "task123"])
    assert result.exit_code == 1
    assert "failed" in result.output


def test_cleanup_default(tmp_path):
    q = _fake_queue(cleanup_terminal=MagicMock(return_value=3), cleanup_all=MagicMock())
    with patch("movie_narrator.cloud.LocalTaskQueue", return_value=q):
        result = runner.invoke(app, ["cleanup"])
    assert result.exit_code == 0
    assert "Cleaned up 3 task(s)." in result.output
    q.cleanup_all.assert_not_called()


def test_cleanup_all(tmp_path):
    q = _fake_queue(cleanup_terminal=MagicMock(), cleanup_all=MagicMock(return_value=5))
    with patch("movie_narrator.cloud.LocalTaskQueue", return_value=q):
        result = runner.invoke(app, ["cleanup", "--all"])
    assert result.exit_code == 0
    assert "Cleaned up 5 task(s)." in result.output
    q.cleanup_terminal.assert_not_called()


# ── serve command ──────────────────────────────────────────


def test_serve_invalid_log_format(tmp_path):
    result = runner.invoke(app, ["serve", "--log-format", "xml"])
    assert result.exit_code == 2
    assert "--log-format must be" in result.output


def test_serve_public_no_key_warns(tmp_path):
    with (
        patch("movie_narrator.cloud.run_daemon"),
        patch("movie_narrator.utils.logging_config.configure_logging"),
        patch("movie_narrator.config.get_server_ops", return_value=ServerOpsSettings(api_key=None)),
    ):
        result = runner.invoke(app, ["serve", "--public"])
    assert result.exit_code == 0
    assert "without an API key" in result.output


def test_serve_public_insecure_warns(tmp_path):
    with (
        patch("movie_narrator.cloud.run_daemon"),
        patch("movie_narrator.utils.logging_config.configure_logging"),
        patch("movie_narrator.config.get_server_ops", return_value=ServerOpsSettings(api_key=None)),
    ):
        result = runner.invoke(app, ["serve", "--public", "--insecure"])
    assert result.exit_code == 0
    assert "without authentication" in result.output


def test_serve_localhost_runs(tmp_path):
    with (
        patch("movie_narrator.cloud.run_daemon") as rd,
        patch("movie_narrator.utils.logging_config.configure_logging"),
        patch("movie_narrator.config.get_server_ops", return_value=ServerOpsSettings(api_key="k")),
    ):
        result = runner.invoke(app, ["serve", "--port", "9000"])
    assert result.exit_code == 0
    rd.assert_called_once()
    assert rd.call_args.kwargs["port"] == 9000


# ── download command ───────────────────────────────────────


def test_download_with_filename(tmp_path):
    with (
        patch("movie_narrator.cloud.download_artifact", return_value=Path("/out/final.mp4")),
        patch("movie_narrator.cloud.download_all_artifacts"),
    ):
        result = runner.invoke(
            app, ["download", "task123", "--remote", "http://x:1", "--filename", "final.mp4"]
        )
    assert result.exit_code == 0
    assert "Downloaded:" in result.output


def test_download_all(tmp_path):
    with (
        patch("movie_narrator.cloud.download_artifact"),
        patch("movie_narrator.cloud.download_all_artifacts", return_value=[Path("/a/1.mp4"), Path("/a/2.srt")]),
    ):
        result = runner.invoke(app, ["download", "task123", "--remote", "http://x:1"])
    assert result.exit_code == 0
    assert "Downloaded 2 file(s):" in result.output


def test_download_all_empty(tmp_path):
    with (
        patch("movie_narrator.cloud.download_artifact"),
        patch("movie_narrator.cloud.download_all_artifacts", return_value=[]),
    ):
        result = runner.invoke(app, ["download", "task123", "--remote", "http://x:1"])
    assert result.exit_code == 1
    assert "No artifacts found" in result.output


# ── api-spec command ───────────────────────────────────────


def test_api_spec_stdout(tmp_path):
    with patch("movie_narrator.cloud.openapi.build_openapi_spec", return_value={"openapi": "3.1.0"}):
        result = runner.invoke(app, ["api-spec"])
    assert result.exit_code == 0
    assert "3.1.0" in result.output


def test_api_spec_to_file(tmp_path):
    out = tmp_path / "openapi.json"
    with patch("movie_narrator.cloud.openapi.build_openapi_spec", return_value={"openapi": "3.1.0"}):
        result = runner.invoke(app, ["api-spec", "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert "OpenAPI spec written" in result.output


# ── artifacts commands ─────────────────────────────────────


def test_artifacts_list_with_items(tmp_path):
    store = MagicMock()
    store.list.return_value = iter([
        SimpleNamespace(key="abc/final.mp4", size=1024),
        SimpleNamespace(key="abc/narration.mp3", size=2048),
    ])
    with (
        patch("movie_narrator.cloud.artifact_store.get_artifact_store", return_value=store),
        patch("movie_narrator.cloud.lifecycle.format_bytes", side_effect=lambda n: f"{n}B"),
    ):
        result = runner.invoke(app, ["artifacts", "list", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "abc/final.mp4" in result.output
    assert "2 artifact(s)" in result.output


def test_artifacts_list_empty(tmp_path):
    store = MagicMock()
    store.list.return_value = iter([])
    with (
        patch("movie_narrator.cloud.artifact_store.get_artifact_store", return_value=store),
        patch("movie_narrator.cloud.lifecycle.format_bytes", side_effect=lambda n: f"{n}B"),
    ):
        result = runner.invoke(app, ["artifacts", "list", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "No artifacts found." in result.output


def test_artifacts_open_store_error(tmp_path):
    from movie_narrator.cloud.artifact_store import ArtifactStoreError

    def boom(*a, **k):
        raise ArtifactStoreError("bad backend")

    with patch("movie_narrator.cloud.artifact_store.get_artifact_store", side_effect=boom):
        result = runner.invoke(app, ["artifacts", "list", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Artifact store unavailable" in result.output


def test_artifacts_cleanup_disabled(tmp_path):
    policy = MagicMock()
    policy.enabled = False
    store = MagicMock()
    with (
        patch("movie_narrator.cloud.lifecycle.ArtifactLifecyclePolicy.from_env", return_value=policy),
        patch("movie_narrator.cloud.lifecycle.describe_policy", return_value=["line"]),
        patch("movie_narrator.cloud.artifact_store.get_artifact_store", return_value=store),
    ):
        result = runner.invoke(app, ["artifacts", "cleanup", "--root", str(tmp_path)])
    assert result.exit_code == 0
    assert "No retention rule active" in result.output


def test_artifacts_cleanup_with_report(tmp_path):
    policy = MagicMock()
    policy.enabled = True
    report = SimpleNamespace(
        deleted=["a", "b"],
        skipped=["c"],
        errors=[],
        dry_run=True,
        summary=lambda: "SUMMARY",
    )
    store = MagicMock()
    with (
        patch("movie_narrator.cloud.lifecycle.ArtifactLifecyclePolicy.from_env", return_value=policy),
        patch("movie_narrator.cloud.lifecycle.describe_policy", return_value=["line"]),
        patch("movie_narrator.cloud.artifact_store.get_artifact_store", return_value=store),
        patch("movie_narrator.cloud.lifecycle.cleanup_artifacts", return_value=report),
    ):
        result = runner.invoke(app, ["artifacts", "cleanup", "--root", str(tmp_path), "--dry-run"])
    assert result.exit_code == 0
    assert "Would delete:" in result.output
    assert "SUMMARY" in result.output


def test_artifacts_cleanup_with_errors(tmp_path):
    policy = MagicMock()
    policy.enabled = True
    report = SimpleNamespace(
        deleted=[],
        skipped=[],
        errors=[("a", "boom")],
        dry_run=False,
        summary=lambda: "SUMMARY",
    )
    store = MagicMock()
    with (
        patch("movie_narrator.cloud.lifecycle.ArtifactLifecyclePolicy.from_env", return_value=policy),
        patch("movie_narrator.cloud.lifecycle.describe_policy", return_value=["line"]),
        patch("movie_narrator.cloud.artifact_store.get_artifact_store", return_value=store),
        patch("movie_narrator.cloud.lifecycle.cleanup_artifacts", return_value=report),
    ):
        result = runner.invoke(app, ["artifacts", "cleanup", "--root", str(tmp_path)])
    assert result.exit_code == 1
    assert "Errors:" in result.output


# ── remaining branch coverage ──────────────────────────────


def test_create_discover_cwd_job_yaml(tmp_path, monkeypatch):
    """create picks up cwd/job.yaml when --config and --movie don't trigger it."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "job.yaml").write_text("movie: FromYAML\nstyle: YAMLStyle\n", encoding="utf-8")
    ctx = fake_ctx(tmp_path)
    bc, rp = patch_create_pipeline(tmp_path, ctx)
    with (
        patch("movie_narrator.cli.build_context", bc),
        patch("movie_narrator.cli.run_pipeline", rp),
        patch("movie_narrator.workflow.merge_job", return_value=resolved_job()),
        patch("movie_narrator.config.get_settings", return_value=MagicMock()),
    ):
        result = runner.invoke(app, ["create", "--movie", "M"])
    assert result.exit_code == 0
    assert str(ctx.video_path) in result.output


def test_create_discover_example_yaml_fallback(tmp_path, monkeypatch):
    """create falls back to the packaged example YAML when no job.yaml in cwd."""
    monkeypatch.chdir(tmp_path)
    example = tmp_path / "job.example.yaml"
    example.write_text("movie: ExampleMovie\nstyle: default\n", encoding="utf-8")
    monkeypatch.setattr(cli, "_EXAMPLE_YAML", example)
    ctx = fake_ctx(tmp_path)
    bc, rp = patch_create_pipeline(tmp_path, ctx)
    with (
        patch("movie_narrator.cli.build_context", bc),
        patch("movie_narrator.cli.run_pipeline", rp),
        patch("movie_narrator.workflow.merge_job", return_value=resolved_job()),
        patch("movie_narrator.config.get_settings", return_value=MagicMock()),
    ):
        result = runner.invoke(app, ["create", "--movie", "M"])
    assert result.exit_code == 0
    assert str(ctx.video_path) in result.output


def test_imitate_preflight_error(tmp_path, monkeypatch):
    from movie_narrator.pipeline.preflight import PreflightError

    monkeypatch.chdir(tmp_path)
    ref = tmp_path / "ref.mp4"
    ref.write_bytes(b"00")
    ctx = fake_ctx(tmp_path)
    bc = MagicMock(return_value=ctx)
    rp = MagicMock(side_effect=PreflightError("no ffmpeg"))
    with (
        patch("movie_narrator.imitate.analyze_reference", return_value={"density": 2.0}),
        patch("movie_narrator.imitate.metrics_to_params", return_value={"p": 1}),
        patch("movie_narrator.imitate.metrics_to_preset_name", return_value="douyin-fast"),
        patch("movie_narrator.imitate.format_analysis_report", return_value="REPORT"),
        patch("movie_narrator.pipeline.runner.build_context", bc),
        patch("movie_narrator.pipeline.runner.run_pipeline", rp),
    ):
        result = runner.invoke(
            app,
            ["imitate", "--reference", str(ref), "--movie", "M", "--output-dir", str(tmp_path / "out")],
        )
    assert result.exit_code == 1
    assert "no ffmpeg" in result.output


def test_imitate_script_degraded_and_match(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ref = tmp_path / "ref.mp4"
    ref.write_bytes(b"00")
    ctx = fake_ctx(tmp_path)
    ctx.metadata["script_degraded"] = True
    ctx.metadata["match_summary"] = {"segments": 3, "source_counts": {"embedding": 3}}
    bc = MagicMock(return_value=ctx)
    rp = MagicMock(return_value=ctx)
    with (
        patch("movie_narrator.imitate.analyze_reference", return_value={"density": 2.0}),
        patch("movie_narrator.imitate.metrics_to_params", return_value={"p": 1}),
        patch("movie_narrator.imitate.metrics_to_preset_name", return_value="douyin-fast"),
        patch("movie_narrator.imitate.format_analysis_report", return_value="REPORT"),
        patch("movie_narrator.pipeline.runner.build_context", bc),
        patch("movie_narrator.pipeline.runner.run_pipeline", rp),
    ):
        result = runner.invoke(
            app,
            ["imitate", "--reference", str(ref), "--movie", "M", "--output-dir", str(tmp_path / "out")],
        )
    assert result.exit_code == 0
    assert "旁白为占位" in result.output
    assert "match: 3 segs" in result.output


def test_resume_preflight_error(tmp_path, monkeypatch):
    from movie_narrator.pipeline.preflight import PreflightError

    monkeypatch.chdir(tmp_path)
    state = tmp_path / "s.json"
    state.write_text("{}", encoding="utf-8")
    ctx = fake_ctx(tmp_path)
    rp = MagicMock(side_effect=PreflightError("no ffmpeg"))
    with (
        patch("movie_narrator.pipeline.runner._load_pipeline_state", return_value=(ctx, "script")),
        patch("movie_narrator.pipeline.runner._next_step_after", return_value="tts"),
        patch("movie_narrator.utils.console.build_console", return_value=MagicMock()),
        patch("movie_narrator.pipeline.runner.run_pipeline", rp),
    ):
        result = runner.invoke(app, ["resume", "--state", str(state)])
    assert result.exit_code == 1
    assert "no ffmpeg" in result.output


def test_resume_script_degraded(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    state = tmp_path / "s.json"
    state.write_text("{}", encoding="utf-8")
    ctx = fake_ctx(tmp_path)
    ctx.metadata["script_degraded"] = True
    rp = MagicMock(return_value=ctx)
    with (
        patch("movie_narrator.pipeline.runner._load_pipeline_state", return_value=(ctx, "script")),
        patch("movie_narrator.pipeline.runner._next_step_after", return_value="tts"),
        patch("movie_narrator.utils.console.build_console", return_value=MagicMock()),
        patch("movie_narrator.pipeline.runner.run_pipeline", rp),
    ):
        result = runner.invoke(app, ["resume", "--state", str(state)])
    assert result.exit_code == 0
    assert "旁白为占位" in result.output


def test_api_spec_nested_dir(tmp_path):
    out = tmp_path / "nested" / "openapi.json"
    with patch("movie_narrator.cloud.openapi.build_openapi_spec", return_value={"openapi": "3.1.0"}):
        result = runner.invoke(app, ["api-spec", "--output", str(out)])
    assert result.exit_code == 0
    assert out.exists()
    assert "OpenAPI spec written" in result.output


def test_artifacts_cleanup_overrides(tmp_path):
    policy = MagicMock()
    policy.enabled = True
    report = SimpleNamespace(
        deleted=[],
        skipped=[],
        errors=[],
        dry_run=False,
        summary=lambda: "SUMMARY",
    )
    store = MagicMock()
    with (
        patch("movie_narrator.cloud.lifecycle.ArtifactLifecyclePolicy.from_env", return_value=policy),
        patch("movie_narrator.cloud.lifecycle.describe_policy", return_value=["line"]),
        patch("movie_narrator.cloud.artifact_store.get_artifact_store", return_value=store),
        patch("movie_narrator.cloud.lifecycle.cleanup_artifacts", return_value=report),
    ):
        result = runner.invoke(
            app,
            ["artifacts", "cleanup", "--root", str(tmp_path), "--ttl", "100", "--max-bytes", "200", "--keep-last", "3"],
        )
    assert result.exit_code == 0
    assert policy.ttl_seconds == 100
    assert policy.max_total_bytes == 200
    assert policy.keep_last_n == 3