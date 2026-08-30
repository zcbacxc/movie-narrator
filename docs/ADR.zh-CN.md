[![English](https://img.shields.io/badge/English-ADR-blue)](ADR.md)
[![简体中文](https://img.shields.io/badge/简体中文-架构决策记录-green)](ADR.zh-CN.md)

# 架构决策记录

本文档记录了 **movie-narrator** 项目所做的关键架构决策。每条 ADR 均遵循标准结构——状态、背景、决策驱动因素、备选方案、决策结果、后果和参考——供开发者和维护者阅读，以确保重要技术选择背后的理由不会随时间推移而丢失。

## Introduction

### 什么是 ADR？

架构决策记录（Architecture Decision Record, ADR）是一份简短、自包含的笔记，用于记录单一的、重要的架构决策：我们面临的问题、我们做出的选择、我们为何做出该选择、其代价是什么，以及我们考虑过的其他方案。ADR 一经写入即不可更改——如果某个决策发生变化，应编写新的 ADR 来取代旧的记录。

### 如何新增 ADR

1. 选择下一个可用编号（ADR-012、ADR-013、……）。
2. 打开标准模板的副本，并填入七个部分。
3. 在下方添加一个 `## ADR-NNN` 章节。
4. 在 "Decision Index" 表格中追加一行。
5. 合并前与团队一起评审该记录。

每条 ADR 都应基于项目实际代码与历史。不要凭空编造代码库中不存在的架构细节。

---

## ADR-001：契约层隔离

**状态：** Accepted
**版本：** 自早期打包起引入；截至 v1.2.0 仍然有效

**背景**

movie-narrator 由一组包（package）构成——核心的 `movie_narrator` 引擎、一个 `web` 包，以及若干插件。早期，这些包直接互相导入内部模块，导致依赖图纠缠不清，使得在不破坏插件的情况下演进引擎变得不可能，也让跨包版本不匹配的问题难以诊断。

**决策驱动因素**

- 各包直接互相导入内部模块，导致依赖图纠缠不清。
- 引擎在不破坏插件的情况下无法演进，跨包版本不匹配也难以诊断。
- 跨包兼容性需要在加载时可通过机器校验。

**备选方案**

- 为所有包建立集中式服务注册中心（已否决：它掩盖了真实耦合，且未能解决版本问题）。
- 允许直接内部导入但记录边界（已否决：文档约定无法被强制执行，依赖图依然混乱）。

**决策结果**

我们建立了一个稳定的契约表面：`web` 包和每个插件只允许依赖 `movie_narrator.contract`。内部模块不允许跨包边界导入。包之间的兼容性由一个采用语义化版本管理的 `CONTRACT_VERSION` 常量约束，撰写本 ADR 时为 `(0, 9, 5)`。任何对契约的破坏性变更都必须以符合语义化版本规则的方式提升契约版本，以便使用方在加载时检测兼容性。

**后果**

- 正面：依赖图现在无环且可测试；插件与内部实现细节解耦；兼容性可通过 `CONTRACT_VERSION` 机器化校验。
- 负面：契约层必须保持稳定，并成为变更的瓶颈；任何新增的共享能力都必须先加入契约，这带来少量额外的流程负担。

**参考资料**

- `CHANGELOG.md`
- `docs/ARCHITECTURE.md`
- `src/movie_narrator/contract.py`

---

## ADR-002：采用提供者注册表而非工厂

**状态：** Accepted
**版本：** 自 v0.5.1+ 采用

**背景**

TTS、vision、LLM 和 research 的提供者此前是通过经典工厂模式创建的。工厂必须了解每一个提供者，因此新增一个提供者意味着修改工厂以及通常还有核心分发逻辑。此外还存在一个遗留的工厂回退机制，导致行为不一致且难以推理。

**决策驱动因素**

- 工厂必须了解每一个提供者，因此新增提供者意味着修改工厂和分发逻辑。
- 遗留的工厂回退机制导致行为不一致且难以推理。
- 分发需要统一、明确且类型安全。

**备选方案**

- 集中式提供者工厂（已否决：每新增一个提供者都必须修改它）。
- 在注册表之外保留遗留工厂回退（已否决：两条分发路径导致行为不一致）。

**决策结果**

从 v0.5.1 开始，提供者分发仅使用注册表。提供者通过装饰器自行注册——`@register_tts`、`@register_vision`、`@register_llm`、`@register_research`——引擎在运行时按名称查找它们。遗留的工厂回退机制已被移除。如果某个工厂（或任何代码路径）返回的实例不是一致的 ABC 实例，则会抛出 `TypeError`，而不是静默继续。

**后果**

- 正面：新增提供者是纯粹的增量变更（注册 + 装饰）；分发统一且明确；类型安全由 `TypeError` 检查保证。
- 负面：注册是隐式的，因此未被导入的提供者不可用；声明提供者与使用提供者之间引入了一小层间接关系。

**参考资料**

- `CHANGELOG.md`
- `docs/PLUGIN_DEVELOPMENT.md`
- `src/movie_narrator/providers/registry.py`

---

## ADR-003：软步骤的优雅降级

**状态：** Accepted
**版本：** 在 16 步流水线中生效

**背景**

movie-narrator 流水线是一条 16 步处理链。部分步骤具有软依赖（可选库、可选上游数据），这些依赖未必在每个环境中都存在。对任何缺失部分都硬失败会让整个流水线变得脆弱，并阻碍生成部分结果。

**决策驱动因素**

- 部分步骤具有软依赖，这些依赖未必在每个环境中都存在。
- 对缺失依赖硬失败会让流水线变脆弱，并阻碍部分结果生成。
- 当正确性至关重要时，操作者需要能够强制失败。

**备选方案**

- 对每个步骤都硬失败（已否决：过于脆弱，且会阻止部分结果生成）。
- 始终跳过软步骤且无严格覆写（已否决：当正确性至关重要时，操作者无法强制失败）。

**决策结果**

软步骤——`research`、`align`、`scene`、`match`、`bgm`、`translate`、`qa_gate` 和 `export_clips`——会优雅降级：当可选依赖缺失或上游数据不可用时，该步骤被软跳过，流水线继续执行下一步。`--strict` 标志会将此行为转换为硬中止，使严格运行大声失败而非生成部分输出。任何硬步骤（其输出是所有下游步骤所必需的步骤）失败时会立即终止流水线。

**后果**

- 正面：流水线具备韧性，当部分内容缺失时仍能产出有用输出；操作者可有意识地选择严格行为。
- 负面：软失败可能是静默的，因此用户可能不会注意到被跳过的步骤，除非检查日志；必须为每个步骤记录严格/软的区别。

**参考资料**

- `CHANGELOG.md`
- `docs/ARCHITECTURE.md`
- `src/movie_narrator/pipeline/runner.py`

---

## ADR-004：熔断与重试策略

**状态：** Accepted
**版本：** 于 v0.9.1 引入

**背景**

流水线会调用外部服务——LLM、TTS、TMDB 和 VLM——这些服务会受到瞬时故障、限流和短暂中断的影响。天真的重试可能反复冲击一个故障中的服务，而完全缺乏重试则会在第一次小故障时就让运行失败。

**决策驱动因素**

- 外部服务（LLM、TTS、TMDB 和 VLM）会受到瞬时故障、限流和短暂中断的影响。
- 天真的重试可能反复冲击一个故障中的服务，而完全不重试会让本可成功的运行在第一次小故障时就失败。
- 重试需要分散在时间中，避免对上游服务形成惊群冲击。

**备选方案**

- 无限制的固定间隔重试（已否决：存在反复冲击故障服务的风险）。
- 完全不重试（已否决：瞬时故障会导致本可成功的运行失败）。
- 有熔断但无退避（已否决：对上游的恢复仍会过于猛烈）。

**决策结果**

我们在 `reliability/circuit_breaker` 中加入了一个熔断器，采用 `CLOSED → OPEN → HALF_OPEN` 状态机。`@circuit_guard` 装饰器保护对 LLM、TTS、TMDB 和 VLM 的调用。当熔断器打开时，调用会快速失败而非重试。重试行为由一个 `RetryPolicy` 管理，它实现了带抖动（jitter）的指数退避，因此重试会随时间分散开来，不会对上游服务形成惊群冲击。

**后果**

- 正面：外部依赖故障得到控制，服务宕机时快速失败，并会自动恢复；重试具备退避感知，可避免过载。
- 负面：熔断状态带来可观测性要求；阈值与重试预算的调节对环境敏感，需要按服务进行校准。

**参考资料**

- `CHANGELOG.md`
- `src/movie_narrator/reliability/circuit_breaker.py`
- `src/movie_narrator/reliability/retry.py`

---

## ADR-005：任务检查点与断点续跑

**状态：** Accepted
**版本：** 于 v0.9.2 引入

**背景**

渲染一段解说视频是长期运行的任务。如果在运行中途进程崩溃或机器重启，所有工作都会丢失，任务必须从头开始，浪费大量时间和成本。

**决策驱动因素**

- 渲染是长期运行的任务；运行中途崩溃会丢失所有工作。
- 从头重启会浪费大量时间和成本。
- 失败或取消的运行需要可被检查并重新运行。

**备选方案**

- 长时间任务从头重启（已否决：对长期运行任务而言过于浪费）。
- 为每个任务永远持久化检查点（已否决：没有保留策略会导致存储膨胀）。

**决策结果**

我们在 `cloud/checkpoint` 中引入了任务检查点。每个流水线步骤之后，都会持久化一个 `TaskCheckpoint`。崩溃时，任务从其最后一个已持久化检查点的下一步续跑，而不是从头开始。当任务达到 `COMPLETED` 时，其检查点会被删除；当任务以 `FAILED` 或 `CANCELLED` 结束时，检查点会被保留，以便检查并重新运行该次运行。

**后果**

- 正面：长时间运行对崩溃具备韧性；部分进度得以保留且续跑成本低；失败/取消的运行可以被检查。
- 负面：检查点持久化增加了 I/O 和存储开销；必须管理失败运行的过期检查点，以避免累积。

**参考资料**

- `CHANGELOG.md`
- `src/movie_narrator/cloud/checkpoint.py`
- `docs/sdk/cloud.md`

---

## ADR-006：批量处理与调度

**状态：** Accepted
**版本：** 于 v0.9.3 引入

**背景**

用户希望一次性提交大量解说任务并让它们按计划运行，而不是手动触发每个任务。调度器需要解析类 cron 表达式，同时不能引入笨重的外部依赖。

**决策驱动因素**

- 用户希望一次性提交大量任务并让它们按计划运行。
- 需要解析类 cron 表达式，同时不能引入笨重的外部依赖。
- 调度器应当保持轻量且可移植。

**备选方案**

- 使用外部调度器库（已否决：为一个小需求引入了笨重依赖）。
- 完整功能的 cron 支持（已否决：对当前调度需求属于过度设计）。

**决策结果**

我们新增了 `BatchRequest`，支持每批 1–50 个任务。调度由 `cloud/scheduler` 处理，其中包含一个无依赖的 5 字段 cron 解析器（分、时、日、月、周）。一个 `JobScheduler` 在后台线程中运行，并根据解析出的计划调度任务。

**后果**

- 正面：在无外部调度器依赖的情况下支持批量提交和类 cron 调度；调度器轻量且可移植。
- 负面：5 字段 cron 解析器比完整 cron 更简单（不支持秒或特殊语法），因此非常复杂的计划不受支持；必须强制并明确沟通批量限制。

**参考资料**

- `CHANGELOG.md`
- `src/movie_narrator/cloud/scheduler.py`
- `src/movie_narrator/cloud/models.py`

---

## ADR-007：DLQ 与分布式渲染

**状态：** Accepted
**版本：** 于 v0.9.4 引入

**背景**

反复失败的任务可能阻塞队列或被静默丢弃，使故障难以追踪。另外，部分任务产生极长的渲染时间，我们希望在确实值得时考虑将渲染分流到更多节点。

**决策驱动因素**

- 反复失败的任务可能阻塞队列或被静默丢弃，使故障难以追踪。
- 部分任务产生极长的渲染时间，需要分流到更多节点。
- 分布式渲染应仅在确实值得时使用，并具备安全的回退。

**备选方案**

- 静默丢弃失败任务（已否决：故障变得不可见且不可恢复）。
- 始终使用分布式渲染（已否决：对短任务而言开销不值得）。
- 从不分发（已否决：单节点渲染可能耗时过长）。

**决策结果**

我们引入了死信队列（dead-letter queue, DLQ）。无法恢复地失败的任务会进入终态 `DEAD`，稍后可对它们进行 `replay`。分布式渲染是一个条件功能：只有当单节点渲染超过 10 分钟且存在多个可用节点时才会触发；如果分布式渲染失败，任务会回退到本地渲染。

**后果**

- 正面：失败任务显式可见，并可通过 replay 恢复；当成本划算时渲染可以扩展，并安全回退到本地。
- 负面：DLQ 和 replay 需要运维工具；条件式分布式触发增加了复杂度，以及一条必须测试的回退路径。

**参考资料**

- `CHANGELOG.md`
- `src/movie_narrator/cloud/dlq.py`
- `src/movie_narrator/cloud/distributed.py`

---

## ADR-008：配置边界

**状态：** Accepted
**版本：** 在流水线中生效

**背景**

配置在基础设施设置与流水线行为设置之间混杂，且各来源之间的优先级不明确。这导致难以判断哪个值实际生效，也使本地与生产环境的配置不一致。

**决策驱动因素**

- 配置在基础设施与流水线行为设置之间混杂，优先级不明确。
- 存在难以判断哪个值实际生效的困惑。
- 本地与生产环境的配置不一致。

**备选方案**

- 用单一配置文件管理一切（已否决：基础设施与行为混杂，且有机密泄露风险）。
- 仅 YAML 模式且无 CLI 覆写（已否决：操作者无法按每次运行覆写行为）。

**决策结果**

我们将配置拆分为两个清晰来源。`.env` 持有基础设施设置，并使用 `MN_` 前缀。`job.yaml` 持有流水线行为设置。优先级为：`CLI` 参数 > `job.yaml` > 内联默认值。这提供了一个可预测的分层模型，其中最具体的来源胜出。

**后果**

- 正面：基础设施与行为被清晰分离；优先级明确且可预测；机密和特定环境的值被排除在任务文件之外。
- 负面：用户必须知道哪个设置位于哪个文件中；双文件拆分增加了一小点上手成本。

**参考资料**

- `CHANGELOG.md`
- `src/movie_narrator/config.py`
- `docs/PACKAGING.md`

---

## ADR-009：输入净化与安全

**状态：** Accepted
**版本：** 于 v0.9.5 引入

**背景**

任务提交 API 接受任意负载。格式错误或恶意的输入可能进入流水线并导致意外行为；没有大小上限，且 CI 流水线没有安全扫描或测试覆盖率门槛。

**决策驱动因素**

- 任务提交 API 接受任意负载，且没有大小上限。
- 格式错误或恶意的输入可能进入流水线并导致意外行为。
- CI 流水线缺乏安全扫描和测试覆盖率门槛。

**备选方案**

- 接受任意负载并在流水线深处净化（已否决：失败发生得晚且不可预测）。
- 无大小限制（已否决：存在内存/资源耗尽风险）。
- 无安全扫描（已否决：已知漏洞将不被察觉）。

**决策结果**

`TaskRequest` 现在会校验每个字段。恶意或无法解析的负载会被拒绝并返回 HTTP `400`。大于 `1MiB` 的负载会被拒绝并返回 HTTP `413`。在 CI 侧，使用 `Bandit` 和 `pip-audit` 增加了安全扫描，并强制了 `80%` 的测试覆盖率门槛。

**后果**

- 正面：API 尽早拒绝格式错误与超大的输入；安全态势显著增强，CI 能捕获已知漏洞和覆盖率回退。
- 负面：严格校验可能拒绝此前被容忍的合法边界情况；覆盖率门槛和安全扫描增加了 CI 时间。

**参考资料**

- `CHANGELOG.md`
- `src/movie_narrator/utils/sanitize.py`
- `src/movie_narrator/cloud/models.py`

---

## ADR-010：国际化与本地化语音

**状态：** Accepted
**版本：** 于 v0.9.6 引入

**背景**

引擎生成解说脚本时不具备语言感知，TTS 语音选择也未与目标语言绑定。这导致本地化输出在语言和语音选择上不一致。

**决策驱动因素**

- 脚本生成缺乏语言感知能力。
- TTS 语音选择与目标语言不绑定。
- 语音选择需要确定且具备语言感知。

**备选方案**

- 无论语言如何都使用单一全局默认语音（已否决：会产生语言/语音不匹配）。
- 不做语音映射，依赖提供者的默认值（已否决：不确定且非本地化）。

**决策结果**

我们增加了语言感知的脚本生成与匹配，默认语言设为 `zh`。TTS 语音选择通过 `voice_map` 和 `resolve_voice` 处理，优先级顺序为：显式 `voice` > 按语言覆写 > 默认映射 > `default_voice`。这使得语音选择确定且具有语言感知。

**后果**

- 正面：输出始终本地化；语音选择可预测且可显式覆写；默认语言固定为 `zh`。
- 负面：随着语言增加，必须维护语音映射；必须记录解析优先级，以便用户理解先后次序。

**参考资料**

- `CHANGELOG.md`
- `src/movie_narrator/tts/voice_map.py`
- `src/movie_narrator/pipeline/script.py`

---

## ADR-011：许可禁区与 FFmpeg 捆绑策略

**状态：** Accepted
**版本：** 于 v1.1.0 记录

**背景**

引擎在引入第三方组件或打包二进制时面临合规风险。一次内部合规审查识别出两个前瞻性风险：(R4) 部分被推荐的依赖在许可条款上与 AGPL-3.0-or-later 或平台服务条款不兼容；(R3) 捆绑 FFmpeg 二进制的分发义务远高于纯命令行调用。

**决策驱动因素**

- 部分第三方选项的许可条款与 AGPL-3.0-or-later 或平台服务条款不兼容（如自定义定价或未声明许可）。
- 项目不得背负其无法履行的义务（如保留所有权利代码、付费受限许可、违反平台规则的爬虫）。
- 捆绑 FFmpeg 二进制会改变相对于调用外部 `ffmpeg` 的分发义务。
- 策略需在文档层面强制，避免贡献者无意重新引入禁区依赖。

**备选方案**

- 采纳被推荐但受限的组件（已否决：许可/平台服务条款冲突）。
- 复制未声明许可的参考代码（已否决：默认保留所有权利，构成侵权）。
- 将 GPL/LGPL FFmpeg 二进制捆绑进 Windows 发行包（现已否决：引发源码/二进制再分发义务；推迟至有文档记录的分发决策）。

**决策结果**

我们采用一份明确的**禁区清单**，这些内容不得引入核心或捆绑发行包：

- **Remotion**（自定义许可；公司超 3 人需付费）——改用 MIT 替代方案（如 revideo）或 HTML + 无头截图。
- **TypeTale 源码**（许可未声明）——不得复制；自行实现，或使用明确 MIT 许可的参考并在 `NOTICE` 中声明。
- **素材爬取爬虫**（针对流媒体平台的 yt-dlp/Bilibili/Playwright）——平台服务条款 + 版权；坚持"用户自备素材"路线。空镜素材仅可来自公有领域来源（Archive.org、NASA、Wikimedia、Pexels）。
- **声音克隆**（针对任意声音的 IndexTTS/CosyVoice）——声音权法律风险；若未来接入，仅可克隆用户本人/已授权声音。

对于 **FFmpeg**：引擎通过共享的 `utils/ffmpeg_bin.ffmpeg_bin()` 策略解析二进制 —— `MN_FFMPEG_BIN` 覆盖 → 捆绑的 imageio-ffmpeg 构建（功能完整的静态构建，免疫于被精简/残缺的系统 ffmpeg 遮蔽 PATH）→ `PATH` 上的系统二进制 → 裸 `"ffmpeg"`。该捆绑构建是作为 moviepy 的传递依赖（imageio-ffmpeg）提供的，而非项目自身打包进发行物的二进制，因此不引入再分发义务。若未来某个 Windows 发行包确实需要自行捆绑 FFmpeg 二进制，必须 (a) 优先选择 LGPL 构建，(b) 附带 `THIRD_PARTY_NOTICES` 文件（许可全文 + 源码 URL + 构建配置），(c) 在发布前在此记录该决策。`mn doctor` 命令是检测并引导安装 FFmpeg 而非捆绑它的预期载体。

**后果**

- 正面：禁区清单防止意外的许可/平台服务条款违规；FFmpeg 保持外部依赖，无再分发义务；合规姿态有文档记录且可审查。
- 负面：贡献者在添加依赖前必须核对禁区清单；FFmpeg 捆绑策略对项目自身发行物仍停留在文档层面——项目自身并未把二进制打包进发行物，因此 `THIRD_PARTY_NOTICES` 文件是未来捆绑时才需要的条件性产物（捆绑的 imageio-ffmpeg 构建仅作为 moviepy 传递依赖提供）。`mn doctor` 命令（检测并引导安装 FFmpeg 而非捆绑）已实现。

**参考资料**

- `docs/PACKAGING.md`
- `pyproject.toml`

---

## ADR-012：线性兼容的 DAG 契约

**状态：** 已接受
**版本：** 自 v1.3.0 引入

**背景**

16 个流水线步骤在 `run_pipeline` 中构成固定线性序列。插件可以注入步骤，但无法声明其读取/写入哪些数据，因此没有机器可校验的方式来确认插件的数据依赖与线性执行兼容——也没有为未来并行调度打下基础。

**备选方案**

- 现在就把运行器改造成并行 DAG 执行器（否决：风险高、当前无需求；渲染/TTS 是仅有的慢步骤且已有缓存）。
- 为刻意的重新执行扩展 `mn resume --from-step`（否决：resume 语义是崩溃恢复——从最后一个已完成步骤之后继续；混淆两种语义会让下游陈旧的成功状态静默跳过工作）。

**决策结果**

步骤可在注册时声明粗粒度的 `inputs` / `outputs`（Context 属性或 `ctx.metadata` 键名）与 `depends_on`（其输出被本步骤读取的上游步骤）。新增 `pipeline/dag.py` 提供 `StepSpec`、`build_step_graph`、`validate_linear_order`（对未注册/后置/成环依赖给出劝告性警告）与 `topological_order`（带线性决胜的 Kahn 算法）。运行器保持线性 for 循环；对任何线性兼容的注册表，`topological_order` 与 `step_registry.ordered_names()` 相等。重新执行是独立的 `mn rerun --from STEP` 命令，会失效下游软步骤的状态。

**后果**

- 正面：插件依赖错误在验证时即可发现；步骤图可经契约面检视；未来并行调度无需语义变更。
- 负面：声明是劝告性的（运行器无法对可变 `Context` 状态强制执行）；插件作者必须保持 `depends_on` 指向前方，否则接受验证警告。

**参考资料**

- `src/movie_narrator/pipeline/dag.py`
- `src/movie_narrator/pipeline/registry.py`
- `docs/PLUGIN_DEVELOPMENT.md`
## ADR-013：服务与产品语义——租户、套餐与 Webhook

**状态：** Accepted
**版本：** 于 v1.3.1 记录

**背景**

v1.3.1 将引擎从单用户工具推进为服务化接口：运营方需要审计“谁提交了什么”（主体/租户）、按提交限制产品配额（套餐/权益），以及任务终态的推送通知（Webhook）。ROADMAP 明确将完整的多租户隔离（“完整多租户隔离”）排除在本版本之外，且 v1.2 引入的本地免登录单用户路径默认不得改变。

**决策驱动因素**

- 向后兼容：不设置任何新环境变量时，v1.2 的全部行为（环回免认证、无配额限制、无推送通知）逐字节保持不变。
- 不引入新依赖（httpx 已是运行时依赖；重试框架已存在）。
- 管线（`src/movie_narrator/pipeline/`）必须保持与服务无关——服务策略属于 cloud 层。
- 故障隔离：Webhook 投递绝不能影响任务结果。

**备选方案**

- *租户——立即做完整行级隔离*（已否决）：与尚未定义的存储模式和认证模型强耦合；真正的隔离工作是明确的长期目标。
- *租户——按租户发 API 密钥*（已否决）：引入与打标无关的密钥管理和分发问题；推迟。
- *租户——打标/范围限定 MVP*（已采纳）：`tenant_id`/`principal` 记录在任务上（增量列 + JSON），在响应与审计记录中呈现；非默认租户只能看到自己任务的产物，`default` 租户保留完整的单租户视图。
- *套餐——在管线内部执行（如 `pipeline/render.py`）*（已否决）：将产品策略耦合进引擎步骤；渲染准入前置检查将在 v1.3.2 单独落地于该处。
- *套餐——API 侧校验 + worker 侧注入*（已采纳）：提交时按解析出的 `Plan` 校验（违规返回 403 `entitlement_denied`）；worker 通过现有 `render_template.watermark_text` 参数注入强制水印，并在套餐禁用 GPU 编码时强制 CPU 编码提示，同时在 `ctx.metadata` 与 `metadata.json` 中记录 `plan`/`plan_policy` 块。
- *Webhook——SQLite 投递表*（已否决）：对只追加、容忍丢失的数据引入与任务存储的模式耦合。
- *Webhook——基于 Celery/队列的投递*（已否决）：对“发射后不管”的旁路通道而言依赖过重。
- *Webhook——现在采用 JSONL 投递日志 + HMAC 签名*（已采纳）：每次尝试在任务存储旁追加一行 JSONL；请求体以十六进制 HMAC-SHA256 签名；重试遵循 `Retry-After`；消费方以事件 id 去重。重发 API 与按租户的 Webhook 端点推迟。

**决策结果**

- 服务语义全部为增量且默认关闭：`default` 套餐无限制，未认证调用方解析为主体 `local` / 租户 `default` / 套餐 `default`，未设置 `MN_WEBHOOK_URLS` 时 Webhook 关闭。
- 套餐是数据（`cloud/entitlements.py`），只在两个点执行——`cloud/api.py` 的提交校验与 `cloud/worker.py` 的策略注入。
- Webhook 投递被隔离在 `cloud/webhooks.py` 的守护线程池之后；失败仅记录并写入 `webhook_deliveries.jsonl`。
- 完整的租户隔离存储、按租户的 API 密钥、重发 API 与按租户的 Webhook 端点仍为后续工作（依 ROADMAP）。

**后果**

- 正面：引擎在不触碰管线的情况下获得服务/产品能力面；每一项限制都可观测（审计记录、`plan_policy` 元数据、投递记录）；本地路径不变。
- 负面：租户限定只是打标而非隔离——运营方不得把 `X-MN-Tenant` 当作安全边界；Webhook 投递是尽力而为（暂无重发 API）；提交时的套餐限制是启发式判断（产物体积估算为近似值）。

**参考资料**

- `src/movie_narrator/cloud/entitlements.py`、`src/movie_narrator/cloud/webhooks.py`、`src/movie_narrator/cloud/dashboard.py`
- `docs/DEPLOYMENT.md`（配置）、`docs/OBSERVABILITY.md`（仪表盘汇总 API）
- `.env.example`（v1.3.1 变量）

---

## ADR-014：可选开启的 OpenTelemetry 追踪

**状态：** Accepted
**版本：** 于 v1.4.0 记录

**背景**

ROADMAP 中的预留项“基于真实 Span 的追踪（task → step/provider/subprocess）”要求为管线与云服务提供厂商中立的分布式追踪。OpenTelemetry 是行业默认选择，但其 SDK（尤其是 OTLP 导出器）会拖入重量级依赖链（protobuf、grpcio），而本项目的引擎刻意保持极小的依赖面——况且大多数单机用户从不导出追踪数据。

**决策驱动因素**

- 关闭即零依赖：默认安装与 CI 门槛必须逐字节不受影响；未显式安装并启用时，追踪的开销必须是零。
- 管线不耦合：插桩属于运行器/工作线程/提供者边界的 1–2 行钩子，而不是侵入各步骤实现内部。
- 基数有界：稳定、低基数的 Span 名称（`mn.task`、`mn.step`、`mn.provider`、`mn.subprocess`），可变部分作为属性携带。
- 厂商中立：用户必须能自带后端（OTLP、Jaeger、Zipkin），而引擎无需捆绑传输层依赖。

**备选方案**

- *始终开启并内置导出器*（否决）：将所有部署与 SDK 绑定，在没有退出开关的情况下增加开销；与“关闭即零依赖”的要求矛盾。
- *在 extra 中捆绑 `opentelemetry-exporter-otlp`*（否决）：会把 protobuf/grpcio 拖进 `[otel]` extra；需要 OTLP 的用户可自行安装导出器。
- *自研 Span/事件格式 + 可插拔接收端*（否决）：重复造上下文传播、采样与导出器生态的轮子；每个后端集成都会变成引擎代码。
- *可选开启的 `opentelemetry-api` + `opentelemetry-sdk`，导入受保护、缺省空操作*（选定）：`MN_TRACING` 控制 Span 创建；未安装 extra（或开关关闭）时所有辅助函数都是零开销空操作。`MN_TRACING_EXPORTER=none`（默认）通过无导出器的 SDK Provider 创建 Span 并在结束时丢弃；`console` 使用 SDK 内置的 `ConsoleSpanExporter`。若检测到已注册的全局 Tracer Provider（例如用户自备的 OTLP 管道），引擎原样使用、不做覆盖。

**决策结果**

- `movie_narrator.tracing` 暴露四个 Span 工厂（`start_task_span`、`start_step_span`、`start_provider_span`、`start_subprocess_span`），返回上下文管理器句柄；这些工厂属于公开契约（v1.4.0 导出）。
- 挂接点：管线运行器为每步执行打开一个 Span；工作线程打开任务 Span（所有步骤 Span 的父级）；`utils/llm.py` 与 `pipeline/tts.py` 为每次 LLM 调用 / 每段 TTS 打开一个提供者 Span；`utils/process.py` 为 ffmpeg 子进程包裹子进程 Span（惰性导入使该模块保持纯标准库）。
- `pyproject.toml` 新增 `[otel]` extra（`opentelemetry-api`/`opentelemetry-sdk` `>=1.20,<2`）；CI 永不安装它，任何测试都不依赖真实包（测试注入伪造的 `opentelemetry` 模块）。

**后果**

- 正面：ROADMAP 追踪项以零默认行为变更落地；运维可从关联 ID 检索平滑升级到真实追踪而无需重新插桩；导出器的选择权留给部署方。
- 负面：`none` 模式下 Span 会被创建后丢弃（启用期间存在小的可度量开销）；用户注册全局 Provider 必须*先于*启用 `MN_TRACING`（引擎仅在无全局 Provider 时自动注册）；可选 SDK 的版本对齐受 `<2` 约束。

**参考资料**

- `src/movie_narrator/tracing.py`、`src/movie_narrator/pipeline/runner.py`、`src/movie_narrator/cloud/worker.py`、`src/movie_narrator/utils/llm.py`、`src/movie_narrator/pipeline/tts.py`、`src/movie_narrator/utils/process.py`
- `docs/OBSERVABILITY.zh-CN.md` §4.2（分布式追踪）
- `.env.example`（v1.4.0 变量）
## ADR-015：字幕交付模式与输出稳定性承诺

**状态：** Accepted
**版本：** 于 v1.4.1 引入

**背景**

字幕一直以来都是硬烧录的（SRT → PIL 图像在渲染时合成），同时落盘 SRT 外挂文件——ROADMAP 中"可选字幕交付方式"的长期项一直被阻塞在缺少输出契约上。v1.3.0 的 `deliverable_manifest.json`（schema_version + 校验和）使其成为可能。

**决策驱动因素**

- 默认（`burned`）必须与 v1.3.2 字节级一致；不改契约表面。
- 交付模式绝不能导致渲染失败——降级而非中止。
- mov_text 是 MP4 系编码：muxed 只能在 mp4 系容器中表达。

**备选方案**

- 默认总是混流（否决：改变所有现有渲染；mov_text 播放器支持参差；烧录仍是唯一普遍可见的选项）。
- 渲染后由独立步骤烧录字幕（否决：复制合成布局——位置/安全区逻辑会在两条烧录路径之间漂移）。
- 三态 `subtitle_delivery` 参数 + muxed→burned 降级（采纳）。

**决策结果**

`subtitle_delivery: burned | sidecar | muxed`（默认值在合并时丢弃，镜像 `timeline_export_backend`）。sidecar/muxed 跳过全部烧录——包括素材缺失的文字回退卡，burned 之外绝不烧录任何文字。muxed 将模式选定的 SRT 映射为软字幕 `mov_text` 轨道（`-metadata:s:s:0 language=`，ISO 639-2），在 SRT 缺失 / 非 mp4 容器时降级为 burned，并在元数据中记录 `subtitle_delivery_used`、`subtitle_mux_language` 与降级原因。STABILITY.md 新增窄范围输出承诺：清单 schema v1 稳定（仅可新增），默认交付物集合在 1.x 的 patch/minor 间兼容；校验和仅是信息，不构成契约。

**后果**

- 正面：sidecar/muxed 渲染更快；播放器可开关/样式化软字幕；输出承诺有版本化并被测试守护。
- 负面：无素材作业的 sidecar/muxed 渲染只显示背景色卡；mov_text 的样式/语言支持依赖播放器（按原样提供）。

**参考资料**

- `src/movie_narrator/workflow/schema.py`、`src/movie_narrator/pipeline/render.py`
- `docs/STABILITY.zh-CN.md`（输出格式兼容性）、`docs/METADATA_SCHEMA.zh-CN.md`、`examples/job.example.yaml`
## ADR-016：通过 FCP7 XML 交换格式对接 Premiere

**状态：** Accepted
**版本：** 记录于 v1.4.2

**背景**

ROADMAP 长期项要求在 OTIO + 剪映之外扩展时间线适配器；目标创作者主流的非编软件是 Adobe Premiere Pro，但它既不导入 `.otio`，也不导入剪映草稿。

**决策驱动因素**

- 无新依赖——插件必须保持不装可选依赖也可导入（与剪映路径一致）。
- 统一的 `Timeline` 中间表示与步骤分发除新增后端分支外不得改动。

**备选方案**

- *Premiere SDK / `.prproj` 格式*（否决）：二进制且未公开；需要宿主应用与重量级绑定。
- *把 opentimelineio 变成硬依赖*（否决）：会给纯标准库插件拖入额外依赖；OTIO 仍作为 `otio` 后端的可选依赖。
- *在现有 IR 上写 FCP7 XML（`xmeml`）*（选定）：纯文本格式，Premiere 原生导入（`文件 > 导入`），标准库 `xml.etree.ElementTree`。

**决策结果**

- 新增 `premiere` 后端（`premiere.py`）：序列帧率取自渲染元数据（默认 24），视频轨 clipitem 带帧级入出点与文件引用，文本覆盖为 generatoritem，旁白音频排在音频轨；输出 `<movie>.xml`。核心白名单 `VALID_TIMELINE_EXPORT_BACKENDS` 新增 `"premiere"`；契约与管线零改动。

**后果**

- 正面：Premiere 用户零新依赖获得一键交接；IR 在不改步骤逻辑的情况下吸收第三个后端。
- 负面：FCP7 XML 是遗留交换格式——生成器文本样式极简，草稿是起点而非最终对版。

**参考资料**

- `examples/plugins/timeline_export/movie_narrator_timeline_export/premiere.py`、`docs/BEST_PRACTICES.zh-CN.md`（时间线导出一节）

## ADR-017：HDR/4K 管线——10-bit + 色彩元数据、仅 CPU 的 10-bit 编码

**状态：** Accepted
**版本：** v1.5.0 引入

**背景**

渲染一律输出无标签的 8-bit `yuv420p` 码流——无法请求 10-bit 或 HDR10，输出不带任何色彩元数据，QA 也无法区分 4K 成片与 1080p 成片。

**决策驱动因素**

- 默认值保持字节级一致：8-bit 编码 argv 不变；8/`sdr` 默认值在合并时丢弃（镜像 `subtitle_delivery`）；不改契约、无新依赖。
- 结论必须可验证：管线记录实际渲染内容（`render_pixel` 元数据），视频 QA 据此交叉校验成片。

**备选方案**

- *现在就探测 GPU 10-bit 能力*（否决）：本项目支持的 H.264 GPU 后端（NVENC / VAAPI / VideoToolbox）只支持 8-bit——没有可探测的东西；HEVC main10 是后续工作。10-bit 渲染强制 libx264 并记录 `10bit_gpu_unsupported`。
- *完整 HDR 母版——色调映射 + mastering-display / MaxCLL / MaxFALL SEI*（v1.5.0 否决）：需要逐场景分析与播放器特定元数据；v1.5.0 只交付标签级 HDR10。
- *静默接受任何 pix_fmt / 色彩标签*（否决）：编码器回退会静默劣化 10-bit 任务；记录计划 + QA 交叉校验才能让位深真实可信。

**决策结果**

`render_bit_depth`（8|10）+ `render_color_space`（`sdr`|`hdr10`）作业参数：10-bit = `yuv420p10le` + libx264 `high10`，仅限 CPU；hdr10 强制 10-bit（记录备注）；sdr 显式打 bt709 标签。色彩标签写在 STAGE-2 复制混流处（libx264 会丢弃编码级色彩选项）。`render_pixel` 落入 metadata.json；视频 QA 校验 pix_fmt / color_transfer 以及 4K 级尺寸精确性（宽 >= 3840 或高 >= 2160）。

**后果**

- 正面：4K / 10-bit / HDR10 成为一等、可验证的输出；QA 能捕捉静默的位深或色彩回退。
- 负面：真正的 HDR 母版（色调映射、SEI 元数据）明确不在范围内；10-bit 编码受限于 CPU；忽略 HDR 标签的播放器会显示发灰的颜色。

**参考资料：** `src/movie_narrator/pipeline/render.py`、`src/movie_narrator/workflow/schema.py`、`src/movie_narrator/utils/video_qa.py`；`docs/METADATA_SCHEMA.zh-CN.md`（render_pixel）、`docs/BEST_PRACTICES.zh-CN.md`（4K 与 10-bit 渲染）、`examples/job.example.yaml`
## ADR-018: 社区预设是经过校验的数据，而非代码

**状态:** 已接受
**版本:** 记录于 v1.5.1

**背景**

预设样式层稳定之后，ROADMAP 解除了跨安装共享解说预设的限制。已安装的预设可能来自 URL 和其他作者，因此绝不能成为任意代码执行的攻击面。

**决策驱动因素**

- `mn presets install <url>` 绝不能执行不受信任的代码。
- 预设需要覆盖白名单内作业参数的全部表达能力。
- 校验必须复用现有的单一来源白名单——避免漂移。

**备选方案**

- *可执行的预设包*（否决）：运行不受信任来源的代码；沙箱将成为永久的负担。
- *仅允许签名安装*（否决）：没有可用的 PKI/信任基础设施；阻碍了即时分享。
- *按作业参数白名单校验的 YAML 数据*（选定）：纯数据；复用 `workflow/load.py`（`_ALLOWED_TOP` + `JobConfig`）；记录哈希值。

**决策结果**

- `presets/community.py`：install/list/uninstall/load；存储于 `~/.movie-narrator/presets/<name>.yaml` + `registry.json`（来源、sha256、元数据）。仅允许 https（256 KiB 上限、15 秒超时）；YAML 的 `preset.name` 作为注册表键；文件在每次加载时重新校验；`get_preset` 仅在没有内置预设匹配时解析社区预设。

**后果**

- 正面：白名单复用的安全论证——社区预设只能调优 `job.yaml` 已经约束的键，因此安装期校验永远不会与执行现实漂移；无需签名基础设施即可实现零信任安装。
- 负面：预设无法携带代码或任意 prompt 标签（标签仍仅限内置）；自定义行为必须落在白名单键之内。

**参考资料**

- `src/movie_narrator/presets/community.py`、`src/movie_narrator/workflow/load.py`、`docs/TUTORIAL.zh-CN.md`（社区预设一节）

---

## Decision Index

| # | ADR | 状态 | 版本 | 摘要 |
|---|-----|--------|---------|---------|
| ADR-001 | 契约层隔离 | Accepted | — | `web`/插件仅依赖 `movie_narrator.contract`；`CONTRACT_VERSION` (0,9,5) 管理兼容性 |
| ADR-002 | 采用提供者注册表而非工厂 | Accepted | v0.5.1+ | 通过 `@register_*` 装饰器仅使用注册表分发；移除遗留工厂回退 |
| ADR-003 | 软步骤的优雅降级 | Accepted | — | 软步骤在依赖/数据缺失时跳过；`--strict` 中止；硬步骤快速失败 |
| ADR-004 | 熔断与重试策略 | Accepted | v0.9.1 | CLOSED→OPEN→HALF_OPEN 熔断器，`@circuit_guard`，指数退避 + 抖动 |
| ADR-005 | 任务检查点与断点续跑 | Accepted | v0.9.2 | 每步 `TaskCheckpoint`；崩溃后续跑；`COMPLETED` 删除，`FAILED`/`CANCELLED` 保留 |
| ADR-006 | 批量处理与调度 | Accepted | v0.9.3 | `BatchRequest`（1–50）；无依赖 5 字段 cron；`JobScheduler` 后台线程 |
| ADR-007 | DLQ 与分布式渲染 | Accepted | v0.9.4 | `DEAD` 终态 + replay；条件式分布式渲染并回退到本地 |
| ADR-008 | 配置边界 | Accepted | — | `.env`（`MN_`，基础设施）vs `job.yaml`（行为）；CLI > job.yaml > 默认值 |
| ADR-009 | 输入净化与安全 | Accepted | v0.9.5 | 字段校验；HTTP 400/413；Bandit + pip-audit；80% 覆盖率门槛 |
| ADR-010 | 国际化与本地化语音 | Accepted | v0.9.6 | 语言感知生成（默认语言 `zh`）；`voice_map`/`resolve_voice` 优先级解析 |
| ADR-011 | 许可禁区与 FFmpeg 捆绑策略 | Accepted | v1.1.0 | 禁区清单（Remotion/TypeTale 代码/爬虫/声音克隆）；FFmpeg 经 `ffmpeg_bin()` 解析（优先 imageio-ffmpeg），项目自身不将二进制打包进发行物 |
| ADR-012 | 线性兼容的 DAG 契约 | Accepted | v1.3.0 | 步骤声明 `inputs`/`outputs`/`depends_on`；`pipeline/dag.py` 验证线性兼容（`topological_order` == 线性顺序）；运行器保持线性 |
| ADR-013 | 服务与产品语义——租户、套餐与 Webhook | Accepted | v1.3.1 | 租户/主体打标 MVP（非隔离）；套餐在 API 校验 + worker 注入两点执行（管线不动）；Webhook = JSONL 投递日志 + HMAC 签名，重发推迟 |
| ADR-014 | 可选开启的 OpenTelemetry 追踪 | Accepted | v1.4.0 | `movie_narrator.tracing` Span 工厂（task → step/provider/subprocess）；`[otel]` extra 仅含 api+sdk，不捆绑 OTLP；`MN_TRACING` 关闭（默认）即零开销空操作；已注册的全局 Provider 原样使用 |
| ADR-015 | 字幕交付模式与输出稳定性承诺 | Accepted | v1.4.1 | `subtitle_delivery` burned/sidecar/muxed，muxed→burned 降级（软字幕 mov_text 轨道，绝不导致渲染失败）；STABILITY 新增窄范围承诺（清单 schema v1 + 默认交付物集合） |
| ADR-016 | 通过 FCP7 XML 交换格式对接 Premiere | Accepted | v1.4.2 | `timeline_export` 插件新增 `premiere` 后端：标准库 FCP7 XML（`xmeml`）写入器，Premiere 原生导入；核心仅白名单变更 |
| ADR-017 | HDR/4K 管线：10-bit + 色彩元数据、仅 CPU 编码 | Accepted | v1.5.0 | `render_bit_depth` 8/10 + `render_color_space` sdr/hdr10：`yuv420p10le` / libx264 high10（仅 CPU，`10bit_gpu_unsupported` 回退），显式 bt709 / bt2020+smpte2084 混流标签，`render_pixel` 元数据；视频 QA 交叉校验 pix_fmt / 传递函数与 4K 级精确尺寸 |
| ADR-018 | 社区预设是经过校验的数据，而非代码 | Accepted | v1.5.1 | `mn presets install` 存储白名单内的 YAML 数据（绝不执行代码），复用 `job.yaml` 白名单 + schema 进行校验；记录 sha256，加载时重新校验；内置优先 |
