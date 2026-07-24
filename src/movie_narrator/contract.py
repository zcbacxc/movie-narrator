"""Stable API contract between the core engine and external consumers.

This module is the **single import surface** that the Web API layer
(and future consumers) should depend on. It re-exports the types,
protocols, and functions that form the engine's public API, plus
defines a ``PipelineResult`` protocol that formalizes the implicit
``Context`` duck-typing previously used by web_api.

By centralizing the contract here:

- web_api imports ``from ..contract import ...`` instead of reaching
  into internal modules (``..pipeline.runner``, ``..pipeline.errors``,
  ``..utils.console``, ``..utils.sanitize``).
- ``PARAM_WHITELIST`` is accessible without importing the full runner
  module, eliminating the highest drift-risk coupling point.
- When the project is eventually split into separate repositories,
  this module becomes the natural package boundary.

Nothing is *moved* from its current location — this module only
re-exports. Internal modules keep their definitions for backward
compatibility with existing CLI and test code.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Protocol, runtime_checkable

# ── Re-exports: console abstraction ────────────────────────

from .utils.console import BaseConsole, Console
from .utils.sanitize import sanitize_filename

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
    def subtitle_paths(self) -> Optional[Any]: ...


# ── Public API ─────────────────────────────────────────────

__all__ = [
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
]


# SilentConsole is imported here (not at top) to avoid circular import:
# utils/console.py imports from utils/log.py and utils/retention.py,
# which are safe but we keep the import explicit for contract clarity.
from .utils.console import SilentConsole  # noqa: E402
