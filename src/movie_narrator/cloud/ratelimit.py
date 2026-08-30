# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Per-tenant token-bucket rate limiting for task submissions (v1.5.1).

Opt-in protection for ``POST /tasks`` / ``POST /tasks/batch``: when
enabled, each submission costs one token from the caller's tenant
bucket. Read routes (status, artifacts, health, metrics, ...) stay
open. Disabled by default — the default configuration is
byte-for-byte unchanged.

Design:

- :class:`TokenBucket` — classic token bucket (capacity, refill rate,
  monotonic clock). :meth:`TokenBucket.try_acquire` returns ``0.0``
  when a token was granted, otherwise the retry-after delay in
  seconds. Thread-safe.
- :class:`RateLimiter` — per-key (tenant id) buckets with an LRU cap;
  the least recently used bucket is evicted once the cap is exceeded
  so a very large tenant population cannot grow memory without bound.

Environment variables (read from the **process environment** at
:class:`~movie_narrator.cloud.TaskAPIServer` construction — the same
precedent as ``MN_WEBHOOK_*``; see ``.env.example``):

    ``MN_RATE_LIMIT_ENABLED``            opt-in flag (default off)
    ``MN_RATE_LIMIT_CAPACITY``           bucket capacity (default 60)
    ``MN_RATE_LIMIT_REFILL_PER_MINUTE``  refill rate (default 60)

Typical usage::

    from movie_narrator.cloud.ratelimit import RateLimiter

    limiter = RateLimiter.from_env()
    retry_after = limiter.try_acquire("tenant-a")
    if retry_after > 0:
        ...  # respond 429 with Retry-After: retry_after
"""

from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from typing import Callable, Dict, Mapping, Optional

__all__ = ["RateLimiter", "TokenBucket"]

#: Environment variables (documented in .env.example).
ENV_RATE_LIMIT_ENABLED = "MN_RATE_LIMIT_ENABLED"
ENV_RATE_LIMIT_CAPACITY = "MN_RATE_LIMIT_CAPACITY"
ENV_RATE_LIMIT_REFILL_PER_MINUTE = "MN_RATE_LIMIT_REFILL_PER_MINUTE"

#: Default bucket capacity / refill rate (≈ one submission per second
#: sustained, burstable to a full bucket).
DEFAULT_CAPACITY = 60.0
DEFAULT_REFILL_PER_MINUTE = 60.0

#: Maximum number of tenant buckets kept in memory; idle (least
#: recently used) buckets are evicted beyond this cap.
DEFAULT_MAX_BUCKETS = 1000

_TRUTHY = frozenset({"1", "true", "yes", "on"})


class TokenBucket:
    """Thread-safe token bucket with a monotonic clock.

    Args:
        capacity: Maximum tokens the bucket can hold (and starts with).
        refill_per_minute: Tokens added per minute (must be > 0).
        clock: Monotonic time source returning seconds; defaults to
            :func:`time.monotonic`. Tests inject a fake clock.
    """

    def __init__(
        self,
        capacity: float,
        refill_per_minute: float,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        if refill_per_minute <= 0:
            raise ValueError("refill_per_minute must be > 0")
        self.capacity = float(capacity)
        self.refill_per_minute = float(refill_per_minute)
        self._refill_per_second = self.refill_per_minute / 60.0
        self._clock = clock or time.monotonic
        self._tokens = self.capacity
        self._last = self._clock()
        self._lock = threading.Lock()

    @property
    def available_tokens(self) -> float:
        """Best-effort snapshot of the tokens currently available.

        Computes the pending refill since the last acquisition without
        mutating bucket state (pure read).
        """
        now = self._clock()
        with self._lock:
            elapsed = max(0.0, now - self._last)
            return min(self.capacity, self._tokens + elapsed * self._refill_per_second)

    def try_acquire(self, tokens: float = 1.0) -> float:
        """Try to take *tokens* from the bucket.

        Returns:
            ``0.0`` when the tokens were granted; otherwise the number
            of seconds after which the request should be retried (the
            time needed for the deficit to refill at the configured
            rate).
        """
        now = self._clock()
        with self._lock:
            elapsed = max(0.0, now - self._last)
            self._tokens = min(self.capacity, self._tokens + elapsed * self._refill_per_second)
            self._last = now
            if self._tokens >= tokens:
                self._tokens -= tokens
                return 0.0
            deficit = tokens - self._tokens
            return deficit / self._refill_per_second


class RateLimiter:
    """Per-key token buckets with an LRU cap (key = tenant id).

    Args:
        enabled: When False, :meth:`try_acquire` always grants (the
            pre-v1.5.1 unlimited behaviour).
        capacity: Per-tenant bucket capacity.
        refill_per_minute: Per-tenant refill rate.
        max_buckets: LRU cap on tracked tenants; the least recently
            used bucket is evicted beyond the cap.
        clock: Optional monotonic time source (tests inject a fake).
    """

    def __init__(
        self,
        *,
        enabled: bool = False,
        capacity: float = DEFAULT_CAPACITY,
        refill_per_minute: float = DEFAULT_REFILL_PER_MINUTE,
        max_buckets: int = DEFAULT_MAX_BUCKETS,
        clock: Optional[Callable[[], float]] = None,
    ) -> None:
        if max_buckets <= 0:
            raise ValueError("max_buckets must be > 0")
        self.enabled = enabled
        self.capacity = capacity
        self.refill_per_minute = refill_per_minute
        self.max_buckets = max_buckets
        self._clock = clock
        self._buckets: "OrderedDict[str, TokenBucket]" = OrderedDict()
        self._lock = threading.Lock()

    # ── Construction from the process environment ──────────

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "RateLimiter":
        """Build a limiter from ``MN_RATE_LIMIT_*`` process-env variables.

        Disabled unless ``MN_RATE_LIMIT_ENABLED`` is a truthy value
        (``1/true/yes/on``). Invalid numeric values fall back to the
        defaults (same tolerance as the other operational ``MN_*``
        admission variables).
        """
        env = os.environ if environ is None else environ

        def _float_of(name: str, default: float) -> float:
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            try:
                value = float(raw)
            except ValueError:
                return default
            return value if value > 0 else default

        enabled = (env.get(ENV_RATE_LIMIT_ENABLED) or "").strip().lower() in _TRUTHY
        return cls(
            enabled=enabled,
            capacity=_float_of(ENV_RATE_LIMIT_CAPACITY, DEFAULT_CAPACITY),
            refill_per_minute=_float_of(
                ENV_RATE_LIMIT_REFILL_PER_MINUTE, DEFAULT_REFILL_PER_MINUTE
            ),
        )

    # ── Acquisition ────────────────────────────────────────

    @property
    def bucket_count(self) -> int:
        """Number of tenant buckets currently tracked (observability)."""
        with self._lock:
            return len(self._buckets)

    def try_acquire(self, key: str, tokens: float = 1.0) -> float:
        """Try to take *tokens* from the bucket owned by *key*.

        Returns:
            ``0.0`` when granted (or when the limiter is disabled);
            otherwise the retry-after delay in seconds for that tenant.
        """
        if not self.enabled:
            return 0.0
        bucket = self._bucket_for(key)
        return bucket.try_acquire(tokens)

    def _bucket_for(self, key: str) -> TokenBucket:
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                if len(self._buckets) >= self.max_buckets:
                    # LRU idle eviction: drop the least recently used
                    # tenant bucket to bound memory.
                    self._buckets.popitem(last=False)
                bucket = TokenBucket(
                    self.capacity,
                    self.refill_per_minute,
                    clock=self._clock,
                )
                self._buckets[key] = bucket
            else:
                self._buckets.move_to_end(key)
            return bucket

    def snapshot(self) -> Dict[str, float]:
        """Remaining tokens per tenant (best-effort, for observability)."""
        with self._lock:
            return {key: bkt.available_tokens for key, bkt in self._buckets.items()}
