# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- Multi-language subtitle support (v0.3): `--subtitle-lang` / `--subtitle-mode` plus YAML `subtitle_lang` / `subtitle_mode` / `steps.translate` / `params.translate_*`
- New soft step `translate_subtitles` (LLM provider, pluggable). Failure policy: retry-then-soft-degrade (translate provider returns the original text on chunk failure; warnings surfaced via `metadata.warnings`)
- Three-file subtitle output: `subtitle.srt` (original, invariant) + `subtitle.<lang>.srt` (translated) + `subtitle.bilingual.srt` (original + LF + translation per cue)
- `render_subtitle_path` field picks the overlay track per `subtitle_mode`; `subtitle_path` remains original-only for backward compatibility
- `content_language` / `subtitle_mode` / `translate_provider` / `subtitle_paths` / `warnings` exported to `metadata.json`
- `JobConfigError` raised when `subtitle_mode ∈ {translated, bilingual}` is set without `subtitle_lang`
- Tests: `tests/test_translate.py` covering disabled/skipped/empty/provider-unknown/CI-passthrough/length-mismatch/blank-item paths; subtitle SRT tests extended for translated + bilingual file outputs and render_subtitle_path resolution

## [0.3.1] - 2026-07-13

### Added
- 简化 Gitflow 工作流：feature + hotfix 双分支模型
- `importlib.metadata` 动态版本读取（消除双写失配）
- TestPyPI 支持（tag 带 `-test` 后缀时自动发测试源）
- CI 自动 PyPI 发布 + GitHub Release 创建

### Changed
- CI 拆分为 ci.yml（PR/push 测试）+ publish.yml（tag 触发发布）

## [0.3.0] - 2026-07-05

### Added
- `mn create --config` 支持 YAML 配置文件
- workflow_steps 和 params 元数据注入
- 控制台日志重构设计

[Unreleased]: https://github.com/zcbacxc/movie-narrator/compare/v0.3.1...HEAD
0.3.1: https://github.com/zcbacxc/movie-narrator/compare/v0.3.0...v0.3.1
0.3.0: https://github.com/zcbacxc/movie-narrator/compare/v0.2.2...v0.3.0
