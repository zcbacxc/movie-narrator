# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Artifact (blob) storage abstraction (v0.8.3).

This module abstracts *where the produced media files live* — the
``final.mp4`` / narration audio / subtitles / ``metadata.json`` that a
pipeline run writes into its output directory.

It is deliberately **separate** from :mod:`movie_narrator.cloud.storage`,
which persists *task state* (the ``TaskStorage`` JSON index of ``Task``
records). The two solve different problems and must not be conflated:

===========================  ==========================================
``storage.TaskStorage``      task metadata (status, progress, result)
``artifact_store``           the binary artifacts a task produced
===========================  ==========================================

The abstraction is a small, synchronous :class:`StorageBackend` protocol
with two implementations:

- :class:`LocalArtifactStore` — filesystem-backed, the **default**, and
  the only backend needed for single-host deployments.
- :class:`S3ArtifactStore` — S3 / MinIO / R2 compatible. ``boto3`` is an
  optional dependency (``pip install movie-narrator[s3]``) imported
  lazily so importing this module never requires it. The underlying
  client is injectable, which keeps the class unit-testable without the
  real library or network access.

Keys are always **relative POSIX paths** (``"final.mp4"``,
``"clips/001.mp4"``). Absolute paths, ``..`` segments, drive letters and
anything that would escape the store root are rejected with
:class:`UnsafeKeyError` — this preserves the path-traversal protection
the REST API has enforced since v0.6.1.

Typical usage::

    from movie_narrator.cloud.artifact_store import get_artifact_store

    store = get_artifact_store()              # resolves from env
    store.put("final.mp4", "/tmp/final.mp4")
    for info in store.list():
        print(info.key, info.size)

Environment variables:
    ``MN_STORAGE_BACKEND``   ``local`` (default) or ``s3``
    ``MN_STORAGE_ROOT``      root directory for the local backend
    ``MN_S3_BUCKET``         bucket name (required for ``s3``)
    ``MN_S3_PREFIX``         key prefix inside the bucket
    ``MN_S3_ENDPOINT_URL``   custom endpoint (MinIO / Cloudflare R2)
    ``MN_S3_REGION``         AWS region name
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import (
    IO,
    Any,
    Iterable,
    Iterator,
    List,
    Mapping,
    Optional,
    Protocol,
    Union,
    runtime_checkable,
)

logger = logging.getLogger(__name__)

#: Anything accepted where a filesystem path is expected.
PathLike = Union[str, "os.PathLike[str]"]

#: Default root for the local backend when ``MN_STORAGE_ROOT`` is unset.
DEFAULT_LOCAL_ROOT = "output"


# ── Errors ─────────────────────────────────────────────────


class ArtifactStoreError(Exception):
    """Base class for artifact-store failures."""


class ArtifactNotFoundError(ArtifactStoreError):
    """Raised when a key does not exist in the store."""


class UnsafeKeyError(ArtifactStoreError):
    """Raised when a key would escape the store root (path traversal)."""


# ── Value objects ──────────────────────────────────────────


@dataclass(frozen=True)
class ArtifactInfo:
    """Metadata about a single stored artifact.

    Attributes:
        key: Store-relative POSIX key, e.g. ``"final.mp4"``.
        size: Size in bytes.
        modified_at: Last-modified time as a POSIX timestamp (seconds
            since the epoch, UTC). Chosen over ``datetime`` so that TTL
            arithmetic against :func:`time.time` stays trivial and
            backend-agnostic.
        etag: Backend-provided content hash, when available (S3). Always
            ``None`` for the local backend.
    """

    key: str
    size: int
    modified_at: float
    etag: Optional[str] = None


# ── Protocol ───────────────────────────────────────────────


@runtime_checkable
class StorageBackend(Protocol):
    """Synchronous key/blob storage for pipeline artifacts.

    Implementations must treat *key* as a relative POSIX path and reject
    anything that escapes their root (see :func:`normalize_key`).
    """

    def put(self, key: str, src_path: PathLike) -> ArtifactInfo:
        """Upload/copy the local file *src_path* to *key*."""
        ...

    def get(self, key: str, dest_path: PathLike) -> Path:
        """Download/copy *key* to the local file *dest_path*."""
        ...

    def open(self, key: str) -> IO[bytes]:  # noqa: A003 - mirrors builtin open() intent
        """
        Returns:
            A readable binary stream for *key*.
        """
        ...

    def exists(self, key: str) -> bool:
        """
        Returns:
            True when *key* exists.
        """
        ...

    def delete(self, key: str) -> bool:
        """Delete *key*; return True when something was removed."""
        ...

    def list(self, prefix: str = "") -> Iterable[ArtifactInfo]:  # noqa: A003
        """Yield metadata for every key under *prefix*."""
        ...

    def stat(self, key: str) -> ArtifactInfo:
        """
        Returns:
            Metadata for *key*, raising if it does not exist.
        """
        ...

    def url(self, key: str, *, expires_in: int = 3600) -> Optional[str]:
        """
        Returns:
            A public/pre-signed URL for *key*, or None if unsupported.
        """
        ...


# ── Key handling ───────────────────────────────────────────


def normalize_key(key: str) -> str:
    """Validate *key* and return it as a clean relative POSIX path.

    The rules mirror the REST API's long-standing download guard:
    absolute paths, drive letters, ``..`` segments and empty keys are
    all refused. ``.`` segments and redundant separators are collapsed.

    Args:
        key: Candidate store key. Both ``/`` and ``\\`` are accepted as
            separators so Windows-style input is handled consistently.

    Returns:
        The normalized key, e.g. ``"clips/001.mp4"``.

    Raises:
        UnsafeKeyError: if the key is empty, absolute, or escapes the root.
    """
    if not isinstance(key, str) or not key.strip():
        raise UnsafeKeyError("Artifact key must be a non-empty string")

    candidate = key.replace("\\", "/").strip()
    if candidate.startswith("/"):
        raise UnsafeKeyError(f"Absolute artifact keys are not allowed: {key!r}")
    # Windows drive letters ("C:/x") and UNC-ish forms are absolute too.
    if len(candidate) >= 2 and candidate[1] == ":":
        raise UnsafeKeyError(f"Absolute artifact keys are not allowed: {key!r}")

    parts: List[str] = []
    for part in PurePosixPath(candidate).parts:
        if part in ("", "."):
            continue
        if part == "..":
            raise UnsafeKeyError(f"Artifact key escapes the store root: {key!r}")
        parts.append(part)

    if not parts:
        raise UnsafeKeyError(f"Artifact key resolves to the store root: {key!r}")
    return "/".join(parts)


def join_prefix(prefix: str, key: str) -> str:
    """Join a store prefix and a key into a single POSIX key."""
    clean_prefix = prefix.replace("\\", "/").strip("/")
    clean_key = key.replace("\\", "/").strip("/")
    if not clean_prefix:
        return clean_key
    if not clean_key:
        return clean_prefix
    return f"{clean_prefix}/{clean_key}"


# ── Local backend ──────────────────────────────────────────


class LocalArtifactStore:
    """Filesystem-backed :class:`StorageBackend` (the default).

    Every key is resolved beneath *root*; a key that resolves outside of
    it raises :class:`UnsafeKeyError` even when symlinks are involved,
    because the check is performed on the *resolved* paths.

    Args:
        root: Directory that holds the artifacts. Created on demand.
    """

    def __init__(self, root: PathLike) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._resolved_root = self._root.resolve()

    # ── Introspection ───────────────────────────────────────

    @property
    def root(self) -> Path:
        """The store root exactly as configured (not resolved)."""
        return self._root

    def local_path(self, key: str) -> Path:
        """Return the on-disk path for *key* (validated, not resolved).

        Keeping the unresolved form means callers see the same paths the
        pipeline reported in ``TaskResult.output_dir``.

        Raises:
            UnsafeKeyError: if *key* escapes the store root.
        """
        return self._root / normalize_key(key)

    # ── StorageBackend ──────────────────────────────────────

    def put(self, key: str, src_path: PathLike) -> ArtifactInfo:
        """Copy *src_path* into the store under *key*."""
        target = self._safe_path(key)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(os.fspath(src_path), target)
        return self._info(normalize_key(key), target)

    def get(self, key: str, dest_path: PathLike) -> Path:
        """Copy *key* out of the store to *dest_path*."""
        source = self._existing_path(key)
        dest = Path(dest_path)
        if dest.parent and not dest.parent.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, dest)
        return dest

    def open(self, key: str) -> IO[bytes]:  # noqa: A003
        """Open *key* for binary reading."""
        return self._existing_path(key).open("rb")

    def exists(self, key: str) -> bool:
        """
        Returns:
            True when *key* is an existing regular file.
        """
        try:
            return self._safe_path(key).is_file()
        except UnsafeKeyError:
            return False

    def delete(self, key: str) -> bool:
        """Remove *key*; return True when a file was removed."""
        path = self._safe_path(key)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def list(self, prefix: str = "") -> Iterator[ArtifactInfo]:  # noqa: A003
        """Yield every file under *prefix*, recursively, sorted by key."""
        clean_prefix = prefix.replace("\\", "/").strip("/")
        base = self._root
        if clean_prefix:
            try:
                base = self._safe_path(clean_prefix)
            except UnsafeKeyError:
                return
        if not base.is_dir():
            return

        for path in sorted(p for p in base.rglob("*") if p.is_file()):
            try:
                key = path.relative_to(self._root).as_posix()
            except ValueError:  # pragma: no cover - defensive
                continue
            yield self._info(key, path)

    def stat(self, key: str) -> ArtifactInfo:
        """
        Returns:
            Metadata for *key*.
        """
        path = self._existing_path(key)
        return self._info(normalize_key(key), path)

    def url(self, key: str, *, expires_in: int = 3600) -> Optional[str]:
        """Local files have no shareable URL — always None."""
        return None

    # ── Internals ───────────────────────────────────────────

    def _safe_path(self, key: str) -> Path:
        """Resolve *key* under the root, refusing anything that escapes."""
        candidate = self._root / normalize_key(key)
        if not _is_within(candidate.resolve(), self._resolved_root):
            raise UnsafeKeyError(f"Artifact key escapes the store root: {key!r}")
        return candidate

    def _existing_path(self, key: str) -> Path:
        path = self._safe_path(key)
        if not path.is_file():
            raise ArtifactNotFoundError(f"Artifact not found: {key!r}")
        return path

    @staticmethod
    def _info(key: str, path: Path) -> ArtifactInfo:
        stat = path.stat()
        return ArtifactInfo(key=key, size=stat.st_size, modified_at=stat.st_mtime)


def _is_within(path: Path, root: Path) -> bool:
    """
    Returns:
        True when *path* is inside *root* (both already resolved).
    """
    try:
        return path.is_relative_to(root)
    except (AttributeError, ValueError):  # pragma: no cover - py<3.9 / mixed drives
        return False


# ── S3 backend ─────────────────────────────────────────────


_S3_MISSING_HINT = (
    "The S3 artifact backend requires boto3, which is not installed.\n"
    "Install it with:  pip install 'movie-narrator[s3]'\n"
    "Or switch back to the local backend:  MN_STORAGE_BACKEND=local"
)


class S3ArtifactStore:
    """S3-compatible :class:`StorageBackend` (AWS S3, MinIO, R2).

    ``boto3`` is imported lazily inside ``__init__`` so that merely
    importing this module — or running the default local backend — never
    requires the optional dependency.

    The client is injectable, which makes the class testable without
    boto3 or network access::

        store = S3ArtifactStore(bucket="b", client=FakeS3Client())

    Args:
        bucket: Target bucket name.
        prefix: Key prefix inside the bucket (optional).
        client: Pre-built boto3 S3 client. When provided, boto3 is not
            imported at all.
        endpoint_url: Custom endpoint for MinIO / R2 compatibility.
        region_name: AWS region name.

    Raises:
        ArtifactStoreError: if *bucket* is empty, or if boto3 is missing
            and no *client* was injected.
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "",
        *,
        client: Any = None,
        endpoint_url: Optional[str] = None,
        region_name: Optional[str] = None,
    ) -> None:
        if not bucket:
            raise ArtifactStoreError(
                "S3 artifact backend requires a bucket name (set MN_S3_BUCKET)."
            )
        self.bucket = bucket
        self.prefix = prefix.replace("\\", "/").strip("/")

        if client is None:
            try:
                # Imported lazily: boto3 is an optional extra, and the
                # default local backend must never require it.
                import boto3
            except ImportError as exc:  # pragma: no cover - boto3 absent in CI
                raise ArtifactStoreError(_S3_MISSING_HINT) from exc
            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region_name,
            )
        self._client = client

    # ── Introspection ───────────────────────────────────────

    @property
    def client(self) -> Any:
        """The underlying S3 client (boto3 or an injected double)."""
        return self._client

    def object_key(self, key: str) -> str:
        """
        Returns:
            The fully-qualified bucket key for a store *key*.
        """
        return join_prefix(self.prefix, normalize_key(key))

    # ── StorageBackend ──────────────────────────────────────

    def put(self, key: str, src_path: PathLike) -> ArtifactInfo:
        """Upload the local file *src_path* to *key*."""
        clean = normalize_key(key)
        self._client.upload_file(os.fspath(src_path), self.bucket, self.object_key(clean))
        return self.stat(clean)

    def get(self, key: str, dest_path: PathLike) -> Path:
        """Download *key* to the local file *dest_path*."""
        dest = Path(dest_path)
        if dest.parent and not dest.parent.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self.bucket, self.object_key(key), os.fspath(dest))
        except UnsafeKeyError:
            raise
        except Exception as exc:  # noqa: BLE001 - botocore raises client-specific errors
            raise _translate_s3_error(exc, key) from exc
        return dest

    def open(self, key: str) -> IO[bytes]:  # noqa: A003
        """
        Returns:
            The streaming body of *key*.
        """
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=self.object_key(key))
        except UnsafeKeyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise _translate_s3_error(exc, key) from exc
        body: IO[bytes] = response["Body"]
        return body

    def exists(self, key: str) -> bool:
        """
        Returns:
            True when *key* exists in the bucket.
        """
        try:
            self.stat(key)
        except ArtifactStoreError:
            return False
        return True

    def delete(self, key: str) -> bool:
        """Delete *key*; returns False when it did not exist."""
        if not self.exists(key):
            return False
        self._client.delete_object(Bucket=self.bucket, Key=self.object_key(key))
        return True

    def list(self, prefix: str = "") -> Iterator[ArtifactInfo]:  # noqa: A003
        """Yield metadata for every object under *prefix*."""
        search_prefix = join_prefix(self.prefix, prefix.replace("\\", "/").strip("/"))
        for page in self._paginate(search_prefix):
            for obj in page.get("Contents", []) or []:
                raw_key = str(obj.get("Key", ""))
                key = self._strip_prefix(raw_key)
                if not key or raw_key.endswith("/"):
                    continue
                yield ArtifactInfo(
                    key=key,
                    size=int(obj.get("Size", 0)),
                    modified_at=_as_timestamp(obj.get("LastModified")),
                    etag=_clean_etag(obj.get("ETag")),
                )

    def stat(self, key: str) -> ArtifactInfo:
        """
        Returns:
            Metadata for *key* via ``head_object``.
        """
        clean = normalize_key(key)
        try:
            head = self._client.head_object(Bucket=self.bucket, Key=self.object_key(clean))
        except Exception as exc:  # noqa: BLE001
            raise _translate_s3_error(exc, key) from exc
        return ArtifactInfo(
            key=clean,
            size=int(head.get("ContentLength", 0)),
            modified_at=_as_timestamp(head.get("LastModified")),
            etag=_clean_etag(head.get("ETag")),
        )

    def url(self, key: str, *, expires_in: int = 3600) -> Optional[str]:
        """
        Returns:
            A pre-signed GET URL, or None when unsupported.
        """
        generate = getattr(self._client, "generate_presigned_url", None)
        if generate is None:
            return None
        try:
            signed = generate(
                "get_object",
                Params={"Bucket": self.bucket, "Key": self.object_key(key)},
                ExpiresIn=expires_in,
            )
        except Exception as exc:  # noqa: BLE001 - URL signing is best-effort
            logger.debug("Failed to pre-sign URL for %r: %s", key, exc)
            return None
        return str(signed) if signed else None

    # ── Internals ───────────────────────────────────────────

    def _paginate(self, prefix: str) -> Iterator[Mapping[str, Any]]:
        """Yield ``list_objects_v2`` pages, using a paginator when available."""
        get_paginator = getattr(self._client, "get_paginator", None)
        if get_paginator is not None:
            try:
                paginator = get_paginator("list_objects_v2")
            except Exception:  # noqa: BLE001 - fall back to the plain call
                paginator = None
            if paginator is not None:
                yield from paginator.paginate(Bucket=self.bucket, Prefix=prefix)
                return

        token: Optional[str] = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": self.bucket, "Prefix": prefix}
            if token:
                kwargs["ContinuationToken"] = token
            page = self._client.list_objects_v2(**kwargs)
            yield page
            if not page.get("IsTruncated"):
                return
            token = page.get("NextContinuationToken")
            if not token:
                return

    def _strip_prefix(self, raw_key: str) -> str:
        if not self.prefix:
            return raw_key.lstrip("/")
        marker = self.prefix + "/"
        if raw_key.startswith(marker):
            return raw_key[len(marker) :]
        if raw_key == self.prefix:
            return ""
        return raw_key.lstrip("/")


#: Error codes/markers that mean "the object is simply not there".
_S3_MISSING_MARKERS = ("NoSuchKey", "NoSuchBucket", "NotFound", "404")


def _translate_s3_error(exc: Exception, key: str) -> ArtifactStoreError:
    """Map a botocore/client exception onto an artifact-store error.

    ``botocore.exceptions.ClientError`` carries the S3 error code in its
    ``response`` payload; anything else is matched on the exception name
    and message so that MinIO/R2 clients and test doubles behave the
    same way.
    """
    if isinstance(exc, (FileNotFoundError, ArtifactNotFoundError)):
        return ArtifactNotFoundError(f"Artifact not found: {key!r}")

    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        error = response.get("Error", {})
        code = str(error.get("Code", "")) if isinstance(error, dict) else ""
        status = str(response.get("ResponseMetadata", {}).get("HTTPStatusCode", ""))
        if code in _S3_MISSING_MARKERS or status == "404":
            return ArtifactNotFoundError(f"Artifact not found: {key!r}")

    haystack = f"{type(exc).__name__} {exc}"
    if any(marker in haystack for marker in _S3_MISSING_MARKERS):
        return ArtifactNotFoundError(f"Artifact not found: {key!r}")
    return ArtifactStoreError(f"S3 operation failed for {key!r}: {exc}")


def _as_timestamp(value: Any) -> float:
    """Convert an S3 ``LastModified`` value to a POSIX timestamp."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    timestamp = getattr(value, "timestamp", None)
    if callable(timestamp):
        return float(timestamp())
    return 0.0


def _clean_etag(value: Any) -> Optional[str]:
    """Strip the quotes S3 wraps around ETags."""
    if not value:
        return None
    return str(value).strip('"')


# ── Factory ────────────────────────────────────────────────


def get_artifact_store(
    *,
    backend: Optional[str] = None,
    root: Optional[PathLike] = None,
    sub_prefix: str = "",
    env: Optional[Mapping[str, str]] = None,
) -> StorageBackend:
    """Build the configured artifact store.

    Resolution order for every setting is *explicit argument* →
    *environment variable* → *default*.

    Args:
        backend: ``"local"`` or ``"s3"``. Defaults to
            ``MN_STORAGE_BACKEND`` and ultimately ``"local"``.
        root: Local root directory (local backend only). Defaults to
            ``MN_STORAGE_ROOT`` and ultimately ``"output"``.
        sub_prefix: Extra prefix appended to the store scope — used to
            scope a store to a single task (``sub_prefix=task_id``).
        env: Environment mapping to read from (defaults to ``os.environ``;
            injectable for tests).

    Returns:
        A ready-to-use :class:`StorageBackend`.

    Raises:
        ArtifactStoreError: for an unknown backend name, a missing bucket,
            or a missing boto3 install.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    name = (backend or environ.get("MN_STORAGE_BACKEND") or "local").strip().lower()

    if name == "local":
        base = (
            Path(root)
            if root is not None
            else Path(environ.get("MN_STORAGE_ROOT") or DEFAULT_LOCAL_ROOT)
        )
        if sub_prefix:
            base = base / normalize_key(sub_prefix)
        return LocalArtifactStore(base)

    if name == "s3":
        bucket = environ.get("MN_S3_BUCKET", "")
        if not bucket:
            raise ArtifactStoreError("MN_STORAGE_BACKEND=s3 requires MN_S3_BUCKET to be set.")
        prefix = join_prefix(environ.get("MN_S3_PREFIX", ""), sub_prefix)
        return S3ArtifactStore(
            bucket=bucket,
            prefix=prefix,
            endpoint_url=environ.get("MN_S3_ENDPOINT_URL") or None,
            region_name=environ.get("MN_S3_REGION") or None,
        )

    raise ArtifactStoreError(
        f"Unknown storage backend {name!r}. Supported backends: 'local', 's3'."
    )


def get_task_artifact_store(
    task_id: str,
    output_dir: Optional[PathLike] = None,
    *,
    env: Optional[Mapping[str, str]] = None,
) -> Optional[StorageBackend]:
    """Return a store scoped to a single task's artifacts.

    For the default local backend the scope is the task's own output
    directory, so behaviour is byte-for-byte identical to the direct
    filesystem access the REST API used before v0.8.3. For remote
    backends the scope is the ``<prefix>/<task_id>/`` key namespace.

    Args:
        task_id: The task whose artifacts should be exposed.
        output_dir: The task's local output directory (from
            ``TaskResult.output_dir``).
        env: Environment mapping to read from (defaults to ``os.environ``).

    Returns:
        A task-scoped :class:`StorageBackend`, or None when the task has
        no usable artifact location (e.g. the output directory is gone).
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    name = (environ.get("MN_STORAGE_BACKEND") or "local").strip().lower()

    if name == "local":
        if output_dir is None:
            return None
        base = Path(output_dir)
        if not base.is_dir():
            return None
        return LocalArtifactStore(base)

    try:
        return get_artifact_store(sub_prefix=task_id, env=environ)
    except ArtifactStoreError as exc:
        logger.warning("Artifact store unavailable for task %s: %s", task_id, exc)
        return None


def artifact_location(store: StorageBackend, key: str) -> str:
    """
    Returns:
        A consumer-facing location string for *key*.

        Local stores report the on-disk path (unchanged from v0.6.1);
        remote stores report a pre-signed URL when they can produce one,
        falling back to the bare key.
    """
    if isinstance(store, LocalArtifactStore):
        return str(store.local_path(key))
    return store.url(key) or key


__all__ = [
    "DEFAULT_LOCAL_ROOT",
    "ArtifactInfo",
    "ArtifactNotFoundError",
    "ArtifactStoreError",
    "LocalArtifactStore",
    "S3ArtifactStore",
    "StorageBackend",
    "UnsafeKeyError",
    "artifact_location",
    "get_artifact_store",
    "get_task_artifact_store",
    "join_prefix",
    "normalize_key",
]
