[![English](https://img.shields.io/badge/English-Release_Checklist-blue)](RELEASE_CHECKLIST.md)
[![简体中文](https://img.shields.io/badge/简体中文-发布清单-green)](RELEASE_CHECKLIST.zh-CN.md)

# v1.0 发布清单

> **v1.0 稳定版的完成定义（Definition of Done）。** 在创建 v1.0.0 标签
> 并发布到 PyPI 之前，必须逐项核实并勾选本清单中的所有项目。
> 项目按类别分组；每项均附有验证命令或方法。

---

## 代码质量

- [ ] **mypy：零错误**
  - 命令：`mypy src/movie_narrator`
  - 预期结果：`Success: no issues found in 114 source files`
  - 说明：必须在 Python 3.10 目标下通过（如 `pyproject.toml` 中配置）；必须与 CI 的 `mypy` 调用完全一致

- [ ] **ruff：零错误**
  - 命令：`ruff check src/`
  - 预期结果：无输出（退出码 0）
  - 说明：所有 `E`、`F`、`W`、`BLE`、`A` 规则必须通过（详见 `pyproject.toml`）；CI 仅检查 `src/`

> **代码格式化（非阻塞，v1.0 范围外）**：`ruff format` 未通过 CI 或 pre-commit 强制（未配置 `.pre-commit-config.yaml`）。`src/` 下约 80 个文件（含 `tests/` 共约 150 个）目前未格式化。作为独立 `chore/ruff-format` 清理项跟踪，使 diff 与 v1.0 的 docstring/稳定性改动隔离。

- [ ] **测试覆盖率达标**
  - 命令：`pytest --cov=movie_narrator --cov-report=term-missing --cov-fail-under=82`
  - 预期结果：`Required test coverage of 82% reached. Total coverage: XX%`
  - 说明：阈值在 CI 配置中定义；不得低于 v0.9.x 基线

---

## 测试

- [ ] **单元测试：全部通过**
  - 命令：`pytest -v -m "not integration"`
  - 预期结果：`XX passed`（0 失败，0 错误）
  - 说明：`tests/` 下除标记为 `integration` 之外的所有测试

- [ ] **集成测试：全部通过**
  - 命令：`pytest -v -m integration`
  - 预期结果：所有集成测试通过（若 media/ffmpeg 不可用可能被跳过）
  - 说明：需要 `ffmpeg` 和 `scenedetect`（安装 `[media]` 额外依赖）

- [ ] **E2E 冒烟测试通过**
  - 命令：`pytest -v tests/test_e2e_smoke.py`
  - 预期结果：测试无错误通过
  - 说明：验证完整流水线以最小输入执行

- [ ] **契约测试通过**
  - 命令：`pytest -v tests/test_contract.py`
  - 预期结果：所有契约重新导出、协议和版本测试通过
  - 说明：验证 `CONTRACT_VERSION` 值和 `__all__` 完整性

- [ ] **无不稳定测试**
  - 命令：`pytest -v --count=3`
  - 预期结果：相同测试在 3 次运行中一致通过
  - 说明：在 CI 矩阵上运行（Python 3.10、3.11、3.12、3.13）

---

## 安全

- [ ] **SAST (bandit) 通过，零高/严重级别发现**
  - 命令：`bandit -r src/movie_narrator -c pyproject.toml`
  - 预期结果：`No issues identified`（或仅有带文档化例外的低/中级别）
  - 说明：根据 `pyproject.toml` bandit 配置排除 tests、examples、docs

- [ ] **依赖审计（pip-audit）通过**
  - 命令：`pip-audit`
  - 预期结果：`No known vulnerabilities found`
  - 说明：在干净的 `pip install -e ".[dev]"` 环境中运行
  - 说明：已记录的忽略列表条目（如 pillow 11.x）必须重新评估

- [ ] **代码中无硬编码密钥**
  - 方法：人工审查 + CI 密钥扫描（GitHub secret scanning）
  - 预期结果：没有 API 密钥、令牌或凭据被提交到源码
  - 说明：用 `git diff main --name-only | xargs grep -l "sk-\|api_key\|secret"` 验证

- [ ] **SECURITY.md 已更新**
  - 验证：审阅 `SECURITY.md` 和 `SECURITY.zh-CN.md`
  - 预期结果：漏洞报告流程是最新的，联系方式有效

---

## 文档

- [ ] **所有双语文档结构对齐**
  - 方法：比较每对文档的 EN 和 ZH 版本
  - 预期结果：相同的章节数量、相同的章节层级、相同的表格
  - 需验证的文件：`README`、`ARCHITECTURE`、`ROADMAP`、`CONTRIBUTING`、
    `BEST_PRACTICES`、`LLM_PROVIDERS`、`METADATA_SCHEMA`、`PACKAGING`、
    `PLUGIN_DEVELOPMENT`、`QUICKSTART`、`AI_GUIDE`、`ADR`、`MIGRATION`、
    `TUTORIAL`、`DEPLOYMENT`、`OBSERVABILITY`、`STABILITY`、`RELEASE_CHECKLIST`

- [ ] **迁移指南完整并已审查**
  - 验证：从头到尾阅读 `docs/MIGRATION.zh-CN.md`
  - 预期结果：
    - v0.x → v1.0 升级步骤清晰准确
    - 所有破坏性变更均已记录
    - 包含回滚流程
    - FAQ 涵盖常见升级场景

- [ ] **API 参考（SDK 文档）完整**
  - 验证：运行 `mkdocs build` 并检查 SDK 参考页面
  - 预期结果：所有 `movie_narrator.contract` 导出均已记录
  - 说明：`docs/sdk/` 页面列出所有模块：contract、models、pipeline、
    step_registry、errors、registries、tts、vision、presets、cloud、reliability

- [ ] **稳定性文档已发布**
  - 验证：`docs/STABILITY.md` 和 `docs/STABILITY.zh-CN.md` 存在且已在 `mkdocs.yml` 导航中链接
  - 预期结果：API 稳定性承诺、版本化政策、弃用政策、
    升级承诺、Python 版本支持、契约兼容性矩阵

- [ ] **CHANGELOG.md 已定稿**
  - 验证：审阅 `CHANGELOG.md`
  - 预期结果：
    - `[Unreleased]` 部分已移至 `[1.0.0]`
    - 所有 Keep a Changelog 分类均存在（Added、Changed、Deprecated、Removed、Fixed、Security）
    - `CONTRACT_VERSION` 行已更新为 `(1, 0, 0)`
    - 底部的版本比较链接完整

- [ ] **mkdocs 构建成功**
  - 命令：`mkdocs build`
  - 预期结果：构建完成，无警告或错误
  - 说明：所有导航链接可解析、所有图片加载正常、所有代码块正确渲染

---

## 发布准备

- [ ] **版本号已对齐**
  - 验证：
    - `pyproject.toml` → `version = "1.0.0"`
    - `src/movie_narrator/contract.py` → `CONTRACT_VERSION = (1, 0, 0)`
    - `docs/ROADMAP.zh-CN.md` → CONTRACT_VERSION 行显示 `(1, 0, 0)`
    - `docs/MIGRATION.zh-CN.md` → 当前/目标版本引用已更新
  - 预期结果：所有版本引用均匹配 1.0.0 / (1, 0, 0)

- [ ] **标签命名遵循约定**
  - 格式：`v1.0.0`（小写 `v`、语义化版本、无前缀/后缀）
  - 命令：`git tag -a v1.0.0 -m "v1.0.0 - 首个稳定版"`
  - 说明：使用注解标签，非轻量标签

- [ ] **发布分支已合并到 main**
  - 验证：`release/v1.0` 分支已通过 PR 合并到 `main`
  - 预期结果：合并提交上所有 CI 检查通过
  - 说明：禁止直接推送到 `main`

- [ ] **PyPI 发布工作流就绪**
  - 验证：`.github/workflows/publish.yml` 存在且已配置
  - 预期结果：Trusted Publisher 已配置，标签推送触发发布
  - 手动验证：
    ```bash
    python -m build
    twine check dist/*
    pip install dist/movie_narrator-1.0.0-py3-none-any.whl
    mn version  # 应显示 1.0.0
    ```

- [ ] **PyPI 发布已验证**
  - 验证：
    ```bash
    pip install movie-narrator==1.0.0
    python -c "from movie_narrator.contract import CONTRACT_VERSION; print(CONTRACT_VERSION)"
    # 预期结果：(1, 0, 0)
    ```
  - 预期结果：包干净地安装，导入正常，版本匹配

- [ ] **Git 标签已推送**
  - 命令：`git push origin v1.0.0`
  - 预期结果：标签出现在 GitHub 上，发布工作流启动
  - 说明：仅在所有清单项确认后推送标签

- [ ] **GitHub Release 已创建**
  - 验证：在 GitHub 上创建了标签为 `v1.0.0` 的 Release 页面
  - 预期结果：
    - 标题：`v1.0.0 - 首个稳定版`
    - 正文：关键特性摘要、迁移指南和稳定性文档链接
    - 包含 CHANGELOG 条目
    - **未勾选** Pre-release 复选框

---

## 发布后

- [ ] **发布公告已发布**
  - 渠道：GitHub Release 页面、讨论区、社交媒体（如适用）
  - 内容：关键特性、稳定性承诺、迁移指南链接

- [ ] **v1.0.x 维护分支已创建**
  - 命令：`git checkout -b v1.0.x v1.0.0 && git push -u origin v1.0.x`
  - 用途：为 v1.x 用户回溯安全和关键 Bug 修复

- [ ] **ROADMAP 已更新以规划 v1.1**
  - 验证：`docs/ROADMAP.zh-CN.md` 中 v1.0.0 已移至已完成表
  - 预期结果：在"当前与规划"下添加 v1.1.0 规划部分

---

*请在发布候选（RC）阶段使用本清单。每个 RC 都应经过完整清单检查。
通过所有项的最终 RC 即成为 v1.0.0 正式版。*
