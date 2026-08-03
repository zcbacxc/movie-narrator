# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v0.8.3: artifact lifecycle / TTL cleanup (``lifecycle``).

Covers:
- ``ArtifactLifecyclePolicy`` defaults and ``MN_ARTIFACT_*`` env parsing
- TTL expiry boundaries with a frozen clock
- ``keep_last_n`` retention
- size-cap eviction order (oldest first)
- ``dry_run`` previews
- protected in-flight keys (``protected_keys`` and ``is_protected``)
- deletion errors are reported, never raised
- the background ``ArtifactSweeper`` thread
- the ``mn artifacts`` CLI commands
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

import pytest
from typer.testing import CliRunner

from movie_narrator.cli import app
from movie_narrator.cloud.artifact_store import (
    ArtifactInfo,
    ArtifactStoreError,
    LocalArtifactStore,
    StorageBackend,
)
from movie_narrator.cloud.lifecycle import (
    DEFAULT_SWEEP_INTERVAL,
    ArtifactLifecyclePolicy,
    ArtifactSweeper,
    CleanupReport,
    cleanup_artifacts,
    describe_policy,
    format_bytes,
    make_task_protection,
    sweep_interval_from_env,
)

DAY = 86400.0
NOW = 1_800_000_000.0


# ── Test doubles ──────────────────────────────────────────


class MemoryStore:
    """Minimal in-memory :class:`StorageBackend` with a controllable clock."""

    def __init__(self) -> None:
        self._items: dict[str, ArtifactInfo] = {}
        self.deleted: List[str] = []
        self.fail_on: set[str] = set()

    # ── seeding ────────────────────────────────────────────

    def seed(self, key: str, size: int, age_seconds: float, *, now: float = NOW) -> None:
        """Add an artifact of *size* bytes that is *age_seconds* old."""
        self._items[key] = ArtifactInfo(key=key, size=size, modified_at=now - age_seconds)

    @property
    def keys(self) -> List[str]:
        return sorted(self._items)

    # ── StorageBackend ─────────────────────────────────────

    def put(self, key: str, src_path) -> ArtifactInfo:  # pragma: no cover - unused
        raise NotImplementedError

    def get(self, key: str, dest_path) -> Path:  # pragma: no cover - unused
        raise NotImplementedError

    def open(self, key: str):  # pragma: no cover - unused
        raise NotImplementedError

    def exists(self, key: str) -> bool:
        return key in self._items

    def delete(self, key: str) -> bool:
        if key in self.fail_on:
            raise OSError(f"disk on fire: {key}")
        self.deleted.append(key)
        return self._items.pop(key, None) is not None

    def list(self, prefix: str = "") -> Iterator[ArtifactInfo]:
        for key in sorted(self._items):
            if key.startswith(prefix):
                yield self._items[key]

    def stat(self, key: str) -> ArtifactInfo:
        return self._items[key]

    def url(self, key: str, *, expires_in: int = 3600) -> Optional[str]:
        return None


@pytest.fixture
def store() -> MemoryStore:
    return MemoryStore()


# ════════════════════════════════════════════════════════════
#  Policy
# ════════════════════════════════════════════════════════════


class TestPolicy:
    """Defaults, env parsing and the ``enabled`` predicate."""

    def test_defaults_keep_everything(self):
        """Out of the box nothing is ever deleted."""
        policy = ArtifactLifecyclePolicy()
        assert policy.ttl_seconds == 0
        assert policy.max_total_bytes == 0
        assert policy.keep_last_n == 0
        assert policy.dry_run is False
        assert policy.enabled is False

    def test_enabled_with_ttl_or_size(self):
        """Either rule flips the policy on; keep_last alone does not."""
        assert ArtifactLifecyclePolicy(ttl_seconds=1).enabled is True
        assert ArtifactLifecyclePolicy(max_total_bytes=1).enabled is True
        assert ArtifactLifecyclePolicy(keep_last_n=5).enabled is False

    def test_from_env_reads_all_vars(self):
        """MN_ARTIFACT_* variables populate the policy."""
        policy = ArtifactLifecyclePolicy.from_env(
            {
                "MN_ARTIFACT_TTL": "604800",
                "MN_ARTIFACT_MAX_BYTES": "1024",
                "MN_ARTIFACT_KEEP_LAST": "3",
            }
        )
        assert policy.ttl_seconds == 604800
        assert policy.max_total_bytes == 1024
        assert policy.keep_last_n == 3
        assert policy.dry_run is False

    def test_from_env_defaults_when_unset(self):
        """An empty environment yields the keep-forever defaults."""
        policy = ArtifactLifecyclePolicy.from_env({})
        assert (policy.ttl_seconds, policy.max_total_bytes, policy.keep_last_n) == (0, 0, 0)

    @pytest.mark.parametrize("raw", ["", "   ", "abc", "-5", "1.5"])
    def test_from_env_ignores_invalid(self, raw):
        """Malformed values fall back to the default instead of crashing."""
        policy = ArtifactLifecyclePolicy.from_env({"MN_ARTIFACT_TTL": raw})
        assert policy.ttl_seconds == 0

    def test_from_env_dry_run_flag(self):
        """dry_run is passed through, not read from the environment."""
        assert ArtifactLifecyclePolicy.from_env({}, dry_run=True).dry_run is True

    def test_sweep_interval_default(self):
        """The sweep interval defaults to one hour."""
        assert sweep_interval_from_env({}) == DEFAULT_SWEEP_INTERVAL
        assert DEFAULT_SWEEP_INTERVAL == 3600.0

    @pytest.mark.parametrize("raw", ["abc", "0", "-1", ""])
    def test_sweep_interval_invalid(self, raw):
        """Invalid or non-positive intervals fall back to the default."""
        assert sweep_interval_from_env({"MN_ARTIFACT_SWEEP_INTERVAL": raw}) == (
            DEFAULT_SWEEP_INTERVAL
        )

    def test_sweep_interval_from_env(self):
        """A valid interval is honoured."""
        assert sweep_interval_from_env({"MN_ARTIFACT_SWEEP_INTERVAL": "90"}) == 90.0

    def test_describe_policy_lines(self):
        """describe_policy renders every knob for the CLI."""
        lines = "\n".join(describe_policy(ArtifactLifecyclePolicy(ttl_seconds=60)))
        assert "ttl_seconds" in lines
        assert "unlimited" in lines
        assert "disabled" in lines


class TestFormatBytes:
    """Human-readable byte rendering."""

    @pytest.mark.parametrize(
        "size,expected",
        [(0, "0 B"), (512, "512 B"), (2048, "2.0 KB"), (5 * 1024**2, "5.0 MB")],
    )
    def test_format(self, size, expected):
        assert format_bytes(size) == expected


# ════════════════════════════════════════════════════════════
#  TTL expiry
# ════════════════════════════════════════════════════════════


class TestTtlExpiry:
    """TTL pass with a frozen clock."""

    def test_disabled_ttl_deletes_nothing(self, store):
        """ttl_seconds=0 keeps everything, however old."""
        store.seed("old.mp4", 10, age_seconds=365 * DAY)
        report = cleanup_artifacts(store, ArtifactLifecyclePolicy(), now=NOW)
        assert report.deleted == []
        assert store.keys == ["old.mp4"]

    def test_expired_artifact_deleted(self, store):
        """An artifact older than the TTL is removed."""
        store.seed("old.mp4", 100, age_seconds=8 * DAY)
        store.seed("new.mp4", 100, age_seconds=1 * DAY)
        policy = ArtifactLifecyclePolicy(ttl_seconds=int(7 * DAY))
        report = cleanup_artifacts(store, policy, now=NOW)
        assert report.deleted == ["old.mp4"]
        assert report.freed_bytes == 100
        assert store.keys == ["new.mp4"]

    def test_boundary_exactly_at_ttl_is_expired(self, store):
        """Age == TTL counts as expired (inclusive boundary)."""
        store.seed("edge.mp4", 1, age_seconds=100)
        policy = ArtifactLifecyclePolicy(ttl_seconds=100)
        report = cleanup_artifacts(store, policy, now=NOW)
        assert report.deleted == ["edge.mp4"]

    def test_boundary_one_second_below_ttl_survives(self, store):
        """Age == TTL-1 survives."""
        store.seed("edge.mp4", 1, age_seconds=99)
        policy = ArtifactLifecyclePolicy(ttl_seconds=100)
        report = cleanup_artifacts(store, policy, now=NOW)
        assert report.deleted == []
        assert store.keys == ["edge.mp4"]

    def test_boundary_one_second_above_ttl_expires(self, store):
        """Age == TTL+1 is deleted."""
        store.seed("edge.mp4", 1, age_seconds=101)
        policy = ArtifactLifecyclePolicy(ttl_seconds=100)
        assert cleanup_artifacts(store, policy, now=NOW).deleted == ["edge.mp4"]

    def test_now_defaults_to_wall_clock(self, store):
        """Omitting ``now`` uses the real clock."""
        store.seed("old.mp4", 1, age_seconds=10, now=time.time())
        policy = ArtifactLifecyclePolicy(ttl_seconds=5)
        assert cleanup_artifacts(store, policy).deleted == ["old.mp4"]

    def test_report_counts_scanned(self, store):
        """The report tracks how many artifacts were examined."""
        store.seed("a", 1, age_seconds=1)
        store.seed("b", 1, age_seconds=1)
        report = cleanup_artifacts(store, ArtifactLifecyclePolicy(ttl_seconds=10), now=NOW)
        assert report.scanned == 2

    def test_prefix_limits_scope(self, store):
        """A prefix restricts the sweep."""
        store.seed("keep/old.mp4", 1, age_seconds=999)
        store.seed("sweep/old.mp4", 1, age_seconds=999)
        policy = ArtifactLifecyclePolicy(ttl_seconds=10)
        report = cleanup_artifacts(store, policy, now=NOW, prefix="sweep/")
        assert report.deleted == ["sweep/old.mp4"]
        assert "keep/old.mp4" in store.keys


# ════════════════════════════════════════════════════════════
#  keep_last_n
# ════════════════════════════════════════════════════════════


class TestKeepLastN:
    """The N newest artifacts survive every rule."""

    def test_keep_last_protects_newest(self, store):
        """Even fully expired artifacts survive when in the newest N."""
        for i, age in enumerate([10 * DAY, 9 * DAY, 8 * DAY]):
            store.seed(f"a{i}.mp4", 10, age_seconds=age)
        policy = ArtifactLifecyclePolicy(ttl_seconds=int(DAY), keep_last_n=2)
        report = cleanup_artifacts(store, policy, now=NOW)
        # a0 is the oldest, a1/a2 are the two newest and therefore kept.
        assert report.deleted == ["a0.mp4"]
        assert sorted(report.skipped) == ["a1.mp4", "a2.mp4"]
        assert store.keys == ["a1.mp4", "a2.mp4"]

    def test_keep_last_larger_than_store(self, store):
        """keep_last_n >= artifact count keeps everything."""
        store.seed("a.mp4", 10, age_seconds=99 * DAY)
        policy = ArtifactLifecyclePolicy(ttl_seconds=1, keep_last_n=5)
        report = cleanup_artifacts(store, policy, now=NOW)
        assert report.deleted == []
        assert report.skipped == ["a.mp4"]

    def test_keep_last_zero_disables(self, store):
        """keep_last_n=0 offers no protection."""
        store.seed("a.mp4", 10, age_seconds=99 * DAY)
        policy = ArtifactLifecyclePolicy(ttl_seconds=1, keep_last_n=0)
        assert cleanup_artifacts(store, policy, now=NOW).deleted == ["a.mp4"]

    def test_keep_last_survives_size_cap(self, store):
        """keep_last_n also wins against the size cap."""
        store.seed("old.mp4", 100, age_seconds=3 * DAY)
        store.seed("new.mp4", 100, age_seconds=1 * DAY)
        policy = ArtifactLifecyclePolicy(max_total_bytes=50, keep_last_n=2)
        report = cleanup_artifacts(store, policy, now=NOW)
        assert report.deleted == []
        assert sorted(report.skipped) == ["new.mp4", "old.mp4"]


# ════════════════════════════════════════════════════════════
#  Size cap
# ════════════════════════════════════════════════════════════


class TestSizeCap:
    """Oldest-first eviction until the store fits the cap."""

    def test_evicts_oldest_first(self, store):
        """Eviction order follows modification time, oldest first."""
        store.seed("oldest.mp4", 100, age_seconds=3 * DAY)
        store.seed("middle.mp4", 100, age_seconds=2 * DAY)
        store.seed("newest.mp4", 100, age_seconds=1 * DAY)
        policy = ArtifactLifecyclePolicy(max_total_bytes=150)
        report = cleanup_artifacts(store, policy, now=NOW)
        assert report.deleted == ["oldest.mp4", "middle.mp4"]
        assert report.freed_bytes == 200
        assert store.keys == ["newest.mp4"]

    def test_stops_as_soon_as_it_fits(self, store):
        """Eviction stops at the first size that satisfies the cap."""
        store.seed("a.mp4", 100, age_seconds=3 * DAY)
        store.seed("b.mp4", 100, age_seconds=2 * DAY)
        policy = ArtifactLifecyclePolicy(max_total_bytes=100)
        report = cleanup_artifacts(store, policy, now=NOW)
        assert report.deleted == ["a.mp4"]
        assert store.keys == ["b.mp4"]

    def test_under_cap_deletes_nothing(self, store):
        """A store already within the cap is untouched."""
        store.seed("a.mp4", 10, age_seconds=DAY)
        policy = ArtifactLifecyclePolicy(max_total_bytes=1000)
        assert cleanup_artifacts(store, policy, now=NOW).deleted == []

    def test_zero_cap_means_unlimited(self, store):
        """max_total_bytes=0 disables the cap entirely."""
        store.seed("a.mp4", 10_000, age_seconds=DAY)
        assert cleanup_artifacts(store, ArtifactLifecyclePolicy(), now=NOW).deleted == []

    def test_ttl_runs_before_size_cap(self, store):
        """TTL frees space first, so the cap may need no further eviction."""
        store.seed("expired.mp4", 100, age_seconds=10 * DAY)
        store.seed("fresh.mp4", 100, age_seconds=1 * DAY)
        policy = ArtifactLifecyclePolicy(ttl_seconds=int(2 * DAY), max_total_bytes=150)
        report = cleanup_artifacts(store, policy, now=NOW)
        assert report.deleted == ["expired.mp4"]
        assert store.keys == ["fresh.mp4"]


# ════════════════════════════════════════════════════════════
#  Protection & dry-run
# ════════════════════════════════════════════════════════════


class TestProtection:
    """In-flight work must never be swept."""

    def test_protected_keys_are_skipped(self, store):
        """Exact protected keys survive TTL expiry."""
        store.seed("running.mp4", 10, age_seconds=99 * DAY)
        store.seed("done.mp4", 10, age_seconds=99 * DAY)
        policy = ArtifactLifecyclePolicy(ttl_seconds=1)
        report = cleanup_artifacts(
            store, policy, now=NOW, protected_keys=["running.mp4"]
        )
        assert report.deleted == ["done.mp4"]
        assert report.skipped == ["running.mp4"]
        assert "running.mp4" in store.keys

    def test_protected_keys_survive_size_cap(self, store):
        """Protection also applies to size-cap eviction."""
        store.seed("running.mp4", 100, age_seconds=9 * DAY)
        store.seed("done.mp4", 100, age_seconds=1 * DAY)
        policy = ArtifactLifecyclePolicy(max_total_bytes=50)
        report = cleanup_artifacts(
            store, policy, now=NOW, protected_keys={"running.mp4"}
        )
        assert report.deleted == ["done.mp4"]
        assert "running.mp4" in store.keys

    def test_is_protected_predicate(self, store):
        """A predicate can protect artifacts by any rule."""
        store.seed("keepme.mp4", 10, age_seconds=99 * DAY)
        store.seed("other.mp4", 10, age_seconds=99 * DAY)
        report = cleanup_artifacts(
            store,
            ArtifactLifecyclePolicy(ttl_seconds=1),
            now=NOW,
            is_protected=lambda info: info.key.startswith("keep"),
        )
        assert report.deleted == ["other.mp4"]

    def test_task_protection_by_key_prefix(self, store):
        """make_task_protection guards the ``<task_id>/`` namespace."""
        store.seed("task-a/final.mp4", 10, age_seconds=99 * DAY)
        store.seed("task-b/final.mp4", 10, age_seconds=99 * DAY)
        report = cleanup_artifacts(
            store,
            ArtifactLifecyclePolicy(ttl_seconds=1),
            now=NOW,
            is_protected=make_task_protection(["task-a"]),
        )
        assert report.deleted == ["task-b/final.mp4"]
        assert report.skipped == ["task-a/final.mp4"]

    def test_task_protection_matches_bare_key(self):
        """A bare ``<task_id>`` key is also protected."""
        guard = make_task_protection(["abc"])
        assert guard(ArtifactInfo(key="abc", size=1, modified_at=0)) is True
        assert guard(ArtifactInfo(key="abcdef/x", size=1, modified_at=0)) is False

    def test_task_protection_ignores_empty_ids(self):
        """Empty IDs never match anything."""
        guard = make_task_protection(["", None])  # type: ignore[list-item]
        assert guard(ArtifactInfo(key="x", size=1, modified_at=0)) is False


class TestDryRun:
    """Preview mode reports without mutating."""

    def test_dry_run_reports_but_keeps(self, store):
        """dry_run lists deletions and frees nothing on disk."""
        store.seed("old.mp4", 100, age_seconds=9 * DAY)
        policy = ArtifactLifecyclePolicy(ttl_seconds=1, dry_run=True)
        report = cleanup_artifacts(store, policy, now=NOW)
        assert report.dry_run is True
        assert report.deleted == ["old.mp4"]
        assert report.freed_bytes == 100
        assert store.deleted == []
        assert store.keys == ["old.mp4"]

    def test_dry_run_size_cap(self, store):
        """The size-cap pass is previewed too, without deleting."""
        store.seed("a.mp4", 100, age_seconds=3 * DAY)
        store.seed("b.mp4", 100, age_seconds=1 * DAY)
        policy = ArtifactLifecyclePolicy(max_total_bytes=100, dry_run=True)
        report = cleanup_artifacts(store, policy, now=NOW)
        assert report.deleted == ["a.mp4"]
        assert store.keys == ["a.mp4", "b.mp4"]

    def test_summary_mentions_dry_run(self, store):
        """The one-line summary flags preview mode."""
        store.seed("a.mp4", 100, age_seconds=9 * DAY)
        policy = ArtifactLifecyclePolicy(ttl_seconds=1, dry_run=True)
        summary = cleanup_artifacts(store, policy, now=NOW).summary()
        assert "dry-run" in summary
        assert "100 B" in summary


class TestErrorHandling:
    """A failing delete is reported, never raised."""

    def test_delete_error_is_collected(self, store):
        """One unusable file does not abort the sweep."""
        store.seed("bad.mp4", 10, age_seconds=9 * DAY)
        store.seed("good.mp4", 10, age_seconds=9 * DAY)
        store.fail_on.add("bad.mp4")
        report = cleanup_artifacts(store, ArtifactLifecyclePolicy(ttl_seconds=1), now=NOW)
        assert report.deleted == ["good.mp4"]
        assert [k for k, _ in report.errors] == ["bad.mp4"]
        assert "disk on fire" in report.errors[0][1]

    def test_list_error_is_reported(self):
        """A store that cannot be listed yields an error report, not a crash."""

        class BrokenStore(MemoryStore):
            def list(self, prefix: str = "") -> Iterator[ArtifactInfo]:
                raise ArtifactStoreError("bucket unreachable")

        report = cleanup_artifacts(
            BrokenStore(), ArtifactLifecyclePolicy(ttl_seconds=1), now=NOW
        )
        assert report.deleted == []
        assert report.errors and "bucket unreachable" in report.errors[0][1]

    def test_empty_report_summary(self):
        """A default report renders a sensible summary."""
        assert "0 artifact(s)" in CleanupReport().summary()
        assert CleanupReport().deleted_count == 0


# ════════════════════════════════════════════════════════════
#  Real filesystem end-to-end
# ════════════════════════════════════════════════════════════


class TestLocalStoreCleanup:
    """cleanup_artifacts against a real LocalArtifactStore."""

    def test_deletes_expired_files_on_disk(self, tmp_path):
        """Expired files are actually unlinked."""
        root = tmp_path / "artifacts"
        store = LocalArtifactStore(root)
        old = root / "old.mp4"
        new = root / "new.mp4"
        old.write_bytes(b"x" * 100)
        new.write_bytes(b"y" * 100)
        stale = time.time() - 10 * DAY
        import os

        os.utime(old, (stale, stale))

        policy = ArtifactLifecyclePolicy(ttl_seconds=int(DAY))
        report = cleanup_artifacts(store, policy)
        assert report.deleted == ["old.mp4"]
        assert not old.exists()
        assert new.exists()

    def test_protocol_conformance(self, tmp_path):
        """MemoryStore and LocalArtifactStore both satisfy the protocol."""
        assert isinstance(LocalArtifactStore(tmp_path), StorageBackend)
        assert isinstance(MemoryStore(), StorageBackend)


# ════════════════════════════════════════════════════════════
#  Background sweeper
# ════════════════════════════════════════════════════════════


class TestArtifactSweeper:
    """The daemon thread that periodically runs cleanup_artifacts."""

    def test_sweep_once_deletes(self, store):
        """A manual sweep applies the policy."""
        store.seed("old.mp4", 10, age_seconds=99 * DAY, now=time.time())
        sweeper = ArtifactSweeper(store, ArtifactLifecyclePolicy(ttl_seconds=1))
        report = sweeper.sweep_once()
        assert report is not None
        assert report.deleted == ["old.mp4"]
        assert sweeper.last_report is report

    def test_sweep_once_honours_protected_ids(self, store):
        """Active task IDs protect their key namespace."""
        store.seed("live/final.mp4", 10, age_seconds=99 * DAY, now=time.time())
        store.seed("dead/final.mp4", 10, age_seconds=99 * DAY, now=time.time())
        sweeper = ArtifactSweeper(
            store,
            ArtifactLifecyclePolicy(ttl_seconds=1),
            protected_ids=lambda: ["live"],
        )
        report = sweeper.sweep_once()
        assert report is not None
        assert report.deleted == ["dead/final.mp4"]

    def test_sweep_survives_callback_failure(self, store):
        """A broken protected-ids callback aborts the sweep, not the process."""

        def _boom() -> Iterable[str]:
            raise RuntimeError("queue is down")

        sweeper = ArtifactSweeper(
            store, ArtifactLifecyclePolicy(ttl_seconds=1), protected_ids=_boom
        )
        assert sweeper.sweep_once() is None

    def test_sweep_survives_store_failure(self):
        """A store that raises a non-store error is caught and logged."""

        class ExplodingStore(MemoryStore):
            def list(self, prefix: str = "") -> Iterator[ArtifactInfo]:
                raise RuntimeError("boom")

        sweeper = ArtifactSweeper(ExplodingStore(), ArtifactLifecyclePolicy(ttl_seconds=1))
        assert sweeper.sweep_once() is None

    def test_thread_lifecycle(self, store):
        """start()/stop() manage a daemon thread cleanly."""
        store.seed("old.mp4", 10, age_seconds=99 * DAY, now=time.time())
        sweeper = ArtifactSweeper(
            store, ArtifactLifecyclePolicy(ttl_seconds=1), interval=1.0
        )
        assert sweeper.is_running is False
        sweeper.start()
        try:
            assert sweeper.is_running is True
            deadline = time.time() + 5
            while sweeper.last_report is None and time.time() < deadline:
                time.sleep(0.05)
            assert sweeper.last_report is not None
            assert store.keys == []
        finally:
            sweeper.stop()
        assert sweeper.is_running is False

    def test_start_is_idempotent(self, store):
        """Calling start() twice does not spawn a second thread."""
        sweeper = ArtifactSweeper(store, ArtifactLifecyclePolicy(ttl_seconds=1))
        sweeper.start()
        try:
            first = sweeper._thread
            sweeper.start()
            assert sweeper._thread is first
        finally:
            sweeper.stop()

    def test_stop_before_start_is_safe(self, store):
        """stop() on a never-started sweeper is a no-op."""
        ArtifactSweeper(store, ArtifactLifecyclePolicy()).stop()

    def test_context_manager(self, store):
        """The sweeper works as a context manager."""
        with ArtifactSweeper(store, ArtifactLifecyclePolicy(ttl_seconds=1)) as sweeper:
            assert sweeper.is_running
        assert sweeper.is_running is False

    def test_interval_floor(self, store):
        """A sub-second interval is clamped to avoid a hot loop."""
        sweeper = ArtifactSweeper(store, ArtifactLifecyclePolicy(), interval=0.01)
        assert sweeper.interval == 1.0


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════


@pytest.fixture
def cli_root(tmp_path) -> Path:
    """A populated artifact root: one stale file, one fresh file."""
    root = tmp_path / "store"
    root.mkdir()
    (root / "old.mp4").write_bytes(b"x" * 100)
    (root / "new.mp4").write_bytes(b"y" * 50)
    stale = time.time() - 30 * DAY
    import os

    os.utime(root / "old.mp4", (stale, stale))
    return root


class TestArtifactsCli:
    """``mn artifacts list`` / ``mn artifacts cleanup``."""

    def test_list_reports_files(self, cli_root):
        """`mn artifacts list` prints every key and a total."""
        result = CliRunner().invoke(app, ["artifacts", "list", "--root", str(cli_root)])
        assert result.exit_code == 0
        assert "old.mp4" in result.output
        assert "new.mp4" in result.output
        assert "2 artifact(s)" in result.output

    def test_list_empty_store(self, tmp_path):
        """An empty store says so instead of printing a bare total."""
        result = CliRunner().invoke(
            app, ["artifacts", "list", "--root", str(tmp_path / "empty")]
        )
        assert result.exit_code == 0
        assert "No artifacts found." in result.output

    def test_cleanup_dry_run_keeps_files(self, cli_root):
        """--dry-run previews the deletion without touching the disk."""
        result = CliRunner().invoke(
            app,
            [
                "artifacts", "cleanup",
                "--root", str(cli_root),
                "--ttl", str(int(7 * DAY)),
                "--dry-run",
            ],
        )
        assert result.exit_code == 0
        assert "Would delete:" in result.output
        assert "old.mp4" in result.output
        assert "dry-run" in result.output
        assert (cli_root / "old.mp4").exists()

    def test_cleanup_deletes_expired(self, cli_root):
        """Without --dry-run the expired artifact is removed."""
        result = CliRunner().invoke(
            app,
            ["artifacts", "cleanup", "--root", str(cli_root), "--ttl", str(int(7 * DAY))],
        )
        assert result.exit_code == 0
        assert "Deleted:" in result.output
        assert not (cli_root / "old.mp4").exists()
        assert (cli_root / "new.mp4").exists()

    def test_cleanup_keep_last_protects(self, cli_root):
        """--keep-last shields the newest artifacts."""
        result = CliRunner().invoke(
            app,
            [
                "artifacts", "cleanup",
                "--root", str(cli_root),
                "--ttl", "1",
                "--keep-last", "2",
            ],
        )
        assert result.exit_code == 0
        assert (cli_root / "old.mp4").exists()
        assert (cli_root / "new.mp4").exists()

    def test_cleanup_max_bytes(self, cli_root):
        """--max-bytes evicts oldest-first until the cap is met."""
        result = CliRunner().invoke(
            app, ["artifacts", "cleanup", "--root", str(cli_root), "--max-bytes", "60"]
        )
        assert result.exit_code == 0
        assert not (cli_root / "old.mp4").exists()
        assert (cli_root / "new.mp4").exists()

    def test_cleanup_no_rule_is_noop(self, cli_root):
        """With no rule active the command explains and exits cleanly."""
        result = CliRunner().invoke(
            app, ["artifacts", "cleanup", "--root", str(cli_root), "--ttl", "0"]
        )
        assert result.exit_code == 0
        assert "No retention rule active" in result.output
        assert (cli_root / "old.mp4").exists()

    def test_cleanup_unknown_backend_exits_1(self, cli_root):
        """An unsupported backend is a clean error, not a traceback."""
        result = CliRunner().invoke(
            app,
            ["artifacts", "cleanup", "--backend", "gcs", "--ttl", "1"],
        )
        assert result.exit_code == 1
        assert "Artifact store unavailable" in result.output
