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
| v1.2.x | Render subprocess governance / atomic artifact publication / checkpoint fingerprint / orphan task recovery / non-loopback auth enforcement / task admission limits / structured step logging / execution manifest / generation dry-run / provider retry governance / TTS cache statistics / portrait QA fix |
| v1.2.1 | Persistent GPU capability cache / encoder fallback-reason reporting (incl. runtime GPU→CPU fallback audit) |
| v1.3.0 | Workflow semantics — selective rerun (`mn rerun`) / linear-compatible DAG contract / versioned deliverable manifest |
| v1.3.1 | Service semantics — tenant/principal foundation / plans & entitlements / webhook MVP / dashboard contract |
| v1.3.2 | Ecosystem & efficiency — reference media input contract / timeline export hardening / prompt-script cache / render admission / encoder benchmark |
| v1.4.0 | Tracing & queue governance — opt-in OpenTelemetry tracing / GPU-vs-CPU queue split / webhook redelivery / plan artifact TTL |
| v1.4.1 | Output experience — subtitle delivery burned/sidecar/muxed / versioned output-format stability promise |

`CONTRACT_VERSION` (current): `(1, 3, 0)` (bumped in v1.4.0 — tracing exports; unchanged in v1.4.1)

---

## Current & Planned

> **Planning principle**: Alternate user-visible improvements with infrastructure work. v1.0 target users: local CLI creators + optional single-tenant service deployment. Engine positioning for the 1.x series: a reliable single-node / lightweight-service video engine — linear pipeline + resumable checkpoints + explicit artifact contract + resource-bounded rendering. Distributed workflow engines (Temporal / Celery) stay deferred until measured queue latency, render duration, recovery success rate, and duplicate provider calls justify the migration cost.

### v1.3 — Workflow Semantics & Product Foundation (shipped as v1.3.0–v1.3.2)

> Theme: prepare selective rerun and product-grade service semantics while keeping linear execution. Expected `CONTRACT_VERSION` MINOR bump (new contract exports).

#### Carried over from v1.2 (deferred items)

- OpenTelemetry tracing — v1.2 shipped structured step logging instead; real span-based tracing (task → step/provider/subprocess) remains open. Deferred beyond v1.3: structured step logs + execution/deliverable manifests cover the near-term need; revisit when an external tracing backend is a concrete requirement.
- Hardware encoding productization — v1.2 unified ffmpeg detection; capability cache and fallback-reason reporting shipped as a v1.2.1 patch; benchmark tooling shipped in v1.3.2 (`benchmarks/encoder_benchmark.py`).
- Prompt/script cache — keyed by normalized topic / style / language / prompt-template version / model, with hit-source attribution. **(shipped in v1.3.2)**
- Resource-aware admission — temp-disk / resolution/duration checks shipped in v1.3.2 (`MN_ADMISSION_DISK_CHECK`, opt-in); GPU-vs-CPU queue separation deferred (entitlements GPU flag already gates access; revisit with measured multi-task contention).
- Provider idempotency keys — v1.2 decided against strong idempotency for non-deterministic LLM output (documented); stays deferred — revisit only if duplicate-billing becomes measurable.

#### v1.3 scope

- Selective rerun — rerun from any registered step via `mn rerun` with downstream invalidation, no re-research. **(shipped in v1.3.0)**
- Linear-compatible DAG contract — explicit step inputs / outputs / artifact keys / dependency declarations with a linear adapter (no parallelism yet). **(shipped in v1.3.0)**
- Versioned deliverable manifest — `deliverable_manifest.json` declaring MP4 / audio / SRT / script / clips / timeline / checksums / compatibility version. **(shipped in v1.3.0)**
- Dashboard contract — stable manifest / API surface for the external `movie-narrator-web` UI. **(shipped in v1.3.1)**
- Principal & tenant foundation — tenant/principal propagated through tasks, artifacts, and audit records with per-tenant artifact scoping (full lifecycle/row isolation long-term). **(shipped in v1.3.1)**
- Plans & entitlements — max duration / resolution / artifact bytes / watermark / GPU-encoder permission / artifact TTL, enforced at submission + worker injection. **(shipped in v1.3.1)**
- Webhook MVP — signed events, delivery retry, idempotent event IDs, delivery records (replaces pure polling). **(shipped in v1.3.1)**
- Timeline export hardening — `timeline_export_backend` accepted by the core whitelist with integration tests. **(shipped in v1.3.2)**
- Reference media input contract — `reference_media[]` entries with video/image kind, usage, license source, and style features; image-reference style hints via the VLM provider. **(shipped in v1.3.2)**

### v1.4 — Tracing, Output Experience & Ecosystem (next)

> Theme: close out the v1.3 deferrals whose prerequisites are now in place and deepen the output/product surface. Shipped as three incremental releases.

#### v1.4.0 — Tracing & Queue Governance **(shipped)**

- Opt-in OpenTelemetry tracing — `task → step/provider/subprocess` spans via the new `movie_narrator.tracing` module; `[otel]` extra (api+sdk only), `MN_TRACING`, `MN_TRACING_EXPORTER=none|console`; OTLP users register their own exporter (auto-detected). (ADR-014)
- GPU-vs-CPU queue separation — `MN_WORKER_QUEUES=split` with a dedicated GPU pool routed by plan × encoder hint; default single-pool unchanged.
- Webhook operations — `GET /api/v1/webhooks/deliveries` and `POST /api/v1/webhooks/redeliver/{event_id}` (same idempotent event id, re-signed).
- Plan artifact TTL wired — plan `artifact_ttl_hours` narrows the lifecycle policy per artifact (effective = min), recorded as `artifact_retention` in `metadata.json`.

#### v1.4.1 — Output Experience **(shipped)**

- Subtitle delivery `burned | sidecar | muxed` — muxed embeds a `mov_text` soft track with ISO-639-2 language normalization; graceful burned fallback with a recorded reason. (ADR-015 planned)
- Output format stability promise — `deliverable_manifest.json` schema v1 + the default deliverable set compatibility-protected across 1.x (STABILITY.md).

#### v1.4.2 — Ecosystem (planned — next)

- Premiere timeline adapter — FCP7-XML (`xmeml`) export in the timeline_export plugin; `timeline_export_backend=premiere`. (ADR-016 planned)
- CLI ergonomics — `mn benchmark` and `mn rerun --dry-run`.

### Long-term — Architecture Outgrowths (demand-driven)

Commitments are made only against real metrics (queue latency, render duration, recovery success rate, duplicate provider calls, cache hit rate, disk/GPU utilization):

- Temporal pilot (Celery as fallback) — only when multi-node workers, durable timers, heartbeats, manual approval steps, or replayable execution history become actual requirements.
- HDR / 4K pipeline — 10-bit pix_fmt, profile, color primaries/transfer/mastering metadata, VRAM budgeting, and a 4K QA baseline (not just a `video_sizes` bump).
- Optional soft subtitles — `subtitle_delivery=burned|sidecar|muxed` with `mov_text` compatibility testing.
- Extended timeline adapters — Premiere XML beyond the current OTIO + Jianying support.
- Media cache pool — content-hash + TTL + license-metadata cache for future external stock-footage integration.

### Community & SaaS Ecosystem (demand-driven)

The following remain out of the v1.3 scope and will be prioritized only when community feedback and enterprise demand materialize:

- Community preset sharing — `mn presets install <url>` mechanism (depends on stable API after contract freeze)
- Helm chart / K8s deployment templates — for teams actually running on Kubernetes
- Full multi-tenant isolation — tenant-scoped task storage and artifacts (foundation laid in v1.3)
- OAuth2 authentication — full auth flow for web clients (only if SaaS demand materializes)
- Token bucket rate limiting — per-tenant request throttling (only if multi-user deployment demand materializes)
