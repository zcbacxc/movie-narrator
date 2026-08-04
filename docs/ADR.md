[![English](https://img.shields.io/badge/English-ADR-blue)](ADR.md)
[![简体中文](https://img.shields.io/badge/简体中文-架构决策记录-green)](ADR.zh-CN.md)

# Architecture Decision Records

This document records the key architecture decisions made for the **movie-narrator** project. Each ADR follows a standard structure — Status, Context, Decision, Consequences, and Alternatives — and is intended to be read by developers and maintainers so that the rationale behind important technical choices is not lost over time.

## Introduction

### What is an ADR?

An Architecture Decision Record (ADR) is a short, self-contained note that captures a single significant architectural decision: the problem we were facing, the choice we made, why we made it, what it costs us, and what we considered instead. ADRs are immutable once written — if a decision changes, a new ADR is written that supersedes the old one.

### How to add a new ADR

1. Pick the next available number (ADR-011, ADR-012, ...).
2. Open a copy of the standard template and fill in the five sections.
3. Add a `###` subsection in the "Decision Records" list below.
4. Append a row to the "Decision Index" table.
5. Review the record with the team before merging.

Each ADR should be grounded in the project's actual code and history. Do not invent architecture details that do not exist in the codebase.

---

## Decision Records

### ADR-001: Contract Layer Isolation

- **Status:** Accepted
- **Version:** Introduced since early packaging; still in force as of v0.9.6

**Context**

movie-narrator is structured as a set of packages — the core `movie_narrator` engine, a `web` package, and several plugins. Early on, these packages imported each other's internal modules directly, which made the dependency graph tangled, made it impossible to evolve the engine without breaking plugins, and made cross-package version mismatches hard to diagnose.

**Decision**

We established a single stable contract surface: the `web` package and every plugin may depend only on `movie_narrator.contract`. Internal modules are not allowed to be imported across package boundaries. Compatibility across packages is governed by a semantic versioned `CONTRACT_VERSION` constant, currently `(0, 9, 5)`. Any breaking change to the contract must bump the contract version in a way that obeys semantic versioning rules, so consumers can detect compatibility at load time.

**Consequences**

- Positive: the graph is now acyclic and testable; plugins are decoupled from internal implementation details; compatibility is machine-checkable via `CONTRACT_VERSION`.
- Negative: the contract layer must be kept stable and becomes a bottleneck for change; any new shared capability must be added to the contract first, which adds a small amount of ceremony.

**Alternatives**

- A centralized service registry for all packages (rejected: it hid the real coupling and did not solve the versioning problem).
- Allowing direct internal imports but documenting the boundaries (rejected: documentation is not enforced, and the graph stays tangled).

---

### ADR-002: Provider Registry over Factory

- **Status:** Accepted
- **Version:** Adopted in v0.5.1+

**Context**

Providers for TTS, vision, LLM, and research used to be created through a classic factory pattern. The factory had to know about every provider, so adding a new provider meant modifying the factory and often the core dispatch logic. A legacy factory fallback also existed, which made behavior inconsistent and hard to reason about.

**Decision**

Starting from v0.5.1, provider dispatch uses a registry only. Providers register themselves through decorators — `@register_tts`, `@register_vision`, `@register_llm`, `@register_research` — and the engine looks them up by name at runtime. The legacy factory fallback has been removed. If a factory (or any code path) returns an instance that is not a consistent ABC instance, a `TypeError` is raised rather than silently proceeding.

**Consequences**

- Positive: adding a provider is a pure additive change (register + decorate); dispatch is uniform and explicit; type safety is enforced by the `TypeError` check.
- Negative: registration is implicit, so a provider that is not imported is not available; a small amount of indirection is introduced between declaring and using a provider.

**Alternatives**

- A centralized provider factory (rejected: it must be modified for every new provider).
- Keeping the legacy factory fallback alongside the registry (rejected: two dispatch paths caused inconsistent behavior).

---

### ADR-003: Soft-Step Graceful Degradation

- **Status:** Accepted
- **Version:** In force across the 16-step pipeline

**Context**

The movie-narrator pipeline is a 16-step processing chain. Some steps have soft dependencies (optional libraries, optional upstream data) that may not be present in every environment. Failing hard on any missing piece made the whole pipeline brittle and prevented partial results from being produced.

**Decision**

Soft steps — `research`, `align`, `scene`, `match`, `bgm`, `translate`, `qa_gate`, and `export_clips` — degrade gracefully: when an optional dependency is missing or upstream data is unavailable, the step is skipped softly and the pipeline continues with the next step. A `--strict` flag converts this behavior into a hard abort so that a strict run fails loudly instead of producing partial output. Any hard step (a step whose output is required for everything downstream) fails by terminating the pipeline immediately.

**Consequences**

- Positive: the pipeline is resilient and can still produce useful output when bits are missing; operators can opt into strict behavior deliberately.
- Negative: soft failures can be silent, so users may not notice a skipped step unless they inspect the logs; the strict/soft distinction must be documented for each step.

**Alternatives**

- Fail hard on every step (rejected: too brittle, and it prevented partial results).
- Always skip soft steps with no strict override (rejected: operators could not force a failure when correctness mattered).

---

### ADR-004: Circuit Breaker and Retry Strategy

- **Status:** Accepted
- **Version:** Introduced in v0.9.1

**Context**

The pipeline calls external services — LLM, TTS, TMDB, and VLM — which are subject to transient failures, throttling, and brief outages. Naive retries could hammer a failing service, and a total lack of retries would fail runs on the first hiccup.

**Decision**

We added a circuit breaker in `reliability/circuit_breaker` with a `CLOSED → OPEN → HALF_OPEN` state machine. The `@circuit_guard` decorator protects calls to LLM, TTS, TMDB, and VLM. When the circuit is open, calls fail fast instead of retrying. Retry behavior is governed by a `RetryPolicy` that implements exponential backoff with jitter, so retries spread out over time and do not thundering-herd the upstream service.

**Consequences**

- Positive: external dependency failures are contained, fail fast when the service is down, and recover automatically; retries are backoff-aware and avoid overload.
- Negative: circuit state adds observability requirements; tuning thresholds and retry budgets are environment-sensitive and need per-service calibration.

**Alternatives**

- Unlimited fixed-interval retries (rejected: risk of hammering a failing service).
- No retry at all (rejected: transient failures would fail otherwise-successful runs).
- Circuit breaker without backoff (rejected: recovery would still be harsh on the upstream).

---

### ADR-005: Task Checkpoints and Resume from Breakpoint

- **Status:** Accepted
- **Version:** Introduced in v0.9.2

**Context**

Rendering a narration video is a long-running job. If the process crashed or the machine restarted mid-run, all work was lost and the task had to restart from step one, wasting significant time and cost.

**Decision**

We introduced task checkpoints in `cloud/checkpoint`. After each pipeline step, a `TaskCheckpoint` is persisted. On a crash, a task resumes from the next step after its last persisted checkpoint rather than the beginning. When a task reaches `COMPLETED`, its checkpoint is deleted; when it ends in `FAILED` or `CANCELLED`, the checkpoint is retained so that the run can be inspected and re-run.

**Consequences**

- Positive: long runs are resilient to crashes; partial progress is preserved and resumption is cheap; failed/cancelled runs can be inspected.
- Negative: checkpoint persistence adds I/O and storage overhead; stale checkpoints for failed runs must be managed to avoid accumulation.

**Alternatives**

- Restart long tasks from the beginning (rejected: wasteful for long-running jobs).
- Persisting checkpoints for every task forever (rejected: storage bloat without a retention policy).

---

### ADR-006: Batching and Scheduling

- **Status:** Accepted
- **Version:** Introduced in v0.9.3

**Context**

Users wanted to submit many narration jobs at once and have them run on a schedule, rather than triggering each job manually. The scheduler needed to parse cron-like expressions without pulling in a heavy external dependency.

**Decision**

We added `BatchRequest` supporting 1–50 jobs per batch. Scheduling is handled by `cloud/scheduler`, which includes a dependency-free 5-field cron parser (minute, hour, day-of-month, month, day-of-week). A `JobScheduler` runs in a background thread and dispatches jobs according to the parsed schedule.

**Consequences**

- Positive: batch submission and cron-like scheduling are supported with no external scheduler dependency; the scheduler is lightweight and portable.
- Negative: the 5-field cron parser is simpler than full cron (no seconds or special syntax), so very complex schedules are not supported; batch limits must be enforced and communicated.

**Alternatives**

- Using an external scheduler library (rejected: added a heavy dependency for a small need).
- Full-featured cron support (rejected: over-engineering for the current scheduling needs).

---

### ADR-007: DLQ and Distributed Rendering

- **Status:** Accepted
- **Version:** Introduced in v0.9.4

**Context**

Tasks that repeatedly failed could block the queue or be silently dropped, making failures hard to track. Separately, some jobs produced very long render times, and we wanted to consider offloading rendering to more nodes — but only when it was actually worth it.

**Decision**

We introduced a dead-letter queue (DLQ). Tasks that fail unrecoverably move to a terminal `DEAD` state and can be `replay`ed later. Distributed rendering is a conditional feature: it is triggered only when a single-node render exceeds 10 minutes and there are multiple nodes available; if distributed rendering fails, the job falls back to local rendering.

**Consequences**

- Positive: failed tasks are explicitly visible and recoverable via replay; rendering can scale out when it pays off, with safe fallback to local.
- Negative: the DLQ and replay need operational tooling; the conditional distributed trigger adds complexity and a fallback path that must be tested.

**Alternatives**

- Silently dropping failed tasks (rejected: failures became invisible and unrecoverable).
- Always using distributed rendering (rejected: overhead not worth it for short jobs).
- Never distributing (rejected: single-node renders could take too long).

---

### ADR-008: Configuration Boundary

- **Status:** Accepted
- **Version:** In force across the pipeline

**Context**

Configuration was mixed between infrastructure settings and pipeline behavior settings, and precedence between sources was unclear. This caused confusion about which value is actually in effect and made local vs. production setups inconsistent.

**Decision**

We split configuration into two clear sources. `.env` holds infrastructure settings and uses the `MN_` prefix. `job.yaml` holds pipeline behavior settings. Precedence is: `CLI` arguments > `job.yaml` > inline defaults. This gives a predictable, layered model where the most specific source wins.

**Consequences**

- Positive: infrastructure and behavior are cleanly separated; precedence is explicit and predictable; secrets and env-specific values stay out of job files.
- Negative: users must know which setting lives in which file; the two-file split adds a small onboarding cost.

**Alternatives**

- A single configuration file for everything (rejected: mixed infrastructure and behavior, and secrets risk).
- A YAML-only model with no CLI override (rejected: operators could not override behavior per-run).

---

### ADR-009: Input Sanitization and Security

- **Status:** Accepted
- **Version:** Introduced in v0.9.5

**Context**

The task submission API accepted arbitrary payloads. Malformed or malicious input could reach the pipeline and cause unexpected behavior; there was no size cap, and the CI pipeline had no security scanning or test-coverage gate.

**Decision**

`TaskRequest` now validates every field. Malicious or unparseable payloads are rejected with HTTP `400`. Payloads larger than `1MiB` are rejected with HTTP `413`. On the CI side, security scanning was added using `Bandit` and `pip-audit`, and a test-coverage gate of `80%` was enforced.

**Consequences**

- Positive: the API rejects malformed and oversized input early; security posture is far stronger and CI catches known vulnerabilities and coverage regressions.
- Negative: strict validation can reject legitimate edge cases that were previously tolerated; the coverage gate and security scans add CI time.

**Alternatives**

- Accepting any payload and sanitizing deep in the pipeline (rejected: failed late and unpredictably).
- No size limit (rejected: risk of memory/resource exhaustion).
- No security scanning (rejected: known vulnerabilities would go unnoticed).

---

### ADR-010: i18n and Localized Voice

- **Status:** Accepted
- **Version:** Introduced in v0.9.6

**Context**

The engine generated narration scripts without language awareness, and the TTS voice selection was not tied to the target language. This produced inconsistent language and voice choices for localized output.

**Decision**

We added language-aware script generation and matching, with the default language set to `zh`. TTS voice selection is handled through `voice_map` and `resolve_voice`, with a priority order: explicit `voice` > per-language override > default mapping > `default_voice`. This makes voice selection deterministic and language-aware.

**Consequences**

- Positive: output is consistently localized; voice selection is predictable and can be overridden explicitly; the default language is fixed to `zh`.
- Negative: voice mapping must be maintained as languages are added; the resolution priority must be documented so users understand precedence.

**Alternatives**

- A single global default voice regardless of language (rejected: produced mismatched language/voice).
- No voice mapping, relying on the provider's default (rejected: nondeterministic and not localized).

---

## Decision Index

| # | ADR | Status | Version | Summary |
|---|-----|--------|---------|---------|
| ADR-001 | Contract Layer Isolation | Accepted | — | `web`/plugins depend only on `movie_narrator.contract`; `CONTRACT_VERSION` (0,9,5) governs compatibility |
| ADR-002 | Provider Registry over Factory | Accepted | v0.5.1+ | Registry-only dispatch via `@register_*` decorators; legacy factory fallback removed |
| ADR-003 | Soft-Step Graceful Degradation | Accepted | — | Soft steps skip on missing deps/data; `--strict` aborts; hard steps fail fast |
| ADR-004 | Circuit Breaker and Retry Strategy | Accepted | v0.9.1 | CLOSED→OPEN→HALF_OPEN breaker, `@circuit_guard`, exponential backoff + jitter |
| ADR-005 | Task Checkpoints and Resume | Accepted | v0.9.2 | `TaskCheckpoint` per step; resume after crash; `COMPLETED` deletes, `FAILED`/`CANCELLED` keeps |
| ADR-006 | Batching and Scheduling | Accepted | v0.9.3 | `BatchRequest` (1–50); dependency-free 5-field cron; `JobScheduler` background thread |
| ADR-007 | DLQ and Distributed Rendering | Accepted | v0.9.4 | `DEAD` terminal state + replay; conditional distributed rendering with local fallback |
| ADR-008 | Configuration Boundary | Accepted | — | `.env` (`MN_`, infra) vs `job.yaml` (behavior); CLI > job.yaml > defaults |
| ADR-009 | Input Sanitization and Security | Accepted | v0.9.5 | Field validation; HTTP 400/413; Bandit + pip-audit; 80% coverage gate |
| ADR-010 | i18n and Localized Voice | Accepted | v0.9.6 | Language-aware generation (lang default `zh`); `voice_map`/`resolve_voice` priority resolution |