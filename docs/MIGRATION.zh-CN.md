[![English](https://img.shields.io/badge/English-Migration-blue)](MIGRATION.md)
[![简体中文](https://img.shields.io/badge/简体中文-迁移指南-green)](MIGRATION.zh-CN.md)

# 迁移指南

本指南帮助 `movie-narrator` **v0.x** 的现有用户平滑迁移到 **v1.0 稳定版**。涵盖 breaking change（破坏性变更）、配置迁移、插件更新、升级步骤与回滚流程。

## 概述

movie-narrator v1.0 是首个提供长期 API 与行为保障的稳定版。大多数 v0.9.x 配置与插件仅需少量改动即可工作，但部分 breaking change 需要显式更新。

| 原版本            | 目标版本 | 迁移工作量 | 说明                                   |
|------------------|----------|------------|----------------------------------------|
| `< v0.7.3`       | v1.0     | 中等       | 更新 `mn serve` 绑定与认证方式          |
| `v0.7.3 - v0.7.9`| v1.0     | 较低       | 更新 `format` → `video_format` 重命名   |
| `v0.8.0 - v0.8.x`| v1.0     | 较低       | 核实配置边界分离                        |
| `v0.9.x`         | v1.0     | 极低       | 基本向后兼容                            |

### v0.9.x 新增内容（迁移时充分利用）

v0.9.x 为 v1.0 的稳定性奠定基础，并新增了大量可靠性与运维改进：

- **v0.9.1**：外部 API 调用的 circuit breaker（熔断器）与重试策略
- **v0.9.2**：任务检查点 + 优雅停机
- **v0.9.3**：批量任务提交 + cron 调度
- **v0.9.4**：死信队列（DLQ）+ 条件化分布式渲染
- **v0.9.5**：输入清洗 + 安全扫描 + 覆盖率门禁
- **v0.9.6**：i18n 基础设施 + 语言感知的本地化 TTS 语音映射

## Breaking Changes

### 1. `format` → `video_format` 重命名（v0.8.0+）

**变更内容：**

- 在 `job.yaml` 中，顶层输出格式键由 `format` 重命名为 `video_format`。
- 在 CLI 上，参数由 `--format` 重命名为 `--video-format`。

**向后兼容：**

- `job.yaml` 中旧的 `format` 键仍作为被弃用的别名被接受。若使用会打印警告。
- 旧的 `--format` CLI 参数仍作为别名被接受。

**需要执行的操作：**

更新你的作业文件与脚本以使用新命名：

```yaml
# 之前（v0.x）：
format: "9:16"

# 之后（v1.0）：
video_format: "9:16"
```

```bash
# 之前（v0.x）：
mn create -m "Inception" --format 9:16

# 之后（v1.0）：
mn create -m "Inception" --video-format 9:16
```

### 2. `mn serve` 默认绑定变更（v0.7.3+）

**变更内容：**

- 之前，`mn serve` 默认绑定 `0.0.0.0`（所有接口）。
- 现在，`mn serve` 默认绑定 `127.0.0.1`（仅本机）。
- 若要向外部网络暴露服务，必须显式使用 `--public`。

当你使用 `--public` 绑定 `0.0.0.0` 时，**必须启用 `MN_API_KEY` 认证**（自 v0.8.0 起强制）。若未配置 API key，服务器将拒绝启动。仅限开发环境可使用 `--insecure` 绕过此项检查。

**向后兼容：**

- 本地开发工作流（不带参数直接运行 `mn serve`）无需改动即可继续工作——服务器仅可从本机访问。

**需要执行的操作：**

如果你依赖旧的 `0.0.0.0` 默认值来暴露服务：

1. 启动服务器时添加 `--public` 参数：
   ```bash
   mn serve --public --port 8765
   ```

2. 在 `.env` 中设置 `MN_API_KEY`，或通过 `--api-key` 传入：
   ```bash
   # 在 .env 中
   MN_API_KEY=your-secret-api-key-here
   ```

3. 连接公共服务器的客户端必须包含 `X-API-Key` 请求头：
   ```bash
   curl -H "X-API-Key: $MN_API_KEY" http://your-server:8765/health
   ```

## 配置迁移

### 配置边界分离

v1.0 强制要求两个配置层之间严格分离：

| 层               | 位置       | 前缀/格式                 | 用途                                    |
|------------------|------------|---------------------------|-----------------------------------------|
| 基础设施         | `.env`     | `MN_*` 环境变量           | LLM/TTS/VLM 凭据、端点、模型名、调用参数、可靠性设置 |
| 流水线行为       | `job.yaml` | YAML 键                   | 场景检测、匹配阈值、渲染选项、翻译、BGM 选择、预设等 |

**变更内容：**

- 之前，部分流水线设置可放在 `.env` 中并使用 `MN_` 前缀。现在不再支持。
- 所有流水线行为**必须**在 `job.yaml` 中配置。只有基础设施凭据应放在 `.env` 中。

**需要执行的操作：**

将 `.env` 中的任何流水线参数移入 `job.yaml` 文件。示例：

```env
# ❌ 之前（错误——这应属于 job.yaml）：
MN_VIDEO_FPS=30
MN_TRANSLATE_ENABLED=true
```

```yaml
# ✅ 之后（正确——在 job.yaml 中，渲染参数应放在 params: 部分下）：
params:
  render_fps: 30
  translate_source_lang: zh
  translate_provider: llm
```

### 配置优先级规则（v1.0）

引擎按以下顺序评估配置（优先级从高到低）：

1. **显式环境变量**（在 shell 环境中导出的 `MN_*` 变量）
2. `./.env`（当前工作目录中的 `.env` 文件）
3. `~/.movie-narrator/.env`（用户级配置，首次运行时自动创建）
4. **内置默认值**（本地 Ollama 端点、合理的运维默认值）

**说明：**

- 首次运行时，若 `./.env` 与 `~/.movie-narrator/.env` 均不存在，引擎会依据 `.env.example` 模板自动创建 `~/.movie-narrator/.env`。
- 你可以通过创建项目专属的 `./.env` 来覆盖用户级默认值。

### 迁移后验证你的配置

更新文件后，请核实配置能正确加载。没有专门的 `mn config check` 命令；相反，请运行一个轻量命令来检验你的配置并报告弃用警告或缺失的凭据：

```bash
mn version                    # 确认已安装的版本
mn plugin list                # 确认引擎能无错误地加载其插件
mn resolve -m "Inception"     # 针对你的影片库执行 resolve 路径
```

任何被弃用的键（例如旧的 `format` 别名）或边界违规，都会在这些命令执行期间作为警告被报告。

## 插件迁移

movie-narrator v1.0 使用标准化的插件发现与注册系统。如果你有自定义插件，请遵循以下步骤。

### 通过 Entry Point 注册插件

所有插件都必须在插件的 `pyproject.toml` 中通过 `movie_narrator.plugins` entry point（入口点）组进行注册：

```toml
# pyproject.toml
[project.entry-points."movie_narrator.plugins"]
my_plugin = "my_plugin_module:MyPlugin"
```

核心引擎会自动通过该 entry point 机制发现并加载环境中已安装的插件。

### 注册装饰器

插件使用装饰器向引擎注册自定义组件：

| 装饰器           | 用途                          |
|------------------|-------------------------------|
| `@register_step`   | 注册一个流水线步骤            |
| `@register_tts`    | 注册一个 TTS 提供者          |
| `@register_vision`| 注册一个 VLM 提供者          |
| `@register_llm`    | 注册一个 LLM 提供者          |
| `@register_research` | 注册一个研究提供者          |

示例：

```python
from movie_narrator import register_tts
from movie_narrator.tts import TTSProvider

@register_tts("my_tts")
class MyCustomTTS(TTSProvider):
    # implementation here
    ...
```

### 版本检查

插件**必须**在导入时检查 `CONTRACT_VERSION` 以确保兼容性：

```python
from movie_narrator.contract import CONTRACT_VERSION, check_version

# Require at least contract version 0.9.0
check_version((0, 9, 0))
```

如果引擎的契约版本低于插件的版本要求，将抛出 `ImportError` 并附带清晰的升级提示。

### 类型检查规则

如果你的工厂函数返回的实例不符合预期的 ABC/protocol，引擎会立即抛出 `TypeError`。这能在早期发现插件集成缺陷。

示例：如果 TTS 提供者工厂返回 `None` 而不是满足 `TTSProvider` 的实例，你会得到：

```
TypeError: Expected TTSProvider instance, got NoneType
```

**插件作者需要执行的操作：**

1. 确保你的插件在 `pyproject.toml` 中的 `movie_narrator.plugins` 下声明了 entry point。
2. 添加 `check_version()` 调用，并传入所需的最低契约版本。
3. 核实所有注册都使用 `movie_narrator.contract` 中的官方装饰器。
4. 测试你的工厂函数返回符合预期协议的合法实例。

## CLI 迁移

下表汇总了需要更新脚本或自动化内容的 CLI 变更：

| 旧命令 / 参数                    | 新等效命令                     | 变更版本 |
|-----------------------------------|--------------------------------|----------|
| `mn ... --format 9:16`           | `mn ... --video-format 9:16`   | v0.8.0   |
| `mn serve`（绑定 0.0.0.0）        | `mn serve --public`            | v0.7.3   |
| （公共接口无需认证）              | 需要 `MN_API_KEY` / `--api-key` | v0.8.0   |

所有其他 CLI 子命令（`resolve`、`research`、`submit`、`artifacts` 等）仍保持向后兼容。

## CONTRACT_VERSION 语义

v1.0 正式确立了公共 API 契约的 `CONTRACT_VERSION` 语义化版本规则：

| 组件     | 含义                                                                    |
|----------|-------------------------------------------------------------------------|
| **MAJOR** | Breaking change：符号被移除、签名被更改。依赖旧主版本的插件/应用将无法工作。 |
| **MINOR** | 新增符号/导出（完全向后兼容）。旧代码仍可继续工作。                     |
| **PATCH** | Bug 修复、文档变更（不涉及 API 表面变化）。                           |

版本**仅**在公共 API 表面发生变化时递增。不影响导出符号的内部重构无需递增版本。

**当前版本（v0.9.7）：** `(0, 9, 5)`
**目标版本（v1.0）：** `(1, 0, 0)` —— v1.0 之后，契约将被冻结，同一主版本内将保证向后兼容。

### 在代码中检查契约版本

如果你正在构建一个将 `movie-narrator` 作为库依赖的应用或工具，请在导入时检查契约版本：

```python
from movie_narrator.contract import CONTRACT_VERSION, check_version

# Require at least 1.0.0
check_version((1, 0, 0))
```

这可确保当已安装的版本过旧时，你的用户会得到清晰的升级提示错误信息。

## 分步升级流程

请遵循以下步骤从 v0.x 升级到 v1.0：

### 步骤 1：检查 Python 版本要求

movie-narrator v1.0 要求 **Python >= 3.10**。支持 Python 3.13（通过 `audioop-lts` 处理旧版音频处理）。

检查你的 Python 版本：

```bash
python --version
# Should be >= 3.10.0
```

如有必要，请先升级 Python 再继续。

### 步骤 2：备份当前配置

升级前，先备份你现有的配置：

```bash
# Backup project .env if you have one
cp .env .env.v0-backup

# Backup any custom job files
cp my-job.yaml my-job.v0-backup.yaml

# Backup user-level env
cp ~/.movie-narrator/.env ~/.movie-narrator/.env.v0-backup
```

### 步骤 3：升级软件包

```bash
pip install --upgrade movie-narrator
```

如果你是从 git 安装的：

```bash
git pull origin main
pip install --upgrade .
```

### 步骤 4：更新配置文件

1. 在所有 `job.yaml` 文件中：将 `format` 替换为 `video_format`。
2. 核实配置边界分离：`.env` 中不得有流水线参数，所有 `MN_*` 均为基础设施设置。
3. 如果你要公开暴露 `mn serve`：在 `.env` 中设置 `MN_API_KEY`，并在 serve 命令中添加 `--public`。
4. 审阅并启用新的可靠性特性（可选但推荐）：
   - Circuit breaker：`MN_CIRCUIT_FAILURE_THRESHOLD`、`MN_CIRCUIT_RECOVERY_TIMEOUT`
   - 优雅停机：`MN_GRACEFUL_SHUTDOWN_TIMEOUT`
   - 产物保留：`MN_ARTIFACT_TTL`、`MN_ARTIFACT_MAX_BYTES`

### 步骤 5：运行配置检查

```bash
mn version
mn plugin list
```

核实版本正确且引擎能无错误地加载插件。处理 CLI 报告的任何弃用警告。

### 步骤 6：用示例作业测试

运行一个小型测试作业以验证一切正常：

```bash
mn resolve -m "Inception" --library-dir /path/to/library
```

如果成功，再尝试完整渲染：

```bash
mn create -m "Inception" --video /path/to/inception.mp4 -o output/
```

### 步骤 7：更新插件（如适用）

如果你使用第三方插件，请检查插件是否已针对 v1.0 更新。如有必要，请升级插件。

如果你维护自己的自定义插件：

1. 在 `pyproject.toml` 中添加 entry point 注册。
2. 添加 `check_version()` 调用。
3. 测试插件加载：`mn plugin list` 应显示你的插件。

## 回滚指南

如果升级后遇到问题，请回滚到之前的版本：

### 步骤 1：恢复配置文件

```bash
# Restore project .env
cp .env.v0-backup .env

# Restore user-level env
cp ~/.movie-narrator/.env.v0-backup ~/.movie-narrator/.env

# Restore your job files
cp my-job.v0-backup.yaml my-job.yaml
```

### 步骤 2：降级软件包

如果你需要回到之前的版本：

```bash
# Replace X.Y.Z with your previous version
pip install movie-narrator==X.Y.Z
```

如果你是从 git 安装的：

```bash
git checkout <previous-commit>
pip install .
```

### 步骤 3：验证回滚

运行一个测试作业以确认一切恢复正常：

```bash
mn version
mn resolve -m "Inception" --library-dir /path/to/library
```

## 常见问题

### Q：我现有的 v0.9.x 作业文件能在 v1.0 中不做任何修改直接使用吗？

**A：** 大多数可以。唯一需要改动的，是如果你正在使用旧的 `format` 键，需要将其重命名为 `video_format`。旧键仍会被接受（伴随警告），所以技术上仍可运行。我们建议更新以消除警告。

### Q：我还在使用 v0.6.x —— 能直接升级到 v1.0 吗？

**A：** 可以。请遵循本指南，特别注意 v0.7.3 以来的绑定与认证改动，以及 v0.8.0 的 format 重命名。所有改动都是累积性的，但均已在此文档中说明。

### Q：如果我只使用 CLI 进行本地渲染而非 `mn serve`，需要改动任何内容吗？

**A：** 只需在现有的任何作业文件中将 `format` → `video_format`。`mn serve` 的默认值改动不影响纯本地使用。其余全部保持向后兼容。

### Q：我的自定义插件在 v0.8 中可正常工作，v1.0 需要改什么？

**A：** 在 `pyproject.toml` 中添加 entry point 发现，添加 `check_version` 导入检查，并确保你正在使用来自 `movie_narrator.contract` 的官方注册装饰器。如果你已经遵循 v0.9 的插件模式，则只需极少量改动。

### Q：如果我不想添加 API key 认证，是否还能不带认证地公开提供服务？

**A：** 你可以使用 `--insecure` 绕过该要求：

```bash
mn serve --public --insecure
```

**我们强烈建议不要在生成环境或公网暴露时使用此方式。** API key 是防止未授权访问的一种简单而有效的保护。

### Q：我的 `~/.movie-narrator/.env` 去哪了？升级会删除它吗？

**A：** 升级不会删除任何内容。v1.0 仍将 `~/.movie-narrator/.env` 作为环境变量与 `./.env` 之后优先级最低的配置来源。如果之前存在，它仍会保留在那里。

### Q：迁移后我应该启用哪些新特性？

**A：** 我们建议启用：

- 调用外部 API 用于稳定性保障的 circuit breaker（`MN_CIRCUIT_FAILURE_THRESHOLD=5`）
- 用于控制磁盘占用的产物保留（`MN_ARTIFACT_TTL` 或 `MN_ARTIFACT_MAX_BYTES`）
- 如果你制作多语言视频，可启用按语言进行语音覆盖（`MN_VOICE_ZH`、`MN_VOICE_EN`）（v0.9.6+）

### Q：v1.0 的 API 稳定吗？我的插件会在未来的 1.x 版本中受影响吗？

**A：** 是的，v1.0 冻结了公共契约。所有 1.x 版本都将保持向后兼容。Breaking change 只会发生在 2.0 中，届时迁移也会有类似的文档说明。

## Python 版本支持

| Python 版本 | 在 v1.0 中是否支持 | 说明                     |
|-------------|--------------------|--------------------------|
| 3.9 及更早  | ❌ 否              | 需要升级                 |
| 3.10        | ✅ 是              | 已充分测试               |
| 3.11        | ✅ 是              | 已充分测试               |
| 3.12        | ✅ 是              | 已充分测试               |
| 3.13        | ✅ 是              | 通过 `audioop-lts` 支持  |

## 后续步骤

成功迁移后：

- 阅读 [快速入门指南](QUICKSTART.md) 以了解新特性概览
- 如果你在编写自定义插件，请参阅 [插件开发](PLUGIN_DEVELOPMENT.md)
- 查看 [部署](DEPLOYMENT.md) 了解生产环境部署的最佳实践

如果你遇到本指南未涵盖的问题，请携带你的迁移详情 [open an issue](https://github.com/zcbacxc/movie-narrator/issues)。