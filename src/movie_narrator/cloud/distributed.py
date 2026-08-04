# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Conditional distributed rendering (v0.9.4).

This module delivers the **conditional-trigger basic framework** for
distributed rendering: rendering work is only offloaded to remote nodes
when every precondition holds —

- ``MN_DISTRIBUTED_ENABLED`` is set (default off),
- at least one node from ``MN_DISTRIBUTED_NODES`` is configured and
  healthy (``GET /ready`` probe),
- the estimated render duration clears
  ``MN_DISTRIBUTED_MIN_RENDER_SECONDS`` (default 600s).

This is **not** a strict-SLA scheduler: no latency, throughput, or
availability guarantee is made for the remote leg. Any failure in the
distributed path falls back to local rendering, and callers should treat
the remote leg as best-effort.

Components:

- :class:`NodeRegistry` — parses ``MN_DISTRIBUTED_NODES`` and probes
  each node's readiness.
- :class:`DistributedRenderPlanner` — decides whether a task's render
  phase should be dispatched.
- :func:`render_task_dispatcher` — submits the render phase as a subtask
  on a remote node, polls for completion, and downloads the artifacts.

Note on inputs: the remote node must be able to resolve the render
inputs (clips, narration audio, BGM, subtitles). Wiring shared storage
(S3 backend, NFS, …) into the nodes is deployment-specific and out of
scope for this foundational release.
"""

from __future__ import annotations

import json
import logging
import urllib.request
from pathlib import Path
from typing import List, Optional

from ..config import Settings
from .models import TaskProgress, TaskRequest, TaskResult
from .remote_queue import RemoteQueueError, RemoteTaskQueue

logger = logging.getLogger(__name__)

#: Name of the pipeline step dispatched to remote nodes.
RENDER_STEP = "render_video"

#: Default per-node readiness probe timeout (seconds).
_DEFAULT_HEALTH_TIMEOUT = 5.0

#: Default wall-clock budget for a remote render subtask (seconds).
_DEFAULT_RENDER_TIMEOUT = 3600.0

#: Rough local render-time heuristic used when no history exists: a
#: video of *N* seconds is estimated to take roughly ``N * MULTIPLIER``
#: seconds to render locally. Intentional crude — the distributed path
#: is a conditional-trigger framework, not a latency-accurate scheduler.
_ESTIMATE_MULTIPLIER = 1.0


class DistributedRenderError(Exception):
    """Raised when a distributed render leg fails.

    Callers (e.g. the worker soft hook) catch this and fall back to
    local rendering.
    """


# ── Node registry ──────────────────────────────────────────


class NodeRegistry:
    """Registry of remote rendering nodes (v0.9.4).

    Parses a comma-separated ``base_url`` list (defaulting to
    ``MN_DISTRIBUTED_NODES``) and probes each node's readiness endpoint
    (``GET /ready``) to determine availability.

    Args:
        nodes: Comma-separated list of node base URLs. When None, the
            value is read from ``MN_DISTRIBUTED_NODES``.
        health_timeout: Per-probe timeout in seconds. When None, read
            from ``MN_DISTRIBUTED_NODE_HEALTH_TIMEOUT``.
    """

    def __init__(
        self,
        *,
        nodes: Optional[str] = None,
        health_timeout: Optional[float] = None,
    ) -> None:
        if nodes is None or health_timeout is None:
            settings = Settings()
            if nodes is None:
                nodes = settings.distributed_nodes or ""
            if health_timeout is None:
                health_timeout = settings.distributed_node_health_timeout
        self._nodes: List[str] = [
            url.strip() for url in nodes.split(",") if url.strip()
        ]
        self._health_timeout = float(health_timeout)
        self._healthy: List[str] = []
        self._probed = False

    # ── Introspection ───────────────────────────────────────

    @property
    def configured_nodes(self) -> List[str]:
        """All configured node base URLs (regardless of health)."""
        return list(self._nodes)

    @property
    def healthy_nodes(self) -> List[str]:
        """Node base URLs that passed the last readiness probe."""
        return list(self._healthy)

    # ── Probing ─────────────────────────────────────────────

    def _probe(self, base_url: str) -> bool:
        """Probe one node's ``GET /ready`` endpoint.

        Returns:
            True when the node reports ``{"ready": true}`` (or any
            truthy ``ready`` value). Never raises — unreachable or malformed
            nodes simply read as unhealthy.
        """
        url = f"{base_url.rstrip('/')}/ready"
        try:
            with urllib.request.urlopen(url, timeout=self._health_timeout) as resp:  # nosec B310  # trusted node /ready probe
                payload = json.loads(resp.read().decode("utf-8"))
                return bool(payload.get("ready"))
        except Exception:  # noqa: BLE001 — probing must never raise
            return False

    def refresh(self) -> List[str]:
        """Re-probe every configured node.

        Returns:
            The currently healthy node base URLs.
        """
        self._healthy = [
            url for url in self._nodes if self._probe(url)
        ]
        self._probed = True
        return list(self._healthy)

    def available_nodes(self) -> List[str]:
        """
        Returns:
            Healthy node base URLs.

            Probes on first call; afterwards returns the cached result of
            the most recent :meth:`refresh`.
        """
        if not self._probed:
            self.refresh()
        return list(self._healthy)


# ── Planning ───────────────────────────────────────────────


class DistributedRenderPlanner:
    """Decides whether a task's render phase should be dispatched.

    A task is distributable only when ALL of the following hold:

    - distributed rendering is enabled,
    - at least one healthy node is available,
    - the estimated render duration is at least the configured
      threshold (``MN_DISTRIBUTED_MIN_RENDER_SECONDS``).

    Args:
        enabled: Override for ``MN_DISTRIBUTED_ENABLED``. When None,
            read from ``Settings``.
        min_render_seconds: Override for
            ``MN_DISTRIBUTED_MIN_RENDER_SECONDS``. When None, read from
            ``Settings``.
        available_nodes: List of healthy node base URLs. When given,
            no registry probing happens; pass ``[]`` to force the
            no-node path deterministically.
        node_registry: A :class:`NodeRegistry` to probe for healthy
            nodes. Ignored when *available_nodes* is provided.
    """

    def __init__(
        self,
        *,
        enabled: Optional[bool] = None,
        min_render_seconds: Optional[float] = None,
        available_nodes: Optional[List[str]] = None,
        node_registry: Optional[NodeRegistry] = None,
    ) -> None:
        if enabled is None or min_render_seconds is None:
            settings = Settings()
            if enabled is None:
                enabled = bool(settings.distributed_enabled)
            if min_render_seconds is None:
                min_render_seconds = float(settings.distributed_min_render_seconds)
        self.enabled: bool = bool(enabled)
        self.min_render_seconds: float = float(min_render_seconds)
        self._injected_nodes = available_nodes is not None
        self._nodes: List[str] = (
            list(available_nodes) if available_nodes is not None else []
        )
        self._registry = node_registry

    # ── Public API ──────────────────────────────────────────

    @property
    def available_nodes(self) -> List[str]:
        """Healthy node base URLs.

        Uses the injected list when provided; otherwise probes the
        registry lazily — but only when distributed rendering is
        enabled, so the default (disabled) path never touches the
        network.
        """
        if self._injected_nodes:
            return list(self._nodes)
        if not self.enabled:
            return []
        if self._registry is None:
            self._registry = NodeRegistry()
        return self._registry.available_nodes()

    def should_distribute(self, estimated_seconds: float) -> bool:
        """Return True when the render phase should be dispatched.

        Args:
            estimated_seconds: Estimated local render duration.
        """
        if not self.enabled:
            return False
        if not self.available_nodes:
            return False
        return estimated_seconds >= self.min_render_seconds


# ── Duration estimation ────────────────────────────────────


def estimate_render_seconds(
    request: TaskRequest,
    progress: Optional[TaskProgress] = None,
    history_seconds: Optional[float] = None,
) -> float:
    """Estimate how long the render step will take (v0.9.4).

    Prefers explicit render-history data, then ``TaskProgress`` step
    timing, then falls back to a crude duration-based heuristic. This is
    intentionally simple — the distributed path is a conditional-trigger
    framework, not a latency-accurate scheduler.

    Args:
        request: The task request (its ``duration`` is the fallback
            signal).
        progress: Optional live ``TaskProgress`` used for a step-timing
            estimate.
        history_seconds: Optional measured render duration from history.

    Returns:
        Estimated render time in seconds (always ``>= 0``).
    """
    if history_seconds is not None and history_seconds > 0:
        return float(history_seconds)
    if progress is not None and progress.step_elapsed_seconds > 0:
        return float(progress.step_elapsed_seconds)
    return float(request.duration) * _ESTIMATE_MULTIPLIER


# ── Dispatcher ─────────────────────────────────────────────


def _render_only_workflow() -> dict:
    """Build a ``workflow_steps`` map that enables only the render step."""
    from ..pipeline.runner import STEPS

    return {step.__name__: (step.__name__ == RENDER_STEP) for step in STEPS}


def render_task_dispatcher(
    *,
    request: TaskRequest,
    node: str,
    timeout: float = _DEFAULT_RENDER_TIMEOUT,
    poll_interval: float = 2.0,
    download_dir: Optional[str] = None,
    api_key: Optional[str] = None,
) -> TaskResult:
    """Dispatch a task's render phase to a remote node (v0.9.4).

    The render phase is submitted as a *subtask* to *node* via a
    :class:`RemoteTaskQueue`: the request is copied with
    ``workflow_steps`` narrowed to the render step so the node only
    renders. The dispatcher polls until the subtask reaches a terminal
    state, then downloads the produced artifacts (``final.mp4``, etc.)
    back to *download_dir* and rewrites the result paths to the local
    copies.

    Args:
        request: The original task request; a render-only copy is sent
            to the node.
        node: Base URL of the remote rendering node.
        timeout: Wall-clock budget for the remote subtask.
        poll_interval: Polling interval while waiting.
        download_dir: Local directory for downloaded artifacts. Defaults
            to the current working directory.
        api_key: Optional ``X-API-Key`` for the remote node.

    Returns:
        A ``TaskResult`` whose artifact paths point at the locally
        downloaded copies.

    Raises:
        DistributedRenderError: if submission, waiting, or artifact
            download fails — the caller should fall back to local
            rendering.
    """
    from .remote_provider import download_artifact

    render_only = request.model_copy(deep=True)
    render_only.workflow_steps = _render_only_workflow()

    queue = RemoteTaskQueue(
        node,
        timeout=max(float(timeout), 30.0),
        api_key=api_key,
    )
    try:
        task_id = queue.submit(render_only)
    except RemoteQueueError as e:
        raise DistributedRenderError(
            f"submit render subtask to {node} failed: {e}"
        ) from e

    try:
        result = queue.wait(task_id, timeout=timeout, poll_interval=poll_interval)
    except RemoteQueueError as e:
        raise DistributedRenderError(
            f"wait for render subtask {task_id} failed: {e}"
        ) from e
    if result is None or not result.succeeded:
        raise DistributedRenderError(
            f"render subtask {task_id} did not succeed"
        )

    dest = download_dir or "."
    if result.video_path:
        filename = Path(result.video_path).name
        try:
            local_path = download_artifact(
                node,
                task_id,
                filename,
                dest_dir=dest,
                timeout=timeout,
                api_key=api_key,
            )
        except RemoteQueueError as e:
            raise DistributedRenderError(
                f"download artifact from {node} failed: {e}"
            ) from e
        result.video_path = str(local_path)
    if result.audio_path:
        filename = Path(result.audio_path).name
        try:
            local_path = download_artifact(
                node,
                task_id,
                filename,
                dest_dir=dest,
                timeout=timeout,
                api_key=api_key,
            )
        except RemoteQueueError as e:
            raise DistributedRenderError(
                f"download audio from {node} failed: {e}"
            ) from e
        result.audio_path = str(local_path)
    return result
