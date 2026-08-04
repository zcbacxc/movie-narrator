[![English](https://img.shields.io/badge/English-Roadmap-blue)](ROADMAP.md)
[![简体中文](https://img.shields.io/badge/简体中文-路线图-green)](ROADMAP.zh-CN.md)

# Roadmap

> Per-release details in [CHANGELOG.md](../CHANGELOG.md). Configuration reference in [`.env.example`](../.env.example) and [`job.example.yaml`](../examples/job.example.yaml).

## Completed

| Version | Theme | Summary |
|---------|-------|---------|
| v0.1.x | Core Pipeline | CLI / LLM script / Edge-TTS / SRT / MoviePy rendering / TTS cache / CI |
| v0.2.x | Scene & Media | research agent / WhisperX alignment / scene detection / clip matching / BGM / graceful degradation |
| v0.3.x | Platform & Workflow | YAML job config / multi-language subtitles / Gradio WebUI (superseded) |
| v0.4.x | TTS Abstraction & Infrastructure | TTS provider abstraction / config overhaul / FastAPI + React WebUI / render quality / match intelligence / effect portfolio / contract layer |
| v0.5.x | Ecosystem | Plugin API / SDK freeze / plugin discovery / VLM vision / narrative presets / scene filtering / WebUI split / QA dashboard. `CONTRACT_VERSION` → `(0, 5, 1)` |
| v0.6.x | Task Queue & Remote Inference | async jobs / persistence / cancel / progress / retry / REST API server / worker daemon / artifact mgmt / remote proxies. `CONTRACT_VERSION` → `(0, 6, 1)` |
| v0.7.x | Output Experience | GPU encoding / cost tracking / preview mode / scene transitions / text animation / multi-track audio / security hardening. `CONTRACT_VERSION` → `(0, 7, 2)` |
| v0.8.x | Service Deployment Basics | API key auth / video_format rename / render templates / exception narrowing / lint toolchain / queue deadlock fix. `CONTRACT_VERSION` → `(0, 8, 0)` |
| v0.9.x | Reliability, Batch & Docs | circuit breaker / checkpoints / graceful shutdown / retry policy / batch jobs / cron / DLQ / distributed rendering / sanitization / SAST / coverage gate / integration tests / i18n / voice map / tutorial / ADR / migration guide. `CONTRACT_VERSION` → `(0, 9, 5)` |

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
