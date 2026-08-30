[![English](https://img.shields.io/badge/English-Observability-blue)](OBSERVABILITY.md)
[![简体中文](https://img.shields.io/badge/简体中文-可观测性-green)](OBSERVABILITY.zh-CN.md)

# Observability (v0.8.1)

> Structured logging and Prometheus metrics for the `mn serve` API server.
> No new third-party dependencies — the JSON formatter and the metrics
> renderer are implemented with the Python standard library only.
>
> For component boundaries, see [ARCHITECTURE.md](ARCHITECTURE.md); for
> deployment setup, see [DEPLOYMENT.md](DEPLOYMENT.md).

## 1. Structured logging

### 1.1 JSON mode

By default the server emits human-readable text lines. Enable JSON to
ship logs to ELK / Loki / CloudWatch without a grok pattern:

```bash
mn serve --log-format json
# or
export MN_LOG_FORMAT=json
mn serve
```

Each line is a single JSON object with a stable key set:

| Key              | Present            | Meaning                                   |
|------------------|--------------------|-------------------------------------------|
| `ts`             | always             | ISO-8601 UTC, millisecond precision (`Z`) |
| `level`          | always             | level name (`INFO`, `ERROR`, …)           |
| `logger`         | always             | logger name                               |
| `msg`            | always             | interpolated message                      |
| `correlation_id` | when bound         | ties records of one unit of work together |
| `exc_type`       | on exception       | exception class name                      |
| `exc_message`    | on exception       | `str(exc)`                                |
| `traceback`      | on exception       | formatted traceback                       |
| `<extra>`        | when provided      | any field passed via `extra=`             |

`correlation_id` is **omitted** (not `null`) when no ID is bound, so
un-correlated lines stay compact.

### 1.2 Correlation IDs

A `contextvars.ContextVar` carries a 12-hex-char correlation ID across
threads. The API server adopts an inbound ID from the `X-Request-ID` or
`X-Correlation-ID` request header (so an upstream trace continues here),
otherwise it generates a fresh one. Every response echoes the active ID
in the `X-Correlation-ID` header. The ID also propagates from a
submitted task into its worker thread, so the request log, the
`/tasks/{id}` log, and the worker log all share one ID.

In application code:

```python
from movie_narrator.utils.logging_config import correlation_scope, get_correlation_id

with correlation_scope() as cid:
    logger.info("handling request")  # record carries cid
```

### 1.3 Log level

```bash
mn serve --log-level DEBUG
# or
export MN_LOG_LEVEL=DEBUG
```

Default: `INFO`. Server logs default to INFO even though pipeline runs
default lower, to avoid flooding long-running services.

## 2. Prometheus metrics

### 2.1 Endpoint

`GET /metrics` serves the text exposition format (version `0.0.4`).

The endpoint is **authenticated by default** — the payload leaks task
volumes and error rates. Send the same `X-API-Key` as every other route.
To let in-cluster scrapers without a secret, opt out:

```bash
export MN_METRICS_PUBLIC=1   # 1/true/yes/on
```

### 2.2 Metric families

| Name                            | Type       | Labels                          | Description                          |
|---------------------------------|------------|---------------------------------|--------------------------------------|
| `mn_build_info`                 | gauge      | `version`                       | constant `1` with the version label  |
| `mn_tasks_total`                | counter    | `status`                        | tasks by lifecycle status            |
| `mn_queue_depth`                | gauge      | —                               | pending tasks                        |
| `mn_active_tasks`               | gauge      | —                               | currently executing tasks            |
| `mn_task_duration_seconds`      | histogram  | —                               | end-to-end task duration             |
| `mn_render_duration_seconds`     | histogram  | —                               | `render_video` step duration         |
| `mn_errors_total`               | counter    | `type`                          | errors by coarse type (`http_401`, …)|
| `mn_http_requests_total`        | counter    | `method`, `path`, `code`        | HTTP requests (path is a template)   |

Histograms use buckets (seconds):
`0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800`.

The `mn_http_requests_total` `path` label is always a **route template**
(e.g. `/tasks/{id}`), never a concrete path containing an ID, so
cardinality stays bounded. Unrecognised paths collapse to `/other`.

### 2.3 Sample scrape config

```yaml
scrape_configs:
  - job_name: movie-narrator
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /run/secrets/mn-api-key   # when MN_METRICS_PUBLIC != 1
    static_configs:
      - targets: ['movie-narrator:8765']
```

### 2.4 Note on helpers

Every `record_*` / `observe_*` / `set_*` helper is best-effort: a
telemetry failure is swallowed and debug-logged, never propagated, so it
can never break a request or kill a worker thread.

## 3. Programmatic access

```python
from movie_narrator.cloud import metrics

metrics.record_task_submitted()
text = metrics.render_prometheus_text()
```

```python
from movie_narrator.utils.logging_config import configure_logging, JsonFormatter
```

## 4. Distributed tracing

### 4.1 Correlation IDs (default)

By default, tracing is correlation-ID based with **zero new
dependencies**. The `X-Correlation-ID` header ties together, across
services:

- the API request log,
- the `/tasks/{id}` log,
- the worker thread log,
- and the response header echo.

To follow one unit of work end-to-end, grep all logs by the same
correlation ID:

```bash
mn serve --log-format json | jq -c 'select(.correlation_id == "3f2a9c1b")'
```

Because the ID propagates from the submitted task into its worker
thread, a single `correlation_id` string is enough to reconstruct the
full journey of one job — request, queueing, pipeline steps, and final
render.

### 4.2 OpenTelemetry spans (v1.4.0, opt-in)

Real span-based tracing (`task → step / provider / subprocess`) is
available through the optional `[otel]` extra. Nothing changes unless
you opt in: without the extra installed, or with `MN_TRACING` off (the
default), every span helper is a zero-overhead no-op.

```bash
pip install "movie-narrator[otel]"   # opentelemetry-api + opentelemetry-sdk
export MN_TRACING=1
export MN_TRACING_EXPORTER=console   # none (default) | console
```

Span hierarchy (children nest via OpenTelemetry context propagation):

| Span           | Attributes                                                            |
|----------------|-----------------------------------------------------------------------|
| `mn.task`      | `mn.task.id`, `mn.task.movie`, `mn.task.tenant`, `mn.task.plan`       |
| `mn.step`      | `mn.step.name`, `mn.step.attempt`, `mn.step.duration_s`, `mn.step.result`, `mn.step.error_class` |
| `mn.provider`  | `mn.provider.name`, `mn.provider.kind`, `mn.provider.model`, `mn.provider.cache_hit` |
| `mn.subprocess`| `mn.subprocess.cmd` (head only), `mn.subprocess.timeout`              |

Exporters:

- `none` (default) — spans are created through a no-exporter SDK
  provider and dropped on end. Cheap; useful to measure the
  instrumentation cost before shipping traces anywhere.
- `console` — the SDK's built-in `ConsoleSpanExporter` prints every
  finished span to stdout.
- **OTLP / Jaeger / Zipkin are intentionally not bundled** (they would
  drag protobuf/grpcio into the dependency tree). Install the exporter
  package yourself (e.g. `opentelemetry-exporter-otlp`) and register its
  global tracer provider *before* enabling `MN_TRACING` — the engine
  detects an already-registered provider and uses it unchanged. See
  [ADR-014](ADR.md) for the decision record.

## 5. Dashboards

No dashboard is bundled with the engine. The `/metrics` endpoint emits
standard Prometheus text format, so any dashboard tool that consumes
Prometheus (e.g. Grafana) can be pointed at it:

```yaml
# prometheus.yml — scrape as in §2.3, then configure Grafana data source:
# URL http://prometheus:9090  →  import the metric families from §2.2
```

Recommended first panels: `mn_queue_depth` (backlog), `mn_active_tasks`
(concurrency), `mn_task_duration_seconds` histogram (p95 latency), and
`mn_errors_total{type!="http_401"}` (real failures). Each family's
docstring in §2.2 says what it measures and which labels are available.

### 5.1 Dashboard summary API (v1.3.1)

`GET /api/v1/dashboard/summary` returns the whole monitoring state as a
single, **versioned** JSON document (`schema_version: 1`) — a stable
contract for external dashboards that prefer one pull over scraping
metrics. Auth matches the other read routes: loopback binds are open,
non-loopback binds require `MN_API_KEY`.

| Key | Meaning |
|-----|---------|
| `schema_version` | Bumped on incompatible schema changes; consumers gate on it |
| `generated_at` | ISO-8601 UTC timestamp of the aggregation |
| `tasks.total` | Total persisted tasks |
| `tasks.by_status` | Task count per lifecycle status (incl. `dead`) |
| `tasks.recent` | Up to 10 newest tasks, minimal views (`task_id`, `movie`, `status`, `progress`, `tenant_id`, `plan`, `created_at`) |
| `queue.depth` | Pending tasks (classic backlog gauge) |
| `queue.active` | Active tasks (pending/running/retrying) |
| `queue.max_workers` | Configured worker parallelism |
| `artifacts` | Stored artifact `count` and `total_bytes` (zeros when no artifact store is available) |
| `plans` | The effective default plan name and all configured plan names |

Example response:

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-30T09:00:00.000000+00:00",
  "tasks": {
    "total": 42,
    "by_status": {
      "pending": 2, "running": 1, "completed": 30, "failed": 5,
      "cancelled": 2, "retrying": 0, "dead": 2
    },
    "recent": [
      {
        "task_id": "9f8e7d6c5b4a",
        "movie": "飞驰人生",
        "status": "completed",
        "progress": 100.0,
        "tenant_id": "default",
        "plan": "default",
        "created_at": "2026-08-30T08:44:12.000000+00:00"
      }
    ]
  },
  "queue": {"depth": 2, "active": 3, "max_workers": 2},
  "artifacts": {"count": 118, "total_bytes": 53687091200},
  "plans": {"default": "default", "configured": ["default", "free", "pro"]}
}
```

## 6. Alerting

Alerting is also left to the external stack — engine code never fires
alerts. Define Prometheus alert rules over the same metric families:

```yaml
groups:
  - name: movie-narrator
    rules:
      - alert: MNTasksStuck
        expr: mn_queue_depth > 0 and mn_active_tasks == 0
        for: 5m
        labels: { severity: warning }
        annotations: { summary: "Queued tasks are not being processed" }
      - alert: MNHighErrorRate
        expr: rate(mn_errors_total[5m]) > 0.1
        for: 10m
        labels: { severity: critical }
        annotations: { summary: "Error rate above 0.1/s over 5m" }
```

These rules are **examples** — tune the thresholds to your deployment
(refer to `DEPLOYMENT.md` for cluster sizing and scaling guidance).

## 7. Submission rate limiting (v1.5.1, opt-in)

`mn serve` can throttle **task submissions** (`POST /tasks`,
`POST /tasks/batch`) with a per-tenant token bucket. Read routes are
never throttled. The feature is off by default; it is configured via
process-env variables (see `.env.example`):

| Variable | Default | Meaning |
|----------|---------|---------|
| `MN_RATE_LIMIT_ENABLED` | off | opt-in flag |
| `MN_RATE_LIMIT_CAPACITY` | 60 | burst size per tenant |
| `MN_RATE_LIMIT_REFILL_PER_MINUTE` | 60 | sustained submissions per tenant per minute |

A throttled submission answers **429** with a `Retry-After` header and
the JSON body `{"error": "rate_limited", "retry_after_s": ...}` —
clients should honour `Retry-After`. Tenants are keyed by the existing
tenant resolution (the `X-MN-Tenant` header for API-key callers);
unauthenticated loopback callers share the `"default"` bucket.

Monitoring: throttled submissions flow through the standard request
metrics — watch `mn_http_requests_total{path="/tasks",code="429"}` (and
`/tasks/batch`) or `mn_errors_total{type="http_429"}`:

```yaml
- alert: MNSubmissionThrottling
  expr: increase(mn_errors_total{type="http_429"}[15m]) > 0
  for: 5m
  labels: { severity: warning }
  annotations: { summary: "Task submissions are being rate limited" }
```

## 8. Provider usage ledger (v1.5.1)

Alongside Prometheus metrics (which cover the *service*), the engine
keeps an always-on, in-memory **usage ledger** of what a run costs at
the provider boundaries. It is cheap by design: pure counters behind
one lock, no I/O, no opt-in flag.

| Domain | Counters |
|--------|----------|
| `llm` | `attempts`, `errors` (retry outcomes included), `cache_hits`, `prompt_chars`, `resp_chars`; per-kind breakdown (`research`, `script_beats`, `script_expand`, `judge`, ...) |
| `tts` | `synth_calls`, `chars`, `cache_hits`, `retries`; per-provider breakdown |
| `vlm` | `calls`, `cache_hits` |

Surfaces:

- `ctx.metadata["usage"]` — snapshot taken at the end of the TTS step.
- `metadata.json` — the same snapshot flows into the `usage` key via
  the existing metadata export. Post-TTS provider calls (e.g.
  translate-stage LLM usage) are not part of this snapshot; the
  counters themselves cover the whole run.

Motivation: make the deferred LLM-idempotency decision measurable
("revisit only if duplicate-billing becomes measurable") — duplicate
LLM/TTS spend becomes a number in `metadata.json`, not an anecdote.
`metadata.json` is the only surface for now; execution-manifest
integration waits for a runner-touching release.
