# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for v1.4.2 Feature 2 — CLI ergonomics.

- ``mn benchmark``: thin Typer wrapper over the v1.3.2 encoder benchmark
  script (``benchmarks/encoder_benchmark.py``), loaded by file path via
  ``importlib.util.spec_from_file_location`` (the repo ships ``benchmarks/``
  as a plain script dir without ``__init__.py``). Unit tests monkeypatch
  the loaded module — no ffmpeg involved.
- ``mn rerun --dry-run``: computes the invalidation plan via the v1.3.0
  ``prepare_rerun`` helper, prints it, and exits 0 without executing the
  pipeline. Error paths (missing state, unknown step) match the
  non-dry-run behavior exactly.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

import movie_narrator.cli as cli_mod
import movie_narrator.pipeline.runner as runner_mod
from movie_narrator.cli import app, _load_encoder_benchmark
from movie_narrator.models import Context, Services
from movie_narrator.pipeline.runner import _save_pipeline_state

runner = CliRunner()


# ── mn benchmark ──────────────────────────────────────────


class TestBenchmarkCommand:
    def test_loads_benchmark_module_by_path(self):
        """The loader imports the script module and caches it."""
        bench = _load_encoder_benchmark()
        assert callable(bench.run_benchmark)
        assert callable(bench.format_table)
        assert callable(bench.main)
        # Second call returns the same cached module object.
        assert _load_encoder_benchmark() is bench
        assert sys.modules.get(cli_mod._BENCHMARK_MOD_NAME) is bench

    def test_option_passthrough(self, monkeypatch):
        """--duration/--out/--encoders are forwarded to the script main."""
        bench = _load_encoder_benchmark()
        captured: list[list[str]] = []

        def fake_main(argv):
            captured.append(list(argv))
            return 0

        monkeypatch.setattr(bench, "main", fake_main)
        result = runner.invoke(
            app,
            [
                "benchmark",
                "--duration",
                "8",
                "--out",
                "r.json",
                "--encoders",
                "libx264,nvenc",
            ],
        )
        assert result.exit_code == 0, result.output
        assert captured == [
            ["--duration", "8", "--out", "r.json", "--encoders", "libx264,nvenc"]
        ]

    def test_defaults_duration_and_autodetect(self, monkeypatch):
        """No options → duration 5, no --out, no --encoders (auto-detect)."""
        bench = _load_encoder_benchmark()
        captured: list[list[str]] = []

        def fake_main(argv):
            captured.append(list(argv))
            return 0

        monkeypatch.setattr(bench, "main", fake_main)
        result = runner.invoke(app, ["benchmark"])
        assert result.exit_code == 0, result.output
        assert captured == [["--duration", "5"]]

    def test_encoders_filter_whitespace_normalized(self, monkeypatch):
        """Spaces/empties in the comma list are stripped; empty → dropped."""
        bench = _load_encoder_benchmark()
        captured: list[list[str]] = []
        monkeypatch.setattr(
            bench, "main", lambda argv: captured.append(list(argv)) or 0
        )
        result = runner.invoke(app, ["benchmark", "--encoders", " libx264 , , nvenc "])
        assert result.exit_code == 0, result.output
        assert captured == [["--duration", "5", "--encoders", "libx264,nvenc"]]

    def test_script_exit_code_propagates(self, monkeypatch):
        bench = _load_encoder_benchmark()
        monkeypatch.setattr(bench, "main", lambda argv: 3)
        result = runner.invoke(app, ["benchmark"])
        assert result.exit_code == 3

    def test_report_written_via_script_main(self, tmp_path, monkeypatch):
        """End-to-end through the real script main with a fake benchmark —
        report path handling and table output match the script CLI."""
        bench = _load_encoder_benchmark()
        out = tmp_path / "reports" / "gpu.json"

        def fake_run_benchmark(*args, **kwargs):
            return {
                "schema_version": 1,
                "environment": {"ffmpeg_bin": "/fake/ffmpeg", "gpu": {}},
                "status": "ok",
                "results": [
                    {
                        "label": "libx264",
                        "codec": "libx264",
                        "params": ["-crf", "20", "-preset", "medium"],
                        "command": [],
                        "status": "ok",
                        "wall_time_s": 1.0,
                        "output_bytes": 2048,
                        "encode_fps": 42.0,
                        "stderr_tail": "",
                    }
                ],
            }

        monkeypatch.setattr(bench, "run_benchmark", fake_run_benchmark)
        result = runner.invoke(app, ["benchmark", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert out.is_file()
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["results"][0]["label"] == "libx264"
        assert "libx264" in result.output
        assert "JSON report written to" in result.output

    def test_missing_script_fails_with_clear_error(self, tmp_path, monkeypatch):
        """No benchmarks/ next to the package (wheel install) → exit 1."""
        monkeypatch.delitem(sys.modules, cli_mod._BENCHMARK_MOD_NAME, raising=False)
        monkeypatch.setattr(
            cli_mod, "_benchmark_script_path", lambda: tmp_path / "nope.py"
        )
        result = runner.invoke(app, ["benchmark"])
        assert result.exit_code == 1
        assert "source checkout" in result.output


# ── mn rerun --dry-run ────────────────────────────────────


def _make_ctx(tmp_path: Path) -> Context:
    return Context(
        movie_name="test-movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )


class TestRerunDryRun:
    def test_dry_run_prints_plan_and_never_runs(self, tmp_path, monkeypatch):
        """Plan printed (from/completed/reusable/invalidated), pipeline untouched."""
        ctx = _make_ctx(tmp_path)
        state_path = _save_pipeline_state(ctx, "generate_voice")

        def boom(*a, **k):  # pragma: no cover - must never be called
            raise AssertionError("run_pipeline must not be called in dry-run")

        monkeypatch.setattr(runner_mod, "run_pipeline", boom)
        result = runner.invoke(
            app, ["rerun", str(state_path), "--from", "match_clips", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "dry run" in result.output
        assert "from step:        match_clips" in result.output
        assert "state completed:  generate_voice" in result.output
        # Reusable upstream = every step strictly before match_clips.
        assert (
            "reusable upstream (8): resolve_video, prepare_assets, research_plot, "
            "generate_script, export_script_md, generate_voice, align_audio, detect_scenes"
            in result.output
        )
        # Invalidated = from_step and everything after it, in order.
        assert "invalidated (" in result.output
        assert "match_clips, mix_bgm" in result.output
        assert "export_clips" in result.output
        assert "pipeline not executed" in result.output

    def test_dry_run_from_first_step_has_no_reusable(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        state_path = _save_pipeline_state(ctx, "resolve_video")

        def boom(*a, **k):  # pragma: no cover
            raise AssertionError("run_pipeline must not be called in dry-run")

        monkeypatch.setattr(runner_mod, "run_pipeline", boom)
        result = runner.invoke(
            app, ["rerun", str(state_path), "--from", "resolve_video", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert "reusable upstream (0): (none)" in result.output

    def test_dry_run_missing_state_file_same_error_as_run(self, tmp_path):
        result = runner.invoke(
            app,
            ["rerun", str(tmp_path / "missing.json"), "--from", "render_video", "--dry-run"],
        )
        assert result.exit_code == 1
        assert "State file not found" in result.output

    def test_dry_run_unknown_step_same_error_as_run(self, tmp_path):
        state_path = tmp_path / "pipeline_state.json"
        state_path.write_text("{}", encoding="utf-8")
        result = runner.invoke(
            app, ["rerun", str(state_path), "--from", "no_such_step", "--dry-run"]
        )
        assert result.exit_code != 0
        assert "no_such_step" in result.output
        assert "render_video" in result.output  # a valid step is listed

    def test_dry_run_uses_prepare_rerun_semantics(self, tmp_path, monkeypatch):
        """The plan comes from the v1.3.0 helper: rerun metadata recorded,
        soft statuses reset (no-op here, but the call path is shared)."""
        ctx = _make_ctx(tmp_path)
        state_path = _save_pipeline_state(ctx, "render_video")
        calls: list[tuple] = []
        real_prepare = runner_mod.prepare_rerun

        def spy(c, completed_step, from_step):
            calls.append((completed_step, from_step))
            return real_prepare(c, completed_step, from_step)

        monkeypatch.setattr(runner_mod, "prepare_rerun", spy)
        result = runner.invoke(
            app, ["rerun", str(state_path), "--from", "mix_bgm", "--dry-run"]
        )
        assert result.exit_code == 0, result.output
        assert calls == [("render_video", "mix_bgm")]
        assert "from step:        mix_bgm" in result.output
