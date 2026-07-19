import json
from pathlib import Path

from typer.testing import CliRunner

from movie_narrator.cli import app

runner = CliRunner()


def test_resolve_json_shape(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["resolve", "--movie", "Inception", "--library-dir", str(tmp_path), "--json"])
    parsed = json.loads(result.output.strip())
    assert "matched" in parsed
    assert "path" in parsed
    assert parsed["matched"] is False


def test_resolve_plain_no_match(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["resolve", "--movie", "Nope", "--library-dir", str(tmp_path)])
    assert result.exit_code == 1
    assert "No match found" in result.output


def test_resolve_found_in_library(tmp_path):
    video = tmp_path / "Inception.mp4"
    video.write_bytes(b"00")
    result = runner.invoke(app, ["resolve", "--movie", "Inception", "--library-dir", str(tmp_path)])
    assert result.exit_code == 0
    assert "Inception.mp4" in result.output


def test_research_writes_envelope(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = runner.invoke(app, ["research", "--movie", "Inception"])
    research_path = tmp_path / "output" / "Inception" / "research.json"
    assert research_path.exists()
    data = json.loads(research_path.read_text(encoding="utf-8"))
    assert "status" in data
    assert "error" in data


# ── --output-dir / -o option tests ──


def test_resolve_output_dir_option_writes_to_user_dir(tmp_path, monkeypatch):
    """--output-dir writes to user-specified directory instead of output/<movie>."""
    monkeypatch.chdir(tmp_path)
    # Place a fake video so resolve succeeds (exit_code 0)
    (tmp_path / "Inception.mp4").write_bytes(b"00")
    custom_dir = tmp_path / "my-custom-output"
    result = runner.invoke(
        app,
        ["resolve", "--movie", "Inception", "--library-dir", str(tmp_path),
         "--output-dir", str(custom_dir)],
    )
    assert result.exit_code == 0
    # Custom directory should exist (created by the command)
    assert custom_dir.exists()
    assert custom_dir.is_dir()


def test_resolve_output_dir_short_option(tmp_path, monkeypatch):
    """-o short option works the same as --output-dir."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Inception.mp4").write_bytes(b"00")
    custom_dir = tmp_path / "short-opt-out"
    result = runner.invoke(
        app,
        ["resolve", "--movie", "Inception", "--library-dir", str(tmp_path),
         "-o", str(custom_dir)],
    )
    assert result.exit_code == 0
    assert custom_dir.exists()


def test_resolve_output_dir_default_when_not_specified(tmp_path, monkeypatch):
    """Without --output-dir, falls back to output/<sanitized-movie> (backward compat)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "Inception.mp4").write_bytes(b"00")
    result = runner.invoke(
        app,
        ["resolve", "--movie", "Inception", "--library-dir", str(tmp_path)],
    )
    assert result.exit_code == 0
    # Default path: output/Inception/ (sanitized)
    default_dir = tmp_path / "output" / "Inception"
    assert default_dir.exists()


def test_research_output_dir_option_writes_to_user_dir(tmp_path, monkeypatch):
    """research --output-dir creates the user-specified directory.

    Note: we don't assert exit_code==0 because research requires an LLM API
    key which is not available in CI. We only verify that --output-dir is
    parsed and the custom directory is created (the CLI-level contract).
    The research.json write itself is tested by test_research_writes_envelope.
    """
    monkeypatch.chdir(tmp_path)
    custom_dir = tmp_path / "research-custom"
    result = runner.invoke(
        app,
        ["research", "--movie", "Inception", "--output-dir", str(custom_dir)],
    )
    # Custom directory should be created regardless of research success/failure
    # (mkdir happens before research_plot is called)
    assert custom_dir.exists()
    assert custom_dir.is_dir()
    # Default path should NOT exist
    assert not (tmp_path / "output" / "Inception").exists()
