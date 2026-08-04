# Cloud

Async task execution, remote inference, and cloud service infrastructure
(v0.9.x): task models, local and remote queues, REST API server, worker
daemon, artifact management, task checkpointing, graceful shutdown, batch
submission, cron scheduling, dead-letter queue, and conditional distributed
rendering.

::: movie_narrator.cloud

## Related modules

- [Models](models.md) — `Task`, `TaskRequest`, `TaskResult`, `TaskStatus`
- [Pipeline](pipeline.md) — `run_pipeline` and step registry
- [Reliability](reliability.md) — circuit breaker and retry policy
- [Contract](contract.md) — `CONTRACT_VERSION` and public API surface
