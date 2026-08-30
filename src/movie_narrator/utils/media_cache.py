# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Content-addressed media cache (v1.5.2).

Downloads remote ``reference_media`` URLs into
``~/.movie-narrator/media-cache/`` so the pipeline consumes local files
and repeated jobs avoid the network entirely. Follows the user-cache
conventions of :mod:`movie_narrator.utils.prompt_cache` (same
``~/.movie-narrator/`` base directory, TTL + cap eviction on write,
corrupt-tolerant reads, best-effort bookkeeping).

Design notes:

- **Storage**: one blob per entry, named ``<sha256>.<ext>`` (content
  addressed — identical bytes share one blob regardless of source URL),
  plus a ``<sha256>.json`` sidecar recording provenance:
  ``{sha256, source_url, license_note, kind, ext, fetched_at, bytes,
  content_type}``.
- **Dedupe**: a lookup by URL scans sidecars for the newest match
  (cache sizes are small by policy) and reuses the blob without any
  network round-trip; after a download, a blob whose sha256 already
  exists is reused instead of rewritten (cross-URL content dedupe).
- **TTL / size cap**: sidecars older than ``ttl_seconds`` (default 30
  days) are treated as expired on lookup and deleted; on every write the
  cache-wide ``max_bytes`` cap (default 2 GiB) evicts oldest-mtime blobs
  (and their sidecars) first — mirroring ``prompt_cache``.
- **Compliance**: ``license_note`` is REQUIRED for every fetch — a URL
  without an auditable provenance note is refused. Only ``https://``
  URLs are accepted.
- **Robustness**: a corrupt / missing sidecar or missing blob is treated
  as a miss (cleaned up, re-fetched) — never fatal on read. A failed
  download (network, HTTP status, size cap) raises
  :class:`MediaCacheError` so callers fail fast with a clear message;
  ``pipeline/resolve.py`` surfaces it as a hard input error.
- **Stats**: ``hits`` / ``misses`` / ``bytes`` counters per instance and
  module-level via :func:`stats` (shared instance) for tests and run
  metadata.

The cache is safe to delete at any time — entries re-download on the
next fetch. Uses the core ``httpx`` dependency (no new extras) and no
subprocess.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from contextlib import suppress
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx

__all__ = [
    "DEFAULT_TTL_SECONDS",
    "DEFAULT_MAX_BYTES",
    "DEFAULT_MAX_ENTRY_BYTES",
    "MediaCache",
    "MediaCacheError",
    "fetch_into_cache",
    "get_media_cache",
    "reset_media_cache",
    "stats",
]

#: Default entry lifetime (seconds): 30 days.
DEFAULT_TTL_SECONDS = 30 * 24 * 3600

#: Default cache-wide size cap (bytes): 2 GiB.
DEFAULT_MAX_BYTES = 2 * 1024**3

#: Default per-entry download cap (bytes): 2 GiB.
DEFAULT_MAX_ENTRY_BYTES = 2 * 1024**3

#: User-level cache subdirectory (same base convention as prompt_cache).
_DEFAULT_CACHE_DIR = Path.home() / ".movie-narrator" / "media-cache"

#: Content types whose canonical extension we trust when the URL path
#: does not name one (https://example.com/fetch?id=1 → still classifiable).
_EXT_BY_CONTENT_TYPE = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/x-matroska": ".mkv",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
    "video/x-m4v": ".m4v",
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}

#: Fallback extension when neither the URL nor the content type names one.
_FALLBACK_EXT = ".bin"

_EXT_RE = re.compile(r"\.[a-z0-9]{1,5}\Z")


class MediaCacheError(RuntimeError):
    """Raised when a media fetch fails or violates cache policy.

    Callers (``pipeline/resolve.py``) surface this as a hard input error;
    the message is always user-facing and includes the URL.
    """


def _now() -> float:
    """Wall clock, isolated for tests to monkeypatch."""
    return time.time()


def _default_cache_dir() -> Path:
    """Return the user-level media cache directory (overridable in tests)."""
    return _DEFAULT_CACHE_DIR


def _ext_for(url: str, content_type: str) -> str:
    """Pick a usable file extension from the URL path or content type."""
    suffix = Path(urlparse(url).path).suffix.lower()
    if _EXT_RE.fullmatch(suffix):
        return suffix
    ct = content_type.split(";")[0].strip().lower()
    return _EXT_BY_CONTENT_TYPE.get(ct, _FALLBACK_EXT)


class MediaCache:
    """File-backed, content-addressed cache for remote media files."""

    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_bytes: int = DEFAULT_MAX_BYTES,
        timeout: float = 30.0,
        transport: Optional[httpx.BaseTransport] = None,
    ) -> None:
        self.cache_dir = (
            Path(cache_dir) if cache_dir is not None else _default_cache_dir()
        )
        self.ttl_seconds = ttl_seconds
        self.max_bytes = max_bytes
        self.timeout = timeout
        # Tests inject httpx.MockTransport — no network in unit tests.
        self._transport = transport
        self.hits = 0
        self.misses = 0
        self.bytes = 0

    # ── Entry paths / sidecar reads ───────────────────────

    def _blob_path(self, sha: str, ext: str) -> Path:
        return self.cache_dir / f"{sha}{ext}"

    def _sidecar_path(self, sha: str) -> Path:
        return self.cache_dir / f"{sha}.json"

    def _iter_sidecars(self) -> list[Path]:
        try:
            return sorted(self.cache_dir.glob("*.json"))
        except OSError:
            return []

    def _read_sidecar(self, path: Path) -> Optional[dict]:
        """Read + validate one sidecar; corrupt files are deleted on sight.

        Returns ``None`` when unreadable, malformed, or self-inconsistent
        (the sidecar filename must match the recorded sha256).
        """
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            with suppress(OSError):
                path.unlink()
            return None
        sha = path.stem
        if (
            not isinstance(entry, dict)
            or entry.get("sha256") != sha
            or not isinstance(entry.get("bytes"), int)
            or not isinstance(entry.get("fetched_at"), (int, float))
            or not isinstance(entry.get("ext"), str)
        ):
            # Corrupt or foreign sidecar — treat as a miss and clean up.
            with suppress(OSError):
                path.unlink()
            return None
        return entry

    def _find_by_url(self, url: str) -> Optional[tuple[Path, dict]]:
        """Newest live (unexpired, blob-present) sidecar for ``url``.

        Expired entries and dead blobs (sidecar without its blob) are
        deleted on sight so the cache self-heals.
        """
        best: Optional[tuple[float, Path, dict]] = None
        for path in self._iter_sidecars():
            entry = self._read_sidecar(path)
            if entry is None or entry.get("source_url") != url:
                continue
            age = _now() - float(entry["fetched_at"])
            if age < 0 or age > self.ttl_seconds:
                # Expired — delete blob + sidecar and keep scanning.
                with suppress(OSError):
                    path.unlink()
                with suppress(OSError):
                    self._blob_path(path.stem, str(entry.get("ext") or _FALLBACK_EXT)).unlink()
                continue
            blob = self._blob_path(path.stem, str(entry.get("ext") or _FALLBACK_EXT))
            if not blob.is_file():
                # Blob vanished — the entry is dead; clean the sidecar.
                with suppress(OSError):
                    path.unlink()
                continue
            if best is None or float(entry["fetched_at"]) > best[0]:
                best = (float(entry["fetched_at"]), blob, entry)
        if best is None:
            return None
        return best[1], best[2]

    # ── Fetch ─────────────────────────────────────────────

    def fetch_into_cache(
        self,
        url: str,
        *,
        license_note: str,
        kind: str,
        timeout: float = 30.0,
        max_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    ) -> Path:
        """Fetch ``url`` into the cache and return the local blob path.

        Args:
            url: https-only source URL.
            license_note: REQUIRED non-empty provenance note (refused
                when blank — compliance).
            kind: Declared media kind (``"video"`` / ``"image"``),
                recorded in the sidecar; the downstream resolve step
                validates the blob's extension against it.
            timeout: Per-request timeout (seconds).
            max_bytes: Per-entry download cap; exceeding it raises
                :class:`MediaCacheError` and leaves nothing behind.

        Returns:
            Path to the cached blob. A hit (URL seen before, entry alive)
            reuses the existing blob with ``fetched_at`` refreshed and
            never touches the network.
        """
        url = str(url)
        if not str(license_note or "").strip():
            raise MediaCacheError(
                f"license_note is required to fetch {url!r} — set the item's "
                "'note' field (source attribution / license) and retry"
            )
        if urlparse(url).scheme.lower() != "https":
            raise MediaCacheError(
                f"refusing to fetch {url!r}: only https:// URLs are allowed"
            )

        # 1. URL-level hit: reuse without any network round-trip.
        found = self._find_by_url(url)
        if found is not None:
            blob, entry = found
            self.hits += 1
            self._refresh_fetched_at(blob, entry)
            return blob

        # 2. Miss: stream the download with a hard per-entry size cap.
        self.misses += 1
        tmp_path, sha, ext, data_len, content_type = self._download(url, timeout, max_bytes)

        # 3. Content-level dedupe: identical bytes already cached → reuse
        #    the existing blob (cross-URL dedupe), refresh provenance.
        sidecar = self._sidecar_path(sha)
        existing = self._read_sidecar(sidecar)
        if existing is not None:
            old_ext = str(existing.get("ext") or ext)
            old_blob = self._blob_path(sha, old_ext)
            if old_blob.is_file():
                with suppress(OSError):
                    os.unlink(tmp_path)
                self._write_sidecar(
                    sidecar, sha, url, license_note, kind, old_ext,
                    int(existing["bytes"]), str(existing.get("content_type") or content_type),
                )
                self._refresh_fetched_at(old_blob, existing)
                return old_blob
            # Sidecar without a blob — drop the stale sidecar, store fresh.
            with suppress(OSError):
                sidecar.unlink()

        # 4. Store: temp → <sha256><ext>, then write the sidecar.
        blob = self._blob_path(sha, ext)
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            os.replace(tmp_path, blob)
        except OSError as e:
            with suppress(OSError):
                os.unlink(tmp_path)
            raise MediaCacheError(f"cannot store media cache entry: {e}") from e
        self.bytes += data_len
        self._write_sidecar(
            sidecar, sha, url, license_note, kind, ext, data_len, content_type
        )
        self._evict_over_cap()
        return blob

    # ── Download ──────────────────────────────────────────

    def _download(
        self, url: str, timeout: float, max_bytes: int
    ) -> tuple[Path, str, str, int, str]:
        """Stream ``url`` to a ``.part`` temp file inside the cache dir.

        Returns ``(tmp_path, sha256, ext, size, content_type)``. Raises
        :class:`MediaCacheError` on network / HTTP / size-cap failure;
        the temp file is always cleaned up on failure.
        """
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise MediaCacheError(f"cannot create media cache dir: {e}") from e
        tmp_path: Optional[str] = None
        content_type = ""
        ext = _FALLBACK_EXT
        digest = hashlib.sha256()
        total = 0
        try:
            with httpx.Client(
                timeout=timeout, transport=self._transport, follow_redirects=True
            ) as client:
                with client.stream("GET", url) as resp:
                    resp.raise_for_status()
                    content_type = str(resp.headers.get("content-type", ""))
                    ext = _ext_for(url, content_type)
                    fd, tmp_path = tempfile.mkstemp(
                        dir=str(self.cache_dir), suffix=".part"
                    )
                    try:
                        with os.fdopen(fd, "wb") as f:
                            for chunk in resp.iter_bytes():
                                total += len(chunk)
                                if total > max_bytes:
                                    raise MediaCacheError(
                                        f"{url!r} exceeds the per-entry download cap "
                                        f"({max_bytes} bytes) — aborted"
                                    )
                                digest.update(chunk)
                                f.write(chunk)
                    except BaseException:
                        with suppress(OSError):
                            os.unlink(tmp_path)
                        raise
        except MediaCacheError:
            raise
        except Exception as e:  # noqa: BLE001 — network layer: wrap into MediaCacheError
            raise MediaCacheError(f"failed to fetch {url!r}: {e}") from e
        assert tmp_path is not None  # narrows for mypy; set before any success path
        return Path(tmp_path), digest.hexdigest(), ext, total, content_type

    # ── Sidecar writes ────────────────────────────────────

    def _write_sidecar(
        self,
        sidecar: Path,
        sha: str,
        url: str,
        license_note: str,
        kind: str,
        ext: str,
        data_len: int,
        content_type: str,
    ) -> None:
        """Persist the provenance sidecar atomically (best-effort)."""
        entry = {
            "sha256": str(sha),
            "source_url": str(url),
            "license_note": str(license_note),
            "kind": str(kind),
            "ext": str(ext),
            "fetched_at": _now(),
            "bytes": int(data_len),
            "content_type": str(content_type),
        }
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(self.cache_dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(entry, f, ensure_ascii=False)
                os.replace(tmp_path, sidecar)
            except BaseException:
                with suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except OSError:
            # A failed sidecar write must not lose the stored blob; the
            # next fetch sees a blobless sidecar and self-heals.
            return

    def _refresh_fetched_at(self, blob: Path, entry: dict) -> None:
        """Refresh ``fetched_at`` in a sidecar (hit → keep-alive)."""
        self._write_sidecar(
            self._sidecar_path(blob.stem),
            str(entry.get("sha256") or blob.stem),
            str(entry.get("source_url") or ""),
            str(entry.get("license_note") or ""),
            str(entry.get("kind") or ""),
            str(entry.get("ext") or blob.suffix),
            int(entry.get("bytes") or 0),
            str(entry.get("content_type") or ""),
        )

    # ── Eviction ──────────────────────────────────────────

    def _evict_over_cap(self) -> None:
        """Enforce the cache-wide byte cap, evicting oldest blobs first.

        Mirrors ``prompt_cache._evict_over_cap`` (enforced on write,
        oldest mtime first); every blob eviction also removes its
        sidecar. Best-effort — filesystem errors abort quietly.
        """
        try:
            blobs = [
                p
                for p in self.cache_dir.iterdir()
                if p.is_file()
                and not p.name.endswith(".json")
                and not p.name.endswith(".tmp")
                and not p.name.endswith(".part")
            ]
            sizes = {p: p.stat().st_size for p in blobs}
        except OSError:
            return
        total = sum(sizes.values())
        if total <= self.max_bytes:
            return
        try:
            ordered = sorted(blobs, key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        for path in ordered:
            if total <= self.max_bytes:
                break
            size = sizes.get(path, 0)
            with suppress(OSError):
                path.unlink()
                total -= size
            with suppress(OSError):
                self._sidecar_path(path.stem).unlink()

    # ── Stats ─────────────────────────────────────────────

    def stats(self) -> dict:
        """Return hit/miss/byte counters as a JSON-serializable dict."""
        total = self.hits + self.misses
        return {
            "hits": self.hits,
            "misses": self.misses,
            "bytes": self.bytes,
            "hit_rate": (self.hits / total) if total else 0.0,
        }


# ── Process-level shared instance ─────────────────────────

_shared_cache: Optional[MediaCache] = None


def get_media_cache() -> MediaCache:
    """Return the process-level shared :class:`MediaCache`.

    Constructed lazily so tests can call :func:`reset_media_cache` and
    inject a fresh instance.
    """
    global _shared_cache
    if _shared_cache is None:
        _shared_cache = MediaCache()
    return _shared_cache


def reset_media_cache() -> None:
    """Drop the shared instance (used by tests / config reloads)."""
    global _shared_cache
    _shared_cache = None


def fetch_into_cache(
    url: str,
    *,
    license_note: str,
    kind: str,
    timeout: float = 30.0,
    max_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
    cache: Optional[MediaCache] = None,
) -> Path:
    """Module-level fetch into the shared cache.

    See :meth:`MediaCache.fetch_into_cache` for the full contract.
    ``cache`` lets callers (tests) inject an explicit instance with a
    custom directory or httpx transport; production callers omit it.
    """
    active = cache if cache is not None else get_media_cache()
    return active.fetch_into_cache(
        url, license_note=license_note, kind=kind, timeout=timeout, max_bytes=max_bytes
    )


def stats() -> dict:
    """Return the shared cache's hit/miss/byte counters (module-level)."""
    return get_media_cache().stats()
