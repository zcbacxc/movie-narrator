# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the v1.3.2 encoder benchmark script (Feature 12).

Import-safe by design: importing the module never runs ffmpeg. Report
structure is exercised with monkeypatched command runners and fake GPU
detection; the ffmpeg-absent path degrades to a graceful report.
"""

import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCH_PATH = REPO_ROOT / "benchmarks" / "encoder_benchmark.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("encoder_benchmark_v132", BENCH_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def bench():
    return _load_module()


# ── 1. Import safety / command builders ──────────────────


class TestImportSafety:
    def test_import_does_not_run_ffmpeg(self, bench):
        """Importing the module must not shell out or crash without ffmpeg."""
        assert bench.SCHEMA_VERSION == 1
        assert callable(bench.run_benchmark)

    def test_generate_command_structure(self, bench, tmp_path):
        cmd = bench.build_generate_command("ffmpeg", tmp_path / "clip.mp4")
        joined = " ".join(cmd)
        assert "lavfi" in joined
        assert "testsrc2=size=1920x1080" in joined
        assert "sine=" in joined
        assert str(tmp_path / "clip.mp4") == cmd[-1]

    def test_encode_command_structure(self, bench, tmp_path):
        cmd = bench.build_encode_command(
            "ffmpeg", tmp_path / "clip.mp4", tmp_path / "out.mp4", "libx264", ["-crf", "20"]
        )
        assert cmd[cmd.index("-c:v") + 1] == "libx264"
        assert "-crf" in cmd and "20" in cmd
        assert str(tmp_path / "out.mp4") == cmd[-1]

    def test_no_moviepy_usage(self, bench):
        """The benchmark's own code must not import or use moviepy.

        (Importing ``movie_narrator.utils`` transitively loads the package
        SDK surface which may pull moviepy — that is the package's doing,
        not the benchmark's. The benchmark's own module must reference
        moviepy nowhere and must drive ffmpeg via subprocess only.)
        """
        source = BENCH_PATH.read_text(encoding="utf-8")
        assert "import moviepy" not in source
        assert "from moviepy" not in source
        assert not any("moviepy" in name.lower() for name in vars(bench))


# ── 2. Parsers ────────────────────────────────────────────


class TestParsers:
    def test_parse_encode_fps_last_value(self, bench):
        stderr = "frame=   30 fps= 25 q=28.0 size=256kB time=00:00:01.20\n" \
                 "frame=  150 fps= 58 q=-1.0 Lsize=1024kB"
        assert bench.parse_encode_fps(stderr) == 58.0

    def test_parse_encode_fps_absent(self, bench):
        assert bench.parse_encode_fps("no progress here") is None

    def test_tail_keeps_last_lines(self, bench):
        text = "\n".join(f"line{i}" for i in range(10))
        tail = bench._tail(text, lines=3)
        assert tail == "line7\nline8\nline9"


# ── 3. Report structure with fakes ───────────────────────


class _FakeProc:
    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


class TestRunBenchmark:
    def test_report_structure_with_fake_runner(self, bench, tmp_path, monkeypatch):
        """Monkeypatched encode runner + fake GPU detection → full report."""
        calls: list[list[str]] = []

        def fake_run(cmd, timeout=None):
            calls.append(cmd)
            # The clip-generation call produces the clip file; encode
            # calls produce their output file so size measurement works.
            out = Path(cmd[-1])
            out.write_bytes(b"x" * 1024)
            return _FakeProc(
                returncode=0,
                stderr="frame=  150 fps= 58.00 q=-1.0 Lsize=1024kB time=00:00:05.00",
            )

        monkeypatch.setattr(bench, "run_command", fake_run)
        monkeypatch.setattr(bench, "_resolve_ffmpeg_or_none", lambda: "/fake/ffmpeg")
        monkeypatch.setattr(
            bench, "detect_gpu_encoder", lambda: "h264_nvenc"
        )
        monkeypatch.setattr(
            bench,
            "get_encoder_info",
            lambda requested=None: {
                "requested": "auto",
                "detected": "h264_nvenc",
                "active": "h264_nvenc",
                "gpu_available": True,
                "fallback_reason": None,
            },
        )

        report = bench.run_benchmark(work_dir=tmp_path / "work")

        assert report["schema_version"] == 1
        env = report["environment"]
        assert env["ffmpeg_bin"] == "/fake/ffmpeg"
        assert "platform" in env and "python" in env
        assert env["gpu"]["detected"] == "h264_nvenc"
        assert env["gpu_benchmarked"] == "h264_nvenc"
        assert report["clip"]["width"] == 1920 and report["clip"]["duration_s"] == 5

        # libx264 baseline + the detected GPU encoder.
        labels = [r["label"] for r in report["results"]]
        assert labels == ["libx264", "nvenc"]
        for r in report["results"]:
            assert r["status"] == "ok"
            assert r["wall_time_s"] is not None
            assert r["output_bytes"] == 1024
            assert r["encode_fps"] == 58.0
        # GPU encode uses the renderer's own recommended params.
        nvenc = report["results"][1]
        assert nvenc["codec"] == "h264_nvenc"
        assert "-cq" in nvenc["params"]
        # JSON-serializable.
        json.dumps(report)

    def test_report_without_gpu_detection(self, bench, tmp_path, monkeypatch):
        """No GPU detected (or CI) → libx264-only run."""

        def fake_run(cmd, timeout=None):
            Path(cmd[-1]).write_bytes(b"clip")
            return _FakeProc(0, "fps= 40")

        monkeypatch.setattr(bench, "run_command", fake_run)
        monkeypatch.setattr(bench, "_resolve_ffmpeg_or_none", lambda: "/fake/ffmpeg")
        monkeypatch.setattr(bench, "detect_gpu_encoder", lambda: None)

        def _fake_info(requested=None):
            return {
                "requested": "auto",
                "detected": None,
                "active": "libx264",
                "gpu_available": False,
                "fallback_reason": "ci_skipped",
            }

        monkeypatch.setattr(bench, "get_encoder_info", _fake_info)
        report = bench.run_benchmark(work_dir=tmp_path)
        assert [r["label"] for r in report["results"]] == ["libx264"]
        assert report["environment"]["gpu"]["fallback_reason"] == "ci_skipped"

    def test_encoder_failure_recorded_not_raised(self, bench, tmp_path, monkeypatch):
        """A GPU encode that fails (e.g. no VAAPI device) records a failed row."""
        state = {"clip": False}

        def fake_run(cmd, timeout=None):
            if "-f" in cmd and "lavfi" in cmd and not state["clip"]:
                state["clip"] = True
                Path(cmd[-1]).write_bytes(b"clip")
                return _FakeProc(0, "fps= 60")
            # Second encoder fails; first (libx264) succeeds.
            if cmd[cmd.index("-c:v") + 1] == "libx264":
                Path(cmd[-1]).write_bytes(b"out")
                return _FakeProc(0, "fps= 50")
            return _FakeProc(2, "Cannot load libva")

        monkeypatch.setattr(bench, "run_command", fake_run)
        monkeypatch.setattr(bench, "_resolve_ffmpeg_or_none", lambda: "/fake/ffmpeg")
        monkeypatch.setattr(bench, "detect_gpu_encoder", lambda: "h264_vaapi")
        monkeypatch.setattr(bench, "get_encoder_info", lambda requested=None: {})
        report = bench.run_benchmark(work_dir=tmp_path)
        assert report["status"] == "ok"
        assert report["results"][0]["status"] == "ok"
        assert report["results"][1]["status"] == "failed"
        assert "libva" in report["results"][1]["stderr_tail"]

    def test_ffmpeg_absent_graceful_skip(self, bench, monkeypatch):
        """No usable ffmpeg → report with status ffmpeg_unavailable, no raise."""
        monkeypatch.setattr(bench, "_resolve_ffmpeg_or_none", lambda: None)
        report = bench.run_benchmark()
        assert report["status"] == "ffmpeg_unavailable"
        assert report["results"] == []
        assert report["environment"]["ffmpeg_bin"] is None

    def test_clip_generation_failure_graceful(self, bench, tmp_path, monkeypatch):
        monkeypatch.setattr(bench, "_resolve_ffmpeg_or_none", lambda: "/fake/ffmpeg")
        monkeypatch.setattr(
            bench, "run_command", lambda cmd, timeout=None: _FakeProc(1, "lavfi error")
        )
        monkeypatch.setattr(bench, "detect_gpu_encoder", lambda: None)
        monkeypatch.setattr(bench, "get_encoder_info", lambda requested=None: {})
        report = bench.run_benchmark(work_dir=tmp_path)
        assert report["status"] == "clip_generation_failed"
        assert "lavfi error" in report.get("stderr_tail", "")


# ── 4. CLI ────────────────────────────────────────────────


class TestCLI:
    def test_main_writes_json_report(self, bench, tmp_path, monkeypatch, capsys):
        out = tmp_path / "reports" / "r.json"
        monkeypatch.setattr(
            bench,
            "run_benchmark",
            lambda: {
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
                        "wall_time_s": 1.2,
                        "output_bytes": 2048,
                        "encode_fps": 42.0,
                        "stderr_tail": "",
                    }
                ],
            },
        )
        rc = bench.main(["--out", str(out)])
        assert rc == 0
        assert out.is_file()
        payload = json.loads(out.read_text(encoding="utf-8"))
        assert payload["schema_version"] == 1
        assert payload["results"][0]["label"] == "libx264"
        captured = capsys.readouterr().out
        assert "libx264" in captured

    def test_real_subprocess_smoke_optional(self, bench, tmp_path):
        """Real ffmpeg (if importable) must produce a valid clip command —
        kept off the regular path: only checks the command builds."""
        cmd = bench.build_generate_command("ffmpeg", tmp_path / "c.mp4")
        assert cmd[0] == "ffmpeg"
        assert isinstance(bench.run_command, type(lambda *a, **k: None))
