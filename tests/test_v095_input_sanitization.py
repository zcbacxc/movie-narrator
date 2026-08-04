# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.9.5 input sanitization (GAP: 输入净化).

Covers:
- ``TaskRequest`` field constraints: bounded enums (``lang``,
  ``video_format``, ``subtitle_mode``), ranges (``duration``,
  ``max_retries``, ``retry_delay``), string length caps, and
  ``movie_name`` trimming/emptiness.
- ``log_level`` normalisation to upper-case.
- POST body size limit (``_MAX_BODY_BYTES``) → HTTP 413.
- ``limit`` query parameter validation/clamping → HTTP 400 / capped.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

import pytest
from pydantic import ValidationError

from movie_narrator.cloud import TaskAPIServer
from movie_narrator.cloud.models import BatchRequest, TaskRequest
from movie_narrator.utils.prompts import SUPPORTED_LANGS


# ── Mock pipeline ─────────────────────────────────────────


def _mock_pipeline(ctx, **kwargs):
    """Mock pipeline that doesn't do any actual work."""
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    ctx.audio_path = str(Path(ctx.output_dir) / "narration.mp3")
    ctx.output_dir = str(ctx.output_dir)
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    Path(ctx.video_path).write_bytes(b"mock video")
    Path(ctx.audio_path).write_bytes(b"mock audio")
    return ctx


@pytest.fixture(autouse=True)
def mock_pipeline(monkeypatch):
    """Mock run_pipeline to prevent actual pipeline execution in tests."""
    monkeypatch.setattr(
        "movie_narrator.cloud.worker.run_pipeline",
        _mock_pipeline,
    )


# ── HTTP helpers ──────────────────────────────────────────


def _request(
    url: str,
    *,
    method: str = "GET",
    data: Optional[bytes] = None,
    timeout: float = 5.0,
):
    headers = {}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    return urllib.request.urlopen(req, timeout=timeout)


def _request_code(url: str, **kwargs) -> int:
    """Send a request and return the HTTP status code (never raises on 4xx/5xx)."""
    try:
        with _request(url, **kwargs) as resp:
            return resp.getcode()
    except urllib.error.HTTPError as e:
        return e.code


@pytest.fixture
def open_server(tmp_path):
    """API server with no api_key (open, loopback default)."""
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=1,
        api_key=None,
    )
    server.start(blocking=False)
    time.sleep(0.1)
    yield server
    server.stop()


# ════════════════════════════════════════════════════════════
#  TaskRequest model validation
# ════════════════════════════════════════════════════════════


class TestTaskRequestValidation:
    """v0.9.5: TaskRequest field constraints."""

    def test_movie_name_trimmed(self):
        req = TaskRequest(movie_name="  Test Movie  ")
        assert req.movie_name == "Test Movie"

    def test_empty_movie_name_rejected(self):
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="   ")

    def test_movie_name_too_long_rejected(self):
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x" * 201)

    def test_lang_must_be_supported(self):
        for lang in SUPPORTED_LANGS:
            assert TaskRequest(movie_name="x", lang=lang).lang == lang
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", lang="xx")

    def test_video_format_bounded(self):
        assert TaskRequest(movie_name="x", video_format="9:16").video_format == "9:16"
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", video_format="4:3")

    def test_subtitle_mode_bounded(self):
        assert (
            TaskRequest(movie_name="x", subtitle_mode="bilingual").subtitle_mode
            == "bilingual"
        )
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", subtitle_mode="invalid")

    def test_duration_range(self):
        assert TaskRequest(movie_name="x", duration=1).duration == 1
        assert TaskRequest(movie_name="x", duration=3600).duration == 3600
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", duration=0)
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", duration=-5)
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", duration=3601)

    def test_max_retries_range(self):
        assert TaskRequest(movie_name="x", max_retries=0).max_retries == 0
        assert TaskRequest(movie_name="x", max_retries=100).max_retries == 100
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", max_retries=-1)
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", max_retries=101)

    def test_retry_delay_range(self):
        assert TaskRequest(movie_name="x", retry_delay=0).retry_delay == 0
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", retry_delay=-1)

    def test_log_level_normalised(self):
        assert TaskRequest(movie_name="x", log_level="info").log_level == "INFO"
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", log_level="trace")

    def test_style_length_capped(self):
        with pytest.raises(ValidationError):
            TaskRequest(movie_name="x", style="s" * 201)

    def test_batch_upper_bound(self):
        BatchRequest(requests=[TaskRequest(movie_name=f"m{i}") for i in range(50)])
        with pytest.raises(ValidationError):
            BatchRequest(
                requests=[TaskRequest(movie_name=f"m{i}") for i in range(51)]
            )


# ════════════════════════════════════════════════════════════
#  HTTP-level input sanitization
# ════════════════════════════════════════════════════════════


class TestApiInputSanitization:
    """v0.9.5: HTTP request validation (body size, limit, enum rejection)."""

    def test_invalid_task_payload_returns_400(self, open_server):
        """POST /tasks with an invalid lang is rejected with 400."""
        data = json.dumps({"movie_name": "Bad", "lang": "xx"}).encode("utf-8")
        assert _request_code(
            f"{open_server.base_url}/tasks", method="POST", data=data
        ) == 400

    def test_invalid_video_format_returns_400(self, open_server):
        data = json.dumps({"movie_name": "Bad", "video_format": "4:3"}).encode("utf-8")
        assert _request_code(
            f"{open_server.base_url}/tasks", method="POST", data=data
        ) == 400

    def test_empty_movie_name_returns_400(self, open_server):
        data = json.dumps({"movie_name": "   "}).encode("utf-8")
        assert _request_code(
            f"{open_server.base_url}/tasks", method="POST", data=data
        ) == 400

    def test_valid_task_returns_201(self, open_server):
        data = json.dumps({"movie_name": "Good", "max_retries": 0}).encode("utf-8")
        assert _request_code(
            f"{open_server.base_url}/tasks", method="POST", data=data
        ) == 201

    def test_oversized_body_returns_413(self, open_server):
        """POST /tasks body larger than _MAX_BODY_BYTES → 413 (not 400)."""
        big = json.dumps({"movie_name": "Big", "params": {"pad": "x" * (2 * 1024 * 1024)}})
        assert len(big.encode("utf-8")) > 1024 * 1024
        assert _request_code(
            f"{open_server.base_url}/tasks", method="POST", data=big.encode("utf-8")
        ) == 413

    def test_oversized_batch_body_returns_413(self, open_server):
        big = json.dumps(
            {
                "requests": [
                    {"movie_name": "m", "params": {"pad": "x" * (2 * 1024 * 1024)}}
                ]
            }
        )
        assert _request_code(
            f"{open_server.base_url}/tasks/batch", method="POST", data=big.encode("utf-8")
        ) == 413

    def test_invalid_limit_returns_400(self, open_server):
        assert _request_code(
            f"{open_server.base_url}/tasks?limit=abc"
        ) == 400
        assert _request_code(
            f"{open_server.base_url}/tasks?limit=-5"
        ) == 400

    def test_limit_clamped(self, open_server):
        """A large limit is clamped to _MAX_LIST_LIMIT instead of erroring."""
        assert _request_code(
            f"{open_server.base_url}/tasks?limit=99999"
        ) == 200
        assert _request_code(
            f"{open_server.base_url}/batches?limit=99999"
        ) == 200
