[![English](https://img.shields.io/badge/English-AI_Guide-blue)](AI_GUIDE.md)
[![简体中文](https://img.shields.io/badge/简体中文-AI指南-green)](AI_GUIDE.zh-CN.md)

# AI Coding Assistant Guide

> This page is an English navigation index for AI coding tools (Claude Code, Codex, Cursor, Copilot, etc.). All content is maintained by the corresponding authoritative documents; this page only provides quick links.

## Quick Start

| Topic | Document |
|------|------|
| Project overview and installation | [README](../README.md) |
| 5-minute quickstart | [QUICKSTART.md](QUICKSTART.md) |
| From-zero-to-advanced walkthrough | [TUTORIAL.md](TUTORIAL.md) |
| LLM provider configuration | [LLM_PROVIDERS.md](LLM_PROVIDERS.md) |

## Architecture & Design

| Topic | Document |
|------|------|
| System architecture and component relationships | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Architecture decision records | [ADR.md](ADR.md) |
| Metadata field reference | [METADATA_SCHEMA.md](METADATA_SCHEMA.md) |
| 16-step pipeline and step responsibilities | [ARCHITECTURE.md § Pipeline Overview](ARCHITECTURE.md#pipeline-overview) |

## Plugins & Extensions

| Topic | Document |
|------|------|
| Complete plugin development guide | [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) |
| Packaging and publishing | [PACKAGING.md](PACKAGING.md) |
| SDK API reference | [sdk/](sdk/contract.md) |

## Contributing & Releases

| Topic | Document |
|------|------|
| Contributing guide | [CONTRIBUTING.md](CONTRIBUTING.md) |
| Version roadmap | [ROADMAP.md](ROADMAP.md) |
| v0.x → v1.0 upgrade path | [MIGRATION.md](MIGRATION.md) |

## CLI Command Cheat Sheet

```bash
mn create --movie "Inception" --style "action-comedy" --duration 60
mn create --config examples/job.example.yaml
CI=1 mn create --movie "CI-Test" --duration 10    # offline smoke test
mn version                                         # show version
mn --help                                          # full command list
```

> Task queue commands (`mn submit` / `mn serve` / `mn status`, etc.) — see `mn --help`.
