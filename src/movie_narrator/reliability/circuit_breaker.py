# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Circuit breaker for external API calls (v0.9.1).

Protects calls to third-party services (LLM, TTS, TMDB, VLM) from
repeatedly hitting an unhealthy endpoint. Once the failure threshold is
reached the circuit **opens** and subsequent calls fail fast (raise
:class:`CircuitOpenError`) without touching the network. After the
recovery timeout elapses the circuit transitions to **half-open** and
lets a small number of probe requests through; a probe success closes
the circuit again, a probe failure re-opens it.

State machine::

    CLOSED --failure_threshold failures--> OPEN
    OPEN   --recovery_timeout elapses-->    HALF_OPEN
    HALF_OPEN --probe success-->            CLOSED
    HALF_OPEN --probe failure-->            OPEN

Usage::

    breaker = CircuitBreaker("tmdb")
    with breaker.guard():
        return fetch_from_tmdb()

    @circuit_guard("tmdb")
    def fetch():
        ...

All state transitions are logged at DEBUG level through the project's
standard ``logging`` facade.
"""

from __future__ import annotations

import logging
import threading
import time
from contextlib import contextmanager
from enum import Enum
from functools import wraps
from typing import Any, Callable, Dict, Iterator, Optional, TypeVar, cast

logger = logging.getLogger(__name__)


class CircuitState(str, Enum):
    """Lifecycle states of a circuit breaker.

    Attributes:
        CLOSED: Normal operation — calls pass through, failures counted.
        OPEN: Failure threshold reached — calls rejected without network.
        HALF_OPEN: Recovery probe phase — a limited number of probe
            requests are let through to test the service.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a guarded call is rejected because the circuit is open.

    Carries a ``retryable=True`` attribute so the pipeline's retry
    machinery (``workflow.errors`` conventions) treats it as a transient
    failure rather than a permanent one.
    """

    def __init__(self, service: str, message: Optional[str] = None) -> None:
        self.service = service
        super().__init__(
            message
            or f"circuit breaker for service '{service}' is open; "
            f"request rejected without network call"
        )

    @property
    def retryable(self) -> bool:
        """Transient by nature — the circuit may recover, so retryable."""
        return True


_T = TypeVar("_T")

# Sentinel for "no override provided" so that ``None`` stays a valid
# ``half_open_max_calls`` value internally (it is normalized to 1).
_DEFAULT = object()


class CircuitBreaker:
    """Thread-safe circuit breaker guarding a single external service.

    Args:
        service: Name of the guarded service (used for logs and errors).
        failure_threshold: Number of consecutive failures before the
            circuit opens. Must be >= 1.
        recovery_timeout: Seconds to stay OPEN before transitioning to
            HALF_OPEN and allowing probe requests.
        half_open_max_calls: Maximum number of concurrent probe requests
            allowed while HALF_OPEN. Extra callers are rejected with
            :class:`CircuitOpenError`.
    """

    def __init__(
        self,
        service: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 1,
    ) -> None:
        if failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if recovery_timeout < 0:
            raise ValueError("recovery_timeout must be >= 0")
        if half_open_max_calls < 1:
            raise ValueError("half_open_max_calls must be >= 1")

        self.service = service
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at = 0.0
        self._half_open_calls = 0
        self._lock = threading.RLock()

    # ── Introspection ──────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        """Current circuit state (thread-safe read)."""
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        """Number of consecutive failures recorded in the CLOSED state."""
        with self._lock:
            return self._failure_count

    @property
    def is_open(self) -> bool:
        """True while the circuit rejects calls (OPEN state)."""
        return self.state is CircuitState.OPEN

    # ── State transitions (test / ops helpers) ─────────────

    def reset(self) -> None:
        """Force the circuit back to CLOSED and clear the failure count."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._half_open_calls = 0
        logger.debug(f"circuit[{self.service}] reset to CLOSED")

    def force_open(self) -> None:
        """Force the circuit to OPEN (e.g. for tests or manual tripping)."""
        with self._lock:
            self._state = CircuitState.OPEN
            self._opened_at = time.monotonic()
            self._half_open_calls = 0
        logger.debug(f"circuit[{self.service}] forced to OPEN")

    def force_half_open(self) -> None:
        """Force the circuit to HALF_OPEN (e.g. for tests)."""
        with self._lock:
            self._state = CircuitState.HALF_OPEN
            self._half_open_calls = 0
        logger.debug(f"circuit[{self.service}] forced to HALF_OPEN")

    # ── Execution helpers ──────────────────────────────────

    def call(self, fn: Callable[..., _T], *args: Any, **kwargs: Any) -> _T:
        """Invoke *fn* inside the circuit guard.

        Args:
            fn: Callable to execute under circuit protection.
            *args: Positional arguments forwarded to ``fn``.
            **kwargs: Keyword arguments forwarded to ``fn``.

        Returns:
            The return value of ``fn``.

        Raises:
            CircuitOpenError: If the circuit is open and no probe slot
                is available.
        """
        with self.guard():
            return fn(*args, **kwargs)

    @contextmanager
    def guard(self) -> Iterator[None]:
        """Context manager form of the circuit guard.

        Usage::

            breaker = CircuitBreakerRegistry["tmdb"]
            with breaker.guard():
                ...  # protected external call

        Raises:
            CircuitOpenError: If the circuit is open and no probe slot
                is available (raised before the body runs). Exceptions
                raised inside the body are recorded as failures and
                re-raised unchanged; a clean exit records a success.
        """
        probe = self._acquire()
        try:
            yield
        except CircuitOpenError:
            # A rejection while HALF_OPEN (probe slot contention) is
            # already accounted for at acquire time — do not double count.
            # A CircuitOpenError raised *inside* a probe body (nested
            # guard) releases the probe slot without recording an outcome.
            if probe:
                with self._lock:
                    self._half_open_calls = max(0, self._half_open_calls - 1)
            raise
        except BaseException:
            self._record_failure(probe)
            raise
        else:
            self._record_success(probe)

    def record_success(self) -> None:
        """Record a successful call from a non-guard integration point."""
        self._record_success(probe=False)

    def record_failure(self) -> None:
        """Record a failed call from a non-guard integration point."""
        self._record_failure(probe=False)

    # ── Internals (all under self._lock) ───────────────────

    def _acquire(self) -> bool:
        """Check the circuit and, if allowed, reserve a probe slot.

        Returns:
            True if this call is a HALF_OPEN probe request (a slot
            was reserved and must be released on completion). Raises
            :class:`CircuitOpenError` when the circuit is OPEN (or HALF_OPEN
            with all probe slots busy).
        """
        with self._lock:
            if self._state is CircuitState.CLOSED:
                return False

            if self._state is CircuitState.OPEN:
                if time.monotonic() - self._opened_at < self.recovery_timeout:
                    raise CircuitOpenError(self.service)
                # Recovery timeout elapsed — enter half-open probe phase.
                self._state = CircuitState.HALF_OPEN
                self._half_open_calls = 0
                logger.debug(
                    f"circuit[{self.service}] OPEN -> HALF_OPEN after "
                    f"{self.recovery_timeout}s recovery timeout"
                )

            # HALF_OPEN — limit concurrent probe requests.
            if self._half_open_calls >= self.half_open_max_calls:
                raise CircuitOpenError(
                    self.service,
                    f"circuit breaker for service '{self.service}' is "
                    f"half-open; all {self.half_open_max_calls} probe "
                    f"slot(s) are busy",
                )
            self._half_open_calls += 1
            return True

    def _record_success(self, probe: bool) -> None:
        with self._lock:
            if probe:
                # A probe succeeded — the service recovered. Close.
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._half_open_calls = max(0, self._half_open_calls - 1)
                logger.debug(f"circuit[{self.service}] HALF_OPEN -> CLOSED (probe success)")
            else:
                self._failure_count = 0

    def _record_failure(self, probe: bool) -> None:
        with self._lock:
            if probe:
                # A probe failed — the service is still unhealthy. Re-open.
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._half_open_calls = max(0, self._half_open_calls - 1)
                logger.debug(f"circuit[{self.service}] HALF_OPEN -> OPEN (probe failure)")
                return
            self._failure_count += 1
            if self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.debug(
                    f"circuit[{self.service}] CLOSED -> OPEN after "
                    f"{self._failure_count} consecutive failures"
                )


class CircuitBreakerRegistry:
    """Thread-safe registry of named :class:`CircuitBreaker` instances.

    Breakers are created lazily on first access and reused thereafter,
    so all code guarding the same service shares a single breaker state.

    Default construction parameters are resolved from
    :class:`movie_narrator.config.Settings` (``MN_CIRCUIT_*`` env vars)
    on first creation; per-service overrides win over the defaults.
    """

    def __init__(self) -> None:
        self._breakers: Dict[str, CircuitBreaker] = {}
        self._lock = threading.RLock()

    def get(
        self,
        service: str,
        *,
        failure_threshold: Any = _DEFAULT,
        recovery_timeout: Any = _DEFAULT,
        half_open_max_calls: Any = _DEFAULT,
    ) -> CircuitBreaker:
        """Return the breaker for *service*, creating it if necessary.

        Args:
            service: Service name (``"tmdb"``, ``"vlm"``, ``"llm"``,
                ``"tts"``, ...).
            failure_threshold: Optional per-service override.
            recovery_timeout: Optional per-service override.
            half_open_max_calls: Optional per-service override.

        Returns:
            The (shared) :class:`CircuitBreaker` for *service*.
        """
        with self._lock:
            if service not in self._breakers:
                params = self._default_params()
                for key, value in (
                    ("failure_threshold", failure_threshold),
                    ("recovery_timeout", recovery_timeout),
                    ("half_open_max_calls", half_open_max_calls),
                ):
                    if value is not _DEFAULT:
                        params[key] = cast(int, value)
                self._breakers[service] = CircuitBreaker(service, **params)
            return self._breakers[service]

    def __getitem__(self, service: str) -> CircuitBreaker:
        """``registry["tmdb"]`` sugar for :meth:`get`."""
        return self.get(service)

    def __contains__(self, service: str) -> bool:
        """True if a breaker for *service* has already been created."""
        with self._lock:
            return service in self._breakers

    def reset(self) -> None:
        """Reset all registered breakers to CLOSED (keeps them registered)."""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()

    def clear(self) -> None:
        """Drop all registered breakers (fresh state on next access)."""
        with self._lock:
            self._breakers.clear()

    @staticmethod
    def _default_params() -> Dict[str, Any]:
        """Resolve default breaker parameters from ``Settings``."""
        from ..config import get_settings  # lazy — keeps this module importable standalone

        settings = get_settings()
        return {
            "failure_threshold": settings.circuit_failure_threshold,
            "recovery_timeout": settings.circuit_recovery_timeout,
            "half_open_max_calls": settings.circuit_half_open_max_calls,
        }


# Global registry used by the ``circuit_guard`` decorator and the
# integration points (tmdb / vlm / llm / tts).
CIRCUIT_REGISTRY: CircuitBreakerRegistry = CircuitBreakerRegistry()


def circuit_guard(
    service: str,
    *,
    registry: Optional[CircuitBreakerRegistry] = None,
) -> Callable[[Callable[..., _T]], Callable[..., _T]]:
    """Decorator that wraps a callable with a named circuit breaker.

    Usage::

        @circuit_guard("tmdb")
        def fetch_movie_metadata(url: str) -> dict:
            ...

    The callable is executed inside ``breaker.guard()`` for the given
    service. When the circuit is open a :class:`CircuitOpenError` is
    raised without invoking the callable.

    Args:
        service: Service name used to look up the shared breaker.
        registry: Registry to use; defaults to the global
            :data:`CIRCUIT_REGISTRY`.

    Returns:
        A decorator that wraps ``fn``.
    """

    def decorator(fn: Callable[..., _T]) -> Callable[..., _T]:
        """Register a decorator function."""

        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> _T:
            """Wrapper function for the decorator pattern."""
            breaker = (registry if registry is not None else CIRCUIT_REGISTRY)[service]
            with breaker.guard():
                return fn(*args, **kwargs)

        return wrapper

    return decorator


__all__ = [
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "CircuitOpenError",
    "CircuitState",
    "CIRCUIT_REGISTRY",
    "circuit_guard",
]
