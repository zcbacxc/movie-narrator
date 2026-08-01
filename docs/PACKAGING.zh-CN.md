[![English](https://img.shields.io/badge/English-Packaging-blue)](PACKAGING.md)
[![简体中文](https://img.shields.io/badge/简体中文-打包-green)](PACKAGING.zh-CN.md)

# 打包指南

本文件介绍 movie-narrator 生态系统的打包约定，包括
核心引擎 (Core Engine)、Web 包 (Web Package) 和第三方插件 (Third-Party Plugin)。

## 版本管理 (Versioning)

### 核心引擎 (`movie-narrator`)

- 遵循 [语义化版本 (Semantic Versioning)](https://semver.org/) 管理包版本
  （`pyproject.toml` → `version`）。
- `CONTRACT_VERSION`（位于 `contract.py`）是一个独立的 semver 元组，跟踪
  公共 API 表面：
  - **MAJOR** — 对导出符号的破坏性移除或签名变更
  - **MINOR** — 新增导出（向后兼容）
  - **PATCH** — bug 修复 / 文档变更（不改变 API 表面）
- `CONTRACT_VERSION` 和包版本在同一提交中一起升级。
- `CHANGELOG.md` 必须在与版本升级相同的提交中更新。

### Web 包 (`movie-narrator-web`)

- 使用**独立版本控制** — 版本号不与核心引擎对齐。兼容性由 `CONTRACT_VERSION` 最低版本决定，而非匹配包版本号。
- 声明 `movie-narrator>=0.6.0` 为依赖。
- 独家依赖 `movie_narrator.contract` — 不允许导入内部模块；合约层是唯一的 API 边界。
- 在导入时通过 `_MIN_CONTRACT` 检查 `CONTRACT_VERSION >= _MIN_CONTRACT`。
- `CONTRACT_VERSION` 遵循 semver：仅在破坏性移除时升 MAJOR，新增导出时升 MINOR（向后兼容），bug 修复升 PATCH；API 表面未变时不需要每次发布都升级。

### 第三方插件

- 使用独立的 semver（例如 `1.0.0`、`0.3.2`）。
- 声明 `movie-narrator>=X.Y.Z` 为依赖。
- 在导入时调用 `check_version()` 强制最低合约版本：

```python
from movie_narrator.contract import check_version
check_version((0, 6, 1))
```

## 入口点 (Entry Points)

插件通过 `movie_narrator.plugins` 入口点组被发现。
权威入口点格式和示例见[插件开发指南](PLUGIN_DEVELOPMENT.zh-CN.md#entry-points)。

## CLI 插件命令

`mn plugin` 命令提供内省 (Introspection) 能力。详见
[插件开发指南](PLUGIN_DEVELOPMENT.zh-CN.md)。

```bash
mn plugin list          # List installed plugins (entry_points)
mn plugin version       # Show CONTRACT_VERSION
```

## 插件模板 (Plugin Template)

最小插件模板位于 `examples/plugins/template/`。复制它即可
引导创建新插件 — 详见 `examples/plugins/template/README.md` 的
说明。

## 发布到 PyPI

### 核心引擎

1. 升级 `pyproject.toml` 中的版本 + `contract.py` 中的 `CONTRACT_VERSION`。
2. 更新 `CHANGELOG.md`。
3. 提交：`feat: bump version to X.Y.Z`。
4. 打标签：`git tag vX.Y.Z -m "..."` → `git push origin vX.Y.Z`。
5. 标签推送触发 `Publish to PyPI` GitHub Actions 工作流
   （使用 Trusted Publisher — 无需 API token）。
6. 验证：`pip install movie-narrator==X.Y.Z`。

### 插件

1. 遵循标准 Python 打包流程：`python -m build` → `twine upload`。
2. 或使用 Trusted Publisher 配置 GitHub Actions（推荐）。
3. 测试安装：`pip install your-plugin && mn plugin list`。

## Git 工作流

分支模型、PR 流程和 CI 要求详见
[贡献指南](CONTRIBUTING.zh-CN.md)。关键规则：

- **绝不直接推送到 `main`** — 使用 `feature/*` 或 `hotfix/*` 分支 + PR。
- 合并前 CI 必须通过。
- 标签推送必须单独执行（`git push origin vX.Y.Z`）。
