[![English](https://img.shields.io/badge/English-Observability-blue)](OBSERVABILITY.md)
[![简体中文](https://img.shields.io/badge/简体中文-可观测性-green)](OBSERVABILITY.zh-CN.md)

# 可观测性（v0.8.1）

> 面向 `mn serve` API 服务的结构化日志与 Prometheus 指标。
> 不引入任何新的第三方依赖——JSON 格式化器与指标渲染器仅使用 Python 标准库实现。

## 1. 结构化日志

### 1.1 JSON 模式

默认情况下服务输出人类可读的文本日志。开启 JSON 模式以便将日志发送到 ELK / Loki / CloudWatch，无需编写 grok 解析规则：

```bash
mn serve --log-format json
# 或
export MN_LOG_FORMAT=json
mn serve
```

每行是一个独立的 JSON 对象，键集合固定：

| 键                | 出现条件       | 含义                                      |
|-------------------|----------------|-------------------------------------------|
| `ts`              | 始终           | ISO-8601 UTC，毫秒精度（`Z` 后缀）         |
| `level`           | 始终           | 级别名称（`INFO`、`ERROR` 等）             |
| `logger`          | 始终           | 日志器名称                                |
| `msg`             | 始终           | 已插值的消息                              |
| `correlation_id`  | 绑定后         | 串联同一工作单元所有记录                  |
| `exc_type`        | 异常时         | 异常类名                                  |
| `exc_message`     | 异常时         | `str(exc)`                                |
| `traceback`       | 异常时         | 格式化后的堆栈                             |
| `<extra>`         | 提供时         | 通过 `extra=` 传入的任意字段               |

当未绑定关联 ID 时，`correlation_id` 会被**省略**（而不是输出 `null`），以保持无关联行简洁。

### 1.2 关联 ID（Correlation ID）

`contextvars.ContextVar` 会跨线程携带 12 位十六进制关联 ID。API 服务优先采用请求头 `X-Request-ID` 或 `X-Correlation-ID` 中的入站 ID（以便上游链路在此延续），否则生成新的 ID。每个响应都会通过 `X-Correlation-ID` 响应头回显当前 ID。该 ID 还会从已提交的任务传播到其工作线程，因此请求日志、`/tasks/{id}` 日志与工作线程日志共享同一 ID。

在应用代码中：

```python
from movie_narrator.utils.logging_config import correlation_scope, get_correlation_id

with correlation_scope() as cid:
    logger.info("处理请求")  # 记录携带 cid
```

### 1.3 日志级别

```bash
mn serve --log-level DEBUG
# 或
export MN_LOG_LEVEL=DEBUG
```

默认：`INFO`。服务日志默认为 INFO（即便流水线运行默认更低），以避免长时间运行的服务被日志淹没。

## 2. Prometheus 指标

### 2.1 端点

`GET /metrics` 提供文本暴露格式（版本 `0.0.4`）。

该端点**默认需要鉴权**——其负载会泄露任务量与错误率。发送与其他路由相同的 `X-API-Key`。若希望集群内 scraper 无需密钥即可抓取，可关闭鉴权：

```bash
export MN_METRICS_PUBLIC=1   # 1/true/yes/on
```

### 2.2 指标族

| 名称                            | 类型       | 标签                            | 说明                                 |
|---------------------------------|------------|---------------------------------|--------------------------------------|
| `mn_build_info`                 | gauge      | `version`                       | 常量 `1`，带版本标签                 |
| `mn_tasks_total`                | counter    | `status`                        | 按生命周期状态统计的任务数           |
| `mn_queue_depth`                | gauge      | —                               | 等待中的任务数                       |
| `mn_active_tasks`               | gauge      | —                               | 当前正在执行的任务数                 |
| `mn_task_duration_seconds`      | histogram  | —                               | 端到端任务耗时                       |
| `mn_render_duration_seconds`     | histogram  | —                               | `render_video` 步骤耗时              |
| `mn_errors_total`               | counter    | `type`                          | 按粗粒度类型统计的错误（`http_401` 等）|
| `mn_http_requests_total`        | counter    | `method`, `path`, `code`        | HTTP 请求（`path` 为路由模板）       |

直方图分桶（秒）：
`0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800`。

`mn_http_requests_total` 的 `path` 标签始终是**路由模板**（如 `/tasks/{id}`），绝不会是包含具体 ID 的路径，从而保持基数可控。无法识别的路径会被折叠为 `/other`。

### 2.3 抓取配置示例

```yaml
scrape_configs:
  - job_name: movie-narrator
    metrics_path: /metrics
    authorization:
      type: Bearer
      credentials_file: /run/secrets/mn-api-key   # 当 MN_METRICS_PUBLIC != 1 时
    static_configs:
      - targets: ['movie-narrator:8765']
```

### 2.4 关于辅助函数的说明

所有 `record_*` / `observe_*` / `set_*` 辅助函数都是尽力而为的：遥测失败会被吞掉并记录为 debug 日志，绝不会向上抛出，因此永远不会破坏请求或终止工作线程。

## 3. 编程式访问

```python
from movie_narrator.cloud import metrics

metrics.record_task_submitted()
text = metrics.render_prometheus_text()
```

```python
from movie_narrator.utils.logging_config import configure_logging, JsonFormatter
```

## 4. 分布式追踪

追踪基于关联 ID（correlation ID）实现——**不集成 OpenTelemetry**（刻意为之：零新增依赖）。`X-Correlation-ID` 头将以下内容在跨服务间串联起来：

- API 请求日志，
- `/tasks/{id}` 日志，
- 工作线程日志，
- 以及响应头中的回显。

要端到端追踪一个工作单元，按同一关联 ID 检索所有日志即可：

```bash
mn serve --log-format json | jq -c 'select(.correlation_id == "3f2a9c1b")'
```

由于 ID 会从提交的任务传播到其工作线程，一个 `correlation_id` 字符串就足以还原某个任务的全部旅程——请求、排队、流水线各步骤以及最终渲染。

## 5. 仪表盘

引擎不捆绑任何仪表盘。`/metrics` 端点输出标准 Prometheus 文本格式，任何能消费 Prometheus 的仪表盘工具（如 Grafana）都可以直接接入：

```yaml
# prometheus.yml — 按 §2.3 抓取，然后配置 Grafana 数据源：
# URL http://prometheus:9090  →  导入 §2.2 中的指标族
```

推荐的首屏面板：`mn_queue_depth`（积压）、`mn_active_tasks`（并发）、`mn_task_duration_seconds` 直方图（p95 延迟）、`mn_errors_total{type!="http_401"}`（真实失败）。§2.2 中每个指标族的说明都写明了它衡量什么、有哪些可用标签。

## 6. 告警

告警同样交由外部技术栈处理——引擎代码从不主动触发告警。在 Prometheus 中基于同样的指标族定义告警规则：

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

以上规则**仅为示例**——请根据你的部署调整阈值（集群规模与扩容指引参见 `DEPLOYMENT.md`）。
