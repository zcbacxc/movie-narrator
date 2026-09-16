# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared execution contract for multi-candidate race parallelization (M2).

Internal module — intentionally **not** exported via ``contract`` /
package ``__init__``. The user-visible surface is the CLI flag
``mn race --parallel N``.

Frozen semantics (IMPLEMENTATION_PLAN M2):

- ``candidate_index`` is the **0-based input order** assigned before
  submit — never completion order.
- ``CandidateOutcome`` ∈ {success, failed, cancelled, paused}.
- Winner selection consumes only ``outcome == success``.
- Cancel state machine: QUEUED → ``future.cancel()`` → CANCELLED;
  RUNNING → per-candidate flag + ``RunController.is_cancelled()`` at
  the next step boundary; COMPLETING → terminal outcome wins.
- ``estimated_cost_total_usd`` is synthesized as
  ``summary.llm.estimated_cost_usd + summary.tts.estimated_cost_usd``
  (there is no top-level field in ``CostTracker.summary()``).

Executor choice: **bounded ThreadPoolExecutor** (Context / services are
not pickle-safe; workload is I/O + subprocess heavy). Never
``ProcessPoolExecutor``.
"""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import CancelledError as FuturesCancelledError
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, Generic, List, Optional, TypeVar

from .pipeline.errors import RunController
from .utils.cost_tracker import CostTracker

logger = logging.getLogger(__name__)

__all__ = [
    "CandidateOutcome",
    "CandidateExecutionMetrics",
    "CandidateExecutor",
    "ExecutorSlot",
    "resolve_parallelism",
    "empty_usage_summary",
    "synthesize_estimated_cost_total_usd",
]


T = TypeVar("T")


# ── Outcome + metrics (P2-1 / P2-4) ───────────────────────


class CandidateOutcome(str, Enum):
    """Executor-level terminal outcome for one candidate.

    Maps from pipeline exception classes (I2):

    - pipeline returns normally           → ``success``
    - ``PipelinePaused``                  → ``paused``
    - ``PipelineCancelled`` / queued cancel → ``cancelled``
    - ``PreflightError`` / any other Exception → ``failed``
    """

    SUCCESS = "success"
    FAILED = "failed"
    CANCELLED = "cancelled"
    PAUSED = "paused"


def empty_usage_summary() -> Dict[str, Any]:
    """Return a zero-valued ``CostTracker.summary()`` snapshot."""
    return CostTracker().summary()


def synthesize_estimated_cost_total_usd(usage_summary: Optional[Dict[str, Any]]) -> float:
    """H1: synthesize total estimated provider cost (LLM + TTS).

    ``CostTracker.summary()`` has no top-level total; Gate-1 uses
    ``llm.estimated_cost_usd + tts.estimated_cost_usd`` only (no
    FFmpeg / GPU / CPU duration cost).
    """
    if not usage_summary:
        return 0.0
    llm = (usage_summary.get("llm") or {}).get("estimated_cost_usd") or 0.0
    tts = (usage_summary.get("tts") or {}).get("estimated_cost_usd") or 0.0
    try:
        return float(llm) + float(tts)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class CandidateExecutionMetrics:
    """Per-candidate execution metrics (P2-4).

    ``usage_summary`` is the **as-is** ``CostTracker.summary()`` schema
    (tokens / calls + per-provider ``estimated_cost_usd``). Do not
    redefine that schema here.
    """

    candidate_index: int
    wall_time_s: float
    usage_summary: Dict[str, Any] = field(default_factory=empty_usage_summary)
    outcome: CandidateOutcome = CandidateOutcome.FAILED

    @property
    def estimated_cost_total_usd(self) -> float:
        """Synthesized LLM+TTS estimated cost (H1)."""
        return synthesize_estimated_cost_total_usd(self.usage_summary)

    def to_json_dict(self) -> Dict[str, Any]:
        """JSON-serializable projection for race reports."""
        return {
            "candidate_index": self.candidate_index,
            "wall_time_s": round(float(self.wall_time_s), 6),
            "usage_summary": self.usage_summary,
            "estimated_cost_total_usd": self.estimated_cost_total_usd,
            "outcome": self.outcome.value,
        }


# ── Parallelism resolver (P2-2) ───────────────────────────


def resolve_parallelism(raw: Optional[Any], *, default: int = 1) -> int:
    """Resolve the CLI ``--parallel`` sentinel to a worker count.

    Frozen rules:

    - unset / ``None`` → ``default`` (1, sequential-equivalent)
    - empty after ``strip()`` → unset
    - non-digit / ``0`` / negative → fail-fast ``ValueError``

    Does **not** read or extend ``Settings``.
    """
    if raw is None:
        return default
    if isinstance(raw, bool):
        # bool is a subclass of int — reject explicitly.
        raise ValueError(f"invalid --parallel value {raw!r}: expected a positive integer")
    if isinstance(raw, int):
        if raw <= 0:
            raise ValueError(f"invalid --parallel value {raw!r}: expected a positive integer")
        return int(raw)
    s = str(raw).strip()
    if not s:
        return default
    if not s.isdigit() or int(s) <= 0:
        raise ValueError(
            f"invalid --parallel value {raw!r}: expected a positive integer "
            f"(unset / empty = sequential)"
        )
    return int(s)


# ── Cancel controller (P2-7) ──────────────────────────────


class _CandidateCancelController:
    """Per-candidate cooperative cancel flag (implements RunController).

    ``Future.cancel`` cannot kill a running worker thread, so RUNNING
    candidates observe this flag at the next pipeline step boundary via
    ``check_cancelled``.
    """

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


class _CandidatePhase(Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETING = "completing"
    CANCELLED = "cancelled"
    DONE = "done"


@dataclass
class ExecutorSlot(Generic[T]):
    """One input-order slot returned by :meth:`CandidateExecutor.run_all`."""

    index: int
    value: Optional[T]
    outcome: CandidateOutcome
    error: Optional[str] = None
    error_type: Optional[str] = None


class CandidateExecutor:
    """Bounded ThreadPoolExecutor wrapper with race cancel semantics.

    Public cancel API is :meth:`cancel` / :meth:`cancel_all` — this is
    **not** ``ThreadPoolExecutor.cancel``. Indices are 0-based input
    order assigned before submit.
    """

    def __init__(self, max_workers: int = 1) -> None:
        self._max_workers = max(1, int(max_workers))
        self._lock = threading.RLock()
        self._controllers: Dict[int, _CandidateCancelController] = {}
        self._futures: Dict[int, Future] = {}
        self._phases: Dict[int, _CandidatePhase] = {}

    @property
    def max_workers(self) -> int:
        return self._max_workers

    def phase(self, index: int) -> Optional[str]:
        """Diagnostic: current phase name for *index* (or None)."""
        with self._lock:
            p = self._phases.get(index)
            return p.value if p is not None else None

    def cancel(self, index: int) -> bool:
        """Cancel one candidate (P2-7 state machine).

        - QUEUED (future not yet submitted) → mark CANCELLED; submit will skip
        - QUEUED (future submitted) → try ``future.cancel()`` → CANCELLED
        - RUNNING → set per-candidate flag (pipeline sees it at next
          step boundary)
        - COMPLETING / DONE → terminal outcome wins; flag may still be
          set but does not rewrite a finished result

        Returns:
            True if a controller existed and cancel was requested.
        """
        with self._lock:
            ctrl = self._controllers.get(index)
            fut = self._futures.get(index)
            phase = self._phases.get(index)

        if ctrl is None:
            return False

        ctrl.cancel()

        if phase is _CandidatePhase.QUEUED:
            if fut is None:
                # Not submitted yet — mark so run_all skips submit / collection
                # treats it as cancelled.
                with self._lock:
                    if self._phases.get(index) is _CandidatePhase.QUEUED:
                        self._phases[index] = _CandidatePhase.CANCELLED
                logger.debug("candidate %s cancelled before submit", index)
                return True
            if fut.cancel():
                with self._lock:
                    if self._phases.get(index) is _CandidatePhase.QUEUED:
                        self._phases[index] = _CandidatePhase.CANCELLED
                logger.debug("candidate %s cancelled while QUEUED", index)
                return True
        return True

    def cancel_all(self) -> None:
        """Request cancel for every known candidate index."""
        with self._lock:
            indices = list(self._controllers.keys())
        for i in indices:
            self.cancel(i)

    def run_all(
        self,
        count: int,
        worker: Callable[[int, RunController], T],
    ) -> List[ExecutorSlot[T]]:
        """Run ``worker(index, controller)`` for indices ``0..count-1``.

        Results are returned in **input order** regardless of completion
        order. ``max_workers`` is bounded by both the executor setting
        and *count*.
        """
        if count <= 0:
            return []

        # Assign indices + controllers BEFORE submit (frozen contract).
        with self._lock:
            self._controllers = {i: _CandidateCancelController() for i in range(count)}
            self._futures = {}
            self._phases = {i: _CandidatePhase.QUEUED for i in range(count)}

        def _wrapped(i: int, ctrl: _CandidateCancelController) -> T:
            with self._lock:
                if self._phases.get(i) is _CandidatePhase.CANCELLED:
                    # Race: future.cancel lost the race with a start.
                    raise FuturesCancelledError()
                self._phases[i] = _CandidatePhase.RUNNING
            try:
                value = worker(i, ctrl)
            except BaseException:
                with self._lock:
                    self._phases[i] = _CandidatePhase.DONE
                raise
            # COMPLETING → terminal wins: do not re-check cancel here.
            with self._lock:
                self._phases[i] = _CandidatePhase.DONE
            return value

        workers = min(self._max_workers, count)
        slots: List[ExecutorSlot[T]] = []

        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="mn-race-cand",
        ) as pool:
            for i in range(count):
                with self._lock:
                    if self._phases.get(i) is _CandidatePhase.CANCELLED:
                        # Cancelled before submit — do not enqueue.
                        continue
                fut = pool.submit(_wrapped, i, self._controllers[i])
                with self._lock:
                    # Re-check: cancel may have landed between the phase check
                    # and submit. If so, try to cancel the fresh future.
                    self._futures[i] = fut
                    if self._phases.get(i) is _CandidatePhase.CANCELLED:
                        fut.cancel()

            # Collect in input order — candidate_index is never completion order.
            for i in range(count):
                with self._lock:
                    fut_opt = self._futures.get(i)
                    phase = self._phases.get(i)
                if fut_opt is None:
                    outcome = (
                        CandidateOutcome.CANCELLED
                        if phase is _CandidatePhase.CANCELLED
                        else CandidateOutcome.FAILED
                    )
                    slots.append(
                        ExecutorSlot(
                            index=i,
                            value=None,
                            outcome=outcome,
                            error="cancelled" if outcome is CandidateOutcome.CANCELLED else "not submitted",
                            error_type="Cancelled" if outcome is CandidateOutcome.CANCELLED else "NotSubmitted",
                        )
                    )
                    continue
                try:
                    value = fut_opt.result()
                except FuturesCancelledError:
                    slots.append(
                        ExecutorSlot(
                            index=i,
                            value=None,
                            outcome=CandidateOutcome.CANCELLED,
                            error="cancelled",
                            error_type="Cancelled",
                        )
                    )
                except Exception as e:  # noqa: BLE001 — worker barrier
                    slots.append(
                        ExecutorSlot(
                            index=i,
                            value=None,
                            outcome=CandidateOutcome.FAILED,
                            error=str(e),
                            error_type=type(e).__name__,
                        )
                    )
                else:
                    slots.append(
                        ExecutorSlot(
                            index=i,
                            value=value,
                            outcome=CandidateOutcome.SUCCESS,
                        )
                    )

        with self._lock:
            self._controllers.clear()
            self._futures.clear()
        return slots


def timed_call(fn: Callable[[], T]) -> tuple[T, float]:
    """Run *fn* and return ``(result, wall_time_s)``."""
    t0 = time.perf_counter()
    result = fn()
    return result, time.perf_counter() - t0
