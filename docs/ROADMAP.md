[![English](https://img.shields.io/badge/English-Roadmap-blue)](ROADMAP.md)
[![简体中文](https://img.shields.io/badge/简体中文-路线图-green)](ROADMAP.zh-CN.md)

# Roadmap

> Per-release details in [CHANGELOG.md](../CHANGELOG.md). Configuration reference in [`.env.example`](../.env.example) and [`job.example.yaml`](../examples/job.example.yaml).

## Completed

| Version | Theme | Summary |
|---------|-------|---------|
| v0.1.x | Core Pipeline | CLI, LLM script, Edge-TTS, SRT, MoviePy rendering, TTS caching, CI |
| v0.2.x | Scene & Media | Research agent, WhisperX alignment, scene detection, clip matching, BGM, graceful degradation |
| v0.3.x | Platform & Workflow | YAML job config, multi-language subtitles, Gradio WebUI (superseded) |
| v0.4.x | TTS Abstraction & Infrastructure | TTS provider abstraction, config overhaul, FastAPI + React WebUI, render quality, manual QA passed, match intelligence, effect portfolio, contract layer |
| v0.5.x | Ecosystem | Plugin API / SDK freeze / plugin discovery (entry_points) / VLM vision provider / narrative presets (3 styles) / scene filtering / WebUI split / narrative & audio quality / subtitle QA / holistic QA dashboard. `CONTRACT_VERSION` → `(0, 5, 1)` |
| v0.6.0 | Task Queue | Async job system, task persistence, cancellation, progress tracking, retry, CLI commands. `CONTRACT_VERSION` → `(0, 6, 0)` |
| v0.6.1 | Remote Inference | REST API server, remote task queue, worker daemon, artifact management, remote provider proxies, CLI commands. `CONTRACT_VERSION` → `(0, 6, 1)` |
| v0.7.x | Output Experience | GPU encoding, cost tracking, preview mode, scene transitions, text animation, multi-track audio, security hardening. `CONTRACT_VERSION` → `(0, 7, 2)` |
| v0.8.0 | Service Deployment Basics | API key authentication (X-API-Key middleware), format→video_format rename, render template system (preset styling with safe areas), exception narrowing (45 broad catches → specific types), ruff/mypy lint toolchain + pytest-timeout, queue deadlock fix. `CONTRACT_VERSION` → `(0, 8, 0)` |

---

## Current & Planned

> **Planning principle**: Alternate user-visible improvements with infrastructure work. v1.0 target users: local CLI creators + optional single-tenant service deployment.

### v0.6.x Planning Revision

The original v0.6.2–v0.6.4 plan (distributed rendering, API gateway & auth, cloud storage) has been re-evaluated:

- **Distributed rendering** — demoted to a conditional feature in v0.9.0 (trigger: single-machine render > 10 minutes with multiple nodes available)
- **API key auth + S3 storage** — merged into v0.8.0 service deployment basics
- **JWT / multi-tenant isolation / token bucket rate limiting** — deferred to post-v1.0, pending community demand

---

### v0.8.0 — Service Deployment Basics (partially delivered)

> **Goal**: Deployable as a reliable single-tenant service, without over-engineering.

**Delivered in v0.8.0:**

- [x] API key authentication — server-side `X-API-Key` validation middleware (client-side header already sent by `RemoteTaskQueue` / `remote_provider`)
- [x] CONTRACT_VERSION → `(0, 8, 0)` — service deployment types exported via SDK
- [x] Render template system — preset-based styling with safe areas, watermark, disclaimer
- [x] Exception narrowing — broad `except Exception` blocks narrowed to specific types
- [x] Lint toolchain — ruff BLE+A rules, mypy, pytest-timeout CI protection

**Deferred to v0.8.x point releases:**

- [ ] Dockerfile — multi-stage build (builder + runtime), GPU support
- [ ] docker-compose.yml — local cluster (API + N workers + storage)
- [x] Storage backend abstraction — `StorageBackend` protocol (local / S3) (v0.8.3)
- [x] Artifact lifecycle — TTL-based cleanup (v0.8.3)
- [x] Structured logging — JSON format with correlation IDs (v0.8.1)
- [x] Prometheus metrics — `/metrics` endpoint (task count, queue depth, render duration, error rate) (v0.8.1)
- [x] Health/readiness probes — `/ready` endpoint + deep health check with dependency connectivity (`/health` already exists in v0.6.1) (v0.8.2)
- [x] OpenAPI spec — auto-generated API documentation (v0.8.2)

### v0.9.0 — Reliability & Batch (planned)

> **Goal**: Long-running tasks don't lose progress; batch video production has scheduling.

- [ ] Circuit breaker — for external APIs (LLM, TTS, TMDB, VLM)
- [ ] Task checkpointing — save intermediate state for long renders, support resume from checkpoint
- [ ] Graceful shutdown — drain in-flight tasks before process exit
- [ ] Retry policy framework — configurable per-step retry strategies (task-level exponential backoff already exists in `cloud/worker.py`)
- [ ] Batch job submission — submit N movies in one API request
- [ ] Scheduled jobs — cron-based recurring task submission
- [ ] Batch progress tracking — aggregate progress across sub-tasks
- [ ] Dead letter queue — failed tasks moved to DLQ for inspection and replay
- [ ] Distributed rendering (conditional) — trigger: single-machine render > 10 minutes with multiple nodes; builds on v0.8.0 containerization
- [ ] CONTRACT_VERSION → `(0, 9, 0)` — reliability and batch types exported via SDK

#### v0.9.1 — Polish & Completeness (planned)

> **Goal**: Security, internationalization, and documentation fully ready for v1.0.

- [ ] Input sanitization — comprehensive validation for all API inputs
- [ ] Security scanning — dependency audit, SAST in CI pipeline
- [ ] Full i18n pipeline — language-aware script generation and matching
- [ ] Localized TTS voices — per-language voice selection with fallback
- [ ] Web UI localization — i18n support in movie-narrator-web
- [ ] Tutorial series — getting started → advanced usage walkthroughs
- [ ] Architecture Decision Records (ADRs) — key design decisions documented
- [ ] Migration guide — v0.x → v1.0 upgrade path
- [ ] Integration test suite — cross-module and end-to-end tests
- [ ] Test coverage gate — >95% coverage enforced in CI

### v1.0.0 — Stable Release

> **Goal**: API stability guarantee, production-ready, feature-complete for target use cases.

- [ ] **CONTRACT_VERSION freeze** → `(1, 0, 0)` — API surface declared stable
- [ ] **API stability guarantee** — no breaking changes in v1.x without v2.0
- [ ] **Final documentation pass** — all docs reviewed and up-to-date
- [ ] **Release announcement** — changelog, migration guide, blog post
- [ ] **Long-term support policy** — v1.x maintenance branch and backport rules

---

### Post-v1.0 — Community Ecosystem (demand-driven)

The following features are out of scope for v1.0 and will be prioritized based on community feedback and enterprise demand:

- Community preset sharing — `mn presets install <url>` mechanism (depends on stable API after contract freeze)
- Helm chart / K8s deployment templates — for teams actually running on Kubernetes
- Multi-tenant isolation — tenant-scoped task storage and artifacts (only if multi-user deployment demand materializes)
- OAuth2 authentication — full auth flow for web clients (only if SaaS demand materializes)
- Token bucket rate limiting — per-tenant request throttling (only if multi-user deployment demand materializes)
