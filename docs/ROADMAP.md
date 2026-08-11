[![English](https://img.shields.io/badge/English-Roadmap-blue)](ROADMAP.md)
[![简体中文](https://img.shields.io/badge/简体中文-路线图-green)](ROADMAP.zh-CN.md)

# Roadmap

> Per-release details in [CHANGELOG.md](../CHANGELOG.md). Configuration reference in [`.env.example`](../.env.example) and [`job.example.yaml`](../examples/job.example.yaml).

## Completed

| Version | Key Themes |
|---------|-------------|
| v0.1.x | Core Pipeline / CLI / LLM script / Edge-TTS / SRT / MoviePy rendering / TTS cache / CI |
| v0.2.x | Scene & Media / research agent / WhisperX alignment / scene detection / clip matching / BGM / graceful degradation |
| v0.3.x | Platform & Workflow / YAML job config / multi-language subtitles / Gradio WebUI (superseded) |
| v0.4.x | TTS Abstraction & Infrastructure / TTS provider abstraction / config overhaul / FastAPI + React WebUI / render quality / match intelligence / effect portfolio / contract layer |
| v0.5.x | Ecosystem / Plugin API / SDK freeze / plugin discovery / VLM vision / narrative presets / scene filtering / WebUI split / QA dashboard |
| v0.6.x | Task Queue & Remote Inference / async jobs / persistence / cancel / progress / retry / REST API server / worker daemon / artifact mgmt / remote proxies |
| v0.7.x | Output Experience / GPU encoding / cost tracking / preview mode / scene transitions / text animation / multi-track audio / security hardening |
| v0.8.x | Service Deployment Basics / API key auth / video_format rename / render templates / exception narrowing / lint toolchain / queue deadlock fix |
| v0.9.x | Reliability / Batch / Docs / circuit breaker / checkpoints / graceful shutdown / retry policy / batch jobs / cron / DLQ / distributed rendering / sanitization / SAST / coverage gate / integration tests / i18n / voice map / tutorial / ADR / migration guide |
| v1.0.x | **Stable Release** / API freeze / stability guarantees / release checklist / final documentation pass / long-term support policy |
| v1.1.x | FunASR Chinese ASR / `mn doctor` / QA slideshow & black-frame detection / EmotionTrack / SQLite task store / visual-embedding match skeleton / timeline_export plugin / compliance (edge-tts + TMDB) / 90% coverage gate |

`CONTRACT_VERSION` (current): `(1, 0, 0)` (unchanged in v1.1 — no new contract exports)

---

## Current & Planned

> **Planning principle**: Alternate user-visible improvements with infrastructure work. v1.0 target users: local CLI creators + optional single-tenant service deployment.

### v1.0.0 — Stable Release

> **Goal**: API stability guarantee, production-ready, feature-complete for target use cases.
> **Status**: Release Candidate phase — see [Release Checklist](RELEASE_CHECKLIST.md).

- [x] **CONTRACT_VERSION freeze** → `(1, 0, 0)` — API surface declared stable
- [x] **API stability guarantee** — no breaking changes in v1.x without v2.0; documented in [STABILITY.md](STABILITY.md)
- [x] **Final documentation pass** — all docs reviewed and up-to-date
- [ ] **Release announcement** — changelog, migration guide, blog post
- [x] **Long-term support policy** — v1.x maintenance branch and backport rules (see [STABILITY.md](STABILITY.md#upgrade-guarantees))
- [x] **Release checklist** — Definition of Done for v1.0 published

---

### v1.1.0 — Community & Polish

> **Goal**: Community-driven improvements, plugin ecosystem growth, quality-of-life features.
> **Status**: Released (v1.1.0, 2026-08-10).
> **Note**: The items below were implemented and released in v1.1. See [CHANGELOG.md](../CHANGELOG.md) for per-item detail.

- [x] **Final-video QA completion** — slideshow-risk score + black-frame detection in `deliverable_qa`/`video_qa`; shared `ffmpeg_bin()` fallback
- [x] **AI Agent Skill distribution surface** — `docs/skill/SKILL.md` CLI capability listing + environment diagnostics
- [x] **`mn doctor` environment precheck** — environment pre-check command with `probe()` three-state distinction (not-installed / dep-missing / ok)
- [x] **EmotionTrack unified modeling** — unified `EmotionTrack` value object converging prosody/bgm/tts emotion consumption
- [x] **FunASR Chinese ASR optional backend** — `providers/asr/funasr.py` three-backend alignment chain (whisperx → faster-whisper → funasr)
- [x] **sidechaincompress ducking optional backend** — envelope/sidechain dispatch with automatic fallback
- [x] **SQLite task storage (WAL)** — idempotent JSON→SQLite migration, contract-compatible
- [x] **Visual-embedding match backend (phase 1)** — pure-FFmpeg visual feature skeleton, default-off (phase 2 decision: not doing — covered by VLMCaptioner)
- [x] **timeline_export plugin** — Jianying + OTIO timeline export plugin (out-of-tree), CI-wired via `plugin-timeline-export` job
- [x] **Compliance completion** — edge-tts commercial warning + TMDB attribution in `research.json`

> **Released in v1.1.0 (2026-08-10).** CONTRACT_VERSION remains `(1, 0, 0)` — no new contract exports.

---

### Post-v1.0 — Community Ecosystem (demand-driven)

The following features are out of scope for v1.0 and will be prioritized based on community feedback and enterprise demand:

- Community preset sharing — `mn presets install <url>` mechanism (depends on stable API after contract freeze)
- Helm chart / K8s deployment templates — for teams actually running on Kubernetes
- Multi-tenant isolation — tenant-scoped task storage and artifacts (only if multi-user deployment demand materializes)
- OAuth2 authentication — full auth flow for web clients (only if SaaS demand materializes)
- Token bucket rate limiting — per-tenant request throttling (only if multi-user deployment demand materializes)
