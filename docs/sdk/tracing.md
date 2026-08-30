# Tracing

Opt-in OpenTelemetry tracing (v1.4.0): span factories for the
`task → step / provider / subprocess` hierarchy. Strictly no-op unless
`MN_TRACING` is enabled and the optional `[otel]` extra is installed —
see [OBSERVABILITY](../OBSERVABILITY.md) and [ADR-014](../ADR.md).

::: movie_narrator.tracing

## Related modules

- [Pipeline](pipeline.md) — step execution wrapped by `start_step_span`
- [Cloud](cloud.md) — task execution wrapped by `start_task_span`
- [Contract](contract.md) — the v1.4.0 contract exports
