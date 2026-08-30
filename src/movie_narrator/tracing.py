# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Opt-in OpenTelemetry tracing (v1.4.0).

Real span-based tracing for the pipeline and the cloud service:
``task -> step / provider / subprocess``. OpenTelemetry is a **strictly
optional** dependency — when the ``otel`` extra is not installed, or the
``MN_TRACING`` flag is off (the default), every helper in this module is
a zero-overhead no-op and the rest of the code base is unaffected.

Spans follow the execution hierarchy via OpenTelemetry context
propagation: the task span is the parent of the step spans, which in
turn are the parents of the provider/subprocess spans created inside
them. The nesting is automatic — each ``start_*_span`` helper opens a
current span, so anything executed inside the ``with`` block (in the
same thread) becomes its child.

Exporters:

- ``MN_TRACING_EXPORTER=none`` (default) — spans are created through a
  no-exporter SDK provider and dropped on end. Cheap, useful to measure
  the instrumentation cost before shipping traces anywhere.
- ``MN_TRACING_EXPORTER=console`` — the SDK's built-in
  ``ConsoleSpanExporter`` prints every finished span (stdout).
- **OTLP / Jaeger / Zipkin are intentionally NOT bundled** — they would
  drag protobuf/grpcio into the dependency tree. Install the exporter
  package yourself (e.g. ``opentelemetry-exporter-otlp``) and register
  its provider *before* enabling ``MN_TRACING``; this module detects an
  already-registered global tracer provider and uses it unchanged.

Environment variables:
    ``MN_TRACING``           ``1``/``true``/``yes``/``on`` enables tracing
                             (default: off)
    ``MN_TRACING_EXPORTER``  ``none`` (default) | ``console``

Typical usage::

    from movie_narrator.tracing import start_task_span

    with start_task_span(task_id, movie) as span:
        span.set_attribute("mn.task.tenant", tenant)
        ...
"""

from __future__ import annotations

import functools
import importlib
import logging
import os
from types import ModuleType
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

#: Environment flag enabling span creation (default: off).
ENV_TRACING = "MN_TRACING"

#: Exporter selector (``none`` | ``console``).
ENV_TRACING_EXPORTER = "MN_TRACING_EXPORTER"

#: Truthy spellings accepted for ``MN_TRACING``.
_TRUTHY = {"1", "true", "yes", "on"}

#: Tracer instrumentation scope name.
_TRACER_NAME = "movie_narrator"

#: Stable, low-cardinality span names (the variable parts travel as
#: attributes, keeping cardinality bounded on trace backends).
TASK_SPAN_NAME = "mn.task"
STEP_SPAN_NAME = "mn.step"
PROVIDER_SPAN_NAME = "mn.provider"
SUBPROCESS_SPAN_NAME = "mn.subprocess"


# ── No-op span handle ──────────────────────────────────────


class SpanHandle:
    """Context-manager span handle returned by every ``start_*_span``.

    The default implementation is the no-op used when tracing is disabled
    or OpenTelemetry is absent: entering/exiting does nothing, attribute
    and status writes are discarded, and nothing is ever recorded.

    When tracing is enabled the module returns a subclass that forwards
    every call to a real OpenTelemetry span (see :meth:`start_task_span`).
    """

    def set_attribute(self, key: str, value: Any) -> None:
        """Record ``key=value`` on the span (no-op when disabled)."""

    def record_exception(self, exception: BaseException) -> None:
        """Record *exception* on the span (no-op when disabled)."""

    def set_status(self, code: str, description: str = "") -> None:
        """Set the span status (no-op when disabled).

        Args:
            code: ``"ok"``, ``"error"`` or ``"unset"`` (case-insensitive).
            description: Optional human-readable detail (used for errors).
        """

    def __enter__(self) -> "SpanHandle":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        return None


# ── Enabled / exporter configuration ───────────────────────


def is_enabled() -> bool:
    """Whether span creation is currently enabled.

    Reads ``MN_TRACING`` on every call so the flag can be flipped in
    tests and long-lived daemons without a restart.
    """
    return os.environ.get(ENV_TRACING, "").strip().lower() in _TRUTHY


def exporter_name() -> str:
    """The configured exporter: ``"none"`` (default) or ``"console"``.

    Any other value is reported as ``"none"`` (with a one-time warning
    when it is non-empty) so a typo can never crash a worker.
    """
    raw = os.environ.get(ENV_TRACING_EXPORTER, "").strip().lower()
    if not raw or raw == "none":
        return "none"
    if raw == "console":
        return "console"
    logger.warning(
        "Ignoring unknown %s=%r — falling back to 'none' (spans created, not exported)",
        ENV_TRACING_EXPORTER,
        raw,
    )
    return "none"


# ── Guarded OpenTelemetry loading ──────────────────────────


class _OtelModules:
    """The OpenTelemetry modules this integration needs, or ``None``.

    Loaded lazily so that importing :mod:`movie_narrator.tracing` never
    imports OpenTelemetry (the package is optional) and so tests can
    inject fakes via ``sys.modules``.
    """

    __slots__ = ("trace", "sdk_trace", "sdk_export")

    def __init__(
        self,
        trace: ModuleType,
        sdk_trace: ModuleType,
        sdk_export: ModuleType,
    ) -> None:
        self.trace = trace
        self.sdk_trace = sdk_trace
        self.sdk_export = sdk_export


def _load_otel() -> Optional[_OtelModules]:
    """Import the OpenTelemetry API/SDK modules; ``None`` when absent."""
    try:
        trace = importlib.import_module("opentelemetry.trace")
        sdk_trace = importlib.import_module("opentelemetry.sdk.trace")
        sdk_export = importlib.import_module("opentelemetry.sdk.trace.export")
    except Exception:  # noqa: BLE001 — optional dependency, absence is normal
        return None
    return _OtelModules(trace, sdk_trace, sdk_export)


#: Cache of the tracer, keyed by the identity of the loaded
#: ``opentelemetry.trace`` module. When tests swap fakes into
#: ``sys.modules`` the module object changes and the cache rebuilds;
#: in normal operation the real module persists and the tracer is
#: resolved once.
_TRACER_CACHE: dict[int, Any] = {}


def _provider_is_unset(trace_mod: ModuleType) -> bool:
    """Whether no global tracer provider has been registered yet.

    The OpenTelemetry API answers with a ``ProxyTracerProvider`` until
    application code (or this module) registers a real one. Matching on
    the class name keeps this working across API versions and with test
    doubles.
    """
    try:
        provider = trace_mod.get_tracer_provider()
    except Exception:  # noqa: BLE001 — a broken provider must not break the run
        return False
    return type(provider).__name__ == "ProxyTracerProvider"


def _setup_provider(otel: _OtelModules) -> None:
    """Register an SDK tracer provider when none is registered yet.

    ``console`` attaches a ``BatchSpanProcessor(ConsoleSpanExporter())``;
    ``none`` registers a bare provider so spans are created (recording)
    but dropped at end time — the documented cheap mode. An
    already-registered global provider (e.g. a user-supplied OTLP
    pipeline) is left untouched.
    """
    try:
        if exporter_name() == "console":
            processor = otel.sdk_export.BatchSpanProcessor(
                otel.sdk_export.ConsoleSpanExporter()
            )
            provider = otel.sdk_trace.TracerProvider()
            provider.add_span_processor(processor)
        else:
            provider = otel.sdk_trace.TracerProvider()
        otel.trace.set_tracer_provider(provider)
    except Exception:  # noqa: BLE001 — telemetry setup must never break a run
        logger.debug("OpenTelemetry provider setup failed", exc_info=True)


def _get_tracer(otel: _OtelModules) -> Optional[Any]:
    """Return the cached tracer for the loaded API module (or None)."""
    key = id(otel.trace)
    cached = _TRACER_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        if _provider_is_unset(otel.trace):
            _setup_provider(otel)
        tracer = otel.trace.get_tracer(_TRACER_NAME)
    except Exception:  # noqa: BLE001 — telemetry must never break a run
        logger.debug("OpenTelemetry tracer resolution failed", exc_info=True)
        return None
    _TRACER_CACHE[key] = tracer
    return tracer


def _reset_for_tests() -> None:
    """Clear the tracer cache (test isolation helper)."""
    _TRACER_CACHE.clear()


# ── Real span handle ───────────────────────────────────────

_STATUS_CODES: dict[str, str] = {"ok": "OK", "error": "ERROR", "unset": "UNSET"}


class _OtelSpanHandle(SpanHandle):
    """Span handle forwarding to a real OpenTelemetry current span.

    The underlying span is created with ``start_as_current_span``, so
    exiting the ``with`` block ends the span — and, per the OpenTelemetry
    SDK contract, records the active exception and flips the status to
    ``ERROR`` when the body raised.
    """

    def __init__(self, cm: Any, trace_mod: ModuleType) -> None:
        self._cm = cm
        self._trace = trace_mod
        self._span: Any = None

    def __enter__(self) -> "_OtelSpanHandle":
        self._span = self._cm.__enter__()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            self._cm.__exit__(exc_type, exc, tb)
        finally:
            self._span = None

    def set_attribute(self, key: str, value: Any) -> None:
        if self._span is not None:
            try:
                self._span.set_attribute(key, value)
            except Exception:  # noqa: BLE001 — telemetry must never break a run
                logger.debug("span.set_attribute(%r) failed", key, exc_info=True)

    def record_exception(self, exception: BaseException) -> None:
        if self._span is not None:
            try:
                self._span.record_exception(exception)
            except Exception:  # noqa: BLE001 — telemetry must never break a run
                logger.debug("span.record_exception failed", exc_info=True)

    def set_status(self, code: str, description: str = "") -> None:
        if self._span is None:
            return
        canonical = _STATUS_CODES.get(code.strip().lower())
        if canonical is None:
            logger.debug("Ignoring unknown span status code %r", code)
            return
        try:
            status_code = getattr(self._trace.StatusCode, canonical)
            self._span.set_status(self._trace.Status(status_code, description or None))
        except Exception:  # noqa: BLE001 — telemetry must never break a run
            logger.debug("span.set_status(%r) failed", code, exc_info=True)


# ── Span factories (public API) ────────────────────────────


def _open_span(name: str, attributes: "dict[str, Any]") -> SpanHandle:
    """Shared span-open path: real span when enabled, no-op otherwise."""
    if not is_enabled():
        return SpanHandle()
    otel = _load_otel()
    if otel is None:
        return SpanHandle()
    tracer = _get_tracer(otel)
    if tracer is None:
        return SpanHandle()
    try:
        cm = tracer.start_as_current_span(name, attributes=attributes)
    except TypeError:
        # Older OpenTelemetry APIs lack the ``attributes`` kwarg.
        cm = tracer.start_as_current_span(name)
    except Exception:  # noqa: BLE001 — telemetry must never break a run
        logger.debug("start_as_current_span(%r) failed", name, exc_info=True)
        return SpanHandle()
    return _OtelSpanHandle(cm, otel.trace)


def start_task_span(task_id: str, movie: str) -> SpanHandle:
    """Open the span for one task execution.

    The parent of every step/provider/subprocess span created while the
    ``with`` block is active (in the same thread).

    Args:
        task_id: Queue task identifier.
        movie: Movie name the task renders.

    Returns:
        A context-manager span handle (a no-op when tracing is disabled).
    """
    return _open_span(
        TASK_SPAN_NAME,
        {
            "mn.task.id": task_id,
            "mn.task.movie": movie,
        },
    )


def start_step_span(step: str, attempt: int) -> SpanHandle:
    """Open the span for one pipeline step execution.

    Args:
        step: Step name (e.g. ``"generate_voice"``).
        attempt: 1-based attempt number (0 for skipped steps).

    Returns:
        A context-manager span handle (a no-op when tracing is disabled).
    """
    return _open_span(
        STEP_SPAN_NAME,
        {
            "mn.step.name": step,
            "mn.step.attempt": attempt,
        },
    )


def start_provider_span(
    provider: str,
    kind: str,
    *,
    model: Optional[str] = None,
    cache_hit: Optional[bool] = None,
) -> SpanHandle:
    """Open the span for one external provider call (LLM, TTS, ...).

    Args:
        provider: Provider name (e.g. ``"openai"``, ``"edge"``).
        kind: Call category (``"llm"``, ``"tts"``).
        model: Model identifier, when known.
        cache_hit: Whether the call was served from a cache, when known.

    Returns:
        A context-manager span handle (a no-op when tracing is disabled).
    """
    attributes: "dict[str, Any]" = {
        "mn.provider.name": provider,
        "mn.provider.kind": kind,
    }
    if model is not None:
        attributes["mn.provider.model"] = model
    if cache_hit is not None:
        attributes["mn.provider.cache_hit"] = bool(cache_hit)
    return _open_span(PROVIDER_SPAN_NAME, attributes)


def start_subprocess_span(cmd_head: str, timeout: float) -> SpanHandle:
    """Open the span for one subprocess execution (ffmpeg et al.).

    Args:
        cmd_head: Short, path-free description of the command line
            (e.g. ``"ffmpeg -i"``) — never the full argv, which embeds
            user paths.
        timeout: Wall-clock deadline in seconds the child was given.

    Returns:
        A context-manager span handle (a no-op when tracing is disabled).
    """
    return _open_span(
        SUBPROCESS_SPAN_NAME,
        {
            "mn.subprocess.cmd": cmd_head,
            "mn.subprocess.timeout": float(timeout),
        },
    )


def wrap_provider_call(
    fn: Callable[..., Any],
    provider: str,
    kind: str,
    *,
    model: Optional[str] = None,
) -> Callable[..., Any]:
    """Wrap a synchronous callable so every invocation runs in a span.

    One call of the wrapped function = one provider span covering all
    internal retry attempts. Used to keep provider hooks in the call
    sites (``utils/llm.py``) down to a single line.
    """

    @functools.wraps(fn)
    def _inner(*args: Any, **kwargs: Any) -> Any:
        with start_provider_span(provider, kind, model=model):
            return fn(*args, **kwargs)

    return _inner


__all__ = [
    "ENV_TRACING",
    "ENV_TRACING_EXPORTER",
    "PROVIDER_SPAN_NAME",
    "STEP_SPAN_NAME",
    "SUBPROCESS_SPAN_NAME",
    "TASK_SPAN_NAME",
    "SpanHandle",
    "exporter_name",
    "is_enabled",
    "start_provider_span",
    "start_step_span",
    "start_subprocess_span",
    "start_task_span",
    "wrap_provider_call",
]
