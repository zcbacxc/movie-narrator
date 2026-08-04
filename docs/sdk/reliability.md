# Reliability

Circuit breaker and retry policy framework (v0.9.1): `CircuitBreaker` with a
CLOSED/OPEN/HALF_OPEN state machine, `CircuitBreakerRegistry`, and a
configurable `RetryPolicy` with exponential backoff and jitter.

::: movie_narrator.reliability

## Related modules

- [Cloud](cloud.md) — task queue, checkpointing, DLQ, distributed rendering
- [Contract](contract.md) — `CONTRACT_VERSION` and public API surface