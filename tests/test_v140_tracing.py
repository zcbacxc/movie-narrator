# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for v1.4.0 Feature 1: opt-in OpenTelemetry tracing.

Every OpenTelemetry behaviour is exercised through fake modules injected
into ``sys.modules`` (monkeypatch) — **no test requires the real
``opentelemetry`` packages** (CI does not install the ``[otel]`` extra).

Covers:
- no-op mode: flag off, or flag on with OpenTelemetry absent — helpers
  never raise and return the base no-op handle
- env gating: ``MN_TRACING`` truthy spellings, ``MN_TRACING_EXPORTER``
- real span path (fakes): attributes, parent/child nesting, exception
  recording, status setting, console-exporter provider setup, respect
  for a pre-registered global provider, tracer caching
- wire-ups: runner step span, worker task span, LLM provider span,
  TTS segment span, ffmpeg subprocess span
"""

from __future__ import annotations

import sys
import types
from contextlib import contextmanager
from pathlib import Path
from typing import Any, List, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from movie_narrator import tracing
from movie_narrator.tracing import (
    ENV_TRACING,
    ENV_TRACING_EXPORTER,
    SpanHandle,
    exporter_name,
    is_enabled,
    start_provider_span,
    start_step_span,
    start_subprocess_span,
    start_task_span,
    wrap_provider_call,
)


# ── Fake OpenTelemetry modules ─────────────────────────────


class FakeSpan:
    """Record-keeping stand-in for an SDK span."""

    def __init__(self, name: str, attributes: Optional[dict], parent: Optional["FakeSpan"]):
        self.name = name
        self.attributes = dict(attributes or {})
        self.parent = parent
        self.exceptions: List[BaseException] = []
        self.status: Optional[Any] = None
        self.ended = False

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def record_exception(self, exception: BaseException) -> None:
        self.exceptions.append(exception)

    def set_status(self, status: Any) -> None:
        self.status = status


class FakeTracerProvider:
    """Stand-in for ``opentelemetry.sdk.trace.TracerProvider``."""

    def __init__(self) -> None:
        self.processors: List[Any] = []

    def add_span_processor(self, processor: Any) -> None:
        self.processors.append(processor)


class FakeBatchSpanProcessor:
    """Stand-in for the SDK batch processor (records its exporter)."""

    def __init__(self, exporter: Any) -> None:
        self.exporter = exporter


class FakeConsoleSpanExporter:
    """Stand-in for the SDK console exporter."""


class _FakeOtel:
    """Hub wiring fake ``opentelemetry`` modules into ``sys.modules``."""

    def __init__(self) -> None:
        self.registered: List[Any] = []  # providers given to set_tracer_provider
        self.tracer_requests: List[str] = []
        self.spans: List[FakeSpan] = []  # creation order
        self.current: List[FakeSpan] = []  # active span stack
        self.global_provider: Optional[Any] = None  # pre-registered user provider

    # -- fake trace module ------------------------------------------------

    def _build_trace_module(self) -> types.ModuleType:
        otel = self
        mod = types.ModuleType("opentelemetry.trace")

        # The class NAME is load-bearing: tracing.py treats a provider of
        # this type as "nothing registered yet".
        class ProxyTracerProvider:  # noqa: D419 — mirrors the real API class
            pass

        class StatusCode:
            OK = "OK"
            ERROR = "ERROR"
            UNSET = "UNSET"

        class Status:
            def __init__(self, status_code: Any, description: Any = None) -> None:
                self.status_code = status_code
                self.description = description

        @contextmanager
        def _current_span(name: str, attributes: Any = None):
            parent = otel.current[-1] if otel.current else None
            span = FakeSpan(name, attributes, parent)
            otel.spans.append(span)
            otel.current.append(span)
            try:
                yield span
            except BaseException as exc:  # noqa: BLE001 — mirrors the SDK contract
                span.exceptions.append(exc)
                span.status = Status(StatusCode.ERROR, str(exc))
                raise
            finally:
                otel.current.pop()
                span.ended = True

        class FakeTracer:
            def start_as_current_span(self, name: str, attributes: Any = None, **kwargs: Any):
                return _current_span(name, attributes)

        def get_tracer_provider() -> Any:
            if otel.global_provider is not None:
                return otel.global_provider
            return ProxyTracerProvider()

        def set_tracer_provider(provider: Any) -> None:
            otel.registered.append(provider)

        def get_tracer(name: str = "", *args: Any, **kwargs: Any) -> FakeTracer:
            otel.tracer_requests.append(name)
            return FakeTracer()

        mod.ProxyTracerProvider = ProxyTracerProvider
        mod.StatusCode = StatusCode
        mod.Status = Status
        mod.get_tracer_provider = get_tracer_provider
        mod.set_tracer_provider = set_tracer_provider
        mod.get_tracer = get_tracer
        return mod

    # -- installation -----------------------------------------------------

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pkg = types.ModuleType("opentelemetry")
        pkg.__path__ = []  # type: ignore[attr-defined]
        sdk_pkg = types.ModuleType("opentelemetry.sdk")
        sdk_pkg.__path__ = []  # type: ignore[attr-defined]
        sdk_trace = types.ModuleType("opentelemetry.sdk.trace")
        sdk_trace.TracerProvider = FakeTracerProvider  # type: ignore[attr-defined]
        export_mod = types.ModuleType("opentelemetry.sdk.trace.export")
        export_mod.BatchSpanProcessor = FakeBatchSpanProcessor  # type: ignore[attr-defined]
        export_mod.ConsoleSpanExporter = FakeConsoleSpanExporter  # type: ignore[attr-defined]

        monkeypatch.setitem(sys.modules, "opentelemetry", pkg)
        monkeypatch.setitem(sys.modules, "opentelemetry.trace", self._build_trace_module())
        monkeypatch.setitem(sys.modules, "opentelemetry.sdk", sdk_pkg)
        monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace", sdk_trace)
        monkeypatch.setitem(sys.modules, "opentelemetry.sdk.trace.export", export_mod)
        self.trace_mod = sys.modules["opentelemetry.trace"]


@pytest.fixture
def fake_otel(monkeypatch: pytest.MonkeyPatch) -> _FakeOtel:
    """Enable tracing against fake OpenTelemetry modules (exporter=none)."""
    otel = _FakeOtel()
    otel.install(monkeypatch)
    monkeypatch.setenv(ENV_TRACING, "1")
    monkeypatch.setenv(ENV_TRACING_EXPORTER, "none")
    tracing._reset_for_tests()
    yield otel
    tracing._reset_for_tests()


# ════════════════════════════════════════════════════════════
#  No-op mode & env gating
# ════════════════════════════════════════════════════════════


class TestNoOpMode:
    def test_disabled_by_default(self, monkeypatch):
        monkeypatch.delenv(ENV_TRACING, raising=False)
        assert is_enabled() is False

    @pytest.mark.parametrize("value", ["1", "true", "True", "YES", "on", " yes "])
    def test_truthy_spellings(self, monkeypatch, value):
        monkeypatch.setenv(ENV_TRACING, value)
        assert is_enabled() is True

    @pytest.mark.parametrize("value", ["", "0", "false", "off", "no", "junk"])
    def test_falsy_spellings(self, monkeypatch, value):
        monkeypatch.setenv(ENV_TRACING, value)
        assert is_enabled() is False

    def test_noop_handles_never_raise(self, monkeypatch):
        """With tracing off every helper returns the base no-op handle."""
        monkeypatch.delenv(ENV_TRACING, raising=False)
        for open_span in (
            lambda: start_task_span("t1", "movie"),
            lambda: start_step_span("render_video", 1),
            lambda: start_provider_span("openai", "llm", model="m"),
            lambda: start_subprocess_span("ffmpeg -i", 30.0),
        ):
            handle = open_span()
            assert type(handle) is SpanHandle
            with handle as entered:
                assert entered is handle
                entered.set_attribute("k", "v")
                entered.record_exception(ValueError("x"))
                entered.set_status("error", "boom")
                entered.set_status("bogus")

    def test_enabled_without_otel_is_noop(self, monkeypatch):
        """Flag on but OpenTelemetry absent → graceful no-op, no raise."""
        monkeypatch.setenv(ENV_TRACING, "1")
        # None in sys.modules makes importlib raise ImportError.
        monkeypatch.setitem(sys.modules, "opentelemetry.trace", None)
        tracing._reset_for_tests()
        try:
            with start_task_span("t1", "movie") as span:
                assert type(span) is SpanHandle
                span.set_attribute("k", "v")
        finally:
            tracing._reset_for_tests()

    def test_zero_overhead_path_reads_env_only(self, monkeypatch):
        """The disabled path must not import anything: no sys.modules churn."""
        monkeypatch.delenv(ENV_TRACING, raising=False)
        before = set(sys.modules)
        start_task_span("t1", "movie")
        assert set(sys.modules) == before


class TestExporterEnv:
    def test_default_is_none(self, monkeypatch):
        monkeypatch.delenv(ENV_TRACING_EXPORTER, raising=False)
        assert exporter_name() == "none"

    def test_console(self, monkeypatch):
        monkeypatch.setenv(ENV_TRACING_EXPORTER, "console")
        assert exporter_name() == "console"

    def test_unknown_falls_back_to_none(self, monkeypatch, caplog):
        monkeypatch.setenv(ENV_TRACING_EXPORTER, "otlp-typo")
        with caplog.at_level("WARNING"):
            assert exporter_name() == "none"
        assert any("MN_TRACING_EXPORTER" in r.message for r in caplog.records)


# ════════════════════════════════════════════════════════════
#  Real span path (fake OpenTelemetry modules)
# ════════════════════════════════════════════════════════════


class TestRealSpans:
    def test_task_span_attributes(self, fake_otel):
        with start_task_span("task-1", "Dark Knight") as handle:
            handle.set_attribute("mn.task.tenant", "acme")
        span = fake_otel.spans[0]
        assert span.name == tracing.TASK_SPAN_NAME
        assert span.attributes["mn.task.id"] == "task-1"
        assert span.attributes["mn.task.movie"] == "Dark Knight"
        assert span.attributes["mn.task.tenant"] == "acme"
        assert span.ended is True

    def test_provider_and_subprocess_span_attributes(self, fake_otel):
        with start_provider_span("openai", "llm", model="gpt-x", cache_hit=False):
            pass
        with start_provider_span("edge", "tts") as handle:
            handle.set_attribute("mn.provider.cache_hit", True)
        with start_subprocess_span("ffmpeg -i", 12.5):
            pass
        p_llm, p_tts, sub = fake_otel.spans
        assert p_llm.attributes["mn.provider.name"] == "openai"
        assert p_llm.attributes["mn.provider.kind"] == "llm"
        assert p_llm.attributes["mn.provider.model"] == "gpt-x"
        assert p_llm.attributes["mn.provider.cache_hit"] is False
        # cache_hit omitted at open, then stamped via set_attribute
        assert p_tts.attributes["mn.provider.cache_hit"] is True
        assert sub.attributes["mn.subprocess.cmd"] == "ffmpeg -i"
        assert sub.attributes["mn.subprocess.timeout"] == 12.5

    def test_cache_hit_omitted_when_unknown(self, fake_otel):
        with start_provider_span("edge", "tts"):
            pass
        assert "mn.provider.cache_hit" not in fake_otel.spans[0].attributes

    def test_parent_child_nesting(self, fake_otel):
        with start_task_span("task-1", "M"):
            with start_step_span("generate_voice", 1):
                with start_provider_span("edge", "tts"):
                    pass
        task, step, provider = fake_otel.spans
        assert step.parent is task
        assert provider.parent is step
        assert task.parent is None

    def test_exception_recorded_and_status_error(self, fake_otel):
        with pytest.raises(RuntimeError):
            with start_step_span("render_video", 2) as handle:
                handle.set_attribute("mn.step.name", "render_video")
                raise RuntimeError("boom")
        span = fake_otel.spans[0]
        assert isinstance(span.exceptions[0], RuntimeError)
        assert span.status.status_code == "ERROR"
        # attributes set before the raise survive
        assert span.attributes["mn.step.name"] == "render_video"

    def test_record_exception_explicit_and_set_status(self, fake_otel):
        err = ValueError("late failure")
        with start_task_span("t", "M") as handle:
            handle.record_exception(err)
            handle.set_status("error", "task failed")
        span = fake_otel.spans[0]
        assert span.exceptions == [err]
        assert span.status.status_code == "ERROR"
        assert span.status.description == "task failed"

    def test_set_status_ok_and_case_insensitive(self, fake_otel):
        with start_task_span("t", "M") as handle:
            handle.set_status("OK")
        assert fake_otel.spans[0].status.status_code == "OK"

    def test_unknown_status_code_is_ignored(self, fake_otel):
        with start_task_span("t", "M") as handle:
            handle.set_status("catastrophic")
        assert fake_otel.spans[0].status is None

    def test_console_exporter_provider_setup(self, fake_otel, monkeypatch):
        monkeypatch.setenv(ENV_TRACING_EXPORTER, "console")
        tracing._reset_for_tests()
        with start_task_span("t", "M"):
            pass
        assert len(fake_otel.registered) == 1
        provider = fake_otel.registered[0]
        assert isinstance(provider, FakeTracerProvider)
        assert len(provider.processors) == 1
        assert isinstance(provider.processors[0].exporter, FakeConsoleSpanExporter)

    def test_none_exporter_provider_has_no_processors(self, fake_otel):
        with start_task_span("t", "M"):
            pass
        assert len(fake_otel.registered) == 1
        assert fake_otel.registered[0].processors == []

    def test_pre_registered_global_provider_is_respected(self, fake_otel):
        """A user-registered provider (e.g. OTLP) is never overridden."""
        fake_otel.global_provider = object()  # type name != ProxyTracerProvider
        with start_task_span("t", "M"):
            pass
        assert fake_otel.registered == []  # set_tracer_provider never called
        assert fake_otel.spans[0].attributes["mn.task.id"] == "t"

    def test_tracer_resolved_once_per_api_module(self, fake_otel):
        for i in range(3):
            with start_task_span(f"t{i}", "M"):
                pass
        assert fake_otel.tracer_requests == ["movie_narrator"]

    def test_wrap_provider_call(self, fake_otel):
        calls: List[str] = []

        def _call(x: int) -> int:
            calls.append("call")
            return x + 1

        wrapped = wrap_provider_call(_call, "openai", "llm", model="m1")
        assert wrapped(41) == 42
        assert calls == ["call"]
        span = fake_otel.spans[0]
        assert span.attributes["mn.provider.name"] == "openai"
        assert span.attributes["mn.provider.kind"] == "llm"
        assert span.attributes["mn.provider.model"] == "m1"

    def test_wrap_provider_call_records_exception(self, fake_otel):
        def _boom() -> None:
            raise ValueError("nope")

        wrapped = wrap_provider_call(_boom, "openai", "llm")
        with pytest.raises(ValueError):
            wrapped()
        assert isinstance(fake_otel.spans[0].exceptions[0], ValueError)


# ════════════════════════════════════════════════════════════
#  Wire-up: pipeline runner step span
# ════════════════════════════════════════════════════════════


def _fake_step(ctx):
    return ctx


def _build_ctx(tmp_path: Path):
    from movie_narrator.pipeline.runner import build_context

    return build_context(
        movie="M",
        style="S",
        duration=10,
        voice=None,
        video_format="16:9",
        output_dir=tmp_path,
    )


class TestRunnerStepSpan:
    def test_step_span_wraps_execution(self, fake_otel, monkeypatch, tmp_path):
        from movie_narrator.pipeline import runner

        _fake_step.__name__ = "probe_step"
        monkeypatch.setattr(runner, "STEPS", [_fake_step])
        monkeypatch.setattr(runner, "run_preflight", lambda ctx: None)
        ctx = _build_ctx(tmp_path)

        runner.run_pipeline(ctx)

        assert len(fake_otel.spans) == 1
        span = fake_otel.spans[0]
        assert span.attributes["mn.step.name"] == "probe_step"
        assert span.attributes["mn.step.attempt"] == 1
        assert span.attributes["mn.step.result"] == "success"
        assert "mn.step.duration_s" in span.attributes
        assert "mn.step.error_class" not in span.attributes

    def test_step_span_records_failure(self, fake_otel, monkeypatch, tmp_path):
        from movie_narrator.pipeline import runner

        def _fail(ctx):
            raise ValueError("step exploded")

        _fail.__name__ = "probe_fail"
        monkeypatch.setattr(runner, "STEPS", [_fail])
        monkeypatch.setattr(runner, "run_preflight", lambda ctx: None)
        ctx = _build_ctx(tmp_path)

        with pytest.raises(ValueError):
            runner.run_pipeline(ctx)

        span = fake_otel.spans[0]
        assert span.attributes["mn.step.attempt"] == 1
        assert span.attributes["mn.step.error_class"] == "ValueError"
        assert isinstance(span.exceptions[0], ValueError)
        assert span.status.status_code == "ERROR"

    def test_step_span_disabled_by_default(self, monkeypatch, tmp_path):
        """With MN_TRACING off the runner wire-up is a silent no-op."""
        from movie_narrator.pipeline import runner

        monkeypatch.delenv(ENV_TRACING, raising=False)
        _fake_step.__name__ = "probe_step"
        monkeypatch.setattr(runner, "STEPS", [_fake_step])
        monkeypatch.setattr(runner, "run_preflight", lambda ctx: None)
        ctx = _build_ctx(tmp_path)
        assert runner.run_pipeline(ctx) is ctx


# ════════════════════════════════════════════════════════════
#  Wire-up: worker task span
# ════════════════════════════════════════════════════════════


class TestWorkerTaskSpan:
    def test_task_span_attributes_and_nesting(self, fake_otel, monkeypatch):
        from movie_narrator.cloud import worker
        from movie_narrator.cloud.models import Task, TaskRequest

        monkeypatch.setattr(
            worker,
            "_run_task_with_retry",
            lambda task, controller, **kwargs: task,
        )
        task = Task(
            request=TaskRequest(movie_name="Hooked"),
            tenant_id="acme",
            plan="pro",
        )
        out = worker.run_task(task, worker.CancelController())

        assert out is task
        span = fake_otel.spans[0]
        assert span.name == tracing.TASK_SPAN_NAME
        assert span.attributes["mn.task.id"] == task.id
        assert span.attributes["mn.task.movie"] == "Hooked"
        assert span.attributes["mn.task.tenant"] == "acme"
        assert span.attributes["mn.task.plan"] == "pro"

    def test_task_span_disabled_by_default(self, monkeypatch):
        from movie_narrator.cloud import worker
        from movie_narrator.cloud.models import Task, TaskRequest

        monkeypatch.delenv(ENV_TRACING, raising=False)
        monkeypatch.setattr(
            worker,
            "_run_task_with_retry",
            lambda task, controller, **kwargs: task,
        )
        task = Task(request=TaskRequest(movie_name="Hooked"))
        assert worker.run_task(task, worker.CancelController()) is task


# ════════════════════════════════════════════════════════════
#  Wire-up: LLM provider span
# ════════════════════════════════════════════════════════════


class _FakeCompletions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **kwargs: Any) -> str:
        self.calls += 1
        return "ok"


class _FakeOpenAIClient:
    def __init__(self) -> None:
        self._completions = _FakeCompletions()
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._completions.create)
        )


class TestLLMProviderSpan:
    def test_create_runs_in_provider_span(self, fake_otel, monkeypatch):
        import movie_narrator.utils.llm as llm_module
        from movie_narrator.utils.llm import _wrap_llm_retry

        monkeypatch.setattr(
            llm_module,
            "get_settings",
            lambda: types.SimpleNamespace(llm_model="test-model"),
        )
        client = _FakeOpenAIClient()
        client.chat.completions.create = _wrap_llm_retry(client).chat.completions.create

        assert client.chat.completions.create() == "ok"

        span = fake_otel.spans[0]
        assert span.attributes["mn.provider.name"] == "openai"
        assert span.attributes["mn.provider.kind"] == "llm"
        assert span.attributes["mn.provider.model"] == "test-model"
        # the wrapped create still reaches the original callable
        assert client._completions.calls == 1

    def test_create_span_disabled_by_default(self, monkeypatch):
        from movie_narrator.utils.llm import _wrap_llm_retry

        monkeypatch.delenv(ENV_TRACING, raising=False)
        client = _FakeOpenAIClient()
        client.chat.completions.create = _wrap_llm_retry(client).chat.completions.create
        assert client.chat.completions.create() == "ok"
        assert client._completions.calls == 1


# ════════════════════════════════════════════════════════════
#  Wire-up: TTS segment span
# ════════════════════════════════════════════════════════════


class TestTTSSegmentSpan:
    @pytest.fixture
    def tts_env(self, monkeypatch, tmp_path):
        """Patch tts.py externals (same pattern as test_tts_step_coverage)."""
        from movie_narrator.config import Settings
        from movie_narrator.models import Context, ScriptSegment
        from movie_narrator.pipeline import tts as tts_module

        class FakeAudio:
            def __init__(self, ms: float) -> None:
                self.ms = ms

            def __add__(self, other: "FakeAudio") -> "FakeAudio":
                return FakeAudio(self.ms + getattr(other, "ms", 0))

            def __len__(self) -> float:
                return self.ms

            def export(self, *args: Any, **kwargs: Any) -> None:
                Path(kwargs.get("fp", args[0] if args else "out.mp3")).touch()

            def set_frame_rate(self, rate: int) -> "FakeAudio":
                return self

        audio = MagicMock()
        audio.empty.return_value = FakeAudio(0)
        audio.silent.side_effect = lambda duration, *a, **k: FakeAudio(duration)
        audio.from_mp3.return_value = FakeAudio(1000)
        monkeypatch.setattr(tts_module, "AudioSegment", audio)

        settings = Settings(_env_file=None)
        monkeypatch.setattr(tts_module, "get_settings", lambda: settings)
        monkeypatch.setattr(tts_module, "is_ci", lambda: False)
        provider = MagicMock()
        provider.synthesize = AsyncMock(
            side_effect=lambda text, voice, path: Path(path).parent.mkdir(
                parents=True, exist_ok=True
            )
            or Path(path).write_bytes(b"mp3")
        )
        monkeypatch.setattr(tts_module, "get_tts_provider", lambda s: provider)
        monkeypatch.setattr(
            tts_module, "analyze_segment", lambda a, i: MagicMock(issues=[])
        )
        monkeypatch.setattr(
            tts_module, "aggregate_metrics", lambda ms: {"segment_count": len(ms)}
        )

        ctx = Context(movie_name="T", output_dir=str(tmp_path), duration=60)
        ctx.segments = [ScriptSegment(text="hello", index=0)]

        return tts_module, ctx, settings

    def test_segment_span_records_provider_and_cache_hit(
        self, fake_otel, tts_env, monkeypatch
    ):
        tts_module, ctx, settings = tts_env
        monkeypatch.setattr(tts_module, "get_cache_stats", lambda: {})

        tts_module.generate_voice(ctx)

        assert len(fake_otel.spans) == 1
        span = fake_otel.spans[0]
        assert span.attributes["mn.provider.kind"] == "tts"
        assert span.attributes["mn.provider.name"] == settings.tts_provider.value
        assert span.attributes["mn.provider.cache_hit"] is False
        assert "mn.provider.model" in span.attributes


# ════════════════════════════════════════════════════════════
#  Wire-up: ffmpeg subprocess span
# ════════════════════════════════════════════════════════════


class TestSubprocessSpan:
    def test_run_ffmpeg_subprocess_opens_span(self, fake_otel, monkeypatch):
        from movie_narrator.utils import process as process_mod

        fake_proc = MagicMock()
        fake_proc.communicate.return_value = ("out", "err")
        fake_proc.returncode = 0
        fake_proc.pid = 42
        monkeypatch.setattr(process_mod.subprocess, "Popen", MagicMock(return_value=fake_proc))

        result = process_mod.run_ffmpeg_subprocess(["ffmpeg", "-i", "a.wav"], timeout=30)

        assert result.returncode == 0
        span = fake_otel.spans[0]
        assert span.name == tracing.SUBPROCESS_SPAN_NAME
        assert span.attributes["mn.subprocess.cmd"] == "ffmpeg -i"
        assert span.attributes["mn.subprocess.timeout"] == 30.0

    def test_subprocess_span_disabled_by_default(self, monkeypatch):
        """No-op path: the lazy tracing import must not raise without otel."""
        from movie_narrator.utils import process as process_mod

        monkeypatch.delenv(ENV_TRACING, raising=False)
        fake_proc = MagicMock()
        fake_proc.communicate.return_value = ("out", "err")
        fake_proc.returncode = 0
        fake_proc.pid = 7
        monkeypatch.setattr(process_mod.subprocess, "Popen", MagicMock(return_value=fake_proc))

        result = process_mod.run_ffmpeg_subprocess(["/opt/ffmpeg", "-i", "a.wav"], timeout=5)
        assert result.returncode == 0
