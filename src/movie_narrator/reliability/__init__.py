# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Reliability toolkit (v0.9.1): circuit breaker + retry policy framework.

Public surface:

- :class:`CircuitBreaker` / :class:`CircuitBreakerRegistry` /
  :class:`CircuitOpenError` / :class:`CircuitState` /
  :func:`circuit_guard` — fail-fast protection for external API calls.
- :class:`RetryPolicy` / :func:`with_retry` /
  :func:`with_async_retry` / :func:`compute_delay` — configurable,
  bounded retries with exponential backoff + jitter.
"""

from .circuit_breaker import (
    CIRCUIT_REGISTRY,
    CircuitBreaker,
    CircuitBreakerRegistry,
    CircuitOpenError,
    CircuitState,
    circuit_guard,
)
from .retry import RetryPolicy, compute_delay, with_async_retry, with_retry

__all__ = [
    "CIRCUIT_REGISTRY",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitOpenError",
    "CircuitState",
    "RetryPolicy",
    "circuit_guard",
    "compute_delay",
    "with_async_retry",
    "with_retry",
]
