from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from movie_narrator.cli import app
from movie_narrator.models import Context

runner = CliRunner()


def _fake_ctx(tmp_path):
    ctx = Context(movie_name="M", output_dir=str(tmp_path))
    ctx.video_path = str(tmp_path / "final.mp4")
    return ctx


def _patch_pipeline(tmp_path):
    """Patch build_context + run_pipeline to return a fake ctx.

    Returns (bc_mock, rp_mock) so tests can inspect call_args.
    """
    ctx = _fake_ctx(tmp_path)
    bc = MagicMock(return_value=ctx)
    rp = MagicMock(return_value=ctx)
    return bc, rp


def test_create_without_config_requires_movie():
    result = runner.invoke(app, ["create"])
    assert result.exit_code != 0


def test_create_with_config_uses_yaml_movie(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    job = tmp_path / "job.yaml"
    job.write_text("movie: FromYAML\nstyle: YAMLStyle\n", encoding="utf-8")

    bc, rp = _patch_pipeline(tmp_path)
    with patch("movie_narrator.cli.build_context", bc), patch("movie_narrator.cli.run_pipeline", rp):
        result = runner.invoke(app, ["create", "--config", str(job)])
    assert result.exit_code == 0, result.output
    kwargs = bc.call_args.kwargs
    assert kwargs["movie"] == "FromYAML"
    assert kwargs["style"] == "YAMLStyle"
    assert str(tmp_path / "final.mp4") in result.output


def test_create_cli_movie_overrides_yaml(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    job = tmp_path / "job.yaml"
    job.write_text("movie: FromYAML\n", encoding="utf-8")

    bc, rp = _patch_pipeline(tmp_path)
    with patch("movie_narrator.cli.build_context", bc), patch("movie_narrator.cli.run_pipeline", rp):
        result = runner.invoke(
            app, ["create", "--config", str(job), "--movie", "FromCLI"]
        )
    assert result.exit_code == 0, result.output
    assert bc.call_args.kwargs["movie"] == "FromCLI"


def test_create_config_missing_movie_errors(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    job = tmp_path / "job.yaml"
    job.write_text("style: only\n", encoding="utf-8")
    bc, rp = _patch_pipeline(tmp_path)
    with patch("movie_narrator.cli.build_context", bc), patch("movie_narrator.cli.run_pipeline", rp):
        result = runner.invoke(app, ["create", "--config", str(job)])
    assert result.exit_code != 0
    assert "movie is required" in result.output.lower() or "movie is required" in str(result.exception).lower()
    bc.assert_not_called()
    rp.assert_not_called()


def test_create_invalid_yaml_exits_before_pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    job = tmp_path / "job.yaml"
    job.write_text("movie: [bad\n", encoding="utf-8")
    bc, rp = _patch_pipeline(tmp_path)
    with patch("movie_narrator.cli.build_context", bc), patch("movie_narrator.cli.run_pipeline", rp):
        result = runner.invoke(app, ["create", "--config", str(job)])
    assert result.exit_code != 0
    bc.assert_not_called()
    rp.assert_not_called()
    assert "invalid YAML" in result.output or "invalid YAML" in str(result.exception)


def test_create_unknown_key_exits_before_pipeline(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    job = tmp_path / "job.yaml"
    job.write_text("movie: X\nnot_a_key: 1\n", encoding="utf-8")
    bc, rp = _patch_pipeline(tmp_path)
    with patch("movie_narrator.cli.build_context", bc), patch("movie_narrator.cli.run_pipeline", rp):
        result = runner.invoke(app, ["create", "--config", str(job)])
    assert result.exit_code != 0
    bc.assert_not_called()
    rp.assert_not_called()


def test_create_passes_workflow_steps_and_params(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    job = tmp_path / "job.yaml"
    job.write_text(
        "\n".join(
            [
                "movie: M",
                "steps:",
                " align: false",
                " export: false",
                "params:",
                " scene_threshold: 31.5",
                "",
            ]
        ),
        encoding="utf-8",
    )
    bc, rp = _patch_pipeline(tmp_path)
    with patch("movie_narrator.cli.build_context", bc), patch("movie_narrator.cli.run_pipeline", rp):
        result = runner.invoke(app, ["create", "--config", str(job)])
    assert result.exit_code == 0, result.output
    kwargs = bc.call_args.kwargs
    assert kwargs["workflow_steps"]["align"] is False
    assert kwargs["workflow_steps"]["export"] is False
    assert kwargs["params"]["scene_threshold"] == 31.5
    assert kwargs["config_path"] == str(job)


def test_create_no_config_backward_compatible_kwargs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    bc, rp = _patch_pipeline(tmp_path)
    with patch("movie_narrator.cli.build_context", bc), patch("movie_narrator.cli.run_pipeline", rp):
        result = runner.invoke(app, ["create", "--movie", "OnlyCLI", "--duration", "12"])
    assert result.exit_code == 0, result.output
    kwargs = bc.call_args.kwargs
    assert kwargs["movie"] == "OnlyCLI"
    assert kwargs["duration"] == 12
    assert not kwargs.get("workflow_steps")
    assert not kwargs.get("params")
