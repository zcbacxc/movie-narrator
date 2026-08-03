# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Stable API contract between the core engine and external consumers.

This module is the **single import surface** that the Web API layer
(and future consumers) should depend on. It re-exports the types,
protocols, and functions that form the engine's public API, plus
defines the plugin extension points (``Step``, ``Plugin``,
``PluginContext``).

By centralizing the contract here:

- web_api imports ``from movie_narrator.contract import ...`` instead
  of reaching into internal modules.
- ``PARAM_WHITELIST`` is accessible without importing the full runner
  module, eliminating the highest drift-risk coupling point.
- External plugins import ``from movie_narrator import register_step,
  register_tts, register_vision, register_llm, register_research, Context``
  to extend the engine.
- This module is the natural package boundary between the core engine
  repo (``movie-narrator``) and the web UI repo (``movie-narrator-web``).

Nothing is *moved* from its current location — this module only
re-exports. Internal modules keep their definitions for backward
compatibility with existing CLI and test code.

Contract versioning (semver):
    ``CONTRACT_VERSION`` follows semantic versioning. External consumers
    (e.g. ``movie-narrator-web``) should check this version to verify
    compatibility at import time::

        from movie_narrator.contract import CONTRACT_VERSION
        assert CONTRACT_VERSION >= (0, 5, 0), "requires contract >= 0.5.0"

    - MAJOR: breaking removals or signature changes to exported symbols
    - MINOR: new exports added (backward compatible)
    - PATCH: bug fixes / doc changes (no API surface change)
"""

# ruff: noqa: E402  (re-exports are intentionally placed after the version guard below)
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Protocol, runtime_checkable

# ── Contract version (semver) ──────────────────────────────
# External consumers (movie-narrator-web, third-party plugins) depend
# on this version to verify API compatibility. Bump according to:
#   MAJOR — breaking changes to exported symbols or signatures
#   MINOR — new exports added (backward compatible)
#   PATCH — bug fixes, doc changes (no API surface change)
CONTRACT_VERSION: tuple[int, int, int] = (0, 9, 1)


def check_version(required: tuple[int, int, int]) -> None:
    """Verify that the installed core engine meets a minimum contract version.

    Intended for use by external consumers (web package, plugins) at
    import time::

        from movie_narrator.contract import check_version
        check_version((0, 5, 1))

    Args:
        required: Minimum required ``(major, minor, patch)`` tuple.

    Raises:
        ImportError: if ``CONTRACT_VERSION < required``.
    """
    if CONTRACT_VERSION < required:
        raise ImportError(
            f"movie-narrator contract version {CONTRACT_VERSION} is below "
            f"the required {required}. Please upgrade: "
            f"pip install -U movie-narrator"
        )

# ── Re-exports: console abstraction ────────────────────────

from .utils.console import BaseConsole, Console
from .utils.sanitize import sanitize_filename

# ── Re-exports: structured logging / correlation (v0.8.1) ──

from .utils.logging_config import (
    JsonFormatter,
    configure_logging,
    correlation_scope,
    get_correlation_id,
    new_correlation_id,
    set_correlation_id,
)

# ── Re-exports: pipeline errors ────────────────────────────

from .pipeline.errors import (
    PipelineCancelled,
    PipelineStrictError,
    RunController,
    StepAction,
    check_cancelled,
)

# ── Re-exports: engine entry points ────────────────────────

from .pipeline.runner import PARAM_WHITELIST, build_context, run_pipeline

# ── Re-exports: models ─────────────────────────────────────

from .models import Context, ResearchInfo, Services, SubtitlePaths

# ── Re-exports: registries ─────────────────────────────────

from .pipeline.registry import StepRegistry, register_step, step, step_registry
from .providers import (
    ProviderRegistry,
    llm_registry,
    register_llm,
    register_research,
    register_tts,
    register_vision,
    research_registry,
    tts_registry,
    vision_registry,
)

# ── Step type alias ────────────────────────────────────────

#: A pipeline step function: ``Context -> Context``.
Step = Callable[[Context], Context]


# ── PipelineResult protocol ────────────────────────────────


@runtime_checkable
class PipelineResult(Protocol):
    """Read-only view of a completed pipeline's output.

    Formalizes the implicit ``Context`` duck-typing that web_api's
    ``collect_artifacts`` and ``TaskManager._run_task`` previously
    relied on without type annotations.

    The core engine's ``Context`` model satisfies this protocol
    structurally. Future consumers (CLI GUI, mobile, etc.) can depend
    on this protocol instead of the full ``Context`` model.
    """

    @property
    def video_path(self) -> Optional[str]: ...

    @property
    def audio_path(self) -> Optional[str]: ...

    @property
    def clips_dir(self) -> Optional[str]: ...

    @property
    def output_dir(self) -> str: ...

    @property
    def subtitle_paths(self) -> Optional[SubtitlePaths]: ...


# ── Plugin extension points ────────────────────────────────


@dataclass
class PluginContext:
    """Context passed to a plugin's ``register`` method.

    Gives plugins access to the global registries so they can
    register custom steps, TTS providers, vision providers, etc.

    Plugins should NOT hold long-lived references to this object —
    it exists only during the registration phase.
    """

    steps: StepRegistry
    tts: ProviderRegistry
    vision: ProviderRegistry
    llm: ProviderRegistry
    research: ProviderRegistry

    @classmethod
    def default(cls) -> "PluginContext":
        """Create a PluginContext backed by the global registries."""
        return cls(
            steps=step_registry,
            tts=tts_registry,
            vision=vision_registry,
            llm=llm_registry,
            research=research_registry,
        )


@runtime_checkable
class Plugin(Protocol):
    """A plugin that extends the movie-narrator pipeline.

    Plugins implement a ``name`` attribute and a ``register`` method
    that receives a :class:`PluginContext` and registers its
    components (steps, providers, etc.) with the appropriate registries.

    Example::

        class WatermarkPlugin:
            name = "watermark"

            def register(self, ctx: PluginContext) -> None:
                ctx.steps.register(
                    "add_watermark",
                    add_watermark,
                    after="render_video",
                )
    """

    name: str

    def register(self, ctx: PluginContext) -> None: ...


def load_plugin(plugin: Plugin) -> None:
    """Register a plugin with the global registries.

    Calls ``plugin.register(PluginContext.default())``, giving the
    plugin access to ``step_registry``, ``tts_registry``,
    ``vision_registry``, ``llm_registry``, and ``research_registry``.

    Args:
        plugin: An object implementing the :class:`Plugin` protocol.

    Raises:
        TypeError: if *plugin* does not implement the Plugin protocol.
    """
    if not isinstance(plugin, Plugin):
        raise TypeError(
            f"{plugin!r} does not implement the Plugin protocol "
            f"(missing 'name' attribute or 'register' method)."
        )
    plugin.register(PluginContext.default())


# ── Plugin discovery (entry_points) ───────────────────────
# Imported at the bottom of the module to avoid circular import:
# plugin_loader imports from contract (Plugin, load_plugin), so we
# import it after those symbols are defined.
# The actual import is done lazily below to keep the top-level
# contract module clean.

# ── Public API ─────────────────────────────────────────────

__all__ = [
    # Contract version
    "CONTRACT_VERSION",
    "check_version",
    # Console
    "BaseConsole",
    "Console",
    "SilentConsole",
    # Errors
    "PipelineCancelled",
    "PipelineStrictError",
    "RunController",
    "StepAction",
    "check_cancelled",
    # Engine
    "PARAM_WHITELIST",
    "build_context",
    "run_pipeline",
    # Utilities
    "sanitize_filename",
    # Protocols
    "PipelineResult",
    # Models
    "Context",
    "ResearchInfo",
    "Services",
    # Registries
    "StepRegistry",
    "ProviderRegistry",
    "step_registry",
    "tts_registry",
    "vision_registry",
    "llm_registry",
    "research_registry",
    # Registration decorators
    "register_step",
    "register_tts",
    "register_vision",
    "register_llm",
    "register_research",
    "step",  # alias for register_step
    # Plugin system
    "Step",
    "PluginContext",
    "Plugin",
    "load_plugin",
    "discover_plugins",
    "list_available_plugins",
    # Presets (re-exported for web package and external consumers)
    "list_presets",
    "get_preset",
    # Cloud / Task Queue (v0.6.0)
    "CancelController",
    "LocalTaskQueue",
    "ProgressConsole",
    "Task",
    "TaskProgress",
    "TaskQueue",
    "TaskRequest",
    "TaskResult",
    "TaskStatus",
    "TaskStorage",
    "run_task",
    # Cloud / Remote Inference (v0.6.1)
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
    # Observability (v0.8.1)
    "JsonFormatter",
    "configure_logging",
    "correlation_scope",
    "get_correlation_id",
    "new_correlation_id",
    "set_correlation_id",
    "CONTENT_TYPE_LATEST",
    "MetricsRegistry",
    "get_registry",
    "render_prometheus_text",
    # Cloud / Health probes + OpenAPI (v0.8.2)
    "build_health_payload",
    "build_readiness_payload",
    "build_openapi_spec",
    # Cloud / Artifact storage & lifecycle (v0.8.3)
    "ArtifactInfo",
    "ArtifactLifecyclePolicy",
    "ArtifactSweeper",
    "CleanupReport",
    "LocalArtifactStore",
    "S3ArtifactStore",
    "StorageBackend",
    "cleanup_artifacts",
    "get_artifact_store",
    # Reliability (v0.9.1)
    "CircuitState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitOpenError",
    "RetryPolicy",
    "with_retry",
    "with_async_retry",
]


# SilentConsole is imported here (not at top) to avoid circular import:
# utils/console.py imports from utils/log.py and utils/retention.py,
# which are safe but we keep the import explicit for contract clarity.
from .utils.console import SilentConsole  # noqa: E402

# Plugin discovery is imported here (not at top) to avoid circular import:
# plugin_loader imports from contract (Plugin, load_plugin), so we import
# it after those symbols are defined.
from .plugin_loader import (  # noqa: E402
    discover_plugins,
    list_available_plugins,
)

# Presets are imported here (not at top) to avoid circular import:
# presets modules import from models and config, which are safe but we
# keep the import explicit for contract clarity.
from .presets import get_preset, list_presets  # noqa: E402

# Cloud / task queue (v0.6.0) — imported here to avoid circular import:
# cloud.worker imports from pipeline.runner, which imports from contract.
from .cloud import (  # noqa: E402
    CancelController,
    LocalTaskQueue,
    ProgressConsole,
    Task,
    TaskProgress,
    TaskQueue,
    TaskRequest,
    TaskResult,
    TaskStatus,
    TaskStorage,
    run_task,
)

# Cloud / remote inference (v0.6.1) — new exports, backward compatible.
from .cloud import (  # noqa: E402
    RemoteQueueError,
    RemoteTaskQueue,
    TaskAPIServer,
    WorkerDaemon,
    download_all_artifacts,
    download_artifact,
    list_artifacts,
    register_remote_llm,
    register_remote_tts,
    run_daemon,
)

# Cloud / metrics (v0.8.1) — new exports, backward compatible.
from .cloud import (  # noqa: E402
    CONTENT_TYPE_LATEST,
    MetricsRegistry,
    get_registry,
    render_prometheus_text,
)

# Cloud / health probes + OpenAPI (v0.8.2) — new exports, backward compatible.
from .cloud import (  # noqa: E402
    build_health_payload,
    build_openapi_spec,
    build_readiness_payload,
)

# Cloud / artifact storage & lifecycle (v0.8.3) — new exports, backward
# compatible. The artifact store is the blob/media counterpart to
# ``TaskStorage`` (which persists task *state*, not artifacts).
from .cloud import (  # noqa: E402
    ArtifactInfo,
    ArtifactLifecyclePolicy,
    ArtifactSweeper,
    CleanupReport,
    LocalArtifactStore,
    S3ArtifactStore,
    StorageBackend,
    cleanup_artifacts,
    get_artifact_store,
)

# Reliability (v0.9.1) — circuit breaker + retry policy framework. The
# reliability package itself has no imports back into contract, so this
# is placed at the bottom purely for grouping consistency.
from .reliability import (  # noqa: E402
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitOpenError,
    CircuitState,
    RetryPolicy,
    with_async_retry,
    with_retry,
)
