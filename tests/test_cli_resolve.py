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
