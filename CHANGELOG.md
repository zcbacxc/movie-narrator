# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.3.4] - 2026-07-13

### Added
- Multi-language subtitle support (v0.3): `--subtitle-lang` / `--subtitle-mode` plus YAML `subtitle_lang` / `subtitle_mode` / `steps.translate` / `params.translate_*`
- New soft step `translate_subtitles` (LLM provider, pluggable). Failure policy: retry-then-soft-degrade (translate provider returns the original text on chunk failure; warnings surfaced via `metadata.warnings`)
- Three-file subtitle output: `subtitle.srt` (original, invariant) + `subtitle.<lang>.srt` (translated) + `subtitle.bilingual.srt` (original + LF + translation per cue)
- `render_subtitle_path` field picks the overlay track per `subtitle_mode`; `subtitle_path` remains original-only for backward compatibility
- `content_language` / `subtitle_mode` / `translate_provider` / `subtitle_paths` / `warnings` exported to `metadata.json`
- `JobConfigError` raised when `subtitle_mode ∈ {translated, bilingual}` is set without `subtitle_lang`
- Tests: `tests/test_translate.py` covering disabled/skipped/empty/provider-unknown/CI-passthrough/length-mismatch/blank-item paths; subtitle SRT tests extended for translated + bilingual file outputs and render_subtitle_path resolution

## [Unreleased]

## [0.3.3] - 2026-07-13

### Changed
- 移除 `render.py` 中重复的自定义 tqdm 进度条；MoviePy 内部 `logger="bar"` 接管进度展示（commit `7980ccd`）

## [0.3.2] - 2026-07-13

### Added
- `workflow_dispatch` 手动触发 CI（GitHub Actions UI 可手动跑测试，无需 push）

### Changed
- 控制台日志重构补完：`workflow_steps` 键统一为 step 函数名（`research_plot` / `align_audio` / `detect_scenes` / `match_clips` / `mix_bgm` / `export_clips`）
- `console.done()` 取代裸 `print(f"\nDone in {elapsed}s")`（commit `36436d8`）

## [0.3.1] - 2026-07-13

### Added
- 简化 Gitflow 工作流：feature + hotfix 双分支模型（无 develop / release 长期分支）
- `importlib.metadata` 动态版本读取（消除双写失配）
- TestPyPI 支持（tag 带 `-test` 后缀时自动发测试源）
- CI 自动 PyPI 发布 + GitHub Release 创建
- 控制台日志重构落地：`utils/console.py` + `utils/log.py` + `utils/retention.py` 引入 Console Protocol / AppLogger / `build_console()`；`models.py` 新增 `StepResult` / `StepState` / `Services`
- `runner.py` 统一状态渲染：每步只设置 `ctx.step_state`，runner 负责 console 输出；soft/hard try/except 分叉；`STATUS_FIELD_FOR_STEP` 提升为模块级常量
- MoviePy 临时音频路由到 `output/.tmp/`（避免污染源码目录）
- 14 个 pipeline step 全部迁移：裸 `print()` → `ctx.services.console`；`match.py` 补 try/except
- CI smoke test（`mn create --config` YAML 配置样例测试）

### Changed
- CI 拆分为 ci.yml（PR/push 测试）+ publish.yml（tag 触发发布）

### Fixed
- `runner.py` 补充缺失的 `Dict, Any` import
- 修复 ci.yml 在插入 smoke test 后的 YAML 语法错误

## [0.3.0] - 2026-07-05

### Added
- `mn create --config` 支持 YAML 配置文件
- workflow_steps 和 params 元数据注入
- 控制台日志重构设计

[Unreleased]: https://github.com/zcbacxc/movie-narrator/compare/v0.3.3...HEAD
0.3.3: https://github.com/zcbacxc/movie-narrator/compare/v0.3.2...v0.3.3
0.3.2: https://github.com/zcbacxc/movie-narrator/compare/v0.3.1...v0.3.2
0.3.1: https://github.com/zcbacxc/movie-narrator/compare/v0.3.0...v0.3.1
0.3.0: https://github.com/zcbacxc/movie-narrator/compare/v0.2.2...v0.3.0
