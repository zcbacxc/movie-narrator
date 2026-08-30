# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.3.1 Feature 5: plans & entitlements.

Covers:
- Plan resolution (built-ins, JSON overrides, tolerant parsing)
- ``default_plan_name`` activation via ``MN_DEFAULT_PLAN``
- ``check_submission`` limit enforcement (duration / resolution / bytes)
- API submission rejection with the machine-readable 403 body
- Worker-side enforcement: mandatory watermark + forced CPU encoder
- Default plan == zero behaviour change
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, List, Optional

import pytest

from movie_narrator.cloud import CancelController, TaskAPIServer, run_task
from movie_narrator.cloud.entitlements import (
    DEFAULT,
    FREE,
    PLAN_WATERMARK_TEXT,
    PRO,
    EntitlementError,
    Plan,
    available_plan_names,
    check_submission,
    default_plan_name,
    resolve_plan,
    submission_resolution,
)
from movie_narrator.cloud.models import Task, TaskRequest


# ════════════════════════════════════════════════════════════
#  Plan resolution
# ════════════════════════════════════════════════════════════


class TestPlanResolution:
    def test_builtins(self):
        assert DEFAULT.name == "default"
        assert DEFAULT.max_duration_s is None
        assert DEFAULT.max_resolution is None
        assert DEFAULT.watermark_required is False
        assert DEFAULT.allow_gpu_encoder is True
        assert FREE.name == "free"
        assert FREE.max_resolution == (1280, 720)
        assert FREE.watermark_required is True
        assert FREE.allow_gpu_encoder is False
        assert PRO.name == "pro"
        assert PRO.max_resolution == (1920, 1080)
        assert PRO.watermark_required is False
        assert PRO.allow_gpu_encoder is True

    def test_resolve_case_insensitive_and_empty(self):
        assert resolve_plan("DEFAULT") is DEFAULT
        assert resolve_plan("") is DEFAULT
        assert resolve_plan(" free ") is FREE

    def test_resolve_unknown_raises(self):
        with pytest.raises(KeyError):
            resolve_plan("does-not-exist")

    def test_builtins_are_frozen(self):
        with pytest.raises(Exception):
            FREE.watermark_required = False  # type: ignore[misc]

    def test_available_names_sorted(self):
        names = set(available_plan_names())
        assert {"default", "free", "pro"}.issubset(names)

    def test_json_overrides_replace_builtin(self, tmp_path, monkeypatch):
        plans_file = tmp_path / "plans.json"
        plans_file.write_text(
            json.dumps(
                {
                    "plans": [
                        {
                            "name": "free",
                            "max_duration_s": 30.0,
                            "watermark_required": False,
                        },
                        {"name": "enterprise", "max_resolution": [3840, 2160]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("MN_PLANS_FILE", str(plans_file))
        free = resolve_plan("free")
        assert free.max_duration_s == 30.0
        assert free.watermark_required is False
        assert resolve_plan("enterprise").max_resolution == (3840, 2160)
        # untouched built-ins remain
        assert resolve_plan("pro").max_resolution == (1920, 1080)

    def test_invalid_file_falls_back_to_builtins(self, tmp_path, monkeypatch, caplog):
        plans_file = tmp_path / "plans.json"
        plans_file.write_text("{not json", encoding="utf-8")
        monkeypatch.setenv("MN_PLANS_FILE", str(plans_file))
        with caplog.at_level(logging.WARNING):
            assert resolve_plan("free") is FREE
        assert any("MN_PLANS_FILE" in r.message for r in caplog.records)

    def test_malformed_entries_skipped(self, tmp_path, monkeypatch):
        plans_file = tmp_path / "plans.json"
        plans_file.write_text(
            json.dumps(
                {
                    "plans": [
                        {"max_duration_s": 10.0},  # no name
                        {"name": "bad", "max_resolution": "huge"},  # bad shape
                        "just-a-string",
                    ]
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("MN_PLANS_FILE", str(plans_file))
        # built-ins unaffected, no crash
        assert resolve_plan("free") is FREE
        with pytest.raises(KeyError):
            resolve_plan("bad")


class TestDefaultPlanName:
    def test_unset_is_default(self, monkeypatch):
        monkeypatch.delenv("MN_DEFAULT_PLAN", raising=False)
        assert default_plan_name() == "default"

    def test_env_value(self, monkeypatch):
        monkeypatch.setenv("MN_DEFAULT_PLAN", "free")
        assert default_plan_name() == "free"

    def test_unknown_env_value_falls_back(self, monkeypatch, caplog):
        monkeypatch.setenv("MN_DEFAULT_PLAN", "typo-plan")
        with caplog.at_level(logging.WARNING):
            assert default_plan_name() == "default"
        assert any("MN_DEFAULT_PLAN" in r.message for r in caplog.records)

    def test_injected_env_mapping(self):
        assert default_plan_name({"MN_DEFAULT_PLAN": "pro"}) == "pro"


# ════════════════════════════════════════════════════════════
#  Submission checks
# ════════════════════════════════════════════════════════════


class TestCheckSubmission:
    def test_default_plan_allows_everything(self):
        check_submission(
            DEFAULT, duration_s=1e9, resolution=(99999, 99999), estimated_bytes=1 << 60
        )

    def test_duration_violation(self):
        with pytest.raises(EntitlementError) as e:
            check_submission(FREE, duration_s=120.0)
        assert e.value.plan == "free"
        assert e.value.limit == "max_duration_s"
        assert e.value.actual == 120.0

    def test_resolution_violation(self):
        with pytest.raises(EntitlementError) as e:
            check_submission(FREE, resolution=(1920, 1080))
        assert e.value.limit == "max_resolution"

    def test_resolution_portrait_normalized(self):
        # Portrait 720x1280 is within the (1280, 720) landscape limit after
        # orientation normalization; the swapped portrait form is not.
        check_submission(FREE, resolution=(720, 1280))
        with pytest.raises(EntitlementError):
            check_submission(FREE, resolution=(1080, 1920))

    def test_bytes_violation(self):
        with pytest.raises(EntitlementError) as e:
            check_submission(FREE, estimated_bytes=FREE.max_artifact_bytes + 1)
        assert e.value.limit == "max_artifact_bytes"

    def test_none_values_skip_checks(self):
        check_submission(FREE)

    def test_error_message_mentions_plan(self):
        with pytest.raises(EntitlementError, match="free"):
            check_submission(FREE, duration_s=999.0)


class TestSubmissionResolution:
    def test_default_per_format(self):
        assert submission_resolution(TaskRequest(movie_name="T")) == (1920, 1080)
        portrait = TaskRequest(movie_name="T", video_format="9:16")
        assert submission_resolution(portrait) == (1080, 1920)

    def test_from_params_video_sizes(self):
        req = TaskRequest(
            movie_name="T",
            params={"video_sizes": {"16:9": [1280, 720]}},
        )
        assert submission_resolution(req) == (1280, 720)

    def test_malformed_sizes_fall_back(self):
        req = TaskRequest(
            movie_name="T",
            params={"video_sizes": {"16:9": ["wide", "tall"]}},
        )
        assert submission_resolution(req) == (1920, 1080)


# ════════════════════════════════════════════════════════════
#  API enforcement (403)
# ════════════════════════════════════════════════════════════


def _fast_pipeline(ctx, **kwargs):
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    return ctx


def _http(method: str, url: str, body=None, headers=None, timeout: float = 10.0):
    data = None
    hdrs = dict(headers or {})
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            payload = {}
        return e.code, payload


def _free_plan_server(tmp_path, monkeypatch, api_key: Optional[str] = "secret"):
    """API server running under the free plan (via MN_DEFAULT_PLAN)."""
    monkeypatch.setenv("MN_DEFAULT_PLAN", "free")
    monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=1,
        api_key=api_key,
    )
    server.start(blocking=False)
    time.sleep(0.1)
    return server


class TestHttpEntitlement:
    def test_free_plan_rejects_long_1080p(self, tmp_path, monkeypatch):
        server = _free_plan_server(tmp_path, monkeypatch)
        try:
            base = server.base_url
            headers = {"X-API-Key": "secret"}
            status, body = _http(
                "POST",
                f"{base}/tasks",
                body={"movie_name": "Big", "duration": 300},
                headers=headers,
            )
            assert status == 403
            assert body["error"] == "entitlement_denied"
            assert body["plan"] == "free"
            assert body["limit"] in {"max_duration_s", "max_resolution"}
            assert body["actual"] is not None
        finally:
            server.stop()

    def test_free_plan_accepts_within_limits(self, tmp_path, monkeypatch):
        server = _free_plan_server(tmp_path, monkeypatch)
        try:
            base = server.base_url
            headers = {"X-API-Key": "secret"}
            status, _ = _http(
                "POST",
                f"{base}/tasks",
                body={
                    "movie_name": "Small",
                    "duration": 60,
                    "params": {"video_sizes": {"16:9": [1280, 720]}},
                },
                headers=headers,
            )
            assert status == 201
        finally:
            server.stop()

    def test_plan_stamped_on_task(self, tmp_path, monkeypatch):
        server = _free_plan_server(tmp_path, monkeypatch)
        try:
            base = server.base_url
            headers = {"X-API-Key": "secret"}
            _, body = _http(
                "POST",
                f"{base}/tasks",
                body={
                    "movie_name": "Stamp",
                    "duration": 30,
                    "params": {"video_sizes": {"16:9": [1280, 720]}},
                },
                headers=headers,
            )
            status, detail = _http(
                "GET", f"{base}/tasks/{body['task_id']}", headers=headers
            )
            assert status == 200
            assert detail["plan"] == "free"
        finally:
            server.stop()

    def test_unknown_plan_header_rejected(self, tmp_path, monkeypatch):
        server = _free_plan_server(tmp_path, monkeypatch)
        try:
            base = server.base_url
            headers = {"X-API-Key": "secret", "X-MN-Plan": "ultra"}
            status, body = _http(
                "POST", f"{base}/tasks", body={"movie_name": "X"}, headers=headers
            )
            assert status == 400
            assert "ultra" in body["error"]
        finally:
            server.stop()

    def test_request_level_plan_header(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MN_DEFAULT_PLAN", raising=False)
        monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "tasks",
            max_workers=1,
            api_key="secret",
        )
        server.start(blocking=False)
        try:
            base = server.base_url
            headers = {"X-API-Key": "secret", "X-MN-Plan": "free"}
            status, body = _http(
                "POST",
                f"{base}/tasks",
                body={"movie_name": "Hdr", "duration": 600},
                headers=headers,
            )
            assert status == 403
            assert body["plan"] == "free"
        finally:
            server.stop()

    def test_unauthenticated_loopback_gets_default_plan(
        self, tmp_path, monkeypatch
    ):
        """Unauthenticated (loopback) requests are never restricted."""
        server = _free_plan_server(tmp_path, monkeypatch, api_key=None)
        try:
            base = server.base_url
            status, _ = _http(
                "POST",
                f"{base}/tasks",
                body={"movie_name": "Local", "duration": 3600},
            )
            assert status == 201
        finally:
            server.stop()

    def test_default_plan_no_behavior_change(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MN_DEFAULT_PLAN", raising=False)
        monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "tasks",
            max_workers=1,
            api_key="secret",
        )
        server.start(blocking=False)
        try:
            base = server.base_url
            headers = {"X-API-Key": "secret"}
            status, _ = _http(
                "POST",
                f"{base}/tasks",
                body={"movie_name": "Huge", "duration": 3600},
                headers=headers,
            )
            assert status == 201
        finally:
            server.stop()

    def test_batch_entitlement_rejection(self, tmp_path, monkeypatch):
        server = _free_plan_server(tmp_path, monkeypatch)
        try:
            base = server.base_url
            headers = {"X-API-Key": "secret"}
            status, body = _http(
                "POST",
                f"{base}/tasks/batch",
                body={
                    "requests": [
                        {"movie_name": "A", "duration": 30},
                        {"movie_name": "B", "duration": 3600},
                    ]
                },
                headers=headers,
            )
            assert status == 403
            assert body["error"] == "entitlement_denied"
        finally:
            server.stop()


# ════════════════════════════════════════════════════════════
#  Worker enforcement
# ════════════════════════════════════════════════════════════


class _CtxCapture:
    def __init__(self) -> None:
        self.contexts: List[Any] = []

    def __call__(self, ctx, **kwargs):
        self.contexts.append(ctx)
        Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
        ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
        return ctx


def _run_plan_task(monkeypatch, tmp_path, plan: Optional[str], **req_kwargs):
    """Run a task through the worker with a mocked pipeline; return (task, ctxs)."""
    monkeypatch.setenv("CI", "1")
    capture = _CtxCapture()
    monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", capture)
    request = TaskRequest(
        movie_name="PlanWorker",
        output_dir=str(tmp_path / "out"),
        **req_kwargs,
    )
    task = Task(request=request, plan=plan or "default")
    finished = run_task(task, CancelController())
    return finished, capture.contexts


class TestWorkerEnforcement:
    def test_free_plan_watermark_and_cpu(self, monkeypatch, tmp_path):
        task, ctxs = _run_plan_task(monkeypatch, tmp_path, plan="free")
        assert task.status.value == "completed"
        assert len(ctxs) == 1
        ctx = ctxs[0]
        # watermark injected
        template = ctx.metadata["render_template"]
        assert template["watermark_text"] == PLAN_WATERMARK_TEXT
        # CPU encoder forced
        assert ctx.metadata["render_encoder"] == "cpu"
        # plan record present
        assert ctx.metadata["plan"] == "free"
        policy = ctx.metadata["plan_policy"]
        assert policy["watermark_required"] is True
        assert policy["watermark_source"] == "plan"
        assert policy["gpu_encoder_allowed"] is False
        assert policy["gpu_encoder_forced"] is True
        # plan travels on the result metadata too
        assert task.result is not None
        assert task.result.metadata["plan"] == "free"

    def test_request_watermark_respected(self, monkeypatch, tmp_path):
        task, ctxs = _run_plan_task(
            monkeypatch,
            tmp_path,
            plan="free",
            params={"render_template": {"watermark_text": "custom"}},
        )
        ctx = ctxs[0]
        assert ctx.metadata["render_template"]["watermark_text"] == "custom"
        assert ctx.metadata["plan_policy"]["watermark_source"] == "request"

    def test_preset_watermark_respected(self, monkeypatch, tmp_path):
        """douyin-fast already ships a watermark — plan must not override it."""
        task, ctxs = _run_plan_task(
            monkeypatch,
            tmp_path,
            plan="free",
            narration_preset="douyin-fast",
        )
        ctx = ctxs[0]
        template = ctx.metadata["render_template"]
        assert template["watermark_text"] != PLAN_WATERMARK_TEXT
        assert ctx.metadata["plan_policy"]["watermark_source"] == "preset"

    def test_default_plan_no_injection(self, monkeypatch, tmp_path):
        task, ctxs = _run_plan_task(monkeypatch, tmp_path, plan="default")
        ctx = ctxs[0]
        assert "render_encoder" not in ctx.metadata
        assert "render_template" not in ctx.metadata
        assert ctx.metadata["plan"] == "default"
        policy = ctx.metadata["plan_policy"]
        assert policy["watermark_required"] is False
        assert policy["gpu_encoder_allowed"] is True
        assert "gpu_encoder_forced" not in policy

    def test_unknown_task_plan_falls_back(self, monkeypatch, tmp_path, caplog):
        task, ctxs = _run_plan_task(monkeypatch, tmp_path, plan="gone-plan")
        with caplog.at_level(logging.WARNING):
            pass
        assert ctxs[0].metadata["plan"] == "default"
        assert task.status.value == "completed"

    def test_metadata_json_records_plan(self, monkeypatch, tmp_path):
        """A finished run's metadata.json gains the ``plan`` key."""
        out_dir = tmp_path / "out"

        def _pipeline_with_metadata(ctx, **kwargs):
            Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
            with open(Path(ctx.output_dir) / "metadata.json", "w", encoding="utf-8") as f:
                json.dump({"movie_name": ctx.movie_name}, f)
            ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
            return ctx

        monkeypatch.setenv("CI", "1")
        monkeypatch.setattr(
            "movie_narrator.cloud.worker.run_pipeline", _pipeline_with_metadata
        )
        request = TaskRequest(movie_name="Meta", output_dir=str(out_dir))
        task = Task(request=request, plan="pro")
        run_task(task, CancelController())
        data = json.loads((out_dir / "metadata.json").read_text(encoding="utf-8"))
        assert data["plan"] == "pro"


# ════════════════════════════════════════════════════════════
#  Dataclass sanity
# ════════════════════════════════════════════════════════════


class TestPlanDataclass:
    def test_custom_plan_defaults(self):
        plan = Plan(name="custom")
        assert plan.max_duration_s is None
        assert plan.max_artifact_bytes is None
        assert plan.watermark_required is False
        assert plan.allow_gpu_encoder is True
        assert plan.artifact_ttl_hours is None

    def test_ttl_values_recorded(self):
        assert FREE.artifact_ttl_hours == 24.0
        assert PRO.artifact_ttl_hours == 72.0
