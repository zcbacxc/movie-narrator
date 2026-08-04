# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.8.3: storage backend abstraction (``artifact_store``).

Covers:
- ``normalize_key`` traversal rejection (absolute, ``..``, drive letters)
- ``LocalArtifactStore`` round-trip put/get/open/list/stat/delete/exists
- traversal rejection through the store surface (incl. symlink escape)
- ``StorageBackend`` protocol conformance for both backends
- ``S3ArtifactStore`` against an injected fake boto3 client
- ``get_artifact_store`` / ``get_task_artifact_store`` env resolution
- REST API artifact endpoints still behave exactly as in v0.6.1
"""

from __future__ import annotations

import importlib.util
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from movie_narrator.cloud import TaskAPIServer
from movie_narrator.cloud.artifact_store import (
    ArtifactInfo,
    ArtifactNotFoundError,
    ArtifactStoreError,
    LocalArtifactStore,
    S3ArtifactStore,
    StorageBackend,
    UnsafeKeyError,
    artifact_location,
    get_artifact_store,
    get_task_artifact_store,
    join_prefix,
    normalize_key,
)
from movie_narrator.cloud.models import Task, TaskRequest, TaskResult, TaskStatus

_HAS_BOTO3 = importlib.util.find_spec("boto3") is not None


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def store(tmp_path) -> LocalArtifactStore:
    """A local artifact store rooted in a temp directory."""
    return LocalArtifactStore(tmp_path / "artifacts")


@pytest.fixture
def source_file(tmp_path) -> Path:
    """A small local file to upload."""
    path = tmp_path / "src" / "final.mp4"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"fake video content")
    return path


# ── Fake boto3 client ─────────────────────────────────────


class FakeS3Client:
    """In-memory stand-in for ``boto3.client('s3')``.

    Only the handful of operations ``S3ArtifactStore`` uses are
    implemented. Keeping the double here means the S3 backend is fully
    exercised without boto3, credentials, or network access.
    """

    def __init__(self) -> None:
        self.objects: Dict[str, bytes] = {}
        self.times: Dict[str, datetime] = {}
        self.calls: List[str] = []

    # ── helpers ────────────────────────────────────────────

    def _require(self, key: str) -> bytes:
        if key not in self.objects:
            raise FakeClientError(f"An error occurred (404) NoSuchKey: {key}")
        return self.objects[key]

    def seed(self, key: str, data: bytes, modified: Optional[datetime] = None) -> None:
        self.objects[key] = data
        self.times[key] = modified or datetime.now(timezone.utc)

    # ── boto3 surface ──────────────────────────────────────

    def upload_file(self, filename: str, bucket: str, key: str) -> None:
        self.calls.append(f"upload_file:{key}")
        self.seed(key, Path(filename).read_bytes())

    def download_file(self, bucket: str, key: str, filename: str) -> None:
        self.calls.append(f"download_file:{key}")
        Path(filename).write_bytes(self._require(key))

    def get_object(self, Bucket: str, Key: str) -> Dict[str, Any]:  # noqa: N803
        self.calls.append(f"get_object:{Key}")
        import io

        return {"Body": io.BytesIO(self._require(Key))}

    def head_object(self, Bucket: str, Key: str) -> Dict[str, Any]:  # noqa: N803
        self.calls.append(f"head_object:{Key}")
        data = self._require(Key)
        return {
            "ContentLength": len(data),
            "LastModified": self.times[Key],
            "ETag": '"deadbeef"',
        }

    def delete_object(self, Bucket: str, Key: str) -> Dict[str, Any]:  # noqa: N803
        self.calls.append(f"delete_object:{Key}")
        self.objects.pop(Key, None)
        self.times.pop(Key, None)
        return {}

    def list_objects_v2(self, **kwargs: Any) -> Dict[str, Any]:
        prefix = kwargs.get("Prefix", "")
        self.calls.append(f"list_objects_v2:{prefix}")
        contents = [
            {
                "Key": key,
                "Size": len(data),
                "LastModified": self.times[key],
                "ETag": '"deadbeef"',
            }
            for key, data in sorted(self.objects.items())
            if key.startswith(prefix)
        ]
        return {"Contents": contents, "IsTruncated": False}

    def generate_presigned_url(  # noqa: N803
        self, op: str, Params: Dict[str, Any], ExpiresIn: int
    ) -> str:
        return f"https://example.invalid/{Params['Key']}?exp={ExpiresIn}"


class FakeClientError(Exception):
    """Stands in for ``botocore.exceptions.ClientError``."""


# ════════════════════════════════════════════════════════════
#  Key normalization / traversal
# ════════════════════════════════════════════════════════════


class TestKeyNormalization:
    """Tests for ``normalize_key`` — the shared traversal guard."""

    @pytest.mark.parametrize(
        "key,expected",
        [
            ("final.mp4", "final.mp4"),
            ("clips/001.mp4", "clips/001.mp4"),
            ("./final.mp4", "final.mp4"),
            ("clips//001.mp4", "clips/001.mp4"),
            ("clips\\001.mp4", "clips/001.mp4"),
            ("  final.mp4  ", "final.mp4"),
        ],
    )
    def test_accepts_relative_keys(self, key, expected):
        """Relative keys are normalized to POSIX form."""
        assert normalize_key(key) == expected

    @pytest.mark.parametrize(
        "key",
        [
            "",
            "   ",
            "..",
            "../secrets",
            "../../../etc/passwd",
            "clips/../../etc/passwd",
            "/etc/passwd",
            "/",
            "\\windows\\system32",
            "C:/Windows/system32",
            "c:\\Windows",
            ".",
        ],
    )
    def test_rejects_unsafe_keys(self, key):
        """Absolute paths, drive letters and ``..`` segments are refused."""
        with pytest.raises(UnsafeKeyError):
            normalize_key(key)

    def test_rejects_non_string(self):
        """A non-string key is refused rather than coerced."""
        with pytest.raises(UnsafeKeyError):
            normalize_key(None)  # type: ignore[arg-type]

    def test_join_prefix(self):
        """join_prefix concatenates cleanly in both directions."""
        assert join_prefix("", "a.mp4") == "a.mp4"
        assert join_prefix("runs", "a.mp4") == "runs/a.mp4"
        assert join_prefix("runs/", "/a.mp4") == "runs/a.mp4"
        assert join_prefix("runs", "") == "runs"


# ════════════════════════════════════════════════════════════
#  LocalArtifactStore
# ════════════════════════════════════════════════════════════


class TestLocalArtifactStore:
    """Round-trip and semantics of the default filesystem backend."""

    def test_satisfies_protocol(self, store):
        """LocalArtifactStore structurally implements StorageBackend."""
        assert isinstance(store, StorageBackend)

    def test_root_created(self, tmp_path):
        """The root directory is created on construction."""
        root = tmp_path / "nested" / "artifacts"
        assert not root.exists()
        LocalArtifactStore(root)
        assert root.is_dir()

    def test_put_get_round_trip(self, store, source_file, tmp_path):
        """put() stores a file and get() copies it back byte-identically."""
        info = store.put("final.mp4", source_file)
        assert info.key == "final.mp4"
        assert info.size == len(b"fake video content")
        assert info.etag is None

        dest = tmp_path / "out" / "copy.mp4"
        returned = store.get("final.mp4", dest)
        assert returned == dest
        assert dest.read_bytes() == b"fake video content"

    def test_put_nested_key(self, store, source_file):
        """put() creates intermediate directories for nested keys."""
        store.put("clips/001.mp4", source_file)
        assert store.exists("clips/001.mp4")
        assert (store.root / "clips" / "001.mp4").is_file()

    def test_open_returns_binary_stream(self, store, source_file):
        """open() yields a readable binary stream."""
        store.put("final.mp4", source_file)
        with store.open("final.mp4") as fh:
            assert fh.read() == b"fake video content"

    def test_exists_and_delete(self, store, source_file):
        """exists()/delete() report accurately and delete is idempotent."""
        store.put("final.mp4", source_file)
        assert store.exists("final.mp4") is True
        assert store.delete("final.mp4") is True
        assert store.exists("final.mp4") is False
        assert store.delete("final.mp4") is False

    def test_list_recurses_and_sorts(self, store, source_file):
        """list() yields every file, recursively, sorted by key."""
        store.put("b.mp4", source_file)
        store.put("a.mp4", source_file)
        store.put("clips/001.mp4", source_file)
        keys = [i.key for i in store.list()]
        assert keys == ["a.mp4", "b.mp4", "clips/001.mp4"]

    def test_list_with_prefix(self, store, source_file):
        """list(prefix) restricts results to that subtree."""
        store.put("a.mp4", source_file)
        store.put("clips/001.mp4", source_file)
        assert [i.key for i in store.list("clips")] == ["clips/001.mp4"]

    def test_list_unknown_prefix_is_empty(self, store):
        """Listing a missing prefix yields nothing rather than raising."""
        assert list(store.list("nope")) == []

    def test_list_unsafe_prefix_is_empty(self, store):
        """A traversal prefix yields nothing rather than escaping."""
        assert list(store.list("../..")) == []

    def test_stat_reports_metadata(self, store, source_file):
        """stat() returns size and mtime for an existing key."""
        store.put("final.mp4", source_file)
        info = store.stat("final.mp4")
        assert isinstance(info, ArtifactInfo)
        assert info.size == 18
        assert info.modified_at > 0

    def test_stat_missing_raises(self, store):
        """stat() on a missing key raises ArtifactNotFoundError."""
        with pytest.raises(ArtifactNotFoundError):
            store.stat("missing.mp4")

    def test_stat_directory_is_not_found(self, store, source_file):
        """A directory is not an artifact."""
        store.put("clips/001.mp4", source_file)
        with pytest.raises(ArtifactNotFoundError):
            store.stat("clips")

    def test_open_missing_raises(self, store):
        """open() on a missing key raises ArtifactNotFoundError."""
        with pytest.raises(ArtifactNotFoundError):
            store.open("missing.mp4")

    def test_url_is_none(self, store, source_file):
        """The local backend has no shareable URL."""
        store.put("final.mp4", source_file)
        assert store.url("final.mp4") is None

    @pytest.mark.parametrize("key", ["../escape.txt", "/etc/passwd", "clips/../../escape.txt"])
    def test_traversal_rejected(self, store, source_file, key):
        """Every mutating/reading entry point refuses traversal keys."""
        with pytest.raises(UnsafeKeyError):
            store.stat(key)
        with pytest.raises(UnsafeKeyError):
            store.open(key)
        with pytest.raises(UnsafeKeyError):
            store.delete(key)
        with pytest.raises(UnsafeKeyError):
            store.put(key, source_file)
        assert store.exists(key) is False

    def test_traversal_cannot_read_sibling_file(self, tmp_path):
        """A key escaping the root cannot read a neighbouring file."""
        secret = tmp_path / "secret.txt"
        secret.write_text("classified", encoding="utf-8")
        inner = LocalArtifactStore(tmp_path / "artifacts")
        with pytest.raises(UnsafeKeyError):
            inner.open("../secret.txt")

    def test_symlink_escape_rejected(self, tmp_path, store):
        """A symlink pointing outside the root is refused on resolution."""
        outside = tmp_path / "outside.txt"
        outside.write_text("nope", encoding="utf-8")
        link = store.root / "link.txt"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):  # pragma: no cover - needs privileges
            pytest.skip("symlink creation not permitted on this platform")
        if not link.is_symlink():  # pragma: no cover - Windows without privileges
            pytest.skip("symlinks are unavailable on this platform")
        with pytest.raises(UnsafeKeyError):
            store.stat("link.txt")

    def test_local_path_matches_root_layout(self, store, source_file):
        """local_path() reports the unresolved on-disk path."""
        store.put("final.mp4", source_file)
        assert store.local_path("final.mp4") == store.root / "final.mp4"
        assert artifact_location(store, "final.mp4") == str(store.root / "final.mp4")


# ════════════════════════════════════════════════════════════
#  S3ArtifactStore (injected fake client)
# ════════════════════════════════════════════════════════════


class TestS3ArtifactStore:
    """The S3 backend, exercised against an injected fake boto3 client."""

    @pytest.fixture
    def client(self) -> FakeS3Client:
        return FakeS3Client()

    @pytest.fixture
    def s3(self, client) -> S3ArtifactStore:
        return S3ArtifactStore(bucket="mn-bucket", prefix="runs", client=client)

    def test_satisfies_protocol(self, s3):
        """S3ArtifactStore structurally implements StorageBackend."""
        assert isinstance(s3, StorageBackend)

    def test_requires_bucket(self):
        """An empty bucket name is rejected with an actionable message."""
        with pytest.raises(ArtifactStoreError, match="MN_S3_BUCKET"):
            S3ArtifactStore(bucket="", client=FakeS3Client())

    def test_object_key_applies_prefix(self, s3):
        """Store keys are prefixed before hitting the bucket."""
        assert s3.object_key("final.mp4") == "runs/final.mp4"

    def test_object_key_rejects_traversal(self, s3):
        """The traversal guard applies to the S3 backend too."""
        with pytest.raises(UnsafeKeyError):
            s3.object_key("../../etc/passwd")

    def test_put_and_stat(self, s3, client, source_file):
        """put() uploads under the prefix; stat() reads it back."""
        info = s3.put("final.mp4", source_file)
        assert "runs/final.mp4" in client.objects
        assert info.key == "final.mp4"
        assert info.size == 18
        assert info.etag == "deadbeef"

    def test_get_round_trip(self, s3, source_file, tmp_path):
        """get() downloads to a local path, creating parent dirs."""
        s3.put("final.mp4", source_file)
        dest = tmp_path / "dl" / "final.mp4"
        assert s3.get("final.mp4", dest) == dest
        assert dest.read_bytes() == b"fake video content"

    def test_open_streams_body(self, s3, source_file):
        """open() returns the object body stream."""
        s3.put("final.mp4", source_file)
        with s3.open("final.mp4") as body:
            assert body.read() == b"fake video content"

    def test_exists_and_delete(self, s3, source_file):
        """exists()/delete() behave like the local backend."""
        assert s3.exists("final.mp4") is False
        s3.put("final.mp4", source_file)
        assert s3.exists("final.mp4") is True
        assert s3.delete("final.mp4") is True
        assert s3.delete("final.mp4") is False

    def test_list_strips_prefix(self, s3, client):
        """Listed keys are store-relative, not bucket-absolute."""
        client.seed("runs/a.mp4", b"a")
        client.seed("runs/clips/b.mp4", b"bb")
        client.seed("other/c.mp4", b"ccc")
        infos = sorted(s3.list(), key=lambda i: i.key)
        assert [i.key for i in infos] == ["a.mp4", "clips/b.mp4"]
        assert [i.size for i in infos] == [1, 2]

    def test_list_skips_directory_markers(self, s3, client):
        """Zero-byte ``dir/`` marker objects are not reported."""
        client.seed("runs/clips/", b"")
        client.seed("runs/a.mp4", b"a")
        assert [i.key for i in s3.list()] == ["a.mp4"]

    def test_list_modified_at_is_timestamp(self, s3, client):
        """LastModified datetimes are converted to POSIX timestamps."""
        when = datetime(2026, 1, 1, tzinfo=timezone.utc)
        client.seed("runs/a.mp4", b"a", modified=when)
        info = next(iter(s3.list()))
        assert info.modified_at == pytest.approx(when.timestamp())

    def test_stat_missing_raises_not_found(self, s3):
        """A 404 from the client maps to ArtifactNotFoundError."""
        with pytest.raises(ArtifactNotFoundError):
            s3.stat("missing.mp4")

    def test_url_is_presigned(self, s3, source_file):
        """url() delegates to generate_presigned_url."""
        s3.put("final.mp4", source_file)
        assert s3.url("final.mp4", expires_in=60) == (
            "https://example.invalid/runs/final.mp4?exp=60"
        )

    def test_url_none_without_signing_support(self, source_file):
        """A client that cannot pre-sign yields None instead of raising."""

        class NoSignClient(FakeS3Client):
            generate_presigned_url = None  # type: ignore[assignment]

        s3 = S3ArtifactStore(bucket="b", client=NoSignClient())
        assert s3.url("final.mp4") is None

    def test_pagination_without_paginator(self, client, source_file):
        """The manual ContinuationToken loop is used when no paginator exists."""

        class PagedClient(FakeS3Client):
            def list_objects_v2(self, **kwargs):
                if "ContinuationToken" not in kwargs:
                    return {
                        "Contents": [
                            {"Key": "a.mp4", "Size": 1, "LastModified": None, "ETag": '"x"'}
                        ],
                        "IsTruncated": True,
                        "NextContinuationToken": "tok",
                    }
                return {
                    "Contents": [{"Key": "b.mp4", "Size": 2, "LastModified": None, "ETag": '"y"'}],
                    "IsTruncated": False,
                }

        s3 = S3ArtifactStore(bucket="b", client=PagedClient())
        assert [i.key for i in s3.list()] == ["a.mp4", "b.mp4"]

    @pytest.mark.skipif(
        _HAS_BOTO3, reason="boto3 is installed, so the missing-dependency path cannot run"
    )
    def test_missing_boto3_error_is_actionable(self):
        """Without boto3 and without an injected client, the error explains the fix."""
        with pytest.raises(ArtifactStoreError, match=r"movie-narrator\[s3\]"):
            S3ArtifactStore(bucket="mn-bucket")

    @pytest.mark.skipif(not _HAS_BOTO3, reason="requires the real boto3 library")
    def test_real_boto3_client_is_built(self):  # pragma: no cover - boto3 absent in CI
        """With boto3 available, a real client is constructed lazily."""
        s3 = S3ArtifactStore(bucket="mn-bucket", endpoint_url="http://localhost:9000")
        assert s3.client is not None


# ════════════════════════════════════════════════════════════
#  Factory / env resolution
# ════════════════════════════════════════════════════════════


class TestArtifactStoreFactory:
    """Tests for get_artifact_store / get_task_artifact_store."""

    def test_default_backend_is_local(self, tmp_path):
        """With no env configured the local backend is used."""
        s = get_artifact_store(env={"MN_STORAGE_ROOT": str(tmp_path / "root")})
        assert isinstance(s, LocalArtifactStore)
        assert s.root == tmp_path / "root"

    def test_local_root_from_env(self, tmp_path):
        """MN_STORAGE_ROOT selects the local root."""
        env = {"MN_STORAGE_BACKEND": "local", "MN_STORAGE_ROOT": str(tmp_path / "a")}
        s = get_artifact_store(env=env)
        assert isinstance(s, LocalArtifactStore)
        assert s.root == tmp_path / "a"

    def test_sub_prefix_scopes_local_root(self, tmp_path):
        """sub_prefix nests the local root (task-scoped store)."""
        env = {"MN_STORAGE_ROOT": str(tmp_path / "a")}
        s = get_artifact_store(env=env, sub_prefix="task123")
        assert isinstance(s, LocalArtifactStore)
        assert s.root == tmp_path / "a" / "task123"

    def test_explicit_root_wins_over_env(self, tmp_path):
        """An explicit root argument overrides MN_STORAGE_ROOT."""
        env = {"MN_STORAGE_ROOT": str(tmp_path / "env")}
        s = get_artifact_store(root=tmp_path / "explicit", env=env)
        assert isinstance(s, LocalArtifactStore)
        assert s.root == tmp_path / "explicit"

    def test_s3_requires_bucket(self):
        """MN_STORAGE_BACKEND=s3 without MN_S3_BUCKET is an actionable error."""
        with pytest.raises(ArtifactStoreError, match="MN_S3_BUCKET"):
            get_artifact_store(env={"MN_STORAGE_BACKEND": "s3"})

    def test_unknown_backend_rejected(self):
        """An unsupported backend name is rejected."""
        with pytest.raises(ArtifactStoreError, match="Unknown storage backend"):
            get_artifact_store(env={"MN_STORAGE_BACKEND": "gcs"})

    def test_backend_name_is_case_insensitive(self, tmp_path):
        """Backend resolution tolerates casing/whitespace."""
        env = {"MN_STORAGE_BACKEND": " Local ", "MN_STORAGE_ROOT": str(tmp_path)}
        assert isinstance(get_artifact_store(env=env), LocalArtifactStore)

    def test_task_store_local_uses_output_dir(self, tmp_path):
        """The task-scoped local store is rooted at the task output dir."""
        output = tmp_path / "output" / "Movie"
        output.mkdir(parents=True)
        s = get_task_artifact_store("abc123", output, env={})
        assert isinstance(s, LocalArtifactStore)
        assert s.root == output

    def test_task_store_none_without_output_dir(self):
        """No output directory means no task store."""
        assert get_task_artifact_store("abc123", None, env={}) is None

    def test_task_store_none_when_dir_missing(self, tmp_path):
        """A vanished output directory yields None (API then reports 404)."""
        assert get_task_artifact_store("abc", tmp_path / "gone", env={}) is None

    def test_task_store_s3_scopes_by_task_id(self):
        """For remote backends the task id becomes the key prefix."""
        env = {
            "MN_STORAGE_BACKEND": "s3",
            "MN_S3_BUCKET": "mn",
            "MN_S3_PREFIX": "runs",
        }
        # boto3 is unavailable in CI, so the factory surfaces the install hint.
        if _HAS_BOTO3:  # pragma: no cover - depends on the environment
            s = get_task_artifact_store("abc123", None, env=env)
            assert isinstance(s, S3ArtifactStore)
            assert s.prefix == "runs/abc123"
        else:
            assert get_task_artifact_store("abc123", None, env=env) is None


# ════════════════════════════════════════════════════════════
#  REST API wiring (behaviour must be unchanged)
# ════════════════════════════════════════════════════════════


def _mock_pipeline(ctx, **kwargs):
    """Mock pipeline that doesn't do any actual work."""
    Path(ctx.output_dir).mkdir(parents=True, exist_ok=True)
    ctx.video_path = str(Path(ctx.output_dir) / "final.mp4")
    Path(ctx.video_path).write_bytes(b"mock video")
    return ctx


@pytest.fixture(autouse=True)
def mock_pipeline(monkeypatch):
    """Prevent any real pipeline execution in this module."""
    monkeypatch.setattr(
        "movie_narrator.cloud.worker.run_pipeline",
        _mock_pipeline,
    )


@pytest.fixture
def api_server(tmp_path, monkeypatch):
    """A running API server with the default (local) artifact backend."""
    monkeypatch.delenv("MN_STORAGE_BACKEND", raising=False)
    monkeypatch.delenv("MN_ARTIFACT_TTL", raising=False)
    monkeypatch.delenv("MN_ARTIFACT_MAX_BYTES", raising=False)
    server = TaskAPIServer(
        host="127.0.0.1",
        port=0,
        storage_dir=tmp_path / "tasks",
        max_workers=1,
    )
    server.start(blocking=False)
    time.sleep(0.1)
    yield server
    server.stop()


@pytest.fixture
def task_with_files(api_server, tmp_path) -> Task:
    """A completed task whose output directory holds four artifacts."""
    output_dir = tmp_path / "output" / "v083_task"
    output_dir.mkdir(parents=True)
    (output_dir / "final.mp4").write_bytes(b"fake video content")
    (output_dir / "narration.mp3").write_bytes(b"fake audio")
    (output_dir / "subtitle.srt").write_text("1\n", encoding="utf-8")
    (output_dir / "metadata.json").write_text('{"v": "0.8.3"}', encoding="utf-8")
    (output_dir / ".hidden").write_text("x", encoding="utf-8")
    (output_dir / "clips").mkdir()
    (output_dir / "clips" / "001.mp4").write_bytes(b"nested")

    task = Task(
        request=TaskRequest(movie_name="V083", max_retries=0),
        status=TaskStatus.COMPLETED,
        result=TaskResult(
            video_path=str(output_dir / "final.mp4"),
            output_dir=str(output_dir),
        ),
    )
    api_server.queue._storage.save(task)
    return task


def _get(url: str):
    return urllib.request.urlopen(url, timeout=5)


def _status(url: str) -> int:
    try:
        with _get(url) as resp:
            return resp.getcode()
    except urllib.error.HTTPError as e:
        return e.code


class TestApiArtifactEndpoints:
    """The REST endpoints now go through the store but behave identically."""

    def test_list_artifacts_top_level_only(self, api_server, task_with_files):
        """Listing reports top-level files, skips dotfiles and subdirectories."""
        url = f"{api_server.base_url}/tasks/{task_with_files.id}/artifacts"
        with _get(url) as resp:
            body = json.loads(resp.read())
        names = [a["filename"] for a in body["artifacts"]]
        assert names == ["final.mp4", "metadata.json", "narration.mp3", "subtitle.srt"]
        assert body["count"] == 4
        assert ".hidden" not in names
        assert all("/" not in n for n in names)

    def test_list_artifacts_reports_size_and_path(self, api_server, task_with_files):
        """Each entry keeps the v0.6.1 filename/size/path shape."""
        url = f"{api_server.base_url}/tasks/{task_with_files.id}/artifacts"
        with _get(url) as resp:
            body = json.loads(resp.read())
        entry = next(a for a in body["artifacts"] if a["filename"] == "final.mp4")
        assert entry["size"] == len(b"fake video content")
        assert Path(entry["path"]).name == "final.mp4"
        assert Path(entry["path"]).is_file()

    def test_list_artifacts_missing_output_dir(self, api_server):
        """A task without output still returns an empty list."""
        task = Task(
            request=TaskRequest(movie_name="NoOutput", max_retries=0),
            status=TaskStatus.PENDING,
        )
        api_server.queue._storage.save(task)
        with _get(f"{api_server.base_url}/tasks/{task.id}/artifacts") as resp:
            body = json.loads(resp.read())
        assert body["artifacts"] == []

    def test_download_artifact(self, api_server, task_with_files):
        """Downloading returns the exact bytes with the right content type."""
        url = f"{api_server.base_url}/tasks/{task_with_files.id}/download/final.mp4"
        with _get(url) as resp:
            assert resp.getcode() == 200
            assert resp.headers["Content-Type"] == "video/mp4"
            assert resp.headers["Content-Length"] == "18"
            assert resp.read() == b"fake video content"

    def test_download_nested_key(self, api_server, task_with_files):
        """Nested keys remain reachable for direct download."""
        url = f"{api_server.base_url}/tasks/{task_with_files.id}/download/clips/001.mp4"
        with _get(url) as resp:
            assert resp.read() == b"nested"

    def test_download_missing_file_404(self, api_server, task_with_files):
        """A missing file is a 404."""
        url = f"{api_server.base_url}/tasks/{task_with_files.id}/download/nope.mp4"
        assert _status(url) == 404

    def test_download_directory_404(self, api_server, task_with_files):
        """A directory key is not downloadable."""
        url = f"{api_server.base_url}/tasks/{task_with_files.id}/download/clips"
        assert _status(url) == 404

    def test_download_traversal_forbidden(self, api_server, task_with_files):
        """Encoded traversal attempts are rejected with 403."""
        url = (
            f"{api_server.base_url}/tasks/{task_with_files.id}/download/..%2f..%2f..%2fetc%2fpasswd"
        )
        assert _status(url) == 403

    def test_download_absolute_path_forbidden(self, api_server, task_with_files):
        """An absolute path in the filename segment is rejected with 403."""
        url = (
            f"{api_server.base_url}/tasks/{task_with_files.id}"
            f"/download/C%3A%2FWindows%2Fsystem32%2Fdrivers%2Fetc%2Fhosts"
        )
        assert _status(url) == 403

    def test_download_no_output_dir_404(self, api_server):
        """A task with no output directory reports 404."""
        task = Task(
            request=TaskRequest(movie_name="NoOutput", max_retries=0),
            status=TaskStatus.COMPLETED,
            result=TaskResult(),
        )
        api_server.queue._storage.save(task)
        assert _status(f"{api_server.base_url}/tasks/{task.id}/download/final.mp4") == 404


class TestApiSweeperLifecycle:
    """The API server starts/stops the TTL sweeper only when configured."""

    def test_no_sweeper_by_default(self, api_server):
        """Without MN_ARTIFACT_* configured no sweeper thread runs."""
        assert api_server.sweeper is None

    def test_sweeper_started_when_ttl_enabled(self, tmp_path):
        """A TTL policy starts a daemon sweeper that stops with the server."""
        from movie_narrator.cloud.lifecycle import ArtifactLifecyclePolicy

        store = LocalArtifactStore(tmp_path / "artifacts")
        server = TaskAPIServer(
            host="127.0.0.1",
            port=0,
            storage_dir=tmp_path / "tasks",
            max_workers=1,
            artifact_store=store,
            artifact_policy=ArtifactLifecyclePolicy(ttl_seconds=3600),
        )
        server.start(blocking=False)
        try:
            assert server.sweeper is not None
            assert server.sweeper.is_running
        finally:
            server.stop()
        assert server.sweeper is None

    def test_active_task_ids_exclude_terminal(self, api_server, task_with_files):
        """Only pending/running tasks are reported as protected."""
        pending = Task(
            request=TaskRequest(movie_name="Pending", max_retries=0),
            status=TaskStatus.PENDING,
        )
        api_server.queue._storage.save(pending)
        ids = api_server._active_task_ids()
        assert pending.id in ids
        assert task_with_files.id not in ids
