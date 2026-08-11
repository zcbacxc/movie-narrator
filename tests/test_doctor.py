# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for ``mn doctor`` — environment pre-flight checks.

Covers the pure diagnostic logic (with mocked ffmpeg / optional-deps / config
lookups) and the CLI wiring (exit code + rendered table). No real ffmpeg or
network is required.
"""

from unittest.mock import patch

from typer.testing import CliRunner

from movie_narrator.cli import app
from movie_narrator.doctor import Diagnostic, DoctorReport, render_report, run_doctor
from movie_narrator.utils.optional_deps import DepStatus

runner = CliRunner()


# ── ffmpeg check ───────────────────────────────────────────


def test_ffmpeg_ok_when_resolved():
    with patch("movie_narrator.doctor.ffmpeg_bin", return_value="/usr/bin/ffmpeg"):
        report = run_doctor()
    ff = next(c for c in report.checks if c.name == "ffmpeg")
    assert ff.ok is True
    assert ff.detail == "/usr/bin/ffmpeg"


def test_ffmpeg_missing_when_bare_name():
    with patch("movie_narrator.doctor.ffmpeg_bin", return_value="ffmpeg"):
        report = run_doctor()
    ff = next(c for c in report.checks if c.name == "ffmpeg")
    assert ff.ok is False
    assert "PATH" in ff.hint


def test_ffmpeg_missing_when_exception():
    def boom():
        raise RuntimeError("nope")

    with patch("movie_narrator.doctor.ffmpeg_bin", side_effect=boom):
        report = run_doctor()
    ff = next(c for c in report.checks if c.name == "ffmpeg")
    assert ff.ok is False


# ── optional deps checks ───────────────────────────────────


def test_optional_deps_reflect_probe():
    real = {
        "scenedetect": DepStatus.OK,
        "whisperx": DepStatus.NOT_INSTALLED,
        "faster_whisper": DepStatus.NOT_INSTALLED,
        "sentence_transformers": DepStatus.MISSING_DEPS,
    }

    def fake_probe_status(key):
        status = real.get(key, DepStatus.NOT_INSTALLED)
        hint = "" if status is DepStatus.OK else "pip install ..."
        return (status, hint)

    with (
        patch("movie_narrator.doctor.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch("movie_narrator.doctor.probe_status", side_effect=fake_probe_status),
    ):
        report = run_doctor()
    sc = next(c for c in report.checks if "scenedetect" in c.name)
    wh = next(c for c in report.checks if "whisperx" in c.name)
    st = next(c for c in report.checks if "sentence" in c.name)
    assert sc.ok is True
    assert wh.ok is False
    assert "not installed" in wh.detail
    assert st.ok is False
    assert "missing deps" in st.detail


# ── config checks ──────────────────────────────────────────


class _FakeSettings:
    llm_provider = "openai"
    llm_base_url = "http://localhost:11434/v1"
    llm_model = "qwen2.5:7b"
    default_voice = "zh-CN-YunxiNeural"
    tts_provider = type("T", (), {"value": "edge"})()


def test_config_checks_report_ok():
    with (
        patch("movie_narrator.doctor.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch("movie_narrator.config._USER_ENV", type("P", (), {"exists": lambda self: True})()),
        patch("movie_narrator.config.get_settings", return_value=_FakeSettings()),
    ):
        report = run_doctor()
    llm = next(c for c in report.checks if c.name == "LLM config")
    tts = next(c for c in report.checks if c.name == "TTS config")
    env = next(c for c in report.checks if c.name == "user .env")
    assert llm.ok and tts.ok and env.ok


def test_config_checks_report_warnings_when_missing():
    class _Empty:
        llm_provider = ""
        llm_base_url = ""
        llm_model = ""
        default_voice = ""
        tts_provider = type("T", (), {"value": ""})()

    with (
        patch("movie_narrator.doctor.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch("movie_narrator.config._USER_ENV", type("P", (), {"exists": lambda self: False})()),
        patch("movie_narrator.config.get_settings", return_value=_Empty()),
    ):
        report = run_doctor()
    llm = next(c for c in report.checks if c.name == "LLM config")
    env = next(c for c in report.checks if c.name == "user .env")
    assert llm.ok is False
    assert env.ok is False


# ── report helpers ─────────────────────────────────────────


def test_report_healthy_flag():
    report = DoctorReport()
    report.add(Diagnostic(name="a", ok=True))
    assert report.healthy is True
    report.add(Diagnostic(name="b", ok=False))
    assert report.healthy is False
    assert report.ok_count == 1
    assert len(report.missing) == 1


def test_render_report_contains_summary():
    report = DoctorReport()
    report.add(Diagnostic(name="ffmpeg", ok=True, detail="/usr/bin/ffmpeg"))
    report.add(Diagnostic(name="ml", ok=False, hint="pip install ..."))
    text = render_report(report)
    assert "1/2 checks passed" in text
    assert "MISSING" in text


# ── CLI wiring ─────────────────────────────────────────────


def test_doctor_cli_ok_exits_zero():
    with (
        patch("movie_narrator.doctor.ffmpeg_bin", return_value="/usr/bin/ffmpeg"),
        patch("movie_narrator.doctor.probe_status", return_value=(DepStatus.OK, "")),
        patch("movie_narrator.config._USER_ENV", type("P", (), {"exists": lambda self: True})()),
        patch("movie_narrator.config.get_settings", return_value=_FakeSettings()),
    ):
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0
    assert "Summary:" in result.output


def test_doctor_cli_missing_exits_one():
    with (
        patch("movie_narrator.doctor.ffmpeg_bin", return_value="ffmpeg"),
        patch("movie_narrator.doctor.probe_status", return_value=(DepStatus.NOT_INSTALLED, "pip install missing")),
        patch("movie_narrator.config._USER_ENV", type("P", (), {"exists": lambda self: False})()),
        patch("movie_narrator.config.get_settings", return_value=_FakeSettings()),
    ):
        result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "MISSING" in result.output
