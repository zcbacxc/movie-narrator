# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [CalVer](https://calver.org/) with format `YYYY.MM.DD[.N]`.

## [Unreleased]

### Added
- 简化 Gitflow 工作流：feature + hotfix 双分支模型
- `importlib.metadata` 动态版本读取（消除双写失配）
- TestPyPI 支持（tag 带 `-test` 后缀时自动发测试源）

### Changed
- CI 拆分为 ci.yml（PR/push 测试）+ publish.yml（tag 触发发布）
- 版本号采用 CalVer (`YYYY.MM.DD[.N]`)

## [2026.7.13] - 2026-07-13

### Added
- `mn create --config` 支持 YAML 配置文件
- workflow_steps 和 params 元数据注入
- 控制台日志重构设计
- CI 自动 PyPI 发布 + GitHub Release 创建

## [0.3.0] - 2026-07-05

### Added
- (prior release)

[Unreleased]: https://github.com/zcbacxc/movie-narrator/compare/v2026.7.13...HEAD
2026.7.13: https://github.com/zcbacxc/movie-narrator/compare/v0.3.0...v2026.7.13
