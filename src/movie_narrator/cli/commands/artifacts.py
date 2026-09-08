# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Artifact-lifecycle CLI commands: ``list`` and ``cleanup`` in the ``artifacts`` sub-app."""

from typing import Optional

import typer


def _artifacts_open_store(backend: Optional[str], root: Optional[str]):
    """Resolve the artifact store for the ``mn artifacts`` commands."""
    from movie_narrator.cloud.artifact_store import ArtifactStoreError, get_artifact_store

    try:
        return get_artifact_store(backend=backend, root=root)
    except ArtifactStoreError as e:
        typer.echo(f"Artifact store unavailable: {e}", err=True)
        raise typer.Exit(1)


def artifacts_list(
    prefix: str = typer.Option(
        "", "--prefix", help="仅列出该前缀下的产物 / Only list keys under this prefix"
    ),
    backend: Optional[str] = typer.Option(
        None, "--backend", help="local 或 s3(默认读 MN_STORAGE_BACKEND) / Backend override"
    ),
    root: Optional[str] = typer.Option(
        None, "--root", help="本地后端根目录(默认读 MN_STORAGE_ROOT) / Local store root"
    ),
):
    """List artifacts in the configured store.

    Examples:
        mn artifacts list
        mn artifacts list --root output --prefix abc123
    """
    from movie_narrator.cloud.lifecycle import format_bytes

    store = _artifacts_open_store(backend, root)
    total = 0
    count = 0
    for info in store.list(prefix):
        count += 1
        total += info.size
        typer.echo(f"  {info.key}  ({format_bytes(info.size)})")
    if count == 0:
        typer.echo("No artifacts found.")
        return
    typer.echo(f"{count} artifact(s), {format_bytes(total)} total.")


def artifacts_cleanup(
    ttl: Optional[int] = typer.Option(
        None,
        "--ttl",
        help="过期秒数,0=永久保留(默认读 MN_ARTIFACT_TTL) / TTL in seconds, 0 = keep forever",
    ),
    max_bytes: Optional[int] = typer.Option(
        None,
        "--max-bytes",
        help="总容量上限字节数,0=不限(默认读 MN_ARTIFACT_MAX_BYTES) / Total size cap in bytes",
    ),
    keep_last: Optional[int] = typer.Option(
        None,
        "--keep-last",
        help="始终保留最新 N 个产物(默认读 MN_ARTIFACT_KEEP_LAST) / Always keep the N newest",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="仅预览不删除 / Preview without deleting"
    ),
    prefix: str = typer.Option(
        "", "--prefix", help="仅清理该前缀下的产物 / Restrict cleanup to this prefix"
    ),
    backend: Optional[str] = typer.Option(
        None, "--backend", help="local 或 s3(默认读 MN_STORAGE_BACKEND) / Backend override"
    ),
    root: Optional[str] = typer.Option(
        None, "--root", help="本地后端根目录(默认读 MN_STORAGE_ROOT) / Local store root"
    ),
):
    """Clean up artifacts by TTL and size cap.

    Options not explicitly specified fall back to MN_ARTIFACT_* environment variables.
    Options left unset fall back to the MN_ARTIFACT_* environment variables.

    Examples:
        mn artifacts cleanup --dry-run
        mn artifacts cleanup --ttl 604800 --keep-last 5
        mn artifacts cleanup --max-bytes 10737418240
    """
    from movie_narrator.cloud.lifecycle import (
        ArtifactLifecyclePolicy,
        cleanup_artifacts,
        describe_policy,
    )

    policy = ArtifactLifecyclePolicy.from_env(dry_run=dry_run)
    if ttl is not None:
        policy.ttl_seconds = max(ttl, 0)
    if max_bytes is not None:
        policy.max_total_bytes = max(max_bytes, 0)
    if keep_last is not None:
        policy.keep_last_n = max(keep_last, 0)

    store = _artifacts_open_store(backend, root)

    typer.echo("Artifact retention policy:")
    for line in describe_policy(policy):
        typer.echo(f"  {line}")

    if not policy.enabled:
        typer.echo("No retention rule active (--ttl / --max-bytes are both 0) — nothing to do.")
        return

    report = cleanup_artifacts(store, policy, prefix=prefix)

    if report.deleted:
        header = "Would delete:" if report.dry_run else "Deleted:"
        typer.echo(header)
        for key in report.deleted:
            typer.echo(f"  - {key}")
    if report.skipped:
        typer.echo(f"Kept (protected / keep-last): {len(report.skipped)}")
    if report.errors:
        typer.echo("Errors:", err=True)
        for key, message in report.errors:
            typer.echo(f"  ! {key}: {message}", err=True)

    typer.echo(report.summary())
    if report.errors:
        raise typer.Exit(1)
