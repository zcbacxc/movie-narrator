[![English](https://img.shields.io/badge/English-Release_Checklist-blue)](RELEASE_CHECKLIST.md)
[![简体中文](https://img.shields.io/badge/简体中文-发布清单-green)](RELEASE_CHECKLIST.zh-CN.md)

# v1.2.1 发布清单

> **v1.2.1 版本的完成定义（Definition of Done）。** 在创建 v1.2.1 标签
> 并发布到 PyPI 之前，必须逐项核实并勾选本清单中的所有项目。
> 项目按类别分组；每项均附有验证命令或方法。

---

## 代码质量

- [x] **mypy：零错误**
  - 命令：`mypy src/movie_narrator`
  - 预期结果：`Success: no issues found in ... source files`
  - 说明：必须在 Python 3.10 目标下通过（如 `pyproject.toml` 中配置）；必须与 CI 的 `mypy` 调用完全一致

- [x] **ruff：零错误**
  - 命令：`ruff check src/`
  - 预期结果：无输出（退出码 0）
  - 说明：所有 `E`、`F`、`W`、`BLE`、`A` 规则必须通过（详见 `pyproject.toml`）；CI 仅检查 `src/`

> **代码格式化（非阻塞）**：`ruff format` 未通过 CI 或 pre-commit 强制（未配置 `.pre-commit-config.yaml`）。作为独立 `chore/ruff-format` 清理项跟踪，使 diff 与发布改动隔离。

- [x] **测试覆盖率达标**
  - 命令：`pytest --cov=movie_narrator --cov-report=term-missing --cov-fail-under=90`
  - 预期结果：`Required test coverage of 90% reached. Total coverage: XX%`
  - 说明：阈值在 CI 配置中定义（`.coveragerc` + `ci.yml`）；v1.2.1 实测 90.75%；不得低于 v1.1 基线

---

## 测试

- [x] **单元测试：全部通过**
  - 命令：`pytest -v -m "not integration"`
  - 预期结果：`XX passed`（0 失败，0 错误）
  - 说明：`tests/` 下除标记为 `integration` 之外的所有测试

- [x] **集成测试：全部通过**
  - 命令：`pytest -v -m integration`
  - 预期结果：所有集成测试通过（若 scenedetect/ffmpeg 不可用可能被跳过）
  - 说明：需要 `scenedetect`（安装 `[media]` 额外依赖）；ffmpeg 经 `ffmpeg_bin()` 解析

- [x] **E2E 冒烟测试通过**
  - 命令：`pytest -v tests/test_e2e_smoke.py`
  - 预期结果：测试无错误通过
  - 说明：验证完整流水线以最小输入执行

- [x] **契约测试通过**
  - 命令：`pytest -v tests/test_contract.py`
  - 预期结果：所有契约重新导出、协议和版本测试通过
  - 说明：验证 `CONTRACT_VERSION` 值和 `__all__` 完整性

- [ ] **新增 v1.2.1 缓存/回退测试通过**
  - 命令：`pytest -v tests/test_gpu_detect.py tests/test_v120_gpu_detect.py tests/test_v121_gpu_cache.py`
  - 预期结果：全部通过（能力缓存 命中/未命中/失效 + 回退原因各分支）
  - 说明：相对 v1.2.0 总计新增 +18 个测试

---

## 安全

- [x] **SAST (bandit) 通过，零高置信度发现**
  - 命令：`bandit -r src/movie_narrator -c pyproject.toml`
  - 预期结果：无问题识别（或仅有带文档化例外的低/中级别）
  - 说明：v1.2.1 已将早期在 `gpu_detect.py` 查出的 B110（`try_except_pass`）通过改用 `contextlib.suppress` 修复

- [x] **依赖审计（pip-audit）通过**
  - 命令：`pip-audit`
  - 预期结果：`No known vulnerabilities found`
  - 说明：在干净的 `pip install -e ".[dev]"` 环境中运行；已记录的忽略列表条目必须重新评估

- [ ] **代码中无硬编码密钥**
  - 方法：人工审查 + CI 密钥扫描（GitHub secret scanning）
  - 预期结果：没有 API 密钥、令牌或凭据被提交到源码

- [x] **ADR-011 禁区依赖检查通过（机器可检测子集）**
  - 命令：`python scripts/check_forbidden_deps.py`
  - 预期结果：未发现禁区 pip 可安装包（Remotion、TypeTale、yt-dlp、Bilibili API、Playwright、IndexTTS、CosyVoice）
  - 说明：非包类禁区（如复制 TypeTale 源码、行为上使用爬虫）仍需人工代码审查

- [ ] **FFmpeg 捆绑检查通过**
  - 命令：`python scripts/check_no_ffmpeg_bundle.py`
  - 预期结果：构建产物中未发现 `ffmpeg` 或 `ffprobe` 二进制
  - 说明：确认 ADR-011 的 FFmpeg 政策；`.github/workflows/publish.yml` 中 `twine check` 之后也会自动运行。

---

## 文档

- [x] **CHANGELOG.md 已定稿**
  - 验证：审阅 `CHANGELOG.md`
  - 预期结果：
    - 新增 `## [1.2.1] - <日期>` 标题（原为 `[Unreleased]`）
    - `CONTRACT_VERSION` 行使用规范格式：`- \`CONTRACT_VERSION\` remains (1, 0, 0). All NNN tests pass (N skipped in CI, 0 failures). +M new tests vs v1.2.0.`
    - 底部版本比较链接已更新（`[Unreleased]` → `.../compare/v1.2.1...HEAD`，新增 `[1.2.1]` 链接）
    - 历史条目保持不变（不对旧版做代号或措辞改动）

- [x] **ROADMAP 反映 v1.2.1**
  - 验证：`docs/ROADMAP.zh-CN.md`（及 `.md`）
  - 预期结果：已完成表中含 v1.2.1 行；延后项（硬件编码）标注 v1.2.1 交付哪些、仍待推进哪些

- [x] **当前版本对齐**
  - 方法：用上个版本号（`v1.2.0`）作为"当前"声明检索文档
  - 预期结果：`docs/DEPLOYMENT.zh-CN.md`/`.md`、`docs/MIGRATION.zh-CN.md`/`.md`（当前版本注记）、`docs/TUTORIAL.zh-CN.md`/`.md`（兼容性说明）、`docs/index.md`（发布清单标签）、`README.md` 及本清单均指向 **v1.2.1**
  - 说明：同时更新本地 `CLAUDE.md` 的"当前版本"行（gitignored，仅本地）

- [ ] **mkdocs 构建成功**
  - 命令：`mkdocs build`
  - 预期结果：构建完成，无警告或错误

---

## 发布准备

- [ ] **版本号已对齐**
  - 验证：
    - `pyproject.toml` → `version = "1.2.1"`
    - `src/movie_narrator/contract.py` → `CONTRACT_VERSION = (1, 0, 0)`（未变——API 表面未变，**不得**递增）
    - `docs/ROADMAP.zh-CN.md` → CONTRACT_VERSION 行显示 `(1, 0, 0)`（v1.2.1 未变）
    - `docs/MIGRATION.zh-CN.md` → 当前版本注记已更新
  - 预期结果：包版本 1.2.1；契约版本保持 (1, 0, 0)

- [ ] **标签命名遵循约定**
  - 格式：`v1.2.1`（小写 `v`、语义化版本、无前缀/后缀）
  - 命令：`git tag -a v1.2.1 -m "v1.2.1 - GPU 能力缓存与编码器回退观测"`
  - 说明：使用注解标签，非轻量标签；标签推送必须与分支推送分开

- [ ] **发布分支已合并到 main**
  - 验证：feature 分支已通过 PR 合并到 `main`（合并提交上所有 CI 检查通过）
  - 说明：禁止直接推送 `main`；按分支保护使用 squash 或 rebase 合并

- [ ] **PyPI 发布工作流就绪**
  - 验证：`.github/workflows/publish.yml` 存在且已配置
  - 预期结果：Trusted Publisher 已配置，标签推送触发发布
  - 手动验证：
    ```bash
    pip install dist/movie_narrator-1.2.1-py3-none-any.whl
    mn version  # 应显示 1.2.1
    ```

- [ ] **GitHub Release 遵循 release.md 规范**
  - 标题：`v1.2.1 - GPU 能力缓存与编码器回退观测`
  - 正文：逐字复制 `CHANGELOG.md` 的 `## [v1.2.1]` 章节（按 `.claude/rules/release.md`），并附完整 CHANGELOG 链接
  - 每个标签只允许一个**非草稿** Release —— 删除 `publish.yml` 可能遗留的空草稿

- [ ] **Git 标签已推送**
  - 命令：`git push origin v1.2.1`
  - 预期结果：标签出现在 GitHub 上，发布工作流启动，PyPI 发布 `movie-narrator==1.2.1`
  - 说明：仅在所有清单项确认后推送标签

---

## 发布后

- [ ] **PyPI 发布已验证**
  - 验证：
    ```bash
    pip install movie-narrator==1.2.1
    python -c "from movie_narrator.contract import CONTRACT_VERSION; print(CONTRACT_VERSION)"
    # 预期结果：(1, 0, 0)
    ```
  - 预期结果：包干净地安装，导入正常，包版本 1.2.1

- [ ] **维护分支存在**
  - 验证：origin 上存在 `v1.2.x` 分支（v1.2.0 时创建）
  - 用途：为 v1.x 用户回溯安全和关键 Bug 修复

---

*请在发布候选（RC）阶段使用本清单。通过所有项的最终 RC
即成为 v1.2.1 正式版。*