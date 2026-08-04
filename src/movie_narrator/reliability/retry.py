# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Configurable retry policy framework (v0.9.1).

A standalone, reusable retry component that applies exponential backoff
with jitter to a callable. It is intentionally independent of the
task-level retry loop in ``cloud/worker.py`` (which remains the v0.9.4
integration point) — use :class:`RetryPolicy` + :func:`with_retry` where
a single external call needs bounded, policy-driven retries.

Retryability is decided uniformly:

1. ``should_retry(exc)`` — if set, its return value wins (full control).
2. ``retryable_exceptions`` — if set, the exception must be an instance
   of one of the listed types to be eligible for retry.
3. Otherwise the exception is retryable when it carries a truthy
   ``retryable`` attribute (e.g. :class:`ProviderError` with
   ``retryable=True``) **or** :func:`movie_narrator.workflow.errors.is_network_error`
   classifies it as a transient network/timeout failure.

The original exception is always re-raised once retries are exhausted —
it is never wrapped or replaced.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from functools import wraps
from typing import Any, Callable, Optional, Tuple, Type, TypeVar

from ..workflow.errors import is_network_error

logger = logging.getLogger(__name__)

_T = TypeVar("_T")


@dataclass
class RetryPolicy:
    """Configuration for a retry loop.

    Attributes:
        max_attempts: Total number of attempts, including the initial
            call (must be >= 1).
        base_delay: Base delay in seconds before the first retry.
        max_delay: Upper bound (seconds) for the exponential backoff.
        multiplier: Backoff growth factor between attempts.
        jitter: Relative jitter fraction (0.0–1.0) applied to each
            sleep, e.g. 0.1 means +/-10% around the nominal delay.
            The nominal delay from :func:`compute_delay` is not jittered.
        retryable_exceptions: Optional tuple of exception types that are
            eligible for retry. When set, exceptions outside this tuple
            are never retried.
        should_retry: Optional callable ``(exc) -> bool`` giving full
            control over retryability. When set, it overrides both
            ``retryable_exceptions`` and the default retryability check.
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    multiplier: float = 2.0
    jitter: float = 0.1
    retryable_exceptions: Optional[Tuple[Type[BaseException], ...]] = None
    should_retry: Optional[Callable[[BaseException], bool]] = None

    def is_retryable(self, exc: BaseException) -> bool:
        """
        Returns:
            True if *exc* warrants another attempt.

            Resolution order: ``should_retry`` → ``retryable_exceptions``
            (whitelist) → ``retryable`` attribute / ``is_network_error``.
        """
        if self.should_retry is not None:
            return bool(self.should_retry(exc))
        if self.retryable_exceptions is not None and not isinstance(exc, self.retryable_exceptions):
            return False
        retryable_attr = getattr(exc, "retryable", False)
        return bool(retryable_attr) or is_network_error(exc)


def compute_delay(attempt: int, policy: RetryPolicy) -> float:
    """Return the deterministic backoff delay before retry *attempt*.

    ``attempt`` is the zero-based index of the upcoming attempt: 0 means
    the initial call (no delay → ``0.0``), 1 means the first retry
    (``base_delay``), 2 the second retry (``base_delay * multiplier``),
    and so on, capped at ``policy.max_delay``.

    The value is intentionally **idempotent** — given the same
    ``(attempt, policy)`` it always returns the same delay. Jitter is
    applied only at sleep time inside :func:`with_retry` /
    :func:`with_async_retry`, so callers can reuse this helper for
    logging "expected next wait" without randomness.

    Args:
        attempt: Zero-based index of the upcoming attempt (>= 0).
        policy: The retry policy.

    Returns:
        The nominal delay in seconds before that attempt (``0.0`` for
        the first attempt).
    """
    if attempt <= 0:
        return 0.0
    delay = policy.base_delay * (policy.multiplier ** (attempt - 1))
    return min(delay, policy.max_delay)


def _sleep_delay(attempt: int, policy: RetryPolicy) -> float:
    """Compute a jittered sleep for retry *attempt* (internal helper)."""
    base = compute_delay(attempt, policy)
    if policy.jitter <= 0 or base <= 0:
        return base
    factor = 1.0 + random.uniform(-policy.jitter, policy.jitter)  # nosec B311  # jitter for backoff, not security
    return max(0.0, base * factor)


def _validate_policy(policy: RetryPolicy) -> None:
    """Validate a RetryPolicy and raise ValueError for bad values."""
    if policy.max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if policy.base_delay < 0:
        raise ValueError("base_delay must be >= 0")
    if policy.max_delay < 0:
        raise ValueError("max_delay must be >= 0")
    if policy.multiplier <= 0:
        raise ValueError("multiplier must be > 0")
    if not 0 <= policy.jitter <= 1:
        raise ValueError("jitter must be within [0, 1]")


def with_retry(policy: RetryPolicy) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Decorator that retries a **synchronous** callable per *policy*.

    Usage::

        @with_retry(RetryPolicy(max_attempts=3, base_delay=0.5))
        def call_llm(prompt: str) -> str:
            ...

    Retryable failures sleep with exponential backoff + jitter between
    attempts. The original exception is re-raised unchanged once
    ``max_attempts`` are exhausted (or immediately for non-retryable
    errors).

    Args:
        policy: The retry configuration.

    Returns:
        A decorator wrapping ``fn``.
    """
    _validate_policy(policy)

    def decorator(fn: Callable[..., _T]) -> Callable[..., _T]:
        """Register a decorator function."""

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> _T:
            """Wrapper function for the decorator pattern."""
            for attempt in range(policy.max_attempts):
                try:
                    return fn(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 — re-raised unchanged
                    is_last = attempt == policy.max_attempts - 1
                    if is_last or not policy.is_retryable(exc):
                        raise
                    delay = _sleep_delay(attempt + 1, policy)
                    logger.debug(
                        "retry[%s]: attempt %d/%d failed with %s: %s; retrying in %.3fs",
                        getattr(fn, "__name__", fn),
                        attempt + 1,
                        policy.max_attempts,
                        type(exc).__name__,
                        exc,
                        delay,
                    )
                    time.sleep(delay)
            # Unreachable: the loop either returns or raises on the last
            # attempt. Kept for the type checker.
            raise AssertionError("unreachable")

        return wrapper

    return decorator


def with_async_retry(
    policy: RetryPolicy,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator that retries an **async** callable per *policy*.

    Identical semantics to :func:`with_retry` but uses ``asyncio.sleep``
    so the event loop is not blocked during backoff.

    Usage::

        @with_async_retry(RetryPolicy(max_attempts=3, base_delay=0.5))
        async def call_tts(text: str) -> None:
            ...

    Args:
        policy: The retry configuration.

    Returns:
        A decorator wrapping the async ``fn``.
    """
    _validate_policy(policy)

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        """Register a decorator function."""

        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            """(async) Wrapper function for the decorator pattern."""
            for attempt in range(policy.max_attempts):
                try:
                    return await fn(*args, **kwargs)
                except BaseException as exc:  # noqa: BLE001 — re-raised unchanged
                    is_last = attempt == policy.max_attempts - 1
                    if is_last or not policy.is_retryable(exc):
                        raise
                    delay = _sleep_delay(attempt + 1, policy)
                    logger.debug(
                        "async_retry[%s]: attempt %d/%d failed with %s: %s; retrying in %.3fs",
                        getattr(fn, "__name__", fn),
                        attempt + 1,
                        policy.max_attempts,
                        type(exc).__name__,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)
            raise AssertionError("unreachable")

        return wrapper

    return decorator


__all__ = [
    "RetryPolicy",
    "compute_delay",
    "with_async_retry",
    "with_retry",
]
