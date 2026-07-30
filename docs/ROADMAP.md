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
| v0.4.x | TTS Abstraction & Infrastructure | TTS provider abstraction, config overhaul, FastAPI + React WebUI, render quality, L2 hand-test passed, match intelligence, effect portfolio, contract layer |
| v0.5.x | Ecosystem | Plugin API / SDK freeze / plugin discovery (entry_points) / VLM vision provider / narrative presets (3 styles) / scene filtering / WebUI split / narrative & audio quality / subtitle QA / holistic QA dashboard. `CONTRACT_VERSION` → `(0, 5, 1)` |
| v0.6.0 | Task Queue | Async job system, task persistence, cancellation, progress tracking, retry, CLI commands. `CONTRACT_VERSION` → `(0, 6, 0)` |
| v0.6.1 | Remote Inference | REST API server, remote task queue, worker daemon, artifact management, remote provider proxies, CLI commands. `CONTRACT_VERSION` → `(0, 6, 1)` |

---

## Current & Planned

### v0.6.x — Cloud (continued)

#### v0.6.2 — Distributed Rendering (planned)

- [ ] Video segment splitting — divide rendered timeline into N independent segments
- [ ] Worker distribution — dispatch segments to available worker nodes via task queue
- [ ] Parallel rendering — each worker renders its segment independently
- [ ] Result stitching — concatenate segment outputs into final video (ffmpeg concat demuxer)
- [ ] `mn render-distributed` CLI command — trigger distributed rendering jobs with `--workers` flag
- [ ] CONTRACT_VERSION → `(0, 6, 2)` — distributed rendering types exported via SDK

#### v0.6.3 — API Gateway & Authentication (planned)

- [ ] API key authentication — server-side `X-API-Key` header validation middleware (client-side header already sent by `RemoteTaskQueue` / `remote_provider`)
- [ ] JWT token support — issued tokens for authenticated sessions
- [ ] Multi-tenant isolation — tenant-scoped task storage and artifacts
- [ ] Rate limiting — per-tenant request throttling (token bucket algorithm)
- [ ] API versioning — `/api/v1/` prefix for stable endpoints
- [ ] CONTRACT_VERSION → `(0, 6, 3)` — auth middleware types exported via SDK

#### v0.6.4 — Cloud Storage & Artifact Management (planned)

- [ ] Storage backend abstraction — `StorageBackend` protocol (local / S3 / GCS)
- [ ] S3-compatible storage — artifact upload/download to S3 buckets
- [ ] Artifact lifecycle — TTL-based cleanup, storage quotas per tenant
- [ ] Presigned URLs — CDN-friendly direct download links
- [ ] Storage migration tool — local → cloud transfer utility
- [ ] CONTRACT_VERSION → `(0, 6, 4)` — storage backend types exported via SDK

### v0.7.x — Production Deployment

> **Goal**: Make the engine production-ready with containerization, observability, and fault tolerance.
>
> **Architecture migration note**: The current cloud layer uses Python stdlib `ThreadingHTTPServer` + `ThreadPoolExecutor`. Before v0.7.0, a decision is needed on whether to introduce FastAPI (replacing stdlib HTTP) and/or Redis/Celery (replacing `ThreadPoolExecutor`) for the K8s deployment target. This decision does not require a separate version bump — it is an implementation choice within v0.7.0.

#### v0.7.0 — Containerization & Orchestration (planned)

- [ ] Dockerfile — multi-stage build (builder + runtime), GPU support
- [ ] docker-compose.yml — local cluster (API + N workers + storage)
- [ ] Helm chart — K8s deployment templates (worker deployment, API deployment, storage)
- [ ] Worker auto-scaling — HPA based on queue depth
- [ ] ConfigMap/Secret management — env injection from K8s secrets
- [ ] Health/readiness probes — `/ready` endpoint + deep health check with dependency connectivity (`/health` already exists in v0.6.1 `TaskAPIServer`)

#### v0.7.1 — Observability & Monitoring (planned)

- [ ] Prometheus metrics — `/metrics` endpoint (task count, queue depth, render duration, error rate)
- [ ] Grafana dashboard — pre-built dashboard JSON templates
- [ ] Distributed tracing — OpenTelemetry spans for cross-node operations
- [ ] Structured logging aggregation — Loki/ELK-ready JSON logs with correlation IDs
- [ ] Alert rules — queue backlog, worker failure rate, render timeout

#### v0.7.2 — Reliability & Fault Tolerance (planned)

- [ ] Circuit breaker — for external APIs (LLM, TTS, TMDB, VLM)
- [ ] Dead letter queue — failed tasks moved to DLQ for inspection and replay
- [ ] Graceful shutdown — drain in-flight tasks before process exit
- [ ] Job checkpointing — save intermediate state for long-running tasks
- [ ] Retry policy framework — configurable per-step retry strategies (task-level retry with exponential backoff already exists in `cloud/worker.py`)
- [ ] Health check framework — dependency health (LLM, TTS, storage)

### v0.8.x — Advanced Features

> **Goal**: Add batch processing, advanced rendering, and multi-language support for power users.

#### v0.8.0 — Batch Processing & Workflow Orchestration (planned)

- [ ] Batch job submission — submit N movies in one API request
- [ ] Batch templates — series, playlists, themed collections
- [ ] Job dependencies — DAG-based task chaining (research → script → render)
- [ ] Scheduled jobs — cron-based recurring task submission
- [ ] Batch progress tracking — aggregate progress across sub-tasks
- [ ] CONTRACT_VERSION → `(0, 8, 0)` — batch workflow types exported via SDK

#### v0.8.1 — Advanced Rendering & Effects (planned)

- [ ] Scene transitions — crossfade, cut, wipe between segments
- [ ] Multi-track audio — narration + BGM + SFX mixing
- [ ] Picture-in-picture — overlay layouts for reaction/commentary style
- [ ] Text animations — kinetic typography for hooks and titles
- [ ] Custom branding — watermark, logo, intro/outro cards
- [ ] Render preset sharing format — shareable render configurations

#### v0.8.2 — Multi-language & Internationalization (planned)

- [ ] Full i18n pipeline — language-aware script generation and matching
- [ ] Localized TTS voices — per-language voice selection and fallback
- [ ] Cross-language clip matching — match clips regardless of audio language
- [ ] Subtitle translation chain — source → intermediate → target language
- [ ] Web UI localization — i18n support in movie-narrator-web

### v0.9.x — Stabilization & Polish

> **Goal**: Optimize performance, harden security, and complete documentation for v1.0 readiness.

#### v0.9.0 — Performance Optimization (planned)

- [ ] Render pipeline parallelization — concurrent segment encoding
- [ ] Memory optimization — streaming processing for large videos
- [ ] Cache strategy refinement — LLM response caching, scene embedding cache
- [ ] Worker warm-up — pre-load models on worker start
- [ ] Cold-start optimization — lazy initialization of heavy dependencies
- [ ] Benchmark suite — automated performance regression tests

#### v0.9.1 — Security Hardening (planned)

- [ ] OAuth2 authentication — full OAuth2 flow for web clients
- [ ] Input sanitization — comprehensive validation for all API inputs
- [ ] Tenant isolation hardening — storage path isolation, resource quotas
- [ ] Audit logging — all API operations logged for compliance
- [ ] Secret management — Vault / Sealed Secrets integration
- [ ] Security scanning — dependency audit, SAST in CI pipeline

#### v0.9.2 — Documentation & Developer Experience (planned)

- [ ] OpenAPI/Swagger spec — auto-generated API documentation
- [ ] Deployment guides — Docker, K8s, bare metal tutorials
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
