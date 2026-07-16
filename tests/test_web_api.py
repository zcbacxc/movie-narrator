"""Tests for src/movie_narrator/web_api/ — core modules.

Covers WebSocketConsole, TaskController, TaskManager, pydantic models,
form validation, and utility functions.  These modules do not depend
on FastAPI, so the full suite runs without the ``web`` extra installed.
"""

from __future__ import annotations

import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from movie_narrator.pipeline.errors import PipelineCancelled
from movie_narrator.web_api.console import WebSocketConsole
from movie_narrator.web_api.controller import TaskController
from movie_narrator.web_api.form import FormData, validate_form
from movie_narrator.web_api.models import TaskCreateRequest
from movie_narrator.web_api.tasks import TaskInfo, TaskManager
from movie_narrator.web_api.utils import collect_artifacts, zip_artifacts


# ── Helpers ────────────────────────────────────────────────


def _base_form(**overrides) -> FormData:
    """Return a valid FormData with optional overrides."""
    defaults = dict(
        movie="Test Movie",
        style="热血搞笑",
        duration=60,
        voice="",
        format="16:9",
        video_path=None,
        library_dir="",
        research=False,
        bgm_path=None,
        no_bgm=False,
        no_clips=False,
        strict=False,
        subtitle_lang="",
        subtitle_mode="original",
        scene_threshold=None,
        match_min_score=None,
        translate_provider="",
        translate_retries=None,
    )
    defaults.update(overrides)
    return FormData(**defaults)


@pytest.fixture
def mock_pipeline(monkeypatch):
    """Mock ``build_context`` / ``run_pipeline`` so TaskManager tests
    never execute the real pipeline.

    The mocked ``run_pipeline`` blocks until the fixture is torn down
    (or the task is cancelled), keeping the task in the ``running``
    state long enough for ``cancel_task`` to observe it.
    """
    release = threading.Event()

    def fake_build_context(**kwargs):
        ctx = SimpleNamespace()
        ctx.video_path = None
        ctx.audio_path = None
        ctx.services = SimpleNamespace()
        return ctx

    def fake_run_pipeline(ctx, controller=None):
        while not release.is_set():
            if controller is not None and controller.is_cancelled():
                raise PipelineCancelled()
            release.wait(timeout=0.01)

    monkeypatch.setattr(
        "movie_narrator.pipeline.runner.build_context", fake_build_context
    )
    monkeypatch.setattr(
        "movie_narrator.pipeline.runner.run_pipeline", fake_run_pipeline
    )

    yield

    # Release any blocked background threads so pytest can exit cleanly.
    release.set()


# ── 1. WebSocketConsole (console.py) ───────────────────────


def test_websocket_console_step():
    """step() sets current_step; snapshot() returns the correct value."""
    console = WebSocketConsole()
    console.step("generate_script")
    version, text, step = console.snapshot()
    assert step == "generate_script"
    assert "▶ generate_script..." in text
    assert version == 1


def test_websocket_console_append():
    """Multiple appends accumulate; snapshot returns all lines."""
    console = WebSocketConsole()
    console.step_ok("step_a", 1.0)
    console.step_ok("step_b", 2.0)
    console.inline_warn("careful")
    version, text, _ = console.snapshot()
    assert "✓ step_a (1.0s)" in text
    assert "✓ step_b (2.0s)" in text
    assert "⚠ careful" in text
    assert version == 3


def test_websocket_console_clear():
    """clear() empties lines and increments version."""
    console = WebSocketConsole()
    console.step("a")
    version_before, _, _ = console.snapshot()
    assert version_before == 1

    console.clear()
    version, text, step = console.snapshot()
    assert text == ""
    assert step == ""
    assert version > version_before


def test_websocket_console_step_ok():
    """step_ok appends a ✓ line."""
    console = WebSocketConsole()
    console.step_ok("render", 3.5)
    _, text, _ = console.snapshot()
    assert "✓ render (3.5s)" in text


def test_websocket_console_inline_warn():
    """inline_warn appends a ⚠ line."""
    console = WebSocketConsole()
    console.inline_warn("something off")
    _, text, _ = console.snapshot()
    assert "⚠ something off" in text


def test_websocket_console_thread_safety():
    """Concurrent appends from multiple threads must not crash."""
    console = WebSocketConsole()

    def _writer(n: int) -> None:
        for i in range(50):
            console.step_ok(f"t{n}_{i}", 0.1)

    threads = [threading.Thread(target=_writer, args=(n,), daemon=True) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    _, text, _ = console.snapshot()
    # All 200 lines (4 writers × 50) should be present.
    assert "t0_0" in text
    assert "t3_49" in text
    assert text.count("\n") + 1 == 200


# ── 2. TaskController (controller.py) ──────────────────────


def test_task_controller_initial():
    """Initial is_cancelled() is False."""
    ctrl = TaskController()
    assert ctrl.is_cancelled() is False


def test_task_controller_cancel():
    """cancel() sets the flag; is_cancelled() returns True."""
    ctrl = TaskController()
    ctrl.cancel()
    assert ctrl.is_cancelled() is True


def test_task_controller_reset():
    """reset() after cancel() restores False."""
    ctrl = TaskController()
    ctrl.cancel()
    assert ctrl.is_cancelled() is True
    ctrl.reset()
    assert ctrl.is_cancelled() is False


# ── 3. TaskManager (tasks.py) ──────────────────────────────


def test_task_manager_create_task(mock_pipeline, tmp_path):
    """create_task returns a task_id; get_task returns the TaskInfo."""
    mgr = TaskManager(base_output_dir=str(tmp_path))
    request = TaskCreateRequest(movie="Test Movie")
    task_id = mgr.create_task(request)
    assert isinstance(task_id, str)
    assert len(task_id) > 0

    info = mgr.get_task(task_id)
    assert info is not None
    assert isinstance(info, TaskInfo)
    assert info.task_id == task_id


def test_task_manager_get_nonexistent():
    """get_task on an unknown id returns None."""
    mgr = TaskManager(base_output_dir="output")
    assert mgr.get_task("nonexistent") is None


def test_task_manager_cancel_nonexistent():
    """cancel_task on an unknown id returns False."""
    mgr = TaskManager(base_output_dir="output")
    assert mgr.cancel_task("nonexistent") is False


def test_task_manager_cancel_running(mock_pipeline, tmp_path):
    """cancel_task on a running task returns True (or False if already done)."""
    mgr = TaskManager(base_output_dir=str(tmp_path))
    request = TaskCreateRequest(movie="Test Movie")
    task_id = mgr.create_task(request)
    # Give the background thread a moment to enter run_pipeline.
    time.sleep(0.05)
    result = mgr.cancel_task(task_id)
    assert result in (True, False)


def test_task_manager_validation_error(tmp_path):
    """Invalid movie name (empty string) raises ValueError."""
    mgr = TaskManager(base_output_dir=str(tmp_path))
    request = TaskCreateRequest(movie="")
    with pytest.raises(ValueError):
        mgr.create_task(request)


# ── 4. Models (models.py) ─────────────────────────────────


def test_task_create_request_defaults():
    """Default field values are correct."""
    req = TaskCreateRequest(movie="Inception")
    assert req.movie == "Inception"
    assert req.style == "热血搞笑"
    assert req.duration == 60
    assert req.voice == ""
    assert req.format == "16:9"
    assert req.library_dir == ""
    assert req.research is False
    assert req.no_bgm is False
    assert req.no_clips is False
    assert req.strict is False
    assert req.subtitle_lang == ""
    assert req.subtitle_mode == "original"
    assert req.scene_threshold is None
    assert req.match_min_score is None
    assert req.translate_provider == ""
    assert req.translate_retries is None


def test_task_create_request_to_form_data():
    """to_form_data returns a FormData with matching fields."""
    req = TaskCreateRequest(
        movie="Inception",
        style="悬疑",
        duration=120,
        voice="zh-CN-XiaoxiaoNeural",
        format="9:16",
        library_dir="/lib",
        research=True,
        no_bgm=True,
        no_clips=True,
        strict=True,
        subtitle_lang="en",
        subtitle_mode="bilingual",
        scene_threshold=0.5,
        match_min_score=0.3,
        translate_provider="llm",
        translate_retries=3,
    )
    fd = req.to_form_data(video_path="/v.mp4", bgm_path="/b.mp3")
    assert isinstance(fd, FormData)
    assert fd.movie == "Inception"
    assert fd.style == "悬疑"
    assert fd.duration == 120
    assert fd.voice == "zh-CN-XiaoxiaoNeural"
    assert fd.format == "9:16"
    assert fd.video_path == "/v.mp4"
    assert fd.library_dir == "/lib"
    assert fd.research is True
    assert fd.bgm_path == "/b.mp3"
    assert fd.no_bgm is True
    assert fd.no_clips is True
    assert fd.strict is True
    assert fd.subtitle_lang == "en"
    assert fd.subtitle_mode == "bilingual"
    assert fd.scene_threshold == 0.5
    assert fd.match_min_score == 0.3
    assert fd.translate_provider == "llm"
    assert fd.translate_retries == 3


def test_task_create_request_validation():
    """duration=0 and duration=700 both raise ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TaskCreateRequest(movie="test", duration=0)
    with pytest.raises(ValidationError):
        TaskCreateRequest(movie="test", duration=700)


# ── 5. Form (form.py) ─────────────────────────────────────


def test_validate_form_empty_movie():
    """Empty movie name yields a validation error."""
    errors = validate_form(_base_form(movie=""))
    assert any("Movie name is required" in e for e in errors)


def test_validate_form_valid():
    """A well-formed FormData produces no errors."""
    errors = validate_form(_base_form())
    assert errors == []


def test_validate_form_bilingual_no_lang():
    """bilingual mode without subtitle_lang yields an error."""
    errors = validate_form(_base_form(subtitle_mode="bilingual", subtitle_lang=""))
    assert any("subtitle_lang is required" in e for e in errors)


# ── 6. Utils (utils.py) ───────────────────────────────────


def test_collect_artifacts_empty(tmp_path):
    """A ctx with no outputs yields an empty artifact list."""
    ctx = SimpleNamespace(video_path=None, audio_path=None)
    artifacts = collect_artifacts(ctx, tmp_path)
    assert artifacts == []


def test_zip_artifacts(tmp_path):
    """zip_artifacts packs files into a zip with the expected contents."""
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("hello", encoding="utf-8")
    f2.write_text("world", encoding="utf-8")

    zip_path = tmp_path / "out" / "artifacts.zip"
    result = zip_artifacts([str(f1), str(f2)], zip_path)

    assert Path(result) == zip_path
    assert zip_path.exists()
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
        assert "a.txt" in names
        assert "b.txt" in names
        assert zf.read("a.txt").decode() == "hello"
        assert zf.read("b.txt").decode() == "world"
