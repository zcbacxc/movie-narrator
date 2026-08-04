# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Prometheus metrics registry and exposition renderer (v0.8.1).

A deliberately small, dependency-free implementation of the three
metric types the project actually needs — Counter, Gauge and Histogram
— plus a renderer for the Prometheus text exposition format (version
0.0.4). Taking ``prometheus_client`` as a hard dependency would be
disproportionate: the exposition format is a handful of lines of text,
and the scrape path is not performance critical.

All metrics share a single registry-wide lock. Contention is negligible
because every operation is a dict lookup plus an arithmetic update,
whereas per-metric locks would complicate taking a consistent snapshot
during a scrape.

Instrumentation helpers (``record_*`` / ``observe_*`` / ``set_*``) never
raise: telemetry must not be able to fail a request or kill a worker
thread. They swallow and debug-log any error instead.

Typical usage::

    from movie_narrator.cloud import metrics

    metrics.record_task_submitted()
    body = metrics.render_prometheus_text()
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Type, TypeVar

from .. import __version__

logger = logging.getLogger(__name__)

__all__ = [
    "ACTIVE_TASKS",
    "BUILD_INFO",
    "CONTENT_TYPE_LATEST",
    "Counter",
    "DEFAULT_DURATION_BUCKETS",
    "ERRORS_TOTAL",
    "Gauge",
    "HTTP_REQUESTS_TOTAL",
    "Histogram",
    "MetricsRegistry",
    "QUEUE_DEPTH",
    "RENDER_DURATION",
    "TASKS_TOTAL",
    "TASK_DURATION",
    "get_registry",
    "observe_render_duration",
    "observe_task_duration",
    "record_error",
    "record_http_request",
    "record_task_submitted",
    "record_task_terminal",
    "render_prometheus_text",
    "reset_registry",
    "set_active_tasks",
    "set_queue_depth",
]

#: Content-Type Prometheus scrapers expect for the text format.
CONTENT_TYPE_LATEST = "text/plain; version=0.0.4; charset=utf-8"

# ── Metric family names ────────────────────────────────────

BUILD_INFO = "mn_build_info"
TASKS_TOTAL = "mn_tasks_total"
QUEUE_DEPTH = "mn_queue_depth"
ACTIVE_TASKS = "mn_active_tasks"
TASK_DURATION = "mn_task_duration_seconds"
RENDER_DURATION = "mn_render_duration_seconds"
ERRORS_TOTAL = "mn_errors_total"
HTTP_REQUESTS_TOTAL = "mn_http_requests_total"

#: Bucket boundaries (seconds) tuned for end-to-end pipeline runs, which
#: span from a few seconds (fully cached, no render) to tens of minutes.
DEFAULT_DURATION_BUCKETS: Tuple[float, ...] = (
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    120.0,
    300.0,
    600.0,
    1800.0,
)

_LabelKey = Tuple[str, ...]


# ── Escaping helpers ───────────────────────────────────────


def _escape_help(text: str) -> str:
    """Escape a HELP string: backslash and newline only (per spec)."""
    return text.replace("\\", "\\\\").replace("\n", "\\n")


def _escape_label_value(value: str) -> str:
    """Escape a label value: backslash, double quote and newline."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _format_float(value: float) -> str:
    """Render a float the way Prometheus expects.

    Integral values lose the fractional part, and the infinities use the
    ``+Inf`` / ``-Inf`` spellings the exposition format mandates.
    """
    if value == float("inf"):
        return "+Inf"
    if value == float("-inf"):
        return "-Inf"
    if value != value:  # NaN
        return "NaN"
    if float(value).is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(float(value))


def _render_labels(
    label_names: Sequence[str],
    label_values: Sequence[str],
    extra: Optional[Tuple[str, str]] = None,
) -> str:
    """Build the ``{k="v",...}`` suffix for a sample line."""
    parts = [
        f'{name}="{_escape_label_value(value)}"' for name, value in zip(label_names, label_values)
    ]
    if extra is not None:
        parts.append(f'{extra[0]}="{_escape_label_value(extra[1])}"')
    if not parts:
        return ""
    return "{" + ",".join(parts) + "}"


# ── Metric base ────────────────────────────────────────────


class _Metric:
    """Common state for all metric types.

    Args:
        name: Metric family name (must be a valid Prometheus name).
        help_text: One-line description emitted as ``# HELP``.
        label_names: Ordered label names; every sample must supply
            exactly these labels.
        lock: Registry-wide lock shared by all metrics.
    """

    metric_type = "untyped"

    def __init__(
        self,
        name: str,
        help_text: str = "",
        label_names: Sequence[str] = (),
        lock: Optional[threading.Lock] = None,
    ) -> None:
        self.name = name
        self.help_text = help_text or name
        self.label_names: Tuple[str, ...] = tuple(label_names)
        self._lock = lock or threading.Lock()

    def _key(self, labels: Optional[Dict[str, str]]) -> _LabelKey:
        """Normalise a label mapping into an ordered tuple key.

        Mismatched label sets raise rather than being coerced: a typo
        would otherwise silently split one time series into two.
        """
        if not self.label_names:
            if labels:
                raise ValueError(f"Metric {self.name!r} takes no labels")
            return ()
        labels = labels or {}
        missing = set(self.label_names) - set(labels)
        if missing:
            raise ValueError(f"Metric {self.name!r} missing labels: {sorted(missing)}")
        unexpected = set(labels) - set(self.label_names)
        if unexpected:
            raise ValueError(f"Metric {self.name!r} got unexpected labels: {sorted(unexpected)}")
        return tuple(str(labels[n]) for n in self.label_names)

    def render(self) -> List[str]:
        """Render this metric family as exposition-format sample lines."""
        raise NotImplementedError


# ── Counter ────────────────────────────────────────────────


class Counter(_Metric):
    """Monotonically increasing cumulative value."""

    metric_type = "counter"

    def __init__(
        self,
        name: str,
        help_text: str = "",
        label_names: Sequence[str] = (),
        lock: Optional[threading.Lock] = None,
    ) -> None:
        super().__init__(name, help_text, label_names, lock)
        self._values: Dict[_LabelKey, float] = {}

    def inc(
        self,
        amount: float = 1.0,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Increment by ``amount`` (must be non-negative)."""
        if amount < 0:
            raise ValueError("Counter increments must be non-negative")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def value(self, labels: Optional[Dict[str, str]] = None) -> float:
        """
        Returns:
            The current value for a label set (0.0 if never set).
        """
        key = self._key(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def render(self) -> List[str]:
        """Render metrics in the specified format."""
        with self._lock:
            items = sorted(self._values.items())
        return [
            f"{self.name}{_render_labels(self.label_names, key)} {_format_float(value)}"
            for key, value in items
        ]


# ── Gauge ──────────────────────────────────────────────────


class Gauge(_Metric):
    """Value that can go up and down (queue depth, active workers…)."""

    metric_type = "gauge"

    def __init__(
        self,
        name: str,
        help_text: str = "",
        label_names: Sequence[str] = (),
        lock: Optional[threading.Lock] = None,
    ) -> None:
        super().__init__(name, help_text, label_names, lock)
        self._values: Dict[_LabelKey, float] = {}

    def set(self, value: float, labels: Optional[Dict[str, str]] = None) -> None:
        """Set the gauge to an absolute value."""
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def inc(
        self,
        amount: float = 1.0,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Add ``amount`` to the gauge."""
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + amount

    def dec(
        self,
        amount: float = 1.0,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Subtract ``amount`` from the gauge."""
        self.inc(-amount, labels=labels)

    def value(self, labels: Optional[Dict[str, str]] = None) -> float:
        """
        Returns:
            The current value for a label set (0.0 if never set).
        """
        key = self._key(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def render(self) -> List[str]:
        """Render metrics in the specified format."""
        with self._lock:
            items = sorted(self._values.items())
        return [
            f"{self.name}{_render_labels(self.label_names, key)} {_format_float(value)}"
            for key, value in items
        ]


# ── Histogram ──────────────────────────────────────────────


class Histogram(_Metric):
    """Cumulative histogram with configurable bucket boundaries.

    Buckets follow ``le`` semantics: an observation counts towards every
    bucket whose upper bound is greater than or equal to the observed
    value. The implicit ``+Inf`` bucket is always emitted and equals
    ``_count``.
    """

    metric_type = "histogram"

    def __init__(
        self,
        name: str,
        help_text: str = "",
        label_names: Sequence[str] = (),
        lock: Optional[threading.Lock] = None,
        buckets: Iterable[float] = DEFAULT_DURATION_BUCKETS,
    ) -> None:
        super().__init__(name, help_text, label_names, lock)
        bounds = sorted(float(b) for b in buckets if b != float("inf"))
        if not bounds:
            raise ValueError("Histogram needs at least one finite bucket")
        self.buckets: Tuple[float, ...] = tuple(bounds)
        self._counts: Dict[_LabelKey, List[int]] = {}
        self._sums: Dict[_LabelKey, float] = {}
        self._totals: Dict[_LabelKey, int] = {}

    def observe(
        self,
        value: float,
        labels: Optional[Dict[str, str]] = None,
    ) -> None:
        """Record one observation."""
        key = self._key(labels)
        with self._lock:
            counts = self._counts.get(key)
            if counts is None:
                counts = [0] * len(self.buckets)
                self._counts[key] = counts
                self._sums[key] = 0.0
                self._totals[key] = 0
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    counts[i] += 1
            self._sums[key] += float(value)
            self._totals[key] += 1

    def count(self, labels: Optional[Dict[str, str]] = None) -> int:
        """Total number of observations for a label set."""
        key = self._key(labels)
        with self._lock:
            return self._totals.get(key, 0)

    def sum(self, labels: Optional[Dict[str, str]] = None) -> float:
        """Sum of all observed values for a label set."""
        key = self._key(labels)
        with self._lock:
            return self._sums.get(key, 0.0)

    def bucket_counts(
        self,
        labels: Optional[Dict[str, str]] = None,
    ) -> List[int]:
        """Cumulative counts aligned with :attr:`buckets`."""
        key = self._key(labels)
        with self._lock:
            counts = self._counts.get(key)
            return list(counts) if counts else [0] * len(self.buckets)

    def render(self) -> List[str]:
        """Render metrics in the specified format."""
        with self._lock:
            keys = sorted(self._counts)
            snapshot = {
                key: (list(self._counts[key]), self._sums[key], self._totals[key]) for key in keys
            }
        lines: List[str] = []
        for key in keys:
            counts, total_sum, total_count = snapshot[key]
            for bound, cumulative in zip(self.buckets, counts):
                suffix = _render_labels(self.label_names, key, extra=("le", _format_float(bound)))
                lines.append(f"{self.name}_bucket{suffix} {cumulative}")
            inf_suffix = _render_labels(self.label_names, key, extra=("le", "+Inf"))
            lines.append(f"{self.name}_bucket{inf_suffix} {total_count}")
            plain = _render_labels(self.label_names, key)
            lines.append(f"{self.name}_sum{plain} {_format_float(total_sum)}")
            lines.append(f"{self.name}_count{plain} {total_count}")
        return lines


# ── Registry ───────────────────────────────────────────────

_M = TypeVar("_M", bound=_Metric)


class MetricsRegistry:
    """Thread-safe collection of metric families keyed by name.

    The ``counter`` / ``gauge`` / ``histogram`` methods are
    get-or-create, so a call site can fetch its metric inline without
    module-level wiring, and :func:`reset_registry` cannot leave stale
    references behind. A mismatch in type or label names raises, which
    catches typos early instead of silently splitting a series.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._metrics: Dict[str, _Metric] = {}

    def _get_or_create(
        self,
        cls: Type[_M],
        name: str,
        help_text: str,
        label_names: Sequence[str],
        **kwargs: Any,
    ) -> _M:
        with self._lock:
            existing = self._metrics.get(name)
            if existing is not None:
                if not isinstance(existing, cls):
                    raise ValueError(
                        f"Metric {name!r} already registered as {type(existing).__name__}"
                    )
                if existing.label_names != tuple(label_names):
                    raise ValueError(
                        f"Metric {name!r} already registered with labels "
                        f"{list(existing.label_names)}"
                    )
                return existing
            metric = cls(
                name,
                help_text,
                tuple(label_names),
                self._lock,
                **kwargs,
            )
            self._metrics[name] = metric
            return metric

    def counter(
        self,
        name: str,
        help_text: str = "",
        label_names: Sequence[str] = (),
    ) -> Counter:
        """Get or create a Counter."""
        return self._get_or_create(Counter, name, help_text, label_names)

    def gauge(
        self,
        name: str,
        help_text: str = "",
        label_names: Sequence[str] = (),
    ) -> Gauge:
        """Get or create a Gauge."""
        return self._get_or_create(Gauge, name, help_text, label_names)

    def histogram(
        self,
        name: str,
        help_text: str = "",
        label_names: Sequence[str] = (),
        buckets: Iterable[float] = DEFAULT_DURATION_BUCKETS,
    ) -> Histogram:
        """Get or create a Histogram."""
        return self._get_or_create(Histogram, name, help_text, label_names, buckets=buckets)

    def names(self) -> List[str]:
        """Sorted names of all registered metric families."""
        with self._lock:
            return sorted(self._metrics)

    def clear(self) -> None:
        """Drop all registered metrics."""
        with self._lock:
            self._metrics.clear()

    def render(self) -> str:
        """Render every family in Prometheus text exposition format."""
        with self._lock:
            families = [self._metrics[name] for name in sorted(self._metrics)]
        chunks: List[str] = []
        for metric in families:
            # Families with no samples yet still advertise HELP/TYPE, so
            # dashboards can tell "zero" apart from "metric unknown".
            chunks.append(f"# HELP {metric.name} {_escape_help(metric.help_text)}")
            chunks.append(f"# TYPE {metric.name} {metric.metric_type}")
            chunks.extend(metric.render())
        return "\n".join(chunks) + "\n" if chunks else ""


# ── Default registry + application metrics ─────────────────

_registry = MetricsRegistry()


def get_registry() -> MetricsRegistry:
    """
    Returns:
        The process-wide default registry.
    """
    return _registry


def _declare_app_metrics() -> None:
    """Register the standard metric families up front.

    Declaring eagerly means ``/metrics`` advertises every family from
    the very first scrape, even before any task has run — otherwise a
    fresh server renders "no data" panels in Grafana.
    """
    _registry.gauge(
        BUILD_INFO,
        "Build information; constant 1 with the version as a label.",
        ("version",),
    ).set(1, labels={"version": __version__})
    _registry.counter(
        TASKS_TOTAL,
        "Total tasks by lifecycle status (submitted plus terminal states).",
        ("status",),
    )
    _registry.gauge(QUEUE_DEPTH, "Tasks waiting in the queue (pending).")
    _registry.gauge(ACTIVE_TASKS, "Tasks currently executing (running).")
    _registry.histogram(
        TASK_DURATION,
        "End-to-end task execution duration in seconds.",
        (),
        DEFAULT_DURATION_BUCKETS,
    )
    _registry.histogram(
        RENDER_DURATION,
        "Duration of the render_video pipeline step in seconds.",
        (),
        DEFAULT_DURATION_BUCKETS,
    )
    _registry.counter(
        ERRORS_TOTAL,
        "Total errors by coarse type label.",
        ("type",),
    )
    _registry.counter(
        HTTP_REQUESTS_TOTAL,
        "Total HTTP API requests by method, route template and status code.",
        ("method", "path", "code"),
    )


_declare_app_metrics()


def reset_registry() -> None:
    """Clear the default registry and re-declare the standard families.

    Intended for tests, which need each case to start from a known
    state without leaking counts into the next one.
    """
    _registry.clear()
    _declare_app_metrics()


# ── Instrumentation helpers ────────────────────────────────
#
# Every helper below is best-effort. Telemetry is never worth failing a
# request or killing a worker thread over, so errors are swallowed and
# logged at DEBUG rather than propagated.


def record_task_submitted() -> None:
    """Count a task accepted into the queue."""
    try:
        _registry.counter(TASKS_TOTAL, label_names=("status",)).inc(labels={"status": "submitted"})
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        logger.debug("Failed to record task submission metric", exc_info=True)


def record_task_terminal(status: str) -> None:
    """Count a task reaching a terminal state (completed/failed/cancelled)."""
    try:
        _registry.counter(TASKS_TOTAL, label_names=("status",)).inc(labels={"status": status})
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        logger.debug("Failed to record terminal task metric", exc_info=True)


def set_queue_depth(value: int) -> None:
    """Publish the number of pending tasks."""
    try:
        _registry.gauge(QUEUE_DEPTH).set(value)
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        logger.debug("Failed to set queue depth gauge", exc_info=True)


def set_active_tasks(value: int) -> None:
    """Publish the number of running tasks."""
    try:
        _registry.gauge(ACTIVE_TASKS).set(value)
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        logger.debug("Failed to set active tasks gauge", exc_info=True)


def observe_task_duration(seconds: float) -> None:
    """Record an end-to-end task duration observation."""
    try:
        _registry.histogram(TASK_DURATION).observe(seconds)
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        logger.debug("Failed to observe task duration", exc_info=True)


def observe_render_duration(seconds: float) -> None:
    """Record a ``render_video`` step duration observation."""
    try:
        _registry.histogram(RENDER_DURATION).observe(seconds)
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        logger.debug("Failed to observe render duration", exc_info=True)


def record_error(error_type: str) -> None:
    """Count an error, bucketed by a coarse ``type`` label.

    Callers must pass a bounded set of values (exception class names,
    ``http_500``…) — never a message or an ID, which would blow up
    series cardinality.
    """
    try:
        _registry.counter(ERRORS_TOTAL, label_names=("type",)).inc(
            labels={"type": error_type or "unknown"}
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        logger.debug("Failed to record error metric", exc_info=True)


def record_http_request(method: str, path: str, code: int) -> None:
    """Count an HTTP request.

    Args:
        method: HTTP verb.
        path: **Route template** (``/tasks/{id}``), never a concrete
            path containing an ID — raw paths would make cardinality
            grow without bound.
        code: HTTP status code.
    """
    try:
        _registry.counter(HTTP_REQUESTS_TOTAL, label_names=("method", "path", "code")).inc(
            labels={"method": method, "path": path, "code": str(code)}
        )
    except Exception:  # noqa: BLE001 — telemetry must never break the caller
        logger.debug("Failed to record HTTP request metric", exc_info=True)


def render_prometheus_text(registry: Optional[MetricsRegistry] = None) -> str:
    """Render a registry (default: the process-wide one) as text."""
    return (registry or _registry).render()
