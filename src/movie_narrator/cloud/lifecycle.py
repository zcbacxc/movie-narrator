# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Artifact lifecycle — TTL-based cleanup and retention (v0.8.3).

Rendered artifacts (``final.mp4``, narration audio, subtitles, clips)
are large and pile up quickly on a long-running worker. This module
adds a retention policy on top of the :class:`~movie_narrator.cloud.
artifact_store.StorageBackend` abstraction:

- **TTL** — delete artifacts older than ``ttl_seconds``.
- **Size cap** — evict oldest-first until the store fits in
  ``max_total_bytes``.
- **keep_last_n** — always retain the N most recent artifacts, no
  matter what the two rules above say.
- **Protection** — never touch artifacts belonging to a task that is
  still pending/running; callers pass ``protected_keys`` or an
  ``is_protected`` predicate.
- **dry_run** — report what *would* be deleted without deleting.

Everything is expressed as a pure function, :func:`cleanup_artifacts`,
so it can be unit-tested with a frozen clock. :class:`ArtifactSweeper`
wraps it in a daemon thread for the API server, and ``mn artifacts
cleanup`` exposes it on the CLI.

Environment variables:
    ``MN_ARTIFACT_TTL``             TTL in seconds (0 = keep forever, default)
    ``MN_ARTIFACT_MAX_BYTES``       total size cap in bytes (0 = unlimited)
    ``MN_ARTIFACT_KEEP_LAST``       always keep the N newest (0 = disabled)
    ``MN_ARTIFACT_SWEEP_INTERVAL``  sweeper period in seconds (default 3600)

Typical usage::

    from movie_narrator.cloud.artifact_store import get_artifact_store
    from movie_narrator.cloud.lifecycle import (
        ArtifactLifecyclePolicy, cleanup_artifacts,
    )

    policy = ArtifactLifecyclePolicy(ttl_seconds=7 * 86400, keep_last_n=5)
    report = cleanup_artifacts(get_artifact_store(), policy)
    print(report.summary())
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .artifact_store import ArtifactInfo, ArtifactStoreError, StorageBackend

logger = logging.getLogger(__name__)

#: Default sweeper period when ``MN_ARTIFACT_SWEEP_INTERVAL`` is unset.
DEFAULT_SWEEP_INTERVAL = 3600.0

#: Predicate deciding whether an artifact must be preserved.
ProtectionPredicate = Callable[[ArtifactInfo], bool]


# ── Policy ─────────────────────────────────────────────────


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    """Read a non-negative integer env var, falling back to *default*."""
    raw = env.get(name)
    if raw is None or not str(raw).strip():
        return default
    try:
        value = int(str(raw).strip())
    except ValueError:
        logger.warning("Ignoring invalid %s=%r (expected an integer)", name, raw)
        return default
    if value < 0:
        logger.warning("Ignoring negative %s=%r", name, raw)
        return default
    return value


@dataclass
class ArtifactLifecyclePolicy:
    """Retention rules for stored artifacts.

    Attributes:
        ttl_seconds: Delete artifacts at least this old. ``0`` disables
            TTL expiry (keep forever) — the default, so existing
            deployments never lose data by upgrading.
        max_total_bytes: Cap on the total size of the store. ``0``
            means unlimited. Enforced oldest-first after TTL expiry.
        keep_last_n: Always retain the N newest artifacts. ``0``
            disables the guarantee.
        dry_run: When True nothing is deleted; the report lists what
            *would* have been removed.
    """

    ttl_seconds: int = 0
    max_total_bytes: int = 0
    keep_last_n: int = 0
    dry_run: bool = False

    @property
    def enabled(self) -> bool:
        """True when at least one reclaiming rule is active."""
        return self.ttl_seconds > 0 or self.max_total_bytes > 0

    @classmethod
    def from_env(
        cls,
        env: Optional[Mapping[str, str]] = None,
        *,
        dry_run: bool = False,
    ) -> "ArtifactLifecyclePolicy":
        """Build a policy from ``MN_ARTIFACT_*`` environment variables."""
        environ: Mapping[str, str] = os.environ if env is None else env
        return cls(
            ttl_seconds=_env_int(environ, "MN_ARTIFACT_TTL", 0),
            max_total_bytes=_env_int(environ, "MN_ARTIFACT_MAX_BYTES", 0),
            keep_last_n=_env_int(environ, "MN_ARTIFACT_KEEP_LAST", 0),
            dry_run=dry_run,
        )


def sweep_interval_from_env(env: Optional[Mapping[str, str]] = None) -> float:
    """
    Returns:
        The sweeper period from ``MN_ARTIFACT_SWEEP_INTERVAL``.
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    raw = environ.get("MN_ARTIFACT_SWEEP_INTERVAL")
    if raw is None or not str(raw).strip():
        return DEFAULT_SWEEP_INTERVAL
    try:
        value = float(str(raw).strip())
    except ValueError:
        logger.warning("Ignoring invalid MN_ARTIFACT_SWEEP_INTERVAL=%r", raw)
        return DEFAULT_SWEEP_INTERVAL
    if value <= 0:
        logger.warning("Ignoring non-positive MN_ARTIFACT_SWEEP_INTERVAL=%r", raw)
        return DEFAULT_SWEEP_INTERVAL
    return value


# ── Report ─────────────────────────────────────────────────


def format_bytes(size: float) -> str:
    """Render a byte count in human-readable form (1 KB = 1024 B)."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(value) < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} B"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{value:.1f} TB"  # pragma: no cover - unreachable


@dataclass
class CleanupReport:
    """Outcome of a :func:`cleanup_artifacts` run.

    Attributes:
        deleted: Keys removed (or, in dry-run mode, that would be).
        freed_bytes: Bytes reclaimed by those deletions.
        skipped: Keys deliberately preserved (protected or kept by
            ``keep_last_n``) that a rule would otherwise have removed.
        errors: ``(key, message)`` pairs for deletions that failed. A
            failure never aborts the sweep.
        scanned: Number of artifacts examined.
        dry_run: Whether this was a preview.
    """

    deleted: List[str] = field(default_factory=list)
    freed_bytes: int = 0
    skipped: List[str] = field(default_factory=list)
    errors: List[Tuple[str, str]] = field(default_factory=list)
    scanned: int = 0
    dry_run: bool = False

    @property
    def deleted_count(self) -> int:
        """Number of artifacts deleted."""
        return len(self.deleted)

    def summary(self) -> str:
        """
        Returns:
            A one-line human-readable summary.
        """
        prefix = "[dry-run] would delete" if self.dry_run else "deleted"
        return (
            f"{prefix} {len(self.deleted)} artifact(s), "
            f"{format_bytes(self.freed_bytes)} freed, "
            f"{len(self.skipped)} kept, {len(self.errors)} error(s) "
            f"({self.scanned} scanned)"
        )


# ── Cleanup ────────────────────────────────────────────────


def make_task_protection(active_task_ids: Iterable[str]) -> ProtectionPredicate:
    """Protect artifacts owned by still-running tasks.

    Assumes the task-scoped key layout ``<task_id>/<filename>`` used by
    :func:`~movie_narrator.cloud.artifact_store.get_task_artifact_store`
    for remote backends, and also matches a bare ``<task_id>`` key.

    Args:
        active_task_ids: IDs of tasks that are pending/running/retrying.

    Returns:
        A predicate suitable for ``cleanup_artifacts(is_protected=...)``.
    """
    ids: Set[str] = {tid for tid in active_task_ids if tid}

    def _protected(info: ArtifactInfo) -> bool:
        head = info.key.split("/", 1)[0]
        return head in ids

    return _protected


def cleanup_artifacts(
    store: StorageBackend,
    policy: ArtifactLifecyclePolicy,
    *,
    now: Optional[float] = None,
    prefix: str = "",
    protected_keys: Optional[Iterable[str]] = None,
    is_protected: Optional[ProtectionPredicate] = None,
) -> CleanupReport:
    """Apply *policy* to the artifacts in *store*.

    The passes run in this order:

    1. **TTL** — every artifact whose age is ``>= policy.ttl_seconds``
       is deleted (skipped when ``ttl_seconds == 0``).
    2. **Size cap** — while the surviving artifacts exceed
       ``policy.max_total_bytes``, the oldest is evicted.

    Both passes refuse to touch a protected artifact or one of the
    ``keep_last_n`` newest artifacts; such candidates are recorded in
    ``report.skipped`` instead.

    Args:
        store: Backend to clean.
        policy: Retention rules to apply.
        now: POSIX timestamp treated as "current time". Defaults to
            :func:`time.time`; injectable so tests can freeze the clock.
        prefix: Restrict the sweep to keys under this prefix.
        protected_keys: Exact keys that must never be deleted — the
            simple way for a caller to guard in-flight work.
        is_protected: Predicate for the same purpose, evaluated per
            artifact (e.g. :func:`make_task_protection`).

    Returns:
        A :class:`CleanupReport`. Deletion failures are collected in
        ``report.errors`` rather than raised, so one bad file cannot
        abort a sweep.
    """
    current = time.time() if now is None else now
    guarded: Set[str] = set(protected_keys or ())

    try:
        artifacts: List[ArtifactInfo] = list(store.list(prefix))
    except ArtifactStoreError as exc:
        report = CleanupReport(dry_run=policy.dry_run)
        report.errors.append((prefix or "<all>", str(exc)))
        return report

    # Oldest first — both passes evict from the front.
    artifacts.sort(key=lambda info: (info.modified_at, info.key))
    report = CleanupReport(scanned=len(artifacts), dry_run=policy.dry_run)

    # The ``keep_last_n`` newest artifacts are exempt from every rule.
    kept_recent: Set[str] = set()
    if policy.keep_last_n > 0:
        kept_recent = {info.key for info in artifacts[-policy.keep_last_n :]}

    def _exempt(info: ArtifactInfo) -> bool:
        if info.key in guarded:
            return True
        if info.key in kept_recent:
            return True
        return bool(is_protected and is_protected(info))

    def _remove(info: ArtifactInfo) -> bool:
        """Delete *info* honouring dry-run; returns True on success."""
        if policy.dry_run:
            report.deleted.append(info.key)
            report.freed_bytes += info.size
            return True
        try:
            store.delete(info.key)
        except Exception as exc:  # noqa: BLE001 - one bad key must not abort the sweep
            logger.warning("Failed to delete artifact %r: %s", info.key, exc)
            report.errors.append((info.key, str(exc)))
            return False
        report.deleted.append(info.key)
        report.freed_bytes += info.size
        return True

    survivors: List[ArtifactInfo] = []

    # ── Pass 1: TTL expiry ─────────────────────────────────
    for info in artifacts:
        expired = policy.ttl_seconds > 0 and (current - info.modified_at) >= policy.ttl_seconds
        if not expired:
            survivors.append(info)
            continue
        if _exempt(info):
            report.skipped.append(info.key)
            survivors.append(info)
            continue
        if not _remove(info):
            survivors.append(info)

    # ── Pass 2: total size cap ─────────────────────────────
    if policy.max_total_bytes > 0:
        total = sum(info.size for info in survivors)
        for info in list(survivors):
            if total <= policy.max_total_bytes:
                break
            if _exempt(info):
                if info.key not in report.skipped:
                    report.skipped.append(info.key)
                continue
            if _remove(info):
                total -= info.size
                survivors.remove(info)

    return report


# ── Background sweeper ─────────────────────────────────────


class ArtifactSweeper:
    """Daemon thread that periodically runs :func:`cleanup_artifacts`.

    Started by :class:`~movie_narrator.cloud.api.TaskAPIServer` when a
    retention rule is configured. The loop is deliberately defensive: a
    failing sweep is logged and retried on the next tick, never
    propagated, so artifact housekeeping can not take the API server
    down.

    Args:
        store: Backend to sweep.
        policy: Retention rules.
        interval: Seconds between sweeps.
        protected_ids: Callable returning the IDs of tasks that are
            currently in flight; their artifacts are preserved.
        prefix: Restrict sweeping to keys under this prefix.
        name: Thread name.
    """

    def __init__(
        self,
        store: StorageBackend,
        policy: ArtifactLifecyclePolicy,
        *,
        interval: float = DEFAULT_SWEEP_INTERVAL,
        protected_ids: Optional[Callable[[], Iterable[str]]] = None,
        prefix: str = "",
        name: str = "mn-artifact-sweeper",
    ) -> None:
        self._store = store
        self._policy = policy
        self._interval = max(float(interval), 1.0)
        self._protected_ids = protected_ids
        self._prefix = prefix
        self._name = name
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_report: Optional[CleanupReport] = None

    # ── Introspection ───────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """Whether the sweeper thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def interval(self) -> float:
        """Seconds between sweeps."""
        return self._interval

    @property
    def last_report(self) -> Optional[CleanupReport]:
        """Report from the most recent sweep (None before the first)."""
        return self._last_report

    # ── Lifecycle ───────────────────────────────────────────

    def start(self) -> None:
        """Start the background thread (no-op when already running)."""
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
        self._thread.start()
        logger.info(
            "Artifact sweeper started (interval=%.0fs, ttl=%ds, max_bytes=%d, keep_last=%d)",
            self._interval,
            self._policy.ttl_seconds,
            self._policy.max_total_bytes,
            self._policy.keep_last_n,
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the thread to exit and wait up to *timeout* seconds."""
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=timeout)

    def sweep_once(self) -> Optional[CleanupReport]:
        """Run a single sweep; returns None when the sweep failed."""
        protection: Optional[ProtectionPredicate] = None
        if self._protected_ids is not None:
            try:
                protection = make_task_protection(self._protected_ids())
            except Exception as exc:  # noqa: BLE001 - never trust the callback
                logger.warning("Artifact sweeper could not resolve active tasks: %s", exc)
                return None
        try:
            report = cleanup_artifacts(
                self._store,
                self._policy,
                prefix=self._prefix,
                is_protected=protection,
            )
        except Exception as exc:  # noqa: BLE001 - housekeeping must never crash the server
            logger.warning("Artifact sweep failed: %s", exc)
            return None
        self._last_report = report
        if report.deleted or report.errors:
            logger.info("Artifact sweep: %s", report.summary())
        return report

    # ── Internals ───────────────────────────────────────────

    def _loop(self) -> None:
        while not self._stop.is_set():
            self.sweep_once()
            if self._stop.wait(self._interval):
                break
        logger.debug("Artifact sweeper stopped")

    def __enter__(self) -> "ArtifactSweeper":
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()


def describe_policy(policy: ArtifactLifecyclePolicy) -> Sequence[str]:
    """
    Returns:
        Human-readable lines describing *policy* (used by the CLI).
    """
    lines = [
        f"ttl_seconds     : {policy.ttl_seconds or 'disabled'}",
        f"max_total_bytes : "
        f"{format_bytes(policy.max_total_bytes) if policy.max_total_bytes else 'unlimited'}",
        f"keep_last_n     : {policy.keep_last_n or 'disabled'}",
        f"dry_run         : {policy.dry_run}",
    ]
    return lines


__all__ = [
    "DEFAULT_SWEEP_INTERVAL",
    "ArtifactLifecyclePolicy",
    "ArtifactSweeper",
    "CleanupReport",
    "ProtectionPredicate",
    "cleanup_artifacts",
    "describe_policy",
    "format_bytes",
    "make_task_protection",
    "sweep_interval_from_env",
]
