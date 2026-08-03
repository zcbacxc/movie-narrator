[![English](https://img.shields.io/badge/English-Observability-blue)](OBSERVABILITY.md)
[![简体中文](https://img.shields.io/badge/简体中文-可观测性-green)](OBSERVABILITY.zh-CN.md)

# Observability (v0.8.1)

> Structured logging and Prometheus metrics for the `mn serve` API server.
> No new third-party dependencies — the JSON formatter and the metrics
> renderer are implemented with the Python standard library only.

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
