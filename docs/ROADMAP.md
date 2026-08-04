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
| v0.6.x | Task Queue & Remote Inference | Async job system, task persistence, cancellation, progress tracking, retry; REST API server, remote task queue, worker daemon, artifact management, remote provider proxies. `CONTRACT_VERSION` → `(0, 6, 1)` |
| v0.7.x | Output Experience | GPU encoding, cost tracking, preview mode, scene transitions, text animation, multi-track audio, security hardening. `CONTRACT_VERSION` → `(0, 7, 2)` |
| v0.8.x | Service Deployment Basics | API key authentication (X-API-Key middleware), format→video_format rename, render template system (preset styling with safe areas), exception narrowing (45 broad catches → specific types), ruff/mypy lint toolchain + pytest-timeout, queue deadlock fix. `CONTRACT_VERSION` → `(0, 8, 0)` |
| v0.9.x | Reliability, Batch & Docs | Circuit breaker + retry policy framework (v0.9.1), task checkpointing + graceful shutdown (v0.9.2), batch job submission + cron scheduled jobs (v0.9.3), dead-letter queue + conditional distributed rendering (v0.9.4), input sanitization + security scanning + integration tests + coverage gate (v0.9.5), i18n pipeline + localized TTS voices + Web UI localization (v0.9.6), documentation & governance — tutorial series, ADR-001..010, migration guide (v0.9.7). `CONTRACT_VERSION` → `(0, 9, 5)` |

---

## Current & Planned

> **Planning principle**: Alternate user-visible improvements with infrastructure work. v1.0 target users: local CLI creators + optional single-tenant service deployment.

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
