[![English](https://img.shields.io/badge/English-ADR-blue)](ADR.md)
[![简体中文](https://img.shields.io/badge/简体中文-架构决策记录-green)](ADR.zh-CN.md)

# Architecture Decision Records

This document records the key architecture decisions made for the **movie-narrator** project. Each ADR follows a standard structure — Status, Context, Decision Drivers, Considered Options, Decision Outcome, Consequences, and References — and is intended to be read by developers and maintainers so that the rationale behind important technical choices is not lost over time.

## Introduction

### What is an ADR?

An Architecture Decision Record (ADR) is a short, self-contained note that captures a single significant architectural decision: the problem we were facing, the choice we made, why we made it, what it costs us, and what we considered instead. ADRs are immutable once written — if a decision changes, a new ADR is written that supersedes the old one.

### How to add a new ADR

1. Pick the next available number (ADR-012, ADR-013, ...).
2. Open a copy of the standard template and fill in the seven sections.
3. Add a `## ADR-NNN` section below.
4. Append a row to the "Decision Index" table.
5. Review the record with the team before merging.

Each ADR should be grounded in the project's actual code and history. Do not invent architecture details that do not exist in the codebase.

---

## ADR-001: Contract Layer Isolation

**Status:** Accepted
**Version:** Introduced since early packaging; still in force as of v1.2.0

**Context**

movie-narrator is structured as a set of packages — the core `movie_narrator` engine, a `web` package, and several plugins. Early on, these packages imported each other's internal modules directly, which made the dependency graph tangled, made it impossible to evolve the engine without breaking plugins, and made cross-package version mismatches hard to diagnose.

**Decision Drivers**

- Packages imported each other's internal modules directly, tangling the dependency graph.
- The engine could not evolve without breaking plugins, and cross-package version mismatches were hard to diagnose.
- Cross-package compatibility needed to be machine-checkable at load time.

**Considered Options**

- A centralized service registry for all packages (rejected: it hid the real coupling and did not solve the versioning problem).
- Allowing direct internal imports but documenting the boundaries (rejected: documentation is not enforced, and the graph stays tangled).

**Decision Outcome**

We established a single stable contract surface: the `web` package and every plugin may depend only on `movie_narrator.contract`. Internal modules are not allowed to be imported across package boundaries. Compatibility across packages is governed by a semantic versioned `CONTRACT_VERSION` constant, at the time of this ADR `(0, 9, 5)`. Any breaking change to the contract must bump the contract version in a way that obeys semantic versioning rules, so consumers can detect compatibility at load time.

**Consequences**

- Positive: the graph is now acyclic and testable; plugins are decoupled from internal implementation details; compatibility is machine-checkable via `CONTRACT_VERSION`.
- Negative: the contract layer must be kept stable and becomes a bottleneck for change; any new shared capability must be added to the contract first, which adds a small amount of ceremony.

**References**

- `CHANGELOG.md`
- `docs/ARCHITECTURE.md`
- `src/movie_narrator/contract.py`

---

## ADR-002: Provider Registry over Factory

**Status:** Accepted
**Version:** Adopted in v0.5.1+

**Context**

Providers for TTS, vision, LLM, and research used to be created through a classic factory pattern. The factory had to know about every provider, so adding a new provider meant modifying the factory and often the core dispatch logic. A legacy factory fallback also existed, which made behavior inconsistent and hard to reason about.

**Decision Drivers**

- The factory had to know about every provider, so adding a provider meant modifying the factory and dispatch logic.
- A legacy factory fallback caused inconsistent and hard-to-reason behavior.
- Dispatch needed to be uniform, explicit, and type-safe.

**Considered Options**

- A centralized provider factory (rejected: it must be modified for every new provider).
- Keeping the legacy factory fallback alongside the registry (rejected: two dispatch paths caused inconsistent behavior).

**Decision Outcome**

Starting from v0.5.1, provider dispatch uses a registry only. Providers register themselves through decorators — `@register_tts`, `@register_vision`, `@register_llm`, `@register_research` — and the engine looks them up by name at runtime. The legacy factory fallback has been removed. If a factory (or any code path) returns an instance that is not a consistent ABC instance, a `TypeError` is raised rather than silently proceeding.

**Consequences**

- Positive: adding a provider is a pure additive change (register + decorate); dispatch is uniform and explicit; type safety is enforced by the `TypeError` check.
- Negative: registration is implicit, so a provider that is not imported is not available; a small amount of indirection is introduced between declaring and using a provider.

**References**

- `CHANGELOG.md`
- `docs/PLUGIN_DEVELOPMENT.md`
- `src/movie_narrator/providers/registry.py`

---

## ADR-003: Soft-Step Graceful Degradation

**Status:** Accepted
**Version:** In force across the 16-step pipeline

**Context**

The movie-narrator pipeline is a 16-step processing chain. Some steps have soft dependencies (optional libraries, optional upstream data) that may not be present in every environment. Failing hard on any missing piece made the whole pipeline brittle and prevented partial results from being produced.

**Decision Drivers**

- Some steps have soft dependencies that may not be present in every environment.
- Failing hard on missing pieces made the pipeline brittle and prevented partial results.
- Operators needed a way to force a hard failure when correctness mattered.

**Considered Options**

- Fail hard on every step (rejected: too brittle, and it prevented partial results).
- Always skip soft steps with no strict override (rejected: operators could not force a failure when correctness mattered).

**Decision Outcome**

Soft steps — `research`, `align`, `scene`, `match`, `bgm`, `translate`, `qa_gate`, and `export_clips` — degrade gracefully: when an optional dependency is missing or upstream data is unavailable, the step is skipped softly and the pipeline continues with the next step. A `--strict` flag converts this behavior into a hard abort so that a strict run fails loudly instead of producing partial output. Any hard step (a step whose output is required for everything downstream) fails by terminating the pipeline immediately.

**Consequences**

- Positive: the pipeline is resilient and can still produce useful output when bits are missing; operators can opt into strict behavior deliberately.
- Negative: soft failures can be silent, so users may not notice a skipped step unless they inspect the logs; the strict/soft distinction must be documented for each step.

**References**

- `CHANGELOG.md`
- `docs/ARCHITECTURE.md`
- `src/movie_narrator/pipeline/runner.py`

---

## ADR-004: Circuit Breaker and Retry Strategy

**Status:** Accepted
**Version:** Introduced in v0.9.1

**Context**

The pipeline calls external services — LLM, TTS, TMDB, and VLM — which are subject to transient failures, throttling, and brief outages. Naive retries could hammer a failing service, and a total lack of retries would fail runs on the first hiccup.

**Decision Drivers**

- External services (LLM, TTS, TMDB, and VLM) are subject to transient failures, throttling, and brief outages.
- Naive retries could hammer a failing service, while no retries would fail runs on the first hiccup.
- Retries needed to spread over time to avoid thundering-herding the upstream service.

**Considered Options**

- Unlimited fixed-interval retries (rejected: risk of hammering a failing service).
- No retry at all (rejected: transient failures would fail otherwise-successful runs).
- Circuit breaker without backoff (rejected: recovery would still be harsh on the upstream).

**Decision Outcome**

We added a circuit breaker in `reliability/circuit_breaker` with a `CLOSED → OPEN → HALF_OPEN` state machine. The `@circuit_guard` decorator protects calls to LLM, TTS, TMDB, and VLM. When the circuit is open, calls fail fast instead of retrying. Retry behavior is governed by a `RetryPolicy` that implements exponential backoff with jitter, so retries spread out over time and do not thundering-herd the upstream service.

**Consequences**

- Positive: external dependency failures are contained, fail fast when the service is down, and recover automatically; retries are backoff-aware and avoid overload.
- Negative: circuit state adds observability requirements; tuning thresholds and retry budgets are environment-sensitive and need per-service calibration.

**References**

- `CHANGELOG.md`
- `src/movie_narrator/reliability/circuit_breaker.py`
- `src/movie_narrator/reliability/retry.py`

---

## ADR-005: Task Checkpoints and Resume from Breakpoint

**Status:** Accepted
**Version:** Introduced in v0.9.2

**Context**

Rendering a narration video is a long-running job. If the process crashed or the machine restarted mid-run, all work was lost and the task had to restart from step one, wasting significant time and cost.

**Decision Drivers**

- Rendering is a long-running job; a crash mid-run lost all work.
- Restarting from step one wasted significant time and cost.
- Failed or cancelled runs needed to be inspectable and re-runnable.

**Considered Options**

- Restart long tasks from the beginning (rejected: wasteful for long-running jobs).
- Persisting checkpoints for every task forever (rejected: storage bloat without a retention policy).

**Decision Outcome**

We introduced task checkpoints in `cloud/checkpoint`. After each pipeline step, a `TaskCheckpoint` is persisted. On a crash, a task resumes from the next step after its last persisted checkpoint rather than the beginning. When a task reaches `COMPLETED`, its checkpoint is deleted; when it ends in `FAILED` or `CANCELLED`, the checkpoint is retained so that the run can be inspected and re-run.

**Consequences**

- Positive: long runs are resilient to crashes; partial progress is preserved and resumption is cheap; failed/cancelled runs can be inspected.
- Negative: checkpoint persistence adds I/O and storage overhead; stale checkpoints for failed runs must be managed to avoid accumulation.

**References**

- `CHANGELOG.md`
- `src/movie_narrator/cloud/checkpoint.py`
- `docs/sdk/cloud.md`

---

## ADR-006: Batching and Scheduling

**Status:** Accepted
**Version:** Introduced in v0.9.3

**Context**

Users wanted to submit many narration jobs at once and have them run on a schedule, rather than triggering each job manually. The scheduler needed to parse cron-like expressions without pulling in a heavy external dependency.

**Decision Drivers**

- Users wanted to submit many jobs at once and have them run on a schedule.
- Cron-like expressions needed to be parsed without pulling in a heavy external dependency.
- The scheduler should stay lightweight and portable.

**Considered Options**

- Using an external scheduler library (rejected: added a heavy dependency for a small need).
- Full-featured cron support (rejected: over-engineering for the current scheduling needs).

**Decision Outcome**

We added `BatchRequest` supporting 1–50 jobs per batch. Scheduling is handled by `cloud/scheduler`, which includes a dependency-free 5-field cron parser (minute, hour, day-of-month, month, day-of-week). A `JobScheduler` runs in a background thread and dispatches jobs according to the parsed schedule.

**Consequences**

- Positive: batch submission and cron-like scheduling are supported with no external scheduler dependency; the scheduler is lightweight and portable.
- Negative: the 5-field cron parser is simpler than full cron (no seconds or special syntax), so very complex schedules are not supported; batch limits must be enforced and communicated.

**References**

- `CHANGELOG.md`
- `src/movie_narrator/cloud/scheduler.py`
- `src/movie_narrator/cloud/models.py`

---

## ADR-007: DLQ and Distributed Rendering

**Status:** Accepted
**Version:** Introduced in v0.9.4

**Context**

Tasks that repeatedly failed could block the queue or be silently dropped, making failures hard to track. Separately, some jobs produced very long render times, and we wanted to consider offloading rendering to more nodes — but only when it was actually worth it.

**Decision Drivers**

- Repeatedly failing tasks could block the queue or be silently dropped, making failures hard to track.
- Some jobs produced very long render times, warranting offloading to more nodes.
- Distributed rendering should be used only when it pays off, with a safe fallback.

**Considered Options**

- Silently dropping failed tasks (rejected: failures became invisible and unrecoverable).
- Always using distributed rendering (rejected: overhead not worth it for short jobs).
- Never distributing (rejected: single-node renders could take too long).

**Decision Outcome**

We introduced a dead-letter queue (DLQ). Tasks that fail unrecoverably move to a terminal `DEAD` state and can be `replay`ed later. Distributed rendering is a conditional feature: it is triggered only when a single-node render exceeds 10 minutes and there are multiple nodes available; if distributed rendering fails, the job falls back to local rendering.

**Consequences**

- Positive: failed tasks are explicitly visible and recoverable via replay; rendering can scale out when it pays off, with safe fallback to local.
- Negative: the DLQ and replay need operational tooling; the conditional distributed trigger adds complexity and a fallback path that must be tested.

**References**

- `CHANGELOG.md`
- `src/movie_narrator/cloud/dlq.py`
- `src/movie_narrator/cloud/distributed.py`

---

## ADR-008: Configuration Boundary

**Status:** Accepted
**Version:** In force across the pipeline

**Context**

Configuration was mixed between infrastructure settings and pipeline behavior settings, and precedence between sources was unclear. This caused confusion about which value is actually in effect and made local vs. production setups inconsistent.

**Decision Drivers**

- Configuration mixed infrastructure and pipeline behavior settings, with unclear precedence.
- Confusion existed about which value is actually in effect.
- Local vs. production setups were inconsistent.

**Considered Options**

- A single configuration file for everything (rejected: mixed infrastructure and behavior, and secrets risk).
- A YAML-only model with no CLI override (rejected: operators could not override behavior per-run).

**Decision Outcome**

We split configuration into two clear sources. `.env` holds infrastructure settings and uses the `MN_` prefix. `job.yaml` holds pipeline behavior settings. Precedence is: `CLI` arguments > `job.yaml` > inline defaults. This gives a predictable, layered model where the most specific source wins.

**Consequences**

- Positive: infrastructure and behavior are cleanly separated; precedence is explicit and predictable; secrets and env-specific values stay out of job files.
- Negative: users must know which setting lives in which file; the two-file split adds a small onboarding cost.

**References**

- `CHANGELOG.md`
- `src/movie_narrator/config.py`
- `docs/PACKAGING.md`

---

## ADR-009: Input Sanitization and Security

**Status:** Accepted
**Version:** Introduced in v0.9.5

**Context**

The task submission API accepted arbitrary payloads. Malformed or malicious input could reach the pipeline and cause unexpected behavior; there was no size cap, and the CI pipeline had no security scanning or test-coverage gate.

**Decision Drivers**

- The task submission API accepted arbitrary payloads with no size cap.
- Malformed or malicious input could reach the pipeline and cause unexpected behavior.
- The CI pipeline lacked security scanning and a test-coverage gate.

**Considered Options**

- Accepting any payload and sanitizing deep in the pipeline (rejected: failed late and unpredictably).
- No size limit (rejected: risk of memory/resource exhaustion).
- No security scanning (rejected: known vulnerabilities would go unnoticed).

**Decision Outcome**

`TaskRequest` now validates every field. Malicious or unparseable payloads are rejected with HTTP `400`. Payloads larger than `1MiB` are rejected with HTTP `413`. On the CI side, security scanning was added using `Bandit` and `pip-audit`, and a test-coverage gate of `80%` was enforced.

**Consequences**

- Positive: the API rejects malformed and oversized input early; security posture is far stronger and CI catches known vulnerabilities and coverage regressions.
- Negative: strict validation can reject legitimate edge cases that were previously tolerated; the coverage gate and security scans add CI time.

**References**

- `CHANGELOG.md`
- `src/movie_narrator/utils/sanitize.py`
- `src/movie_narrator/cloud/models.py`

---

## ADR-010: i18n and Localized Voice

**Status:** Accepted
**Version:** Introduced in v0.9.6

**Context**

The engine generated narration scripts without language awareness, and the TTS voice selection was not tied to the target language. This produced inconsistent language and voice choices for localized output.

**Decision Drivers**

- Script generation lacked language awareness.
- TTS voice selection was not tied to the target language.
- Voice selection needed to be deterministic and language-aware.

**Considered Options**

- A single global default voice regardless of language (rejected: produced mismatched language/voice).
- No voice mapping, relying on the provider's default (rejected: nondeterministic and not localized).

**Decision Outcome**

We added language-aware script generation and matching, with the default language set to `zh`. TTS voice selection is handled through `voice_map` and `resolve_voice`, with a priority order: explicit `voice` > per-language override > default mapping > `default_voice`. This makes voice selection deterministic and language-aware.

**Consequences**

- Positive: output is consistently localized; voice selection is predictable and can be overridden explicitly; the default language is fixed to `zh`.
- Negative: voice mapping must be maintained as languages are added; the resolution priority must be documented so users understand precedence.

**References**

- `CHANGELOG.md`
- `src/movie_narrator/tts/voice_map.py`
- `src/movie_narrator/pipeline/script.py`

---

## ADR-011: Licensing Red Lines and FFmpeg Bundling Policy

**Status:** Accepted
**Version:** Recorded at v1.1.0

**Context**

The engine surfaces compliance risks when adding third-party components or packaging binaries. An internal compliance review identified two forward-looking risks: (R4) some recommended dependencies have license terms incompatible with AGPL-3.0-or-later or platform ToS, and (R3) bundling an FFmpeg binary would introduce redistribution obligations that a plain CLI invocation does not.

**Decision Drivers**

- Some third-party options carry license terms incompatible with AGPL-3.0-or-later or with platform ToS (e.g. custom-priced or undefined licenses).
- The project must not absorb obligations it cannot honor (e.g. all-rights-reserved code, paid-restricted licenses, platform-rule-violating scrapers).
- Bundling an FFmpeg binary changes the redistribution obligations vs. invoking an external `ffmpeg`.
- The policy must be machine-and-doc enforced so contributors do not accidentally reintroduce a red-line dependency.

**Considered Options**

- Adopting the recommended-but-restricted components (rejected: license/platform ToS conflict).
- Copying undefined-license reference code (rejected: all rights reserved by default; would be infringement).
- Bundling a GPL/LGPL FFmpeg binary into a Windows distribution (rejected at present: raises source/binary redistribution duties; deferred to a documented packaging decision).

**Decision Outcome**

We adopt an explicit **red-line list** that must never be introduced into the core or bundled distribution:

- **Remotion** (custom license; paid for companies >3 people) — use MIT alternatives (e.g. revideo) or HTML + headless screenshot instead.
- **TypeTale source code** (license undefined) — do not copy; self-implement or use clearly MIT-licensed references with attribution.
- **Material crawling scrapers** (yt-dlp/Bilibili/Playwright against streaming platforms) — platform ToS + copyright; keep the "user-provided material" route. B-roll may only come from public-domain sources (Archive.org, NASA, Wikimedia, Pexels).
- **Voice cloning** (IndexTTS/CosyVoice for arbitrary voices) — voice-rights legal exposure; only clone the user's own/authorized voice if ever added.

For **FFmpeg**: the engine resolves the binary through the shared `utils/ffmpeg_bin.ffmpeg_bin()` policy — `MN_FFMPEG_BIN` override → bundled imageio-ffmpeg build (a full-featured static build, immune to PATH shadowing by a crippled/minimal system ffmpeg) → system `PATH` binary → bare `"ffmpeg"`. The bundled build arrives as a transitive dependency of moviepy (imageio-ffmpeg), not as a binary the project itself bundles into a distribution, so this does not introduce redistribution obligations. If a future Windows distribution ever bundles an FFmpeg binary itself, it must (a) prefer an LGPL build, (b) include a `THIRD_PARTY_NOTICES` file (license text + source URL + build config), and (c) record the decision here before shipping. A `mn doctor` command is the intended vehicle for detecting and guiding FFmpeg installation rather than bundling it.

**Consequences**

- Positive: the red-line list prevents accidental license/platform-ToS violations; FFmpeg stays an external dependency with no redistribution duty; the compliance posture is documented and reviewable.
- Negative: contributors must check the red-line list before adding dependencies; the FFmpeg bundling policy for the project's own distribution remains documentation-only — no binary is bundled into a distribution by the project itself, so the `THIRD_PARTY_NOTICES` file is a future conditional artifact (the bundled imageio-ffmpeg build ships only as a moviepy transitive dependency). The `mn doctor` command (which guides FFmpeg installation rather than bundling) is implemented.

**References**

- `docs/PACKAGING.md`
- `pyproject.toml`

---

## ADR-012: Linear-compatible DAG Contract

**Status:** Accepted
**Version:** Introduced in v1.3.0

**Context**

The 16 pipeline steps form a fixed linear sequence in `run_pipeline`. Plugins can inject steps but cannot declare what data they read/write, so there is no machine-checkable way to validate that a plugin's data dependencies are compatible with linear execution — nor a foundation for future parallel scheduling.

**Considered Options**

- Making the runner a parallel DAG executor now (rejected: high risk, no current need; render/TTS are the only slow steps and are already cached).
- Overloading `mn resume --from-step` for deliberate re-execution (rejected: resume means crash recovery — continue after the last completed step; mixing the two semantics would make stale downstream success states silently skip work).

**Decision Outcome**

Steps may declare coarse `inputs` / `outputs` (Context attribute or `ctx.metadata` key names) and `depends_on` (upstream steps whose outputs they read) at registration time. A new `pipeline/dag.py` exposes `StepSpec`, `build_step_graph`, `validate_linear_order` (advisory warnings for unregistered/later-ordered/cyclic dependencies) and `topological_order` (Kahn's algorithm with linear tie-breaking). The runner keeps its linear for-loop; `topological_order` equals `step_registry.ordered_names()` for any linear-compatible registry. Re-execution is a separate `mn rerun --from STEP` command that invalidates downstream soft-step statuses.

**Consequences**

- Positive: plugin dependency mistakes become detectable at validation time; the step graph is inspectable via the contract surface; future parallel scheduling needs no semantic change.
- Negative: declarations are advisory (the runner cannot enforce them on mutable `Context` state); plugin authors must keep `depends_on` backwards-pointing or accept validation warnings.

**References**

- `src/movie_narrator/pipeline/dag.py`
- `src/movie_narrator/pipeline/registry.py`
- `docs/PLUGIN_DEVELOPMENT.md`
## ADR-013: Service and Product Semantics — Tenants, Plans, and Webhooks

**Status:** Accepted
**Version:** Recorded at v1.3.1

**Context**

v1.3.1 turns the engine from a single-user tool into a service surface: operators need an audit trail of *who* submitted what (principal/tenant), product limits per submission (plans/entitlements), and push notifications for terminal task transitions (webhooks). The ROADMAP explicitly keeps full multi-tenant isolation ("完整多租户隔离") out of scope for this release, and the local frictionless single-user path introduced in v1.2 must not change by default.

**Decision Drivers**

- Backward compatibility: with no new environment variables set, every v1.2 behaviour (unauthenticated loopback, unlimited submissions, no push notifications) is byte-for-byte unchanged.
- No new dependencies (httpx is already a runtime dependency; the retry framework already exists).
- The pipeline (`src/movie_narrator/pipeline/`) must stay service-agnostic — service policy belongs to the cloud layer.
- Failure isolation: webhook delivery must never affect task outcomes.

**Considered Options**

- *Tenants — full row-level isolation now* (rejected): couples the storage schema and an auth model that is not yet defined; the actual isolation work is explicitly long-term.
- *Tenants — per-tenant API keys* (rejected): introduces key management and distribution concerns unrelated to labelling; deferred.
- *Tenants — labelling/scoping MVP* (chosen): `tenant_id`/`principal` are recorded on tasks (additive columns + JSON), surfaced in responses and audit records; a non-default tenant sees only its own tasks' artifacts while the `default` tenant keeps the full single-tenant view.
- *Plans — enforcement inside the pipeline (e.g. `pipeline/render.py`)* (rejected): couples product policy into engine steps; a render-admission preflight lands there separately in v1.3.2.
- *Plans — API-side validation + worker-side injection* (chosen): submissions are validated against the resolved `Plan` (403 `entitlement_denied` on violation); the worker injects the mandatory watermark via the existing `render_template.watermark_text` param and forces the CPU encoder hint when the plan disallows GPU encoding, recording a `plan`/`plan_policy` block in `ctx.metadata` and `metadata.json`.
- *Webhooks — SQLite delivery table* (rejected): schema coupling with the task store for data that is append-only and loss-tolerant.
- *Webhooks — Celery/queue-based delivery* (rejected): a heavy dependency for a fire-and-forget side channel.
- *Webhooks — JSONL delivery log + HMAC signing now* (chosen): one JSONL line per attempt next to the task store; requests signed with hex HMAC-SHA256 over the raw body; retries honour `Retry-After`; consumers deduplicate on the event id. Redelivery API and per-tenant webhook endpoints are deferred.

**Decision Outcome**

- Service semantics are additive and default-off: the `default` plan is unlimited, unauthenticated callers resolve to principal `local` / tenant `default` / plan `default`, and webhooks are disabled unless `MN_WEBHOOK_URLS` is set.
- Plans are data (`cloud/entitlements.py`), enforced at exactly two points — submission validation in `cloud/api.py` and policy injection in `cloud/worker.py`.
- Webhook delivery is isolated in `cloud/webhooks.py` behind a daemon thread pool; failures are logged and recorded in `webhook_deliveries.jsonl` only.
- Full tenant-isolated storage, per-tenant API keys, redelivery API and per-tenant webhook endpoints remain future work (per ROADMAP).

**Consequences**

- Positive: the engine gains a service/product surface without touching the pipeline; every limit is observable (audit records, `plan_policy` metadata, delivery records); the local path is unchanged.
- Negative: tenant scoping is labelling, not isolation — operators must not treat `X-MN-Tenant` as a security boundary; webhook delivery is best-effort (no redelivery API yet); plan limits are advisory heuristics at submission time (the artifact-size estimate is approximate).

**References**

- `src/movie_narrator/cloud/entitlements.py`, `src/movie_narrator/cloud/webhooks.py`, `src/movie_narrator/cloud/dashboard.py`
- `docs/DEPLOYMENT.md` (configuration), `docs/OBSERVABILITY.md` (dashboard summary API)
- `.env.example` (v1.3.1 variables)

---

## ADR-014: Opt-in OpenTelemetry Tracing

**Status:** Accepted
**Version:** Recorded at v1.4.0

**Context**

The ROADMAP deferred item "real span-based tracing (task → step/provider/subprocess)" asks for vendor-neutral distributed traces across the pipeline and the cloud service. OpenTelemetry is the industry default, but its SDK (and especially the OTLP exporter) pulls a heavyweight dependency chain (protobuf, grpcio) into a project whose engine deliberately ships with a small dependency surface — and most single-machine users never export a trace.

**Decision Drivers**

- Zero-dependency-when-off: the default installation and the CI gate must be byte-for-byte unaffected; tracing must cost nothing unless explicitly installed and enabled.
- No pipeline coupling: instrumentation belongs at the runner/worker/provider boundaries via 1–2-line hooks, not inside step implementations.
- Bounded cardinality: stable, low-cardinality span names (`mn.task`, `mn.step`, `mn.provider`, `mn.subprocess`) with the variable parts as attributes.
- Vendor neutrality: users must be able to bring their own backend (OTLP, Jaeger, Zipkin) without the engine shipping transport dependencies.

**Considered Options**

- *Always-on tracing with a built-in exporter* (rejected): couples every deployment to the SDK and adds overhead with no opt-out; contradicts the zero-dependency-when-off requirement.
- *Bundling `opentelemetry-exporter-otlp` in the extra* (rejected): drags protobuf/grpcio into the `[otel]` extra; users who want OTLP install the exporter themselves.
- *Hand-rolled span/event format with a pluggable sink* (rejected): reinvents context propagation, sampling and the exporter ecosystem; every backend integration would become engine code.
- *Opt-in `opentelemetry-api` + `opentelemetry-sdk` with guarded imports and no-op fallback* (chosen): `MN_TRACING` gates span creation; without the extra (or with the flag off) every helper is a zero-overhead no-op. `MN_TRACING_EXPORTER=none` (default) creates spans through a no-exporter SDK provider and drops them; `console` uses the SDK's built-in `ConsoleSpanExporter`. A pre-registered global tracer provider (e.g. a user-supplied OTLP pipeline) is detected and used unchanged.

**Decision Outcome**

- `movie_narrator.tracing` exposes four span factories (`start_task_span`, `start_step_span`, `start_provider_span`, `start_subprocess_span`) returning context-manager handles; the factories are part of the public contract (v1.4.0 exports).
- Wire-up points: the pipeline runner opens one span per step execution; the worker opens the task span (parent of all step spans); `utils/llm.py` and `pipeline/tts.py` open one provider span per LLM call / TTS segment; `utils/process.py` wraps ffmpeg children in a subprocess span (lazy import keeps the module stdlib-only).
- `pyproject.toml` gains an `[otel]` extra (`opentelemetry-api`/`opentelemetry-sdk` `>=1.20,<2`); CI never installs it and no test requires it (tests inject fake `opentelemetry` modules).

**Consequences**

- Positive: the ROADMAP tracing item is delivered with zero default behaviour change; operators can graduate from correlation-ID grep to real traces without re-instrumentation; the exporter choice stays with the deployment.
- Negative: spans in `none` mode are created but dropped (a small, measurable cost while enabled); a user registering a global provider must do so *before* enabling `MN_TRACING` (the engine only auto-registers when no global provider exists); version alignment of the optional SDK is bounded by the `<2` pin.

**References**

- `src/movie_narrator/tracing.py`, `src/movie_narrator/pipeline/runner.py`, `src/movie_narrator/cloud/worker.py`, `src/movie_narrator/utils/llm.py`, `src/movie_narrator/pipeline/tts.py`, `src/movie_narrator/utils/process.py`
- `docs/OBSERVABILITY.md` §4.2 (Distributed tracing)
- `.env.example` (v1.4.0 variables)
## ADR-015: Subtitle Delivery Modes & the Output Stability Promise

**Status:** Accepted
**Version:** Introduced in v1.4.1

**Context**

Subtitles have always been hard-burned (SRT → PIL images composited at render) with SRT sidecars written alongside — the ROADMAP's long-term item for selectable subtitle delivery was blocked on an output contract. v1.3.0's `deliverable_manifest.json` (schema_version + checksums) now enables one.

**Decision Drivers**

- Default (`burned`) must be byte-identical to v1.3.2; no contract surface changes.
- A delivery mode must never fail the render — degradation over abort.
- mov_text is an MP4-family codec: muxed is only representable in mp4-family containers.

**Considered Options**

- Always-muxed default (rejected: changes every existing render; mov_text player support varies; burn-in remains the only universally visible option).
- External subtitle-burn step after render (rejected: duplicates the composite layout — position/safe-area logic would drift between the two burn paths).
- Three-mode `subtitle_delivery` param with muxed→burned degradation (chosen).

**Decision Outcome**

`subtitle_delivery: burned | sidecar | muxed` (default dropped in merge, mirroring `timeline_export_backend`). sidecar/muxed skip all burn-in — including footage-fallback text cards, so no text is ever burned outside burned mode. muxed maps the mode-selected SRT as a soft `mov_text` track (`-metadata:s:s:0 language=`, ISO 639-2) and degrades to burned on missing SRT / non-mp4 container, recording `subtitle_delivery_used`, `subtitle_mux_language` and a fallback reason in metadata. STABILITY.md gains a narrow output promise: manifest schema v1 stable (additive-only), the default deliverable set compatible across 1.x patch/minor; checksums are informational, not contractual.

**Consequences**

- Positive: faster sidecar/muxed renders; players can toggle/style soft subtitles; the output promise is versioned and test-guarded.
- Negative: sidecar/muxed renders of footage-less jobs show only background cards; mov_text styling/language support is player-dependent (provided-as-is).

**References**

- `src/movie_narrator/workflow/schema.py`, `src/movie_narrator/pipeline/render.py`
- `docs/STABILITY.md` (Output Format Compatibility), `docs/METADATA_SCHEMA.md`, `examples/job.example.yaml`
## ADR-016: Premiere via FCP7 XML Interchange

**Status:** Accepted
**Version:** Recorded at v1.4.2

**Context**

The ROADMAP long-term item asks for timeline adapters beyond OTIO + Jianying; Adobe Premiere Pro is the dominant NLE for the target creators, but it imports neither `.otio` nor Jianying drafts.

**Decision Drivers**

- No new dependencies — the plugin must stay importable without extras (like the jianying path).
- The unified `Timeline` IR and step dispatch must stay untouched beyond a new backend arm.

**Considered Options**

- *Premiere SDK / `.prproj` format* (rejected): binary and undocumented; needs a host application and heavy bindings.
- *opentimelineio as a hard dependency* (rejected): drags an extra into a stdlib-only plugin; OTIO stays optional behind the `otio` backend.
- *FCP7 XML (`xmeml`) writer on the existing IR* (chosen): plain text Premiere imports natively (`File > Import`), stdlib `xml.etree.ElementTree`.

**Decision Outcome**

- New `premiere` backend (`premiere.py`): sequence rate from render metadata (default 24), video clipitems with frame in/out + file refs, text overlays as generatoritems, narration stem on an audio track; output `<movie>.xml`. Core whitelist `VALID_TIMELINE_EXPORT_BACKENDS` gains `"premiere"`; no contract or pipeline changes.

**Consequences**

- Positive: one-click Premiere hand-off with zero new dependencies; the IR absorbs a third backend without step-logic changes.
- Negative: FCP7 XML is a legacy interchange — generator text styling is minimal and the draft is a starting point, not a final conform.

**References**

- `examples/plugins/timeline_export/movie_narrator_timeline_export/premiere.py`, `docs/BEST_PRACTICES.md` (Timeline Export section)

## ADR-017: HDR/4K Pipeline — 10-bit + Color Metadata, CPU-only 10-bit Encode

**Status:** Accepted
**Version:** Introduced in v1.5.0

**Context**

The render emitted an unlabeled 8-bit `yuv420p` stream regardless of intent — no way to request 10-bit or HDR10, no color metadata on the output, and QA could not tell a 4K deliverable from a 1080p one.

**Decision Drivers**

- Defaults stay byte-identical: the 8-bit encode argv is unchanged; the 8/`sdr` defaults are dropped in merge (mirrors `subtitle_delivery`); no contract changes, no new dependencies.
- Findings must be verifiable: the pipeline records what it rendered (`render_pixel` metadata) and video QA cross-checks the deliverable against it.

**Considered Options**

- *GPU 10-bit capability probing now* (rejected): the supported H.264 GPU backends (NVENC / VAAPI / VideoToolbox) are 8-bit only — there is nothing to probe; HEVC main10 is future work. 10-bit renders force libx264 and record `10bit_gpu_unsupported`.
- *Full HDR mastering — tone mapping + mastering-display / MaxCLL / MaxFALL SEI* (rejected for v1.5.0): needs per-scene analysis and player-specific metadata; v1.5.0 ships tag-level HDR10 only.
- *Silently accepting any pix_fmt / color tags* (rejected): encoder fallbacks would silently degrade 10-bit jobs; a recorded plan + QA cross-check keeps the bit depth truthful.

**Decision Outcome**

`render_bit_depth` (8|10) + `render_color_space` (`sdr`|`hdr10`) job params: 10-bit = `yuv420p10le` + libx264 `high10`, CPU-only; hdr10 forces 10-bit (recorded note); sdr tags bt709 explicitly. Color tags are written at the STAGE-2 copy mux (libx264 drops encode-level color options). `render_pixel` lands in metadata.json; video QA verifies pix_fmt / color_transfer and 4K-class size exactness (>= 3840 wide or >= 2160 tall).

**Consequences**

- Positive: 4K / 10-bit / HDR10 become first-class, verifiable outputs; QA catches silent bit-depth or color regressions.
- Negative: true HDR mastering (tone mapping, SEI metadata) is explicitly out of scope; 10-bit encode is CPU-bound; players ignoring HDR tags show washed-out colors.

**References:** `src/movie_narrator/pipeline/render.py`, `src/movie_narrator/workflow/schema.py`, `src/movie_narrator/utils/video_qa.py`; `docs/METADATA_SCHEMA.md` (render_pixel), `docs/BEST_PRACTICES.md` (4K & 10-bit Rendering), `examples/job.example.yaml`
## ADR-018: Community Presets as Validated Data, not Code

**Status:** Accepted
**Version:** Recorded at v1.5.1

**Context**

With the preset style layer stable, ROADMAP unblocks sharing narration presets across installs. Installed presets may arrive from URLs and other authors, so they must never become an arbitrary-code execution vector.

**Decision Drivers**

- `mn presets install <url>` must never execute attacker-controlled code.
- Presets need the expressive range of whitelisted job parameters.
- Validation must reuse the existing single-source whitelists — no drift.

**Considered Options**

- *Executable preset packages* (rejected): runs code from untrusted sources; a sandbox would be a permanent liability.
- *Signed-only installs* (rejected): no PKI/trust infra to bootstrap; blocks ad-hoc sharing.
- *YAML data validated against the job-param whitelist* (chosen): data-only; reuses `workflow/load.py` (`_ALLOWED_TOP` + `JobConfig`); hash-recorded.

**Decision Outcome**

- `presets/community.py`: install/list/uninstall/load; storage `~/.movie-narrator/presets/<name>.yaml` + `registry.json` (source, sha256, metadata). https-only (256 KiB cap, 15 s timeout); the YAML's `preset.name` is the registry key; files re-validated on every load; `get_preset` resolves community presets only when no built-in matches.

**Consequences**

- Positive: the whitelist-reuse security argument — a community preset can only tune keys `job.yaml` already governs, so install-time validation can never drift from execution reality; zero-trust installs without signing infrastructure.
- Negative: presets cannot ship code or arbitrary prompt tags (tags stay built-in-only); custom behaviour must fit whitelisted keys.

**References**

- `src/movie_narrator/presets/community.py`, `src/movie_narrator/workflow/load.py`, `docs/TUTORIAL.md` (Community presets)

---

## ADR-019: Kubernetes Helm Chart & the Distributed-Workflow Pilot Deferral

**Status:** Accepted
**Version:** Recorded at v1.5.2

**Context**

Two ROADMAP items mature together in v1.5.2. The Community & SaaS item asks for "Helm chart / K8s deployment templates — for teams actually running on Kubernetes", while the Long-term item keeps a Temporal pilot (Celery as fallback) demand-gated on real operational metrics. v1.4's instrumentation (tracing spans, usage ledger, queue governance) now makes those triggers measurable.

**Decision Drivers**

- Deployment parity: the compose story (single image, `mn serve`, `/health` + `/ready` probes, mandatory API key on non-loopback binds) must transfer 1:1 to the chart.
- Honesty: CI runs no Kubernetes cluster; shipping an "enterprise-ready" claim without cluster validation would be false confidence.
- Single-node is the product positioning (ROADMAP); orchestration machinery must pay for itself in observed metrics, not anticipation.

**Considered Options**

- *Chart validated in CI against a live cluster* (rejected): no cluster in CI; kind/minikube e2e is future work once a maintainer commits to it.
- *Chart shipped without tests* (rejected): silent values/template drift would break installs invisibly.
- *Structural tests + naive render + documented validation gap* (chosen): `tests/test_v152_helm.py` parses Chart/values YAML, trips on any `.Values.*` drift between templates and values.yaml, and renders every template to parseable YAML via a helm-subset evaluator — no helm binary needed in CI; the remaining gap is stated in the chart, DEPLOYMENT.md, and NOTES.txt.
- *Start the Temporal/Celery pilot now* (rejected): no multi-node demand exists; adds durable-execution/broker ops before any trigger fires.
- *Adopt Celery for task routing only* (rejected): broker operations without the workflow semantics (durable timers, heartbeats, replayable history) that motivate the item.

**Decision Outcome**

1. The chart ships **structurally tested only** — no live-cluster or `helm lint` validation happened at authoring time; users are asked to run `helm template` / `helm lint` and review the manifests before production use.
2. The distributed-workflow pilot is **deferred behind measurable triggers**; single-node remains the supported topology.

Pilot triggers — start the Temporal pilot when ANY one holds sustained (all readable from v1.4 instrumentation):

- p95 task queue latency > 15 min over 7 days, or render step p95 > 30 min (tracing/task spans);
- orphan-recovery rate > 2%/week (tasks left RUNNING after a restart and cleaned by `mn cleanup`);
- duplicate provider-call rate > 5% (usage ledger: retries plus cache misses on identical inputs);
- ≥ 3 workers on ≥ 2 nodes for two consecutive weeks (deployment census);
- manual approval steps or durable timers requested by ≥ 2 integrators.

**Consequences**

- Positive: Kubernetes teams get a reviewed, test-anchored starting point; the deferral is falsifiable — any trigger can be checked from exported metrics rather than taste.
- Negative: first installers are the de facto validators (documented); the trigger list needs revisiting if the product positioning changes.

**References**

- `deploy/helm/movie-narrator/`, `tests/test_v152_helm.py`, `docs/DEPLOYMENT.md` (Kubernetes (Helm); Media cache), ROADMAP Long-term + Community & SaaS items

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
| ADR-011 | Licensing Red Lines and FFmpeg Bundling Policy | Accepted | v1.1.0 | Red-line list (Remotion/TypeTale code/scrapers/voice cloning); FFmpeg resolved via `ffmpeg_bin()` (imageio-ffmpeg preferred), no binary bundled into a distribution by the project |
| ADR-012 | Linear-compatible DAG Contract | Accepted | v1.3.0 | Steps declare `inputs`/`outputs`/`depends_on`; `pipeline/dag.py` validates linear compatibility (`topological_order` == linear order); runner stays linear |
| ADR-013 | Service and Product Semantics — Tenants, Plans, and Webhooks | Accepted | v1.3.1 | Tenant/principal labelling MVP (not isolation); plans enforced at API validation + worker injection (pipeline untouched); webhooks = JSONL delivery log + HMAC signing, redelivery deferred |
| ADR-014 | Opt-in OpenTelemetry Tracing | Accepted | v1.4.0 | `movie_narrator.tracing` span factories (task → step/provider/subprocess); `[otel]` extra = api+sdk only, OTLP not bundled; `MN_TRACING` off (default) = zero-overhead no-op; pre-registered global providers used unchanged |
| ADR-015 | Subtitle Delivery Modes & the Output Stability Promise | Accepted | v1.4.1 | `subtitle_delivery` burned/sidecar/muxed with muxed→burned degradation (soft mov_text track, never fails render); narrow STABILITY promise on manifest schema v1 + default deliverable set |
| ADR-016 | Premiere via FCP7 XML Interchange | Accepted | v1.4.2 | `timeline_export` plugin gains a `premiere` backend: stdlib FCP7 XML (`xmeml`) writer, Premiere imports natively; whitelist-only core change |
| ADR-017 | HDR/4K Pipeline: 10-bit + Color Metadata, CPU-only Encode | Accepted | v1.5.0 | `render_bit_depth` 8/10 + `render_color_space` sdr/hdr10: `yuv420p10le` / libx264 high10 (CPU-only, `10bit_gpu_unsupported` fallback), explicit bt709 / bt2020+smpte2084 mux tags, `render_pixel` metadata; video QA cross-checks pix_fmt / transfer and 4K-class exact size |
| ADR-018 | Community Presets as Validated Data, not Code | Accepted | v1.5.1 | `mn presets install` stores whitelisted YAML data (no code execution, ever), reusing the `job.yaml` whitelist + schema for validation; sha256-registered, re-validated on load; built-ins win |
| ADR-019 | Kubernetes Helm Chart & the Distributed-Workflow Pilot Deferral | Accepted | v1.5.2 | Chart ships structurally tested only (values/template drift tripwire + naive render; no live cluster / `helm lint` — documented); Temporal/Celery pilot deferred behind measurable triggers (queue latency, orphan recovery, duplicate provider calls, multi-node census) |
