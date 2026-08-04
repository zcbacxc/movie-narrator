[![English](https://img.shields.io/badge/English-Stability-blue)](STABILITY.md)
[![简体中文](https://img.shields.io/badge/简体中文-稳定性承诺-green)](STABILITY.zh-CN.md)

# API 稳定性承诺

本文档定义了 `movie-narrator` 公共 API 的稳定性保障。从 **v1.0.0** 开始，
项目为所有导出符号、配置接口和插件扩展点提供正式的语义化版本管理和
向后兼容承诺。

## 稳定性承诺

**自 v1.0.0 起，`movie_narrator.contract` API 表面正式声明为稳定。**

所有从 `movie_narrator.contract` 导出的符号——包括模型、协议、注册表、
装饰器、错误类型和云 SDK 类型——均受本稳定性承诺保护。外部消费者
（Web UI、插件、第三方工具）可以放心地依赖这些符号，它们的签名和行为
在同一主版本内不会发生不兼容的变更。

### 保障范围

- `movie_narrator.contract.__all__` 中列出的所有名称
- 导出的类和函数的公共方法签名与参数名
- 导出函数的返回类型和抛出的错误类型
- `CONTRACT_VERSION` 元组格式与比较语义
- 插件协议（`Plugin`、`PluginContext`、entry point 发现机制）
- CLI 命令名称及其已文档化的参数
- `job.yaml` 模式（顶层键及其文档化的类型）
- 带 `MN_` 前缀的 `.env` 变量名

### 不保障范围

- `movie_narrator.pipeline`、`movie_narrator.utils`、`movie_narrator.tts`
  等内部模块——这些是实现细节，可能随时变更而不另行通知。
  请始终从 `movie_narrator.contract` 或 `movie_narrator` 顶层重新导出
  的符号中导入。
- 私有属性和方法（以下划线 `_` 开头的名称）
- 未明确文档化为稳定的默认值
- 输出文件格式（视频编码、字幕样式）——这些依赖于实现，可能在次版本
  之间发生变化
- 性能特性和耗时
- 明确标记为不稳定的实验性或预览功能

## 版本化政策

`movie-narrator` 遵循 [语义化版本 2.0.0](https://semver.org/lang/zh-CN/)。
`pyproject.toml` 中的包版本与 `contract.py` 中的 `CONTRACT_VERSION`
元组始终在同一版本发布中一起升级。

### 包版本（pyproject.toml）

| 组件     | 含义 |
|----------|------|
| **MAJOR** | 公共 API 的破坏性变更。用户和插件需要更新代码。 |
| **MINOR** | 以向后兼容的方式添加新功能。现有代码无需改动即可继续工作。 |
| **PATCH** | Bug 修复、安全补丁和文档更新。不涉及 API 表面变化。 |

### CONTRACT_VERSION（contract.py）

`CONTRACT_VERSION` 是一个 `(major, minor, patch)` 元组，独立于发布
营销版本来追踪公共 API 表面。它遵循相同的 semver 规则：

| 组件     | 含义 |
|----------|------|
| **MAJOR** | 导出符号的破坏性移除或签名变更。 |
| **MINOR** | 新增导出（向后兼容）。旧代码继续工作。 |
| **PATCH** | Bug 修复、文档变更——API 表面无变化。 |

> **注意**：`CONTRACT_VERSION` 仅在公共 API 表面发生变化时才递增。
> 不影响导出符号的内部重构、Bug 修复和性能改进不需要递增
> CONTRACT_VERSION。

### 版本兼容性规则

要求契约版本为 `(X, Y, Z)` 的消费者，与任何已安装版本 `(A, B, C)`
兼容的条件是：

- `A == X`（主版本相同）
- `B >= Y`（次版本至少为所需的最低版本）
- 当 `B == Y` 时 `C >= Z`（在同一次版本线内，补丁版本至少为所需的
  最低版本）

消费者应使用 `check_version()` 在导入时强制执行此检查：

```python
from movie_narrator.contract import check_version
check_version((1, 0, 0))
```

## 弃用政策

当公共 API 功能需要以破坏性方式移除或变更时，项目遵循"先弃用后移除"
的政策：

1. **弃用公告**：该功能在一个 **次** 版本中被标记为弃用。使用该功能时
   会在运行时发出弃用警告。文档会更新以说明弃用情况并推荐替代方案。

2. **弃用窗口**：被弃用的功能至少保留 **一个完整的次版本周期**
   （例如，在 v1.2 中弃用，最早在 v1.3 中移除）。对于重要或广泛使用的
   功能，弃用窗口可能延长至两个次版本。

3. **移除**：该功能在下一个 **主** 版本中移除。在特殊情况下
   （安全漏洞、严重正确性错误），功能可能提前移除，但会发出适当通知。

### 弃用警告

所有弃用均使用 Python 的 `warnings.warn()` 配合 `DeprecationWarning`
类别，并包含以下信息：

- 被弃用功能的名称
- 被弃用的版本
- 将被移除的版本
- 推荐的替代方案

示例：

```
DeprecationWarning: `old_function()` 自 v1.2 起已弃用，将在 v2.0 中移除。
请改用 `new_function()`。
```

## 升级承诺

### 同一主版本内（v1.x）

- **零破坏性变更**：不会移除符号，不会更改现有函数的签名，不会移除
  CLI 参数或配置键。
- **新功能以增量方式添加**：新的导出、新的 CLI 参数和新的配置选项
  在次版本中添加，不影响现有使用方式。
- **Bug 修复是安全的**：补丁版本修复 Bug 而不改变文档化的行为。
  如果 Bug 修复改变了可观察行为并可能破坏用户代码，则视为次版本
  发布并附带迁移说明。
- **先发出弃用警告**：任何未来的移除都会先经过至少一个次版本的
  弃用警告期。

### 主版本之间（v1.x → v2.0）

- 允许并预期会有破坏性变更。
- 每次主版本升级都会提供完整的 [迁移指南](MIGRATION.zh-CN.md)。
- 所有破坏性变更都会在 `CHANGELOG.md` 的 `Breaking Changes` 部分
  记录。
- 上一个主版本在新主版本发布后，将继续获得 **至少 6 个月** 的安全
  和关键 Bug 修复支持。

### v0.x → v1.0 过渡

v1.0 发布是第一个稳定版。v0.x 系列是快速开发的预稳定阶段，次版本
之间可能发生破坏性变更。从 v0.x 升级的用户请查阅
[迁移指南](MIGRATION.zh-CN.md) 以获取完整的变更列表和升级步骤。

关于 v1.0 过渡的关键信息：

- `CONTRACT_VERSION` 从 `(0, 9, 5)` 升级到 `(1, 0, 0)`。
- 这是一次 MAJOR 版本提升——1.0 是首个稳定版，不是 v0.x 兼容性模型
  的延续。
- `contract.py` 中声明的 API 表面已冻结，并将在整个 v1.x 系列中
  保持向后兼容。
- 所有 v0.9.x 功能在 v1.0 中均得到保留；v1.0 发布本身不包含任何
  功能移除。

## Python 版本支持

`movie-narrator` 支持以下 Python 版本：

| Python 版本 | 在 v1.x 中是否支持 | 支持状态 |
|-------------|--------------------|----------|
| 3.9 及更早  | ❌ 否              | 从未支持 |
| 3.10        | ✅ 是              | 主要目标版本，已充分测试 |
| 3.11        | ✅ 是              | 已充分测试 |
| 3.12        | ✅ 是              | 已充分测试 |
| 3.13        | ✅ 是              | 通过 `audioop-lts` 支持 |

### 支持政策

- 始终支持至少 **3** 个 Python 次版本。
- 新的 Python 次版本在其稳定发布后的下一个次版本中添加支持，前提是
  所有依赖项均支持该版本。
- Python 版本仅在 **主** 版本中停止支持。每个 Python 版本的停止支持
  日期至少提前一个次版本公告。
- 所有支持的 Python 版本均提供安全和 Bug 修复支持。

## 契约兼容性矩阵

此表显示每个包版本引入的 `CONTRACT_VERSION` 及其对应的 API 表面。

| 包版本         | CONTRACT_VERSION | 关键 API 新增 / 变更 |
|----------------|------------------|----------------------|
| v0.4.x         | `(0, 4, 0)`      | 初始契约层、核心流水线导出 |
| v0.5.x         | `(0, 5, 0)`      | 插件 API、注册表、Step/Plugin 协议 |
| v0.6.0         | `(0, 6, 0)`      | 云 / 任务队列类型：`Task`、`TaskQueue` |
| v0.6.1         | `(0, 6, 1)`      | 远程推理：`RemoteTaskQueue`、`WorkerDaemon` |
| v0.8.1         | `(0, 8, 1)`      | 可观测性：`JsonFormatter`、`MetricsRegistry` |
| v0.8.2         | `(0, 8, 2)`      | 健康检查 + OpenAPI：`build_health_payload`、`build_openapi_spec` |
| v0.8.3         | `(0, 8, 3)`      | 产物存储：`ArtifactInfo`、`LocalArtifactStore`、`S3ArtifactStore` |
| v0.9.1         | `(0, 9, 1)`      | 可靠性：`CircuitBreaker`、`RetryPolicy`、`with_retry` |
| v0.9.2         | `(0, 9, 2)`      | 任务生命周期：`TaskCheckpoint`、`CheckpointStore`、`ResumePlan` |
| v0.9.3         | `(0, 9, 3)`      | 批量与调度：`Batch`、`BatchRequest`、`JobScheduler`、`ScheduleRequest` |
| v0.9.4         | `(0, 9, 4)`      | 死信队列 + 分布式渲染：`DeadLetterRecord`、`DistributedRenderPlanner` |
| v0.9.6         | `(0, 9, 5)`      | i18n / 语音映射：`DEFAULT_VOICE_MAP`、`resolve_voice` |
| **v1.0.0**     | **`(1, 0, 0)`**  | **API 冻结——首个稳定版。保留所有 v0.9.6 导出；稳定性保障自此开始。** |

### 向前兼容

针对 `CONTRACT_VERSION (1, 0, 0)` 编写的代码可以不加修改地在所有未来
的 v1.x 版本中运行。在后续 v1.x 版本中添加的新导出将具有更高的次版本
号，但不会破坏现有代码。

### 检查兼容性

在你的插件或应用中使用 `check_version()` 来确保已安装的引擎满足你的
最低要求：

```python
from movie_narrator.contract import check_version

# 要求至少 v1.0.0（稳定 API）
check_version((1, 0, 0))

# 或者要求包含新功能的特定次版本
check_version((1, 2, 0))
```

---

*本稳定性政策自 v1.0.0 起生效。如有关于 API 稳定性或弃用时间表的
问题，请在 GitHub 上提交 issue。*
