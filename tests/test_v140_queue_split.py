# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.4.0 Feature 2: GPU-vs-CPU queue separation.

Covers:
- ``task_requires_gpu`` pure routing function (plan × encoder hint matrix)
- ``MN_WORKER_QUEUES`` / ``MN_GPU_WORKERS`` env resolution
- split-pool execution of fake tasks (routing + structured queue_route log)
- default single-pool layout unchanged
- shutdown drains both pools
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest

from movie_narrator.cloud import LocalTaskQueue
from movie_narrator.cloud.entitlements import GPU_ENCODER_HINTS, task_requires_gpu
from movie_narrator.cloud.models import Task, TaskRequest
from movie_narrator.cloud.queue import (
    ENV_GPU_WORKERS,
    ENV_WORKER_QUEUES,
    QUEUE_MODE_SINGLE,
    QUEUE_MODE_SPLIT,
    gpu_worker_count,
    worker_queue_mode,
)


# ── Helpers ────────────────────────────────────────────────


def _fast_pipeline(ctx, **kwargs):
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    return ctx


def _make_task(
    *,
    plan: str = "default",
    encoder: Any = None,
    movie: str = "Split",
) -> Task:
    params = {} if encoder is None else {"render_encoder": encoder}
    request = TaskRequest(movie_name=movie, params=params, plan=plan)
    return Task(request=request, plan=plan)


# ════════════════════════════════════════════════════════════
#  task_requires_gpu routing matrix
# ════════════════════════════════════════════════════════════


class TestTaskRequiresGpu:
    @pytest.mark.parametrize("hint", ["nvenc", "vaapi", "videotoolbox"])
    def test_gpu_hint_with_gpu_allowed_plan(self, hint):
        assert task_requires_gpu(_make_task(plan="pro", encoder=hint)) is True
        assert task_requires_gpu(_make_task(plan="default", encoder=hint)) is True

    def test_gpu_hint_is_case_insensitive(self):
        assert task_requires_gpu(_make_task(plan="pro", encoder="NVENC")) is True

    @pytest.mark.parametrize("hint", ["cpu", "auto", None, "", "junk"])
    def test_non_gpu_hints_stay_on_cpu_pool(self, hint):
        assert task_requires_gpu(_make_task(plan="pro", encoder=hint)) is False

    def test_cpu_forced_plan_never_routes_to_gpu(self):
        """A plan that disallows GPU (free) routes to CPU even with a GPU hint."""
        assert task_requires_gpu(_make_task(plan="free", encoder="nvenc")) is False

    def test_unknown_plan_falls_back_to_default_plan(self):
        """Unknown plan: worker enforces the unlimited default (GPU allowed)."""
        assert task_requires_gpu(_make_task(plan="no-such-plan", encoder="nvenc")) is True

    def test_missing_params_attribute_is_tolerated(self):
        class _Bare:
            plan = "pro"
            request = None

        assert task_requires_gpu(_Bare()) is False

    def test_gpu_encoder_hints_documented(self):
        assert GPU_ENCODER_HINTS == frozenset({"nvenc", "vaapi", "videotoolbox"})


# ════════════════════════════════════════════════════════════
#  Env resolution
# ════════════════════════════════════════════════════════════


class TestQueueEnvResolution:
    def test_default_is_single(self, monkeypatch):
        monkeypatch.delenv(ENV_WORKER_QUEUES, raising=False)
        assert worker_queue_mode() == QUEUE_MODE_SINGLE

    def test_split_requested(self, monkeypatch):
        monkeypatch.setenv(ENV_WORKER_QUEUES, "split")
        assert worker_queue_mode() == QUEUE_MODE_SPLIT

    def test_single_explicit(self, monkeypatch):
        monkeypatch.setenv(ENV_WORKER_QUEUES, "single")
        assert worker_queue_mode() == QUEUE_MODE_SINGLE

    def test_unknown_mode_falls_back_to_single(self, monkeypatch, caplog):
        monkeypatch.setenv(ENV_WORKER_QUEUES, "tri")
        with caplog.at_level(logging.WARNING):
            assert worker_queue_mode() == QUEUE_MODE_SINGLE
        assert any(ENV_WORKER_QUEUES in r.message for r in caplog.records)

    def test_gpu_workers_default(self, monkeypatch):
        monkeypatch.delenv(ENV_GPU_WORKERS, raising=False)
        assert gpu_worker_count() == 1

    @pytest.mark.parametrize("raw", ["3", " 2 "])
    def test_gpu_workers_parsed(self, monkeypatch, raw):
        monkeypatch.setenv(ENV_GPU_WORKERS, raw)
        assert gpu_worker_count() == int(raw.strip())

    @pytest.mark.parametrize("raw", ["abc", "0", "-2", ""])
    def test_gpu_workers_invalid_falls_back_to_one(self, monkeypatch, raw):
        monkeypatch.setenv(ENV_GPU_WORKERS, raw)
        assert gpu_worker_count() == 1


# ════════════════════════════════════════════════════════════
#  LocalTaskQueue pool layout & execution
# ════════════════════════════════════════════════════════════


@pytest.fixture
def fast_pipeline(monkeypatch):
    """Patch run_pipeline so tasks complete without real work."""
    monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", _fast_pipeline)


class TestPoolLayout:
    def test_default_is_single_pool(self, tmp_path, monkeypatch):
        monkeypatch.delenv(ENV_WORKER_QUEUES, raising=False)
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=True)
        try:
            assert queue.queue_mode == QUEUE_MODE_SINGLE
            assert queue.gpu_executor is None
        finally:
            queue.shutdown()

    def test_split_mode_creates_gpu_pool(self, tmp_path, monkeypatch):
        monkeypatch.setenv(ENV_WORKER_QUEUES, "split")
        monkeypatch.setenv(ENV_GPU_WORKERS, "2")
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=True)
        try:
            assert queue.queue_mode == QUEUE_MODE_SPLIT
            assert queue.gpu_max_workers == 2
            assert queue.gpu_executor is not None
            assert queue.gpu_executor._max_workers == 2
        finally:
            queue.shutdown()

    def test_constructor_overrides_env(self, tmp_path):
        queue = LocalTaskQueue(
            storage_dir=tmp_path,
            auto_start=True,
            queue_mode="split",
            gpu_max_workers=3,
        )
        try:
            assert queue.queue_mode == QUEUE_MODE_SPLIT
            assert queue.gpu_max_workers == 3
        finally:
            queue.shutdown()

    def test_unknown_constructor_mode_falls_back_to_single(self, tmp_path):
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=True, queue_mode="weird")
        try:
            assert queue.queue_mode == QUEUE_MODE_SINGLE
            assert queue.gpu_executor is None
        finally:
            queue.shutdown()


class TestSplitExecution:
    def test_gpu_task_routes_to_gpu_pool(self, tmp_path, fast_pipeline, caplog):
        queue = LocalTaskQueue(
            storage_dir=tmp_path, auto_start=True, queue_mode="split"
        )
        try:
            with caplog.at_level(logging.INFO, logger="movie_narrator.cloud.queue"):
                task_id = queue.submit(
                    TaskRequest(
                        movie_name="GPU Movie",
                        params={"render_encoder": "nvenc"},
                        plan="pro",
                    )
                )
                result = queue.wait(task_id, timeout=30)
            assert result is not None
            routes = [
                r for r in caplog.records if getattr(r, "event", "") == "queue_route"
            ]
            assert len(routes) == 1
            assert routes[0].pool == "gpu"
            assert routes[0].task_id == task_id
        finally:
            queue.shutdown()

    def test_cpu_task_routes_to_cpu_pool_in_split_mode(
        self, tmp_path, fast_pipeline, caplog
    ):
        queue = LocalTaskQueue(
            storage_dir=tmp_path, auto_start=True, queue_mode="split"
        )
        try:
            with caplog.at_level(logging.INFO, logger="movie_narrator.cloud.queue"):
                task_id = queue.submit(
                    TaskRequest(movie_name="CPU Movie", params={"render_encoder": "cpu"})
                )
                result = queue.wait(task_id, timeout=30)
            assert result is not None
            routes = [
                r for r in caplog.records if getattr(r, "event", "") == "queue_route"
            ]
            # CPU routes log at DEBUG — not captured at INFO level.
            assert routes == []
        finally:
            queue.shutdown()

    def test_split_pools_run_fake_tasks_to_completion(self, tmp_path, fast_pipeline):
        """Mixed GPU/CPU submissions all complete and both pools are used."""
        queue = LocalTaskQueue(
            storage_dir=tmp_path,
            auto_start=True,
            queue_mode="split",
            max_workers=2,
        )
        try:
            gpu_ids = [
                queue.submit(
                    TaskRequest(
                        movie_name=f"GPU {i}",
                        params={"render_encoder": "nvenc"},
                        plan="pro",
                    )
                )
                for i in range(2)
            ]
            cpu_ids = [
                queue.submit(TaskRequest(movie_name=f"CPU {i}")) for i in range(2)
            ]
            for tid in gpu_ids + cpu_ids:
                result = queue.wait(tid, timeout=60)
                assert result is not None, f"task {tid} did not finish"
            assert queue.active_count == 0
        finally:
            queue.shutdown()

    def test_single_mode_logs_no_gpu_pool(self, tmp_path, fast_pipeline, caplog):
        """Default layout: zero behaviour change, no gpu pool exists."""
        queue = LocalTaskQueue(storage_dir=tmp_path, auto_start=True)
        try:
            with caplog.at_level(logging.INFO, logger="movie_narrator.cloud.queue"):
                task_id = queue.submit(
                    TaskRequest(
                        movie_name="Plain", params={"render_encoder": "nvenc"}, plan="pro"
                    )
                )
                result = queue.wait(task_id, timeout=30)
            assert result is not None
            routes = [
                r for r in caplog.records if getattr(r, "event", "") == "queue_route"
            ]
            assert routes == []  # cpu-pool routing is DEBUG-only
            assert queue.gpu_executor is None
        finally:
            queue.shutdown()


class TestSplitShutdown:
    def test_shutdown_drains_both_pools(self, tmp_path, fast_pipeline):
        queue = LocalTaskQueue(
            storage_dir=tmp_path,
            auto_start=True,
            queue_mode="split",
            max_workers=1,
            gpu_max_workers=1,
        )
        gpu_id = queue.submit(
            TaskRequest(movie_name="GPU", params={"render_encoder": "nvenc"}, plan="pro")
        )
        cpu_id = queue.submit(TaskRequest(movie_name="CPU"))
        queue.shutdown(wait=True, timeout=60)
        assert queue.get_task(gpu_id).is_terminal
        assert queue.get_task(cpu_id).is_terminal
        assert queue.is_started is False

    def test_shutdown_no_drain_cancels_both_pools(self, tmp_path):
        queue = LocalTaskQueue(
            storage_dir=tmp_path, auto_start=True, queue_mode="split"
        )
        queue.shutdown(wait=False)
        assert queue.is_started is False
        assert queue.gpu_executor is None
        assert queue._executor is None

    def test_submit_after_split_shutdown_rejected(self, tmp_path):
        queue = LocalTaskQueue(
            storage_dir=tmp_path, auto_start=True, queue_mode="split"
        )
        queue.shutdown()
        from movie_narrator.cloud.queue import QueueShutdownError

        with pytest.raises(QueueShutdownError):
            queue.submit(TaskRequest(movie_name="Late"))
