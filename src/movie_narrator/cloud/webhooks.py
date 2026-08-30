# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Webhook dispatch — push notifications for task state transitions (v1.3.1).

When ``MN_WEBHOOK_URLS`` is configured, terminal task transitions
(``completed`` / ``failed`` / ``cancelled``) are pushed as signed JSON
webhooks. Design guarantees:

- **Fire-and-forget** — deliveries run on a small daemon thread pool; a
  webhook failure is logged and recorded but *never* affects the task
  outcome.
- **Signed** — every request carries ``X-MN-Signature``, the hex
  HMAC-SHA256 over the raw request body keyed with ``MN_WEBHOOK_SECRET``.
  Consumers verify the signature before trusting the payload.
- **Retried** — non-2xx responses and transport errors are retried with
  exponential backoff via the shared reliability :func:`with_retry`
  framework; a ``Retry-After`` response header, when present, overrides
  the computed delay.
- **Auditable** — one JSONL line per delivery attempt is appended to
  ``webhook_deliveries.jsonl`` next to the task store.
- **Idempotent-friendly** — the event ``id`` (uuid4 hex) identifies one
  logical event across all retries and all target URLs; consumers should
  deduplicate on it. (Redelivery on demand is deferred.)

Environment variables:
    ``MN_WEBHOOK_URLS``        comma-separated target URLs (unset = off)
    ``MN_WEBHOOK_SECRET``      HMAC-SHA256 signing secret (optional)
    ``MN_WEBHOOK_TIMEOUT``     per-request timeout seconds (default 10)
    ``MN_WEBHOOK_MAX_RETRIES`` retries after the first attempt (default 3)

Typical usage::

    from movie_narrator.cloud.webhooks import WebhookDispatcher

    dispatcher = WebhookDispatcher.from_env(storage_dir=Path("~/.mn_tasks"))
    if dispatcher is not None:
        dispatcher.dispatch_for_task(task)   # fire-and-forget
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import queue as queue_mod
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence
from uuid import uuid4

import httpx

from ..reliability.retry import RetryPolicy, compute_delay, with_retry
from .models import Task, TaskStatus

logger = logging.getLogger(__name__)

#: Environment variables (documented in .env.example).
ENV_WEBHOOK_URLS = "MN_WEBHOOK_URLS"
ENV_WEBHOOK_SECRET = "MN_WEBHOOK_SECRET"  # nosec B105  # env var NAME, not a credential
ENV_WEBHOOK_TIMEOUT = "MN_WEBHOOK_TIMEOUT"
ENV_WEBHOOK_MAX_RETRIES = "MN_WEBHOOK_MAX_RETRIES"

#: Delivery record file, co-located with the task store.
DELIVERY_LOG_FILENAME = "webhook_deliveries.jsonl"

#: Event types emitted on terminal task transitions.
EVENT_TASK_COMPLETED = "task.completed"
EVENT_TASK_FAILED = "task.failed"
EVENT_TASK_CANCELLED = "task.cancelled"

#: Task status -> webhook event type. ``DEAD`` is a failure from a
#: consumer's perspective (retries were exhausted).
_EVENT_TYPES: Dict[TaskStatus, str] = {
    TaskStatus.COMPLETED: EVENT_TASK_COMPLETED,
    TaskStatus.FAILED: EVENT_TASK_FAILED,
    TaskStatus.DEAD: EVENT_TASK_FAILED,
    TaskStatus.CANCELLED: EVENT_TASK_CANCELLED,
}

#: Default dispatcher settings.
_DEFAULT_TIMEOUT_S = 10.0
_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BASE_DELAY_S = 0.5
_MAX_WORKERS = 2
_MAX_ERROR_LENGTH = 200


def _utc_now_iso() -> str:
    """
    Returns:
        Current UTC time in ISO format.
    """
    return datetime.now(timezone.utc).isoformat()


def sign_payload(secret: str, body: bytes) -> str:
    """Hex HMAC-SHA256 of *body* keyed with *secret*.

    This is the exact value carried in the ``X-MN-Signature`` header;
    consumers recompute it over the raw request body.
    """
    return hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


@dataclass
class WebhookEvent:
    """A task lifecycle notification pushed to webhook consumers.

    Attributes:
        id: uuid4 hex — the dedup key (stable across retries and URLs).
        type: One of ``task.completed`` / ``task.failed`` /
            ``task.cancelled``.
        task_id: The task the event is about.
        tenant_id: Tenant label of the task (``"default"`` in the
            single-tenant setup).
        created_at: ISO-8601 UTC timestamp of the transition.
        data: Small payload — status, error summary and artifact *names*
            (no paths, no large payloads).
    """

    id: str = field(default_factory=lambda: uuid4().hex)
    type: str = ""
    task_id: str = ""
    tenant_id: str = "default"
    created_at: str = field(default_factory=_utc_now_iso)
    data: Dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> Dict[str, Any]:
        """
        Returns:
            The JSON-serializable webhook body.
        """
        return {
            "id": self.id,
            "type": self.type,
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "data": dict(self.data),
        }


def event_for_task(task: Task) -> Optional[WebhookEvent]:
    """Build the :class:`WebhookEvent` for a task's terminal state.

    Returns:
        The event, or None when the task has no webhook-relevant state
        (non-terminal statuses never notify).
    """
    event_type = _EVENT_TYPES.get(task.status)
    if event_type is None:
        return None
    error = task.last_error or (task.result.error if task.result else None) or ""
    artifacts: List[str] = []
    if task.result is not None:
        for path in (
            task.result.video_path,
            task.result.audio_path,
            task.result.subtitle_path,
            task.result.script_md_path,
        ):
            if path:
                name = Path(path).name
                if name not in artifacts:
                    artifacts.append(name)
    return WebhookEvent(
        type=event_type,
        task_id=task.id,
        tenant_id=task.tenant_id or "default",
        data={
            "status": task.status.value,
            "error": error[:_MAX_ERROR_LENGTH] or None,
            "artifacts": artifacts,
        },
    )


class _DeliveryError(Exception):
    """Internal: a delivery attempt failed (status or transport).

    Carries the optional ``Retry-After`` hint (seconds) so the retry
    policy can honour it. ``retryable`` follows the reliability
    framework's convention (the ``retryable_exceptions`` whitelist only
    filters non-members; the attribute marks the exception as retryable).
    """

    retryable = True

    def __init__(self, message: str, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class WebhookDispatcher:
    """Signed, retried, fire-and-forget webhook delivery.

    Args:
        urls: Target URLs (one POST per URL per event).
        secret: Optional HMAC-SHA256 signing secret
            (``X-MN-Signature`` header omitted when unset).
        timeout: Per-request timeout in seconds.
        max_retries: Retries after the first attempt
            (total attempts = ``max_retries + 1``).
        base_delay: Base exponential-backoff delay in seconds.
        delivery_log: JSONL file receiving one line per attempt.
            None disables recording.
        transport: Optional httpx transport override (tests inject
            ``httpx.MockTransport`` — no network in unit tests).
        max_workers: Size of the daemon delivery pool.
    """

    def __init__(
        self,
        urls: Sequence[str],
        *,
        secret: Optional[str] = None,
        timeout: float = _DEFAULT_TIMEOUT_S,
        max_retries: int = _DEFAULT_MAX_RETRIES,
        base_delay: float = _DEFAULT_BASE_DELAY_S,
        delivery_log: Optional[Path] = None,
        transport: Optional[httpx.BaseTransport] = None,
        max_workers: int = _MAX_WORKERS,
    ) -> None:
        self._urls = [u.strip() for u in urls if u and u.strip()]
        self._secret = secret
        self._timeout = max(float(timeout), 0.1)
        self._max_retries = max(int(max_retries), 0)
        self._base_delay = max(float(base_delay), 0.0)
        self._delivery_log = Path(delivery_log) if delivery_log else None
        self._log_lock = threading.Lock()
        # A single httpx.Client is thread-safe for concurrent requests.
        self._client = httpx.Client(timeout=self._timeout, transport=transport)
        self._max_workers = max(int(max_workers), 1)
        self._jobs: "queue_mod.Queue[Any]" = queue_mod.Queue()
        self._threads: List[threading.Thread] = []
        self._threads_lock = threading.Lock()
        self._closed = False

    # ── Configuration ────────────────────────────────────────

    @classmethod
    def from_env(
        cls,
        storage_dir: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> Optional["WebhookDispatcher"]:
        """Build a dispatcher from ``MN_WEBHOOK_*`` environment variables.

        Args:
            storage_dir: Directory holding the task store; the delivery
                log is written next to it (``webhook_deliveries.jsonl``).
            env: Environment mapping (defaults to ``os.environ``).

        Returns:
            A configured dispatcher, or None when ``MN_WEBHOOK_URLS`` is
            unset/empty (feature disabled — the no-op default).
        """
        environ: Mapping[str, str] = os.environ if env is None else env
        raw_urls = (environ.get(ENV_WEBHOOK_URLS) or "").strip()
        if not raw_urls:
            return None
        urls = [u for u in (p.strip() for p in raw_urls.split(",")) if u]
        if not urls:
            return None

        timeout = _DEFAULT_TIMEOUT_S
        raw_timeout = (environ.get(ENV_WEBHOOK_TIMEOUT) or "").strip()
        if raw_timeout:
            try:
                timeout = float(raw_timeout)
            except ValueError:
                logger.warning(
                    "Ignoring invalid %s=%r — using default %.1fs",
                    ENV_WEBHOOK_TIMEOUT,
                    raw_timeout,
                    _DEFAULT_TIMEOUT_S,
                )
        max_retries = _DEFAULT_MAX_RETRIES
        raw_retries = (environ.get(ENV_WEBHOOK_MAX_RETRIES) or "").strip()
        if raw_retries:
            try:
                max_retries = int(raw_retries)
            except ValueError:
                logger.warning(
                    "Ignoring invalid %s=%r — using default %d",
                    ENV_WEBHOOK_MAX_RETRIES,
                    raw_retries,
                    _DEFAULT_MAX_RETRIES,
                )
        secret = (environ.get(ENV_WEBHOOK_SECRET) or "").strip() or None
        delivery_log = (
            Path(storage_dir) / DELIVERY_LOG_FILENAME if storage_dir else None
        )
        return cls(
            urls,
            secret=secret,
            timeout=timeout,
            max_retries=max_retries,
            delivery_log=delivery_log,
        )

    @property
    def enabled(self) -> bool:
        """Whether any target URL is configured."""
        return bool(self._urls)

    @property
    def urls(self) -> List[str]:
        """Configured target URLs."""
        return list(self._urls)

    # ── Emission ─────────────────────────────────────────────

    def dispatch(self, event: WebhookEvent) -> None:
        """Queue *event* for delivery to every configured URL.

        Fire-and-forget: returns immediately; failures are logged and
        recorded, never raised.
        """
        if not self.enabled or self._closed:
            return
        body = json.dumps(event.to_payload(), ensure_ascii=False).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-MN-Event-Id": event.id,
            "X-MN-Event-Type": event.type,
            "X-MN-Timestamp": event.created_at,
        }
        if self._secret:
            headers["X-MN-Signature"] = sign_payload(self._secret, body)
        self._ensure_threads()
        for url in self._urls:
            self._jobs.put((url, body, headers))

    def dispatch_for_task(self, task: Task) -> Optional[WebhookEvent]:
        """Build and dispatch the event for a task's terminal state.

        Returns:
            The dispatched event, or None when the task state does not
            warrant a webhook (non-terminal) or the dispatcher is disabled.
        """
        event = event_for_task(task)
        if event is None:
            return None
        self.dispatch(event)
        return event

    # ── Delivery worker pool ─────────────────────────────────

    def _ensure_threads(self) -> None:
        """Start the daemon delivery pool on first use."""
        with self._threads_lock:
            if self._threads:
                return
            for i in range(self._max_workers):
                thread = threading.Thread(
                    target=self._worker_loop,
                    name=f"mn-webhook-{i}",
                    daemon=True,
                )
                thread.start()
                self._threads.append(thread)

    def _worker_loop(self) -> None:
        """Consume delivery jobs; a job failure never kills the thread."""
        while True:
            job = self._jobs.get()
            try:
                if job is None:
                    return
                url, body, headers = job
                self._deliver(url, body, headers)
            except Exception:  # noqa: BLE001 — fire-and-forget, never raise
                logger.warning(
                    "Webhook delivery to %s failed permanently", job[0] if job else "?"
                )
            finally:
                self._jobs.task_done()

    def _retry_policy(self) -> RetryPolicy:
        """Retry policy honouring ``Retry-After`` when a server sends it."""
        policy = RetryPolicy(
            max_attempts=self._max_retries + 1,
            base_delay=self._base_delay,
            max_delay=30.0,
            jitter=0.0,
            # Any delivery failure (HTTP status or transport error) is
            # retried until the attempt budget is exhausted.
            retryable_exceptions=(_DeliveryError,),
        )

        def _delay(exc: BaseException, attempt: int) -> float:
            retry_after = getattr(exc, "retry_after", None)
            if retry_after:
                return float(retry_after)
            return compute_delay(attempt, policy)

        policy.delay_from_exception = _delay
        return policy

    def _deliver(self, url: str, body: bytes, headers: Dict[str, str]) -> None:
        """Deliver *body* to *url* with retries; record every attempt."""
        attempt = {"n": 0}

        @with_retry(self._retry_policy())
        def _attempt() -> None:
            attempt["n"] += 1
            self._deliver_once(url, body, headers, attempt["n"])

        try:
            _attempt()
        except _DeliveryError as e:
            logger.warning(
                "Webhook %s: delivery to %s failed after %d attempt(s): %s",
                headers.get("X-MN-Event-Id", "?"),
                url,
                attempt["n"],
                e,
            )

    def _deliver_once(
        self,
        url: str,
        body: bytes,
        headers: Dict[str, str],
        attempt: int,
    ) -> None:
        """One HTTP attempt plus its JSONL record. Raises ``_DeliveryError``."""
        status_code: Optional[int] = None
        error: Optional[str] = None
        retry_after: Optional[float] = None
        try:
            response = self._client.post(url, content=body, headers=headers)
            status_code = response.status_code
            if 200 <= status_code < 300:
                self._record(url, event_id=None, headers=headers, attempt=attempt,
                             status_code=status_code, error=None, ok=True)
                return
            raw_retry_after = response.headers.get("Retry-After")
            if raw_retry_after:
                try:
                    retry_after = float(raw_retry_after)
                except ValueError:
                    retry_after = None
            error = f"HTTP {status_code}"
        except httpx.HTTPError as e:
            error = f"{type(e).__name__}: {e}"
        self._record(url, event_id=None, headers=headers, attempt=attempt,
                     status_code=status_code, error=error, ok=False)
        raise _DeliveryError(error or "delivery failed", retry_after=retry_after)

    # ── Delivery records ─────────────────────────────────────

    def _record(
        self,
        url: str,
        *,
        event_id: Optional[str],
        headers: Dict[str, str],
        attempt: int,
        status_code: Optional[int],
        error: Optional[str],
        ok: bool,
    ) -> None:
        """Append one JSONL line for this attempt (best-effort)."""
        if self._delivery_log is None:
            return
        line = {
            "event_id": event_id or headers.get("X-MN-Event-Id", ""),
            "url": url,
            "status_code": status_code,
            "error": error,
            "attempt": attempt,
            "timestamp": _utc_now_iso(),
            "ok": ok,
        }
        try:
            self._delivery_log.parent.mkdir(parents=True, exist_ok=True)
            with self._log_lock:
                with self._delivery_log.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.debug("Failed to write webhook delivery record: %s", e)

    # ── Lifecycle ────────────────────────────────────────────

    def flush(self, timeout: Optional[float] = None) -> bool:
        """Wait for all queued deliveries to finish (test/shutdown helper).

        Returns:
            True when the queue drained, False on timeout.
        """
        try:
            if timeout is None:
                self._jobs.join()
                return True
            # Bounded wait: poll the unfinished-task count.
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if not self._jobs.unfinished_tasks:
                    return True
                time.sleep(0.01)
            return not self._jobs.unfinished_tasks
        except Exception:  # noqa: BLE001 — best-effort only
            return False

    def close(self, timeout: float = 5.0) -> None:
        """Stop the pool (idempotent). Pending deliveries get the budget."""
        if self._closed:
            return
        self._closed = True
        self.flush(timeout=timeout)
        for _ in self._threads:
            self._jobs.put(None)
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._client.close()

    def __enter__(self) -> "WebhookDispatcher":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


__all__ = [
    "DELIVERY_LOG_FILENAME",
    "ENV_WEBHOOK_MAX_RETRIES",
    "ENV_WEBHOOK_SECRET",
    "ENV_WEBHOOK_TIMEOUT",
    "ENV_WEBHOOK_URLS",
    "EVENT_TASK_CANCELLED",
    "EVENT_TASK_COMPLETED",
    "EVENT_TASK_FAILED",
    "WebhookDispatcher",
    "WebhookEvent",
    "event_for_task",
    "sign_payload",
]
