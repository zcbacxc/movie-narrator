# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Workflow-level error classes.

Introduces a ``retryable`` flag on provider/service errors so
the pipeline runner can distinguish transient (network-type) failures from
permanent (config/logic) ones and offer interactive retry accordingly.

This module is intentionally dependency-free (no imports from other
``movie_narrator`` subpackages) so it can be imported from anywhere —
pipeline, tts, utils — without risking circular imports.
"""

import logging

logger = logging.getLogger(__name__)


class ProviderError(Exception):
    """Base class for provider/service errors carrying a ``retryable`` flag.

    Network-type failures (timeouts, rate limits, temporary service
    unavailable) set ``retryable=True`` so the pipeline runner can offer
    interactive [R]etry/[S]kip/[A]bort when ``--retry`` is enabled, or at
    least suggest the flag when it is not. Configuration and logic errors
    default to ``retryable=False`` (non-retryable) and keep the existing
    fail-fast behavior.

    This mirrors the generic HTTP 429/503 "retry later" vs 4xx "do not
    retry" semantics as an independently-authored engineering pattern.

    The flag defaults to ``False`` for backward compatibility: existing
    errors that do not opt in remain non-retryable, so no caller's
    error-handling behavior changes unless it explicitly checks the flag.
    """

    def __init__(self, message: str = "", *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable: bool = retryable


class JobConfigError(ProviderError):
    """Raised for job YAML load / validation failures (before the pipeline runs).

    Configuration errors are non-retryable by default — the remediation is
    editing the job file and re-running, not retrying the same config — so
    ``retryable`` stays ``False`` (inherited from :class:`ProviderError`).
    """


def is_network_error(exc: BaseException) -> bool:
    """
    Returns:
        True if *exc* looks like a transient network/timeout failure.

        Network timeouts, connection resets, and rate limits are candidates for
        retry; configuration and logic errors are not. Checks the exception type
        against a generic, independently-authored list of network-type classes:
        the stdlib ``ConnectionError`` / ``TimeoutError`` plus the OpenAI SDK's
        ``APITimeoutError`` / ``APIConnectionError`` / ``RateLimitError`` when
        the SDK is importable.

        The OpenAI import is lazy so this module loads even when the ``openai``
        package is absent (e.g. minimal CI environments).
    """
    # Generic stdlib network errors. ConnectionError is the base for
    # ConnectionResetError / ConnectionAbortedError / ConnectionRefusedError;
    # TimeoutError covers socket-level timeouts.
    if isinstance(exc, (ConnectionError, TimeoutError)):
        return True
    # OpenAI SDK transient errors (imported lazily — optional dependency at
    # module-load time, but always present at runtime in production).
    try:
        from openai import APITimeoutError, APIConnectionError, RateLimitError
    except Exception:  # noqa: BLE001
        logger.debug("openai SDK not importable; cannot check transient errors", exc_info=True)
        return False
    return isinstance(exc, (APITimeoutError, APIConnectionError, RateLimitError))
