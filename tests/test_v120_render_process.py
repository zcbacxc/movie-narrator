# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""v1.2 Wave 1A tests: render subprocess governance + atomic artifact publish.

Covers ``utils/process.py`` (process-tree termination, deadline subprocess
runner), the sidechain timeout, the MoviePy main-encode deadline, and the
atomic clip publish. All ffmpeg/moviepy interactions are mocked — no real
FFmpeg is invoked.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call

import pytest
from pydub import AudioSegment

from movie_narrator.models import Context, Scene
from movie_narrator.pipeline import render as render_mod
from movie_narrator.pipeline.export_clips import export_clips
from movie_narrator.utils import process as process_mod
from movie_narrator.utils.audio_mix import duck_bgm_sidechain


# ── process.terminate_process_tree ──────────────────────────


def test_terminate_process_tree_windows_command(monkeypatch):
    """Windows builds the expected taskkill /T /F command."""
    monkeypatch.setattr(process_mod.os, "name", "nt")
    run = MagicMock()
    monkeypatch.setattr(process_mod.subprocess, "run", run)

    process_mod.terminate_process_tree(123)

    run.assert_called_once_with(
        ["taskkill", "/PID", "123", "/T", "/F"],
        capture_output=True,
        check=False,
    )


def test_terminate_process_tree_ignores_invalid_pid(monkeypatch):
    """Non-positive pids are a no-op (never invoke the kill helper)."""
    run = MagicMock()
    monkeypatch.setattr(process_mod.subprocess, "run", run)
    process_mod.terminate_process_tree(0)
    run.assert_not_called()


def test_terminate_posix_dies_after_sigterm(monkeypatch):
    """POSIX: SIGTERM is enough — no SIGKILL when the tree exits in time."""
    monkeypatch.setattr(process_mod.os, "name", "posix")
    killpg = MagicMock()
    monkeypatch.setattr(process_mod.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(process_mod, "_process_group_alive", MagicMock(return_value=False))
    monkeypatch.setattr(process_mod.time, "monotonic", MagicMock(return_value=0.0))

    process_mod.terminate_process_tree(456, grace=5.0)

    killpg.assert_called_once_with(456, process_mod._SIGTERM)


def test_terminate_posix_escalates_to_sigkill(monkeypatch):
    """POSIX: a tree still alive after the grace window is SIGKILLed."""
    monkeypatch.setattr(process_mod.os, "name", "posix")
    killpg = MagicMock()
    monkeypatch.setattr(process_mod.os, "killpg", killpg, raising=False)
    monkeypatch.setattr(process_mod, "_process_group_alive", MagicMock(return_value=True))
    monkeypatch.setattr(
        process_mod.time, "monotonic", MagicMock(side_effect=[0.0, 1000.0, 1000.0])
    )
    monkeypatch.setattr(process_mod.time, "sleep", MagicMock())

    process_mod.terminate_process_tree(456, grace=5.0)

    assert killpg.call_args_list == [
        call(456, process_mod._SIGTERM),
        call(456, process_mod._SIGKILL),
    ]


# ── process.run_ffmpeg_subprocess ───────────────────────────


def test_run_ffmpeg_subprocess_returns_completed_process(monkeypatch):
    """Normal execution returns a subprocess.CompletedProcess."""
    cmd = ["ffmpeg", "-i", "a.wav", "b.wav"]
    fake_proc = MagicMock()
    fake_proc.communicate.return_value = ("out", "err")
    fake_proc.returncode = 0
    fake_proc.pid = 42
    popen = MagicMock(return_value=fake_proc)
    monkeypatch.setattr(process_mod.subprocess, "Popen", popen)
    monkeypatch.setattr(process_mod.os, "name", "posix")

    result = process_mod.run_ffmpeg_subprocess(cmd, timeout=30)

    assert isinstance(result, subprocess.CompletedProcess)
    assert result.returncode == 0
    assert result.stdout == "out"
    assert result.stderr == "err"
    assert popen.call_args[1]["start_new_session"] is True


def test_run_ffmpeg_subprocess_timeout_terminates_tree(monkeypatch):
    """Timeout terminates the process tree then raises SubprocessTimeoutError."""
    cmd = ["ffmpeg", "-i", "a.wav", "b.wav"]
    fake_proc = MagicMock()
    fake_proc.pid = 42
    fake_proc.communicate.side_effect = subprocess.TimeoutExpired(cmd, 1)
    popen = MagicMock(return_value=fake_proc)
    monkeypatch.setattr(process_mod.subprocess, "Popen", popen)
    monkeypatch.setattr(process_mod.os, "name", "posix")
    term = MagicMock()
    monkeypatch.setattr(process_mod, "terminate_process_tree", term)

    with pytest.raises(process_mod.SubprocessTimeoutError) as excinfo:
        process_mod.run_ffmpeg_subprocess(cmd, timeout=1)

    term.assert_called_once_with(42, grace=process_mod._DEFAULT_KILL_GRACE)
    fake_proc.wait.assert_called_once()
    assert excinfo.value.pid == 42
    assert issubclass(process_mod.SubprocessTimeoutError, subprocess.SubprocessError)


def test_find_processes_posix_parses_pids(monkeypatch):
    """POSIX discovery parses pid/args pairs and filters by substring."""
    fake = MagicMock()
    fake.returncode = 0
    fake.stdout = (
        "123 /usr/bin/ffmpeg -y -i x /tmp/video_only.mp4\n"
        "456 /usr/bin/other-tool --x\n"
    )
    monkeypatch.setattr(process_mod.os, "name", "posix")
    monkeypatch.setattr(process_mod.subprocess, "run", MagicMock(return_value=fake))

    assert process_mod.find_processes_by_cmdline("/tmp/video_only.mp4") == [123]


def test_terminate_processes_matching_terminates_each(monkeypatch):
    """Matching PIDs are each terminated as a process tree."""
    monkeypatch.setattr(
        process_mod, "find_processes_by_cmdline", MagicMock(return_value=[11, 22])
    )
    term = MagicMock()
    monkeypatch.setattr(process_mod, "terminate_process_tree", term)

    process_mod.terminate_processes_matching("needle")

    assert term.call_args_list == [
        call(11, grace=process_mod._DEFAULT_KILL_GRACE),
        call(22, grace=process_mod._DEFAULT_KILL_GRACE),
    ]


# ── sidechain timeout ───────────────────────────────────────


def test_sidechain_timeout_returns_none(monkeypatch):
    """Sidechain passes _SIDECHAIN_TIMEOUT and falls back (None) on timeout."""
    seen = {}

    def fake_run(cmd, *, timeout, **kwargs):
        seen["timeout"] = timeout
        raise process_mod.SubprocessTimeoutError(cmd, timeout, pid=123)

    monkeypatch.setattr("movie_narrator.utils.audio_mix._ffmpeg_bin", lambda: "ffmpeg")
    monkeypatch.setattr("movie_narrator.utils.audio_mix.run_ffmpeg_subprocess", fake_run)

    narr = AudioSegment.silent(duration=300)
    bgm = AudioSegment.silent(duration=300)

    assert duck_bgm_sidechain(narr, bgm) is None
    assert seen["timeout"] == 300.0


# ── MoviePy main-encode deadline ────────────────────────────


def test_write_videofile_with_deadline_timeout(monkeypatch):
    """A hung write_videofile triggers worker termination + TimeoutError."""

    class _NeverThread:
        def __init__(self, target=None, name=None, daemon=None):
            self.target = target

        def start(self):
            pass

        def join(self, timeout=None):
            return

        def is_alive(self):
            return True

    monkeypatch.setattr(render_mod.threading, "Thread", _NeverThread)
    term = MagicMock()
    monkeypatch.setattr(render_mod, "terminate_processes_matching", term)
    video_only_path = Path("/tmp/video_only.mp4")

    with pytest.raises(TimeoutError):
        render_mod._write_videofile_with_deadline(
            MagicMock(), video_only_path, {}, timeout=300
        )

    term.assert_called_once_with(str(video_only_path))


def test_write_videofile_with_deadline_reraises_worker_exception(monkeypatch):
    """write_videofile exceptions are re-raised unchanged on the caller."""
    fake_video = MagicMock()
    fake_video.write_videofile.side_effect = OSError("encode boom")

    with pytest.raises(OSError, match="encode boom"):
        render_mod._write_videofile_with_deadline(
            fake_video, Path("/tmp/video_only.mp4"), {}, timeout=30
        )


def test_write_videofile_with_deadline_disabled_is_synchronous(monkeypatch):
    """timeout <= 0 calls write_videofile synchronously without a thread."""
    fake_video = MagicMock()
    video_only_path = Path("/tmp/video_only.mp4")
    render_mod._write_videofile_with_deadline(
        fake_video, video_only_path, {}, timeout=0
    )
    fake_video.write_videofile.assert_called_once_with(str(video_only_path))


# ── atomic clip publish ─────────────────────────────────────


def _clip_ctx(tmp_path):
    return Context(
        movie_name="Test",
        output_dir=str(tmp_path),
        source_video_path="/v.mp4",
        scenes=[Scene(index=0, start=0.0, end=5.0)],
    )


def test_export_clips_atomic_publish(tmp_path, monkeypatch):
    """Success moves the staged .part into clips/ and leaves no partial."""
    ctx = _clip_ctx(tmp_path)

    def _run(cmd, **kwargs):
        out = Path(cmd[-1])
        out.write_bytes(b"clip-bytes")
        return SimpleNamespace(returncode=0, stderr=b"")

    monkeypatch.setattr(
        "movie_narrator.pipeline.export_clips.probe", lambda *a, **k: (True, "")
    )
    monkeypatch.setattr(
        "movie_narrator.pipeline.export_clips.ffmpeg_bin", lambda: "/usr/bin/ffmpeg"
    )
    monkeypatch.setattr("movie_narrator.pipeline.export_clips.subprocess.run", _run)

    export_clips(ctx)

    assert (tmp_path / "clips" / "scene_0000.mp4").exists()
    assert not (tmp_path / ".tmp" / "scene_0000.mp4.part").exists()
    assert ctx.scenes[0].clip_path is not None


def test_export_clips_failure_removes_partial(tmp_path, monkeypatch):
    """A failing export removes its .part and publishes nothing."""
    ctx = _clip_ctx(tmp_path)

    def _run(cmd, **kwargs):
        out = Path(cmd[-1])
        out.write_bytes(b"partial-bytes")
        return SimpleNamespace(returncode=1, stderr=b"boom")

    monkeypatch.setattr(
        "movie_narrator.pipeline.export_clips.probe", lambda *a, **k: (True, "")
    )
    monkeypatch.setattr(
        "movie_narrator.pipeline.export_clips.ffmpeg_bin", lambda: "/usr/bin/ffmpeg"
    )
    monkeypatch.setattr("movie_narrator.pipeline.export_clips.subprocess.run", _run)

    export_clips(ctx)

    assert not (tmp_path / ".tmp" / "scene_0000.mp4.part").exists()
    assert not (tmp_path / "clips" / "scene_0000.mp4").exists()
    assert ctx.scenes[0].clip_path is None
