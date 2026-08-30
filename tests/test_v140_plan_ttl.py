# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.4.0 Feature 4: plan artifact TTL wired into the lifecycle.

Covers:
- ``effective_ttl_seconds`` (plan narrows policy: min of the finite pair)
- worker records ``artifact_retention`` on the run's artifact metadata
  and emits the structured ``artifact_ttl_narrowed`` log only when the
  plan actually narrows the policy default
- ``cleanup_artifacts`` / ``ArtifactSweeper`` honour the per-artifact
  ``ttl_for`` override
- the API server's sweeper resolver maps ``<task_id>/<file>`` keys to
  the owning task's plan (free narrows, pro narrows less, default and
  unresolvable keys keep the uniform policy TTL)
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, List, Optional


from movie_narrator.cloud import CancelController, TaskAPIServer, run_task
from movie_narrator.cloud.artifact_store import LocalArtifactStore
from movie_narrator.cloud.lifecycle import (
    ArtifactLifecyclePolicy,
    ArtifactSweeper,
    CleanupReport,
    cleanup_artifacts,
    effective_ttl_seconds,
)
from movie_narrator.cloud.models import Task, TaskRequest, TaskStatus
from movie_narrator.cloud.storage import TaskStorage


# ── Helpers ────────────────────────────────────────────────


class _CtxCapture:
    def __init__(self) -> None:
        self.contexts: List[Any] = []

    def __call__(self, ctx, **kwargs):
        self.contexts.append(ctx)
        Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
        ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
        return ctx


def _run_plan_task(monkeypatch, tmp_path, plan: Optional[str]):
    """Run a task through the worker with a mocked pipeline."""
    monkeypatch.setenv("CI", "1")
    capture = _CtxCapture()
    monkeypatch.setattr("movie_narrator.cloud.worker.run_pipeline", capture)
    request = TaskRequest(movie_name="TtlWorker", output_dir=str(tmp_path / "out"))
    task = Task(request=request, plan=plan or "default")
    finished = run_task(task, CancelController())
    return finished, capture.contexts


def _age_file(path: Path, hours: float) -> None:
    stamp = time.time() - hours * 3600.0
    os.utime(path, (stamp, stamp))


# ════════════════════════════════════════════════════════════
#  effective_ttl_seconds
# ════════════════════════════════════════════════════════════


class TestEffectiveTtlSeconds:
    def test_free_plan_narrows_weeklong_policy(self):
        assert effective_ttl_seconds(24.0, 7 * 86400) == 24 * 3600

    def test_pro_plan_narrows_weeklong_policy(self):
        assert effective_ttl_seconds(72.0, 7 * 86400) == 72 * 3600

    def test_tighter_policy_wins(self):
        assert effective_ttl_seconds(72.0, 3600) == 3600

    def test_disabled_policy_yields_plan_ttl(self):
        assert effective_ttl_seconds(24.0, 0) == 24 * 3600

    def test_no_plan_ttl_yields_policy(self):
        assert effective_ttl_seconds(None, 3600) == 3600

    def test_neither_layer_expires(self):
        assert effective_ttl_seconds(None, 0) == 0

    def test_non_positive_plan_ttl_means_no_limit(self):
        assert effective_ttl_seconds(0.0, 7200) == 7200
        assert effective_ttl_seconds(-5.0, 7200) == 7200


# ════════════════════════════════════════════════════════════
#  Worker: retention recorded + structured log
# ════════════════════════════════════════════════════════════


class TestWorkerRetention:
    def test_free_plan_records_retention_and_narrowing(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("MN_ARTIFACT_TTL", str(7 * 86400))  # 7 days
        caplog.set_level(logging.INFO, logger="movie_narrator.cloud.worker")
        task, ctxs = _run_plan_task(monkeypatch, tmp_path, plan="free")

        assert task.status.value == "completed"
        retention = task.result.metadata["artifact_retention"]
        assert retention == {
            "plan_ttl_hours": 24.0,
            "policy_ttl_seconds": 7 * 86400,
            "effective_ttl_seconds": 24 * 3600,
        }
        records = [
            r
            for r in caplog.records
            if getattr(r, "event", "") == "artifact_ttl_narrowed"
        ]
        assert len(records) == 1
        assert records[0].plan == "free"
        assert records[0].effective_ttl_seconds == 24 * 3600

    def test_tighter_policy_is_not_narrowed_by_plan(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("MN_ARTIFACT_TTL", "3600")  # 1 hour < pro's 72 h
        caplog.set_level(logging.INFO, logger="movie_narrator.cloud.worker")
        task, _ = _run_plan_task(monkeypatch, tmp_path, plan="pro")

        retention = task.result.metadata["artifact_retention"]
        assert retention["effective_ttl_seconds"] == 3600
        assert retention["plan_ttl_hours"] == 72.0
        assert not [
            r
            for r in caplog.records
            if getattr(r, "event", "") == "artifact_ttl_narrowed"
        ]

    def test_disabled_policy_logs_narrowing_from_infinity(self, monkeypatch, tmp_path, caplog):
        monkeypatch.delenv("MN_ARTIFACT_TTL", raising=False)
        caplog.set_level(logging.INFO, logger="movie_narrator.cloud.worker")
        task, _ = _run_plan_task(monkeypatch, tmp_path, plan="free")
        retention = task.result.metadata["artifact_retention"]
        assert retention == {
            "plan_ttl_hours": 24.0,
            "policy_ttl_seconds": 0,
            "effective_ttl_seconds": 24 * 3600,
        }
        assert [
            r for r in caplog.records if getattr(r, "event", "") == "artifact_ttl_narrowed"
        ]

    def test_default_plan_is_untouched(self, monkeypatch, tmp_path, caplog):
        monkeypatch.setenv("MN_ARTIFACT_TTL", str(7 * 86400))
        caplog.set_level(logging.INFO, logger="movie_narrator.cloud.worker")
        task, _ = _run_plan_task(monkeypatch, tmp_path, plan="default")
        assert "artifact_retention" not in task.result.metadata
        assert not [
            r for r in caplog.records if getattr(r, "event", "") == "artifact_ttl_narrowed"
        ]


# ════════════════════════════════════════════════════════════
#  Sweeper honours the per-artifact TTL
# ════════════════════════════════════════════════════════════


class TestSweeperPerArtifactTtl:
    def _store(self, tmp_path: Path, ages: dict) -> LocalArtifactStore:
        store = LocalArtifactStore(tmp_path)
        for key, hours in ages.items():
            path = tmp_path / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
            _age_file(path, hours)
        return store

    def test_cleanup_artifacts_ttl_for_override(self, tmp_path):
        store = self._store(tmp_path, {"t1/final.mp4": 25.0, "t2/final.mp4": 25.0})
        policy = ArtifactLifecyclePolicy(ttl_seconds=7 * 86400)  # 7 days
        ttl_for = lambda info: 24 * 3600 if info.key.startswith("t1/") else 0  # noqa: E731

        report = cleanup_artifacts(store, policy, ttl_for=ttl_for)

        assert report.deleted == ["t1/final.mp4"]
        assert store.stat("t2/final.mp4").size > 0

    def test_cleanup_artifacts_without_override_is_uniform(self, tmp_path):
        store = self._store(tmp_path, {"t1/final.mp4": 25.0, "t2/final.mp4": 25.0})
        policy = ArtifactLifecyclePolicy(ttl_seconds=7 * 86400)

        report = cleanup_artifacts(store, policy)

        assert report.deleted == []
        assert store.stat("t1/final.mp4").size > 0

    def test_resolver_failure_falls_back_to_policy(self, tmp_path):
        store = self._store(tmp_path, {"t1/final.mp4": 25.0})

        def _boom(info):
            raise RuntimeError("resolver down")

        report = cleanup_artifacts(
            store, ArtifactLifecyclePolicy(ttl_seconds=7 * 86400), ttl_for=_boom
        )
        assert report.deleted == []

    def test_sweeper_uses_ttl_for(self, tmp_path):
        store = self._store(tmp_path, {"t1/final.mp4": 25.0, "t2/final.mp4": 25.0})
        sweeper = ArtifactSweeper(
            store,
            ArtifactLifecyclePolicy(ttl_seconds=7 * 86400),
            ttl_for=lambda info: (24 * 3600 if info.key.startswith("t1/") else 0),
        )
        report = sweeper.sweep_once()
        assert isinstance(report, CleanupReport)
        assert report.deleted == ["t1/final.mp4"]


# ════════════════════════════════════════════════════════════
#  API server: plan-aware sweeper resolver
# ════════════════════════════════════════════════════════════


class TestServerPlanTtlResolver:
    def test_plan_ttls_narrow_policy_per_task(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CI", "1")
        store = LocalArtifactStore(tmp_path / "store")
        ages = {
            "freetask/final.mp4": 25.0,  # free plan (24 h) → deleted
            "protask/final.mp4": 25.0,  # pro plan (72 h) → survives
            "defaulttask/final.mp4": 25.0,  # default plan → policy (7 d)
            "notask/final.mp4": 25.0,  # unresolvable → policy (7 d)
            "freetask/old.mp4": (72.0 * 24.0) + 1.0,  # beyond pro too → gone
        }
        for key, hours in ages.items():
            path = tmp_path / "store" / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
            _age_file(path, hours)

        storage = TaskStorage(tmp_path / "tasks")
        for tid, plan in (
            ("freetask", "free"),
            ("protask", "pro"),
            ("defaulttask", "default"),
        ):
            storage.save(
                Task(
                    id=tid,
                    request=TaskRequest(movie_name=tid),
                    plan=plan,
                    status=TaskStatus.COMPLETED,
                )
            )

        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "tasks",
            artifact_store=store,
            artifact_policy=ArtifactLifecyclePolicy(ttl_seconds=7 * 86400),
        )
        server._start_artifact_sweeper()
        assert server.sweeper is not None
        # The sweeper loop sweeps once immediately on start; stop() joins
        # that first sweep before returning, so the filesystem assertions
        # below are deterministic.
        server.sweeper.stop()
        server.sweeper.sweep_once()  # idempotent second pass
        server._sweeper = None

        # free plan (24 h) narrows the 7-day policy → both artifacts gone
        assert not (tmp_path / "store" / "freetask" / "final.mp4").exists()
        assert not (tmp_path / "store" / "freetask" / "old.mp4").exists()
        # pro plan (72 h): 25 h-old artifact survives the narrowed TTL
        assert store.stat("protask/final.mp4").size > 0
        # default plan: uniform policy TTL (7 days) untouched
        assert store.stat("defaulttask/final.mp4").size > 0
        # unresolvable key head: uniform policy TTL
        assert store.stat("notask/final.mp4").size > 0

    def test_sweeper_not_started_without_policy(self, tmp_path, monkeypatch):
        monkeypatch.delenv("MN_ARTIFACT_TTL", raising=False)
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "tasks",
            artifact_store=LocalArtifactStore(tmp_path / "store"),
        )
        server._start_artifact_sweeper()
        assert server.sweeper is None
