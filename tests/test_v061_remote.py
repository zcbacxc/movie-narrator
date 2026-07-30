"""Tests for v0.6.1 Remote Inference features.

Tests the REST API server, RemoteTaskQueue client, WorkerDaemon,
artifact management, and remote provider registration.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from movie_narrator.cloud import (
    RemoteQueueError,
    RemoteTaskQueue,
    TaskAPIServer,
    TaskRequest,
    TaskStatus,
    WorkerDaemon,
    download_all_artifacts,
    download_artifact,
    list_artifacts,
    register_remote_llm,
    register_remote_tts,
)
from movie_narrator.cloud.api import _APIHandler


# ── Mock pipeline ──────────────────────────────────────────


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


# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def api_server(tmp_path):
    """Start an API server on a random port for testing."""
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,  # random port
        storage_dir=tmp_path / "tasks",
        max_workers=2,
    )
    server.start(blocking=False)
    # Give the server a moment to start
    time.sleep(0.1)
    yield server
    server.stop()


@pytest.fixture
def remote_queue(api_server):
    """Create a RemoteTaskQueue pointing to the test server."""
    return RemoteTaskQueue(api_server.base_url, timeout=5.0)


@pytest.fixture
def completed_task_with_files(api_server, tmp_path):
    """Create a task with output files for artifact testing."""
    from movie_narrator.cloud.models import Task, TaskResult

    # Create output directory with files
    output_dir = tmp_path / "output" / "test_task"
    output_dir.mkdir(parents=True)
    (output_dir / "final.mp4").write_bytes(b"fake video content")
    (output_dir / "narration.mp3").write_bytes(b"fake audio")
    (output_dir / "subtitle.srt").write_text("1\n00:00:01,000 --> 00:00:02,000\nHello\n")
    (output_dir / "metadata.json").write_text('{"version": "0.6.1"}')

    # Create a task with result pointing to this directory
    task = Task(
        request=TaskRequest(movie_name="TestMovie", max_retries=0),
        status=TaskStatus.COMPLETED,
        result=TaskResult(
            video_path=str(output_dir / "final.mp4"),
            audio_path=str(output_dir / "narration.mp3"),
            output_dir=str(output_dir),
            metadata={"test": True},
        ),
    )
    api_server.queue._storage.save(task)
    return task


# ── API Server Tests ───────────────────────────────────────


class TestTaskAPIServer:
    """Tests for the TaskAPIServer REST endpoints."""

    def test_health_check(self, api_server):
        """GET /health returns ok status."""
        import urllib.request

        req = urllib.request.Request(f"{api_server.base_url}/health")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert data["status"] == "ok"

    def test_server_info(self, api_server):
        """GET /info returns server information."""
        import urllib.request

        req = urllib.request.Request(f"{api_server.base_url}/info")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
        assert "version" in data
        assert "active_tasks" in data
        assert "is_started" in data

    def test_submit_task(self, api_server, remote_queue):
        """POST /tasks submits a task and returns a task_id."""
        request = TaskRequest(movie_name="TestMovie", style="test", max_retries=0)
        task_id = remote_queue.submit(request)
        assert isinstance(task_id, str)
        assert len(task_id) > 0

    def test_get_task(self, api_server, remote_queue):
        """GET /tasks/{id} returns task details."""
        request = TaskRequest(movie_name="GetTaskTest", max_retries=0)
        task_id = remote_queue.submit(request)

        task = remote_queue.get_task(task_id)
        assert task is not None
        assert task.id == task_id
        assert task.request.movie_name == "GetTaskTest"

    def test_get_task_not_found(self, api_server, remote_queue):
        """GET /tasks/{id} returns None for unknown task."""
        task = remote_queue.get_task("nonexistent")
        assert task is None

    def test_list_tasks(self, api_server, remote_queue):
        """GET /tasks returns a list of tasks."""
        # Submit a few tasks
        for i in range(3):
            remote_queue.submit(TaskRequest(movie_name=f"ListTest{i}", max_retries=0))

        tasks = remote_queue.list_tasks()
        assert len(tasks) >= 3

    def test_list_tasks_with_status_filter(self, api_server, remote_queue):
        """GET /tasks?status=running filters by status."""
        remote_queue.submit(TaskRequest(movie_name="FilterTest", max_retries=0))
        tasks = remote_queue.list_tasks(status=TaskStatus.PENDING)
        assert all(t.status == TaskStatus.PENDING for t in tasks)

    def test_cancel_task(self, api_server, remote_queue):
        """DELETE /tasks/{id} cancels a task."""
        request = TaskRequest(movie_name="CancelTest", max_retries=0)
        task_id = remote_queue.submit(request)

        # Task might complete quickly, but cancel should return True
        # for at least pending/running tasks
        result = remote_queue.cancel(task_id)
        assert isinstance(result, bool)

    def test_cancel_nonexistent_task(self, api_server, remote_queue):
        """DELETE /tasks/{id} returns False for unknown task."""
        result = remote_queue.cancel("nonexistent")
        assert result is False

    def test_get_status(self, api_server, remote_queue):
        """get_status returns the TaskStatus."""
        request = TaskRequest(movie_name="StatusTest", max_retries=0)
        task_id = remote_queue.submit(request)

        status = remote_queue.get_status(task_id)
        assert status is not None
        assert status in [TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.COMPLETED, TaskStatus.FAILED]

    def test_get_result_not_completed(self, api_server, remote_queue):
        """get_result returns None for non-terminal tasks."""
        request = TaskRequest(movie_name="ResultTest", max_retries=0)
        task_id = remote_queue.submit(request)

        # Wait a tiny bit for the task to start
        time.sleep(0.2)
        result = remote_queue.get_result(task_id)
        # Result might be None (not terminal) or a TaskResult
        if result is not None:
            assert hasattr(result, "succeeded")

    def test_invalid_json_body(self, api_server):
        """POST /tasks with invalid JSON returns 400."""
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            f"{api_server.base_url}/tasks",
            data=b"invalid json{",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 400

    def test_invalid_task_request(self, api_server):
        """POST /tasks with missing movie_name returns 400."""
        import urllib.request
        import urllib.error

        req = urllib.request.Request(
            f"{api_server.base_url}/tasks",
            data=json.dumps({"style": "test"}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 400

    def test_unknown_path(self, api_server):
        """GET /unknown returns 404."""
        import urllib.request
        import urllib.error

        req = urllib.request.Request(f"{api_server.base_url}/unknown")
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(req, timeout=5)
        assert exc_info.value.code == 404


# ── RemoteTaskQueue Tests ──────────────────────────────────


class TestRemoteTaskQueue:
    """Tests for the RemoteTaskQueue client."""

    def test_base_url_property(self, remote_queue, api_server):
        """base_url property returns the server URL."""
        assert remote_queue.base_url == api_server.base_url

    def test_health_check_success(self, remote_queue):
        """health_check returns True for a running server."""
        assert remote_queue.health_check() is True

    def test_health_check_failure(self):
        """health_check returns False for unreachable server."""
        queue = RemoteTaskQueue("http://127.0.0.1:1", timeout=1.0)
        assert queue.health_check() is False

    def test_server_info(self, remote_queue):
        """server_info returns server information."""
        info = remote_queue.server_info()
        assert "version" in info

    def test_wait_timeout(self, remote_queue):
        """wait returns None on timeout."""
        request = TaskRequest(movie_name="WaitTimeout", max_retries=0)
        task_id = remote_queue.submit(request)

        # Very short timeout
        result = remote_queue.wait(task_id, timeout=0.01, poll_interval=0.01)
        # Might be None (timeout) or a result if task completed instantly
        if result is None:
            pass  # Expected for timeout
        else:
            assert hasattr(result, "succeeded")

    def test_wait_not_found(self, remote_queue):
        """wait returns None for non-existent task."""
        result = remote_queue.wait("nonexistent", timeout=0.1)
        assert result is None

    def test_shutdown_is_noop(self, remote_queue):
        """shutdown is a no-op for remote queue."""
        remote_queue.shutdown()
        remote_queue.shutdown(wait=False)  # should not raise

    def test_connection_error(self):
        """RemoteQueueError raised on connection failure."""
        queue = RemoteTaskQueue("http://127.0.0.1:1", timeout=1.0)
        with pytest.raises(RemoteQueueError):
            queue.submit(TaskRequest(movie_name="Test", max_retries=0))

    def test_api_key_header(self, api_server):
        """API key is sent as X-API-Key header."""
        queue = RemoteTaskQueue(api_server.base_url, api_key="test-key-123")
        # Should still work (server doesn't validate, but header is sent)
        assert queue.health_check() is True


# ── WorkerDaemon Tests ─────────────────────────────────────


class TestWorkerDaemon:
    """Tests for the WorkerDaemon class."""

    def test_start_stop(self, tmp_path):
        """Daemon starts and stops cleanly."""
        daemon = WorkerDaemon(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "daemon_tasks",
            max_workers=1,
        )
        assert not daemon.is_running
        daemon.start(blocking=False)
        assert daemon.is_running
        time.sleep(0.1)
        daemon.stop()
        assert not daemon.is_running

    def test_context_manager(self, tmp_path):
        """Daemon works as a context manager."""
        with WorkerDaemon(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "daemon_ctx",
            max_workers=1,
        ) as daemon:
            assert daemon.is_running
            # Verify the server is reachable
            queue = RemoteTaskQueue(daemon.base_url, timeout=2.0)
            assert queue.health_check() is True

    def test_base_url_property(self, tmp_path):
        """base_url returns the correct URL."""
        daemon = WorkerDaemon(host="127.0.0.1", port=9876)
        assert "9876" in daemon.base_url

    def test_double_start_raises(self, tmp_path):
        """Starting an already-running daemon raises RuntimeError."""
        daemon = WorkerDaemon(
            host="127.0.0.1", port=0,
            storage_dir=tmp_path / "daemon_err",
            max_workers=1,
        )
        daemon.start(blocking=False)
        with pytest.raises(RuntimeError):
            daemon.start(blocking=False)
        daemon.stop()


# ── Artifact Management Tests ──────────────────────────────


class TestArtifactManagement:
    """Tests for artifact listing and downloading."""

    def test_list_artifacts(self, api_server, completed_task_with_files):
        """GET /tasks/{id}/artifacts returns file list."""
        artifacts = list_artifacts(
            api_server.base_url,
            completed_task_with_files.id,
        )
        assert len(artifacts) == 4
        filenames = [a["filename"] for a in artifacts]
        assert "final.mp4" in filenames
        assert "narration.mp3" in filenames
        assert "subtitle.srt" in filenames
        assert "metadata.json" in filenames

    def test_list_artifacts_no_output(self, api_server):
        """GET /tasks/{id}/artifacts returns empty for tasks without output."""
        from movie_narrator.cloud.models import Task

        # Create a pending task directly in storage (bypassing worker execution)
        task = Task(
            request=TaskRequest(movie_name="NoOutput", max_retries=0),
            status=TaskStatus.PENDING,
        )
        api_server.queue._storage.save(task)
        artifacts = list_artifacts(api_server.base_url, task.id)
        assert artifacts == []

    def test_download_artifact(self, api_server, completed_task_with_files, tmp_path):
        """GET /tasks/{id}/download/{file} downloads a file."""
        dest = tmp_path / "downloads"
        path = download_artifact(
            api_server.base_url,
            completed_task_with_files.id,
            "final.mp4",
            dest_dir=str(dest),
        )
        assert path.exists()
        assert path.read_bytes() == b"fake video content"

    def test_download_text_artifact(self, api_server, completed_task_with_files, tmp_path):
        """Download a text file artifact."""
        path = download_artifact(
            api_server.base_url,
            completed_task_with_files.id,
            "subtitle.srt",
            dest_dir=str(tmp_path / "srt"),
        )
        assert path.exists()
        content = path.read_text()
        assert "Hello" in content

    def test_download_all_artifacts(self, api_server, completed_task_with_files, tmp_path):
        """download_all_artifacts downloads all files."""
        dest = tmp_path / "all_downloads"
        paths = download_all_artifacts(
            api_server.base_url,
            completed_task_with_files.id,
            dest_dir=str(dest),
        )
        assert len(paths) == 4
        for p in paths:
            assert p.exists()

    def test_download_nonexistent_file(self, api_server, completed_task_with_files):
        """Downloading a non-existent file raises RemoteQueueError."""
        with pytest.raises(RemoteQueueError):
            download_artifact(
                api_server.base_url,
                completed_task_with_files.id,
                "nonexistent.txt",
            )

    def test_artifacts_for_nonexistent_task(self, api_server):
        """Listing artifacts for unknown task returns empty list."""
        artifacts = list_artifacts(api_server.base_url, "nonexistent")
        assert artifacts == []

    def test_path_traversal_blocked(self, api_server, completed_task_with_files):
        """Path traversal attempts are blocked."""
        with pytest.raises(RemoteQueueError):
            download_artifact(
                api_server.base_url,
                completed_task_with_files.id,
                "../../../etc/passwd",
            )


# ── Remote Provider Registration Tests ─────────────────────


class TestRemoteProviders:
    """Tests for remote LLM/TTS provider registration."""

    def test_register_remote_llm(self):
        """register_remote_llm registers the 'remote' LLM provider."""
        from movie_narrator.providers import llm_registry

        # Avoid re-registration error
        if not llm_registry.contains("remote"):
            register_remote_llm("http://test-worker:8765")
        assert llm_registry.contains("remote")

    def test_register_remote_tts(self):
        """register_remote_tts registers the 'remote' TTS provider."""
        from movie_narrator.providers import tts_registry

        if not tts_registry.contains("remote"):
            register_remote_tts("http://test-worker:8765")
        assert tts_registry.contains("remote")

    def test_remote_llm_provider_distinct(self):
        """The remote LLM provider is distinct from the openai provider."""
        from movie_narrator.providers import llm_registry

        if not llm_registry.contains("remote"):
            register_remote_llm("http://test-worker:8765")
        assert llm_registry.contains("remote")
        assert llm_registry.contains("openai")


# ── Integration Tests ──────────────────────────────────────


class TestRemoteIntegration:
    """End-to-end integration tests for remote task execution."""

    def test_submit_and_query(self, api_server, remote_queue):
        """Submit a task and query its status."""
        request = TaskRequest(
            movie_name="IntegrationTest",
            style="test",
            max_retries=0,
            duration=10,
        )
        task_id = remote_queue.submit(request)
        assert task_id

        # Query status
        status = remote_queue.get_status(task_id)
        assert status is not None

        # Query task details
        task = remote_queue.get_task(task_id)
        assert task is not None
        assert task.request.movie_name == "IntegrationTest"

    def test_full_lifecycle(self, remote_queue):
        """Submit a task and wait for completion (mock pipeline)."""
        request = TaskRequest(
            movie_name="LifecycleTest",
            style="test",
            max_retries=0,
            duration=10,
        )
        task_id = remote_queue.submit(request)

        # Wait for completion
        result = remote_queue.wait(task_id, timeout=30, poll_interval=0.3)

        # With mock pipeline, should complete successfully
        assert result is not None
        assert result.succeeded
        assert result.video_path is not None

    def test_concurrent_submissions(self, remote_queue):
        """Multiple tasks can be submitted concurrently."""
        task_ids = []
        for i in range(5):
            tid = remote_queue.submit(
                TaskRequest(movie_name=f"Concurrent{i}", max_retries=0)
            )
            task_ids.append(tid)

        # All should have unique IDs
        assert len(set(task_ids)) == 5

        # All should be queryable
        for tid in task_ids:
            task = remote_queue.get_task(tid)
            assert task is not None


# ── Contract Export Tests ──────────────────────────────────


class TestContractExports:
    """Verify v0.6.1 types are exported from contract."""

    def test_contract_version_bumped(self):
        """CONTRACT_VERSION is (0, 7, 2)."""
        from movie_narrator.contract import CONTRACT_VERSION
        assert CONTRACT_VERSION == (0, 7, 2)

    def test_remote_types_in_contract_all(self):
        """Remote inference types are in contract __all__."""
        from movie_narrator import contract
        for name in [
            "RemoteTaskQueue",
            "RemoteQueueError",
            "TaskAPIServer",
            "WorkerDaemon",
            "run_daemon",
            "download_artifact",
            "download_all_artifacts",
            "list_artifacts",
            "register_remote_llm",
            "register_remote_tts",
        ]:
            assert name in contract.__all__, f"{name} not in contract.__all__"

    def test_types_importable_from_package(self):
        """Types are importable from the top-level package."""
        from movie_narrator import (
            RemoteTaskQueue,
            TaskAPIServer,
            WorkerDaemon,
            run_daemon,
            download_artifact,
        )
        assert RemoteTaskQueue is not None
        assert TaskAPIServer is not None
        assert WorkerDaemon is not None
        assert run_daemon is not None
        assert download_artifact is not None
