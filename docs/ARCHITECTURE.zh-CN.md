[![English](https://img.shields.io/badge/English-Architecture-blue)](ARCHITECTURE.md)
[![简体中文](https://img.shields.io/badge/简体中文-架构-green)](ARCHITECTURE.zh-CN.md)

# 架构说明

## 组件总览

```text
┌─────────────────────────────────────────────────────────────┐
│                        入口层                               │
│   CLI (mn create/serve/submit)     Web UI (mn-web, 外部包)  │
└──────────┬──────────────────────────────┬───────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐        ┌─────────────────────┐
│  workflow.py        │        │  contract.py        │
│  (job.yaml 合并)    │        │  (API 边界)         │
└─────────┬───────────┘        └─────────┬───────────┘
          │                              │
          ▼                              ▼
┌─────────────────────────────────────────────────────────────┐
│                 pipeline/runner.py                          │
│    build_context() → run_pipeline() → 16 步 STEPS          │
│                                                             │
│  ├── tts/          Edge / OpenAI / MiMo provider           │
│  ├── vision/       Stub / VLM 字幕器                       │
│  ├── providers/    registry: LLM / TTS / Vision / Research │
│  ├── plugin_loader  @register_step / entry_points 发现     │
│  └── cloud/        queue / API server / daemon / remote    │
└──────────┬──────────────────────────────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────────────┐
│                        产物                                 │
│  final.mp4 · narration.mp3 · subtitle.srt · script.md      │
│  metadata.json · matches.json · clips/                     │
└─────────────────────────────────────────────────────────────┘
```

- **CLI**（`cli.py`）—— 入口；解析参数，调用 `workflow` 或直接调用 `run_pipeline`
- **workflow**（`workflow.py`）—— 可选的 job.yaml 合并层（CLI > YAML > Settings）
- **pipeline**（`pipeline/runner.py`）—— 16 步串行编排器；持有 `STEPS`、`build_context`、`run_pipeline`
- **tts / vision / providers** —— 可插拔子系统，基于注册表分派（`@register_tts`、`@register_vision` 等）
- **cloud**（`cloud/`）—— 异步任务队列、REST API 服务、远程推理代理（v0.6.x）
- **contract**（`contract.py`）—— 外部消费者的唯一导入面；固定 `CONTRACT_VERSION`
- **plugin_loader**（`plugin_loader.py`）—— entry_points 发现 + `@register_step` 自定义步骤

## 流水线总览

影片剧情解说由 16 个串联步骤组成，编排入口在 `pipeline/runner.py`。在任何步骤执行前，`preflight.py` 都会预先探测 LLM 连通性与 TTS provider 配置 —— 一旦失败立刻抛 `PreflightError`，而不是悄悄降级成 mock 内容。

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → translate_subtitles → generate_subtitle →
run_qa_gate → render_video → validate_deliverable → export_clips
```

### 步骤分类

| 类别 | 步骤 | 失败处理 |
|------|------|----------|
| **硬步骤**（始终运行） | resolve_video, prepare_assets, generate_script, export_script_md, generate_voice, render_video, validate_deliverable | 必须成功 |
| **软步骤**（依赖缺失可跳过） | research_plot, align_audio, detect_scenes, match_clips, mix_bgm, translate_subtitles, run_qa_gate, export_clips | 优雅跳过 / 软降级；可通过 `--strict` 强制中止 |

### 步骤职责

**Context**（`models.Context`）是在所有步骤间传递的可变共享状态。

| 步骤 | 类别 | 职责 | 关键产物 |
|------|------|------|----------|
| resolve_video | 硬 | 从 `--video`、`--library-dir` 或配置定位源视频 | `ctx.video_path` |
| prepare_assets | 硬 | 验证 BGM、字体、片头素材在磁盘上存在 | — |
| research_plot | 软 | LLM 拉取影片元数据（标题、演职员、关键词） | `research.json` |
| generate_script | 硬 | LLM 返回 JSON → `List[ScriptSegment]` | 脚本数据 |
| export_script_md | 硬 | 将 segments 渲染为可读 Markdown | `script.md` |
| generate_voice | 硬 | TTS 异步合成 + sha256 内容寻址缓存（7 维键、两级扇出）；CI 使用静音回退 | `narration.mp3` + `TimedSegment[]` |
| align_audio | 软 | WhisperX 词级对齐；失败时回退到 faster-whisper 段级 | 词级时间戳 |
| detect_scenes | 软 | PySceneDetect 将源视频切分为 `Scene` 列表 | 场景列表 |
| match_clips | 软 | 将场景映射到台词段：embedding 重排（`[ml]` 已安装时）或比例启发式；探测/模型失败时回退 | `matches.json` |
| mix_bgm | 软 | 为旁白叠加背景音乐；duck 曲线随旁白能量缩放深度 | `mixed.mp3` |
| translate_subtitles | 软 | 按配置的 provider 分段翻译（默认 `llm`）；重试后软降级；CI 直通 | `ctx.translated_texts` |
| generate_subtitle | 硬 | 从 timed_segments 格式化 SRT；双语支持（`subtitle.<lang>.srt`、`subtitle.bilingual.srt`） | `subtitle.srt` 及变体 |
| run_qa_gate | 软 | 质量校验门 | QA 报告 |
| render_video | 硬 | MoviePy 合成：背景 + 文本/素材叠加 + 音频；两阶段编码（视频流 → ffmpeg 混音）；GPU 编码器自动检测（NVENC/VAAPI/VideoToolbox，v0.7.0+）；可选场景转场（v0.7.1+）和文字动画（v0.7.1+）；预览模式（v0.7.2+） | `final.mp4` + `metadata.json` |
| validate_deliverable | 硬 | ffprobe 校验：流、音量、时长比、文件大小；CI 默认跳过 | `ctx.metadata["qa_report"]` |
| export_clips | 软 | 抽取每段素材片段 | `clips/` 目录 |

### 流水线状态模型

每个软步骤将执行结果写入 `PipelineStatus` —— 取值为 `disabled | skipped | success | failed` 其中之一：

```python
class PipelineStatus(BaseModel):
    research: StepStatus   # research_plot
    align: StepStatus      # align_audio
    scene: StepStatus      # detect_scenes
    match: StepStatus      # match_clips
    bgm: StepStatus        # mix_bgm
    export: StepStatus     # export_clips
    translate: StepStatus  # translate_subtitles (default: "skipped" — 功能未启用)
    qa_gate: StepStatus    # run_qa_gate (default: "disabled")
```

`translate` 是唯一的软步骤，**默认** 状态为 `skipped`（而非 `disabled`）—— 「功能默认关闭」与「通过 `steps.translate=false` 或未知 provider 明确禁用」语义不同。

### metadata.json

每次流水线运行都会写出 `metadata.json` —— 供手工 QA 验证、CI 质量门和下游工具消费的审计与诊断文件。关键域：`match_summary`（匹配质量分布，可用 jq 查询）、`duration_metrics`（旁白时长 vs 目标）、align 诊断（后端选择与回退追踪）、`quality_dashboard`（跨步骤评分聚合）。完整的逐字段 schema 见 [METADATA_SCHEMA.md](METADATA_SCHEMA.md)，按功能域（match、align、script、audio、render、quality）组织。

## Job 配置合并层

可选的声明式 job YAML 位于 `run_pipeline` **之前** 一层：

```text
CLI flags + optional job.yaml
        ▼
load_job_config (YAML → JobConfig)
        ▼
merge_job (CLI > YAML > Settings → ResolvedJob)
        ▼
run_pipeline(...) # STEPS 顺序不变
```

- 模块所在：`movie_narrator.workflow`（`load_job_config`, `merge_job`, `JobConfigError`）
- 软步骤遵守 `metadata["workflow_steps"][<field>] is False` → `status.<field> = "disabled"`
- 在 `ctx.metadata` 中通过 `build_context` 拷贝循环注入的参数白名单（77 个键，完整列表见 `examples/job.example.yaml` 注释：场景检测、匹配、视觉、BGM、TTS 速率、翻译、调研、WhisperX 对齐、渲染、质检、文案塑形、异步、视频分辨率、平台、视角）
- 多语言字幕顶层键：`subtitle_lang`、`subtitle_mode`（在 `JobConfig` 中校验 —— 设置 `subtitle_mode ∈ {translated, bilingual}` 但缺 `subtitle_lang` 时会在 merge 阶段抛 `JobConfigError`）
- `STEPS` 仍是步骤顺序的唯一来源；自 v0.5 起，可通过 `@register_step` 插件 API 添加自定义步骤（见下方插件系统章节）
- YAML 自动发现：未传 `--config` 时按 `cwd/job.yaml` → 随包 `examples/job.example.yaml` → 缺省 顺序查找
- `.env.example` 是首次运行配置的真理源头（由 `ensure_user_config()` 读取，避免内联模板漂移）
- 严格的 env/yaml 边界：`.env`（Settings）= 32 个 LLM + TTS 基础设施字段；`job.yaml`（params）= 77 个流水线行为键；无代码常量模块 —— 内联字面值与示例文件保持一致

## 云端架构（v0.6.x）

`cloud/` 包提供异步任务执行和远程推理能力，使流水线可以作为云服务运行，而非仅限于本地 CLI 工具。

### 部署模式

```text
模式 1：本地异步（单机）
┌────────┐     ┌──────────────────┐
│  CLI   │────▶│  LocalTaskQueue  │────▶ ThreadPoolExecutor
│ (mn)   │     │  (进程内)        │      → run_pipeline()
└────────┘     └──────────────────┘

模式 2：远程 worker（客户端-服务端）
┌────────┐     ┌──────────────────┐     ┌──────────────────┐
│  CLI   │────▶│ RemoteTaskQueue  │────▶│  TaskAPIServer   │
│ (mn)   │     │ (HTTP 客户端)    │ HTTP│  + LocalTaskQueue│
└────────┘     └──────────────────┘     │  + WorkerDaemon   │
                                        │  → run_pipeline() │
                                        └──────────────────┘
```

### 任务生命周期

```text
pending → running → completed
              ↘         ↗
            retrying   failed
              ↘         ↗
               cancelled
```

- **`TaskStatus`**：`pending | running | retrying | completed | failed | cancelled | dead`（v0.9.4）
- **终态**：`completed`、`failed`、`cancelled`、`dead`
- **重试**：瞬态错误（ConnectionError、TimeoutError、RateLimitError）触发指数退避重试，上限 `max_retries`（默认 3）
- **死信路由**（v0.9.4）：任务在可重试错误上耗尽重试预算且 `TaskRequest.enable_dlq` 开启（默认）时，转入死信队列（`TaskStatus.DEAD`）而非普通 `FAILED`——可通过 `/deadletters` 检查与重放。

### 可靠性与批量（v0.9.x）

v0.9 系列在云端层之上增加了容错与批量生产能力：

- **熔断器**（v0.9.1）——`reliability/circuit_breaker.py`：按服务的 `CircuitBreaker`（CLOSED → OPEN → HALF_OPEN 状态机）守护外部 API 调用（LLM / TTS / TMDB / VLM）。电路打开时被守护调用直接抛 `CircuitOpenError`（可重试）而不触网。配置：`MN_CIRCUIT_FAILURE_THRESHOLD`、`MN_CIRCUIT_RECOVERY_TIMEOUT`、`MN_CIRCUIT_HALF_OPEN_MAX_CALLS`。
- **重试策略框架**（v0.9.1）——`reliability/retry.py`：策略驱动的 `with_retry` / `with_async_retry`，指数退避 + 抖动，统一的可重试判定。
- **任务检查点**（v0.9.2）——`cloud/checkpoint.py`：worker 在每步完成后把流水线 `Context` 快照写入 `<storage>/checkpoints/<task_id>.json`；重试或崩溃重启时任务从下一步续跑而非全部重来。`COMPLETED` 时删除检查点，`FAILED` / `CANCELLED` 保留。
- **优雅关闭**（v0.9.2）——`LocalTaskQueue.shutdown(wait, timeout)` 排空在途任务（有界等待后协作取消）；API 服务器 `begin_drain()` 拒绝新提交（503）而探针保持响应；daemon 信号处理器退出前排空。配置：`MN_GRACEFUL_SHUTDOWN_TIMEOUT`。
- **批量提交**（v0.9.3）——`BatchRequest`（1–50 任务）/ `Batch` / `BatchProgress`；`submit_batch` 先持久化批次记录再逐条提交，部分失败降级。聚合进度为成员百分比的等权均值。
- **定时任务**（v0.9.3）——`cloud/scheduler.py`：无第三方依赖的 5 段 cron 解析器 + `JobScheduler` 后台线程；调度记录持久化于存储目录。配置：`MN_SCHEDULER_ENABLED`、`MN_SCHEDULER_POLL_INTERVAL`。
- **死信队列**（v0.9.4）——`cloud/dlq.py`：`DeadLetterStore`（每任务原子 JSON）+ `replay_dead_letter()`（以新任务 ID 重建原始请求重新入队）。
- **条件分布式渲染**（v0.9.4）——`cloud/distributed.py`：`NodeRegistry` 探测节点 `/ready` 端点；`DistributedRenderPlanner` 仅在「启用 + 有健康节点 + 预计渲染足够长（`MN_DISTRIBUTED_MIN_RENDER_SECONDS`）」时把渲染阶段分发到远端节点。worker 钩子为软路径——任何分发失败都回退本地渲染。配置：`MN_DISTRIBUTED_*`。

### 关键模块

| 模块 | 职责 |
|------|------|
| `cloud/models.py` | `Task`、`TaskRequest`、`TaskProgress`、`TaskResult`、`TaskStatus`、`TaskPriority` |
| `cloud/queue.py` | `TaskQueue` 协议 + `LocalTaskQueue`（ThreadPoolExecutor 实现） |
| `cloud/remote_queue.py` | `RemoteTaskQueue` —— HTTP 客户端，实现相同的 `TaskQueue` 协议 |
| `cloud/api.py` | `TaskAPIServer` —— 基于 stdlib `http.server` 的 REST API（无额外依赖） |
| `cloud/daemon.py` | `run_daemon` / `WorkerDaemon` —— queue + API server + 信号处理 |
| `cloud/worker.py` | `run_task` —— 流水线执行包装器，含取消 + 进度 + 重试；`CancelController` 实现 `RunController` |
| `cloud/storage.py` | `TaskStorage` —— JSON 持久化，原子写入 |
| `cloud/remote_provider.py` | `register_remote_llm` / `register_remote_tts` —— 代理推理；`download_artifact` / `list_artifacts` —— 拉取产物 |
| `reliability/` | 熔断器 + 重试策略框架（v0.9.1） |
| `cloud/checkpoint.py` | 任务级检查点存储 + 恢复计划（v0.9.2） |
| `cloud/scheduler.py` | cron 解析器 + 定时任务触发循环（v0.9.3） |
| `cloud/dlq.py` | 死信存储 + 重放（v0.9.4） |
| `cloud/distributed.py` | 节点注册表 + 分布式渲染规划器 + 分发器（v0.9.4） |

### REST API 端点

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/tasks` | 提交新任务 |
| GET | `/tasks` | 列出任务（可选 `?status=` 过滤） |
| GET | `/tasks/{id}` | 获取任务详情 |
| DELETE | `/tasks/{id}` | 取消任务 |
| GET | `/tasks/{id}/result` | 获取任务结果（仅终态） |
| GET | `/tasks/{id}/artifacts` | 列出产物文件 |
| GET | `/tasks/{id}/download/{file}` | 下载产物文件 |
| GET | `/health` | 健康检查（`?deep=1` 返回完整报告） |
| GET | `/ready` | 就绪探针（v0.8.2） |
| GET | `/info` | 服务端信息（版本、worker 数） |
| GET | `/openapi.json` | OpenAPI 3.1 规范（v0.8.2） |
| POST | `/tasks/batch` | 一次提交至多 50 个任务为一批（v0.9.3） |
| GET | `/batches` | 列出批次（v0.9.3） |
| GET/DELETE | `/batches/{id}` | 获取批次聚合进度 / 取消成员（v0.9.3） |
| POST/GET | `/schedules` | 创建 / 列出 cron 定时任务（v0.9.3） |
| DELETE | `/schedules/{id}` | 移除定时任务（v0.9.3） |
| GET | `/schedules/{id}/runs` | 最近触发记录（v0.9.3） |
| GET | `/deadletters` | 列出死信记录（v0.9.4） |
| GET/DELETE | `/deadletters/{id}` | 获取 / 移除死信记录（v0.9.4） |
| POST | `/deadletters/{id}/replay` | 以新任务 ID 重新入队原始请求（v0.9.4） |

### 健康与就绪探针（v0.8.2）

`/health`、`/ready`、`/openapi.json` 免除 `X-API-Key` 鉴权 —— 编排器探针与 API
工具无法携带密钥，且这些响应本身不含敏感信息。

**核心检查**（`cloud/health.py`，`/ready` 与 `/health?deep=1` 共用，不产生任何
出站网络请求，目标 < 50 ms）：

| 检查项 | 通过条件 |
|--------|----------|
| `queue` | 任务队列已挂载且已启动 |
| `storage` | 存储目录存在，且可写入临时探测文件 |
| `workers` | worker 执行器存活且 worker 数非零 |
| `shutdown` | 服务端尚未进入关闭流程 |

每项检查返回 `{"status": "pass"｜"fail"｜"skipped", "detail": "...", "duration_ms": …}`，且永不抛异常。

**状态策略** —— 核心检查全部通过 ⇒ `"ok"` / HTTP 200；核心检查通过但依赖探测失败
⇒ `"degraded"` / HTTP 200（服务仍可接单，编排器不应驱逐）；任一核心检查失败 ⇒
`"error"` / HTTP 503。

**向后兼容** —— 不带查询参数的 `GET /health` 仍严格返回 v0.6.1 的
`{"status": "ok"}`，新增字段仅在 `?deep=1` 时出现。

**出站依赖探测**（LLM 端点、TTS 提供方、远程存储）需通过 `MN_HEALTH_DEEP_DEPS=1`
显式开启，并发执行、单次超时 2 秒，且在 `CI=1` 时始终跳过。`MN_REMOTE_STORAGE_URL`
可额外加入一个远程存储端点。

OpenAPI 文档由 `cloud/openapi.py`（`build_openapi_spec`）生成，通过
`GET /openapi.json` 提供，也可用 `mn api-spec -o openapi.json` 导出。

### 关键设计规则

- **同一协议，不同传输**：`LocalTaskQueue` 和 `RemoteTaskQueue` 都实现 `TaskQueue` —— 零代码改动即可切换
- **无额外依赖**：REST API 使用 stdlib `http.server`；远程客户端使用 stdlib `urllib.request`
- **协作式取消**：`CancelController` 实现 `RunController` —— 流水线在步骤边界检查 `is_cancelled()`，而非步骤内部
- **进度通过 console 包装追踪**：`ProgressConsole` 包装真实 `Console`，拦截 `step()` / `step_ok()` 调用以实时更新 `TaskProgress`
- **重试保留缓存**：重试时调用 `CancelController.reset()`，流水线从头重新执行 —— 缓存结果（TTS 片段、场景检测）通过内容寻址缓存复用
- **产物管理**：已完成任务的产物通过 `/tasks/{id}/download/{file}` 提供下载，带路径遍历保护
- **远程推理代理**：`register_remote_llm("remote")` / `register_remote_tts("remote")` 允许将 LLM/TTS 调用卸载到远程 worker，无需改动流水线代码

## TTS 抽象层

`tts/` 包将 TTS 后端选型与流水线编排解耦：

```text
pipeline/tts.generate_voice(ctx)
    ▼
tts.factory.get_tts_provider(settings) → TTSProvider
    ▼
provider.synthesize(text, voice, output_path) → 写出 mp3
    ▼
流水线通过 AudioSegment.from_mp3 探测时长
```

### 关键设计规则

- **不写第二份实现**：流水线负责缓存、并发、时长探测、音频合并；provider 只负责音频生成
- **CI 临时文件隔离**：CI 模式下合成到 `output/.ci_<hash>.mp3`，探测后立刻删除 —— 静音文件永不进入缓存
- **`is_ci()` 作为唯一真理来源**：定义在 `tts/base.py`，由流水线导入（不允许重复出现 `os.getenv("CI")`）
- **`PROVIDER_CACHE_VERSIONS` 字典**：按 provider 独立扩展缓存版本（开放/封闭原则）
- **凭据回退**：`openai_tts_api_key` → `llm_api_key`；`openai_tts_base_url` → `llm_base_url`；`mimo_api_key` → `llm_api_key`

### 模块

| 模块 | 职责 |
|------|------|
| `tts/protocol.py` | `TTSProvider` ABC —— `synthesize(text, voice, output_path) -> None` |
| `tts/base.py` | `BaseTTSProvider`（CI 静音回退）、`is_ci()`、`_estimate_duration_s()` |
| `tts/edge.py` | `EdgeTTSProvider` —— 包装 `edge_tts.Communicate` |
| `tts/openai_provider.py` | `OpenAITTSProvider` —— 通过 `asyncio.to_thread` 包装 sync OpenAI SDK；voice 白名单 |
| `tts/mimo_provider.py` | `MimoTTSProvider` —— 小米 MiMo TTS，通过 `chat.completions`；3 种模型（命名声、声音克隆、声音设计）；wav→mp3 转码 |
| `tts/factory.py` | `get_tts_provider(settings)` —— settings → provider 实例（非单例） |
| `tts/cache.py` | `TTSCacheKey` 数据类、`cache_path_for()`（两级扇出）、`PROVIDER_CACHE_VERSIONS` |
| `utils/errors.py` | `ConfigError` —— 横切配置错误类 |

## 视觉抽象层（v0.4.26+）

`vision/` 包提供视觉场景字幕的抽象层，使未来接入 VLM（视觉语言模型）时无需改动匹配逻辑：

```text
pipeline/match._build_scene_captions()
    ▼
vision.factory.get_vision_captioner(name) → VisionCaptioner
    ▼
captioner.caption_scenes(scenes, video_path) → list[SceneCaption]
```

| 模块 | 职责 |
|------|------|
| `vision/protocol.py` | `VisionCaptioner` ABC —— 定义 `caption_scenes()` 契约 + `SceneCaption` 数据类 |
| `vision/stub.py` | `StubVisionCaptioner` —— 返回占位标签（标记 `is_stub=True`） |
| `vision/vlm.py` | `VLMVisionCaptioner` —— 真实 VLM（视觉语言模型）provider |
| `vision/factory.py` | `get_vision_captioner()` —— 按 `vision_captioner` 参数分派（`"none"` / `"stub"` / 未来 provider） |
| `vision/__init__.py` | 公开 API 导出 |

**与 match 的集成**：视觉字幕补充（而非替代）音频转写字幕。当 `vision_captioner="stub"` 时，标签被标记为 fake，使现有的 fake-caption 守卫将其等同于占位标签处理 —— 跳过 embedding，走启发式路径。真实 VLM provider 可在 `factory.py` 中注册，无需修改 `match.py`。

## 插件系统（v0.5+）

插件 API 提供稳定的扩展机制，无需 fork 核心引擎即可添加自定义流水线步骤和 provider：

```text
第三方包（pyproject.toml entry_points）
    ▼
discover_plugins() — importlib.metadata entry_points("movie_narrator.plugins")
    ▼
Plugin.register(ctx: PluginContext) — 调用 @register_step / @register_tts / @register_vision / @register_llm / @register_research
    ▼
StepRegistry / ProviderRegistry — 中央注册表
    ▼
runner.py — 将已注册步骤插入 STEPS 的 before/after 位置
```

### 关键模块

| 模块 | 职责 |
|------|------|
| `plugin_loader.py` | `StepRegistry`、`Plugin` protocol、`PluginContext`、`load_plugin()`、`discover_plugins()`、`list_available_plugins()` |
| `providers/registry.py` | `ProviderRegistry`、`register_tts`、`register_vision`、`register_llm`、`register_research`、`tts_registry`、`vision_registry`、`llm_registry`、`research_registry` |
| `presets/` | 解说预设系统（`list_presets()`、`get_preset()`） |

### Plugin protocol

插件是任何具有 `name` 属性和 `register(ctx: PluginContext)` 方法的对象：

```python
from movie_narrator import PluginContext, register_step

class MyPlugin:
    name = "my-plugin"

    def register(self, ctx: PluginContext) -> None:
        ctx.steps.register("my_step", my_func, soft=True, after="render_video")
        ctx.services.logger.info("My plugin registered")
```

插件通过 `importlib.metadata` entry points 的 `movie_narrator.plugins` 组自动发现。完整参考实现见 `examples/plugins/watermark/`。

## 流水线暂停/恢复（v0.4.26+）

流水线通过 `PipelinePaused` 异常和状态序列化支持人工暂停点：

```text
mn create ... --pause-at script
    ▼
runner: 在 "generate_script" 步骤完成后
    ▼
_save_pipeline_state(ctx) → output_dir/pipeline_state.json
    ▼
raise PipelinePaused(completed_step="generate_script")

mn resume <output_dir>
    ▼
_load_pipeline_state(path) → Context（自动注入 SilentConsole）
    ▼
run_pipeline(ctx, start_step="align_audio")  # 跳过已完成步骤
```

**状态文件**（`pipeline_state.json`）：序列化 `Context` 的所有字段，除了 `services`（不可序列化）。恢复时通过 `model_validator` 自动注入 `SilentConsole`，再由 `mn resume` 命令替换为真实 `Console`。

**暂停点**：`--pause-at script`（脚本生成后）或 `--pause-at match`（场景匹配后）。用户可在恢复前编辑 `script.md` 或 `matches.json`。

## 场景过滤（v0.5+）

`pipeline/scene_filter.py` 模块提供三个场景过滤功能，通过移除非内容片段和偏向高亮区域来提升解说质量：

| 功能 | 参数 | 说明 |
|------|------|------|
| **片头跳过** | `scene_skip_intro` | 通过亮度和运动分析自动检测并跳过视频开头的 intro/logo 序列 |
| **黑帧检测** | `scene_dark_threshold` | 过滤低于亮度阈值的近黑帧，避免浪费解说预算在非内容片段上 |
| **高亮窗口** | `scene_highlight_window` | 可配置的基于时间窗口的场景优先级 —— 偏向用户指定的高亮范围的场景选择 |

这些参数通过 UnifiedParamSchema 添加到 `PARAM_WHITELIST`，并经由标准 `build_context` → `ctx.metadata` 路径传递。

## Web UI 层

> **Web UI 现已是独立项目。** 自 monorepo 拆分起，FastAPI + React SPA 技术栈位于外部仓库：[`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web)。以独立包的形式安装并运行：
>
> ```bash
> pip install movie-narrator-web
> mn-web            # 启动 FastAPI + React SPA（端口 8760）
> ```

外部 web 包**只**通过 `contract.py` 定义的契约面消费核心引擎。核心包中没有 `mn web` 命令或 `[web]` extra —— `fastapi`、`uvicorn`、`python-multipart` 不是 `movie-narrator` 的依赖。

### 契约边界

```text
movie-narrator-web  →  contract.py  →  pipeline/runner.py (build_context, run_pipeline, PARAM_WHITELIST)
                                →  pipeline/errors.py (PipelineCancelled, RunController, StepAction, ...)
                                →  utils/console.py (BaseConsole, Console, SilentConsole)
                                →  utils/sanitize.py (sanitize_filename)
```

`contract.py` 是**唯一导入面** —— web 包不得直接导入任何内部模块。`CONTRACT_VERSION = (0, 6, 1)` 在 import 时校验，拒绝不匹配的引擎版本。完整符号表见 [docs/sdk/contract.md](sdk/contract.md)。

### 关键设计规则

- **不写第二份实现**：web 包调用 `build_context` + `run_pipeline`，与 CLI 完全使用同一套函数
- **取消是运行时的专属路径**：`RunController` / `PipelineCancelled` 永远不进入 `Context`、`PipelineStatus` 或 `metadata.json`。取消是一种独立的终态路径（非 warning、非 error、不会触发 `--strict`）
- **空字段不覆盖**：表单留空的字段不会注入 `params` —— 直接采用 Settings（`.env` / `MN_*`）默认值
- **上传文件落到稳定目录**：上传文件落到 `output/_uploads`，绝不写到临时目录或 `output/<movie>` 文件夹

## 扩展点

- **新增流水线步骤（推荐）**：通过插件 API 使用 `@register_step("name", ...)` 装饰器注册。若打包为 entry_points 插件则自动发现，也可通过 `load_plugin()` 手动加载。参考实现见 `examples/plugins/watermark/`。
- **新增流水线步骤（旧方式）**：直接在 `pipeline/runner.py` 的 `STEPS` 末尾追加。函数签名必须是 `(ctx: Context) -> Context`。
- **替换 TTS / 渲染器 / LLM**：直接替换 `pipeline/tts.py`、`pipeline/render.py` 或 `utils/llm.py`，保留步骤函数签名即可。
- **新增 TTS / Vision / LLM / Research provider（推荐）**：通过 Provider Registry 使用 `@register_tts("name")`、`@register_vision("name")`、`@register_llm("name")` 或 `@register_research("name")` 装饰器注册。若打包为 entry_points 插件则自动发现。
- **新增 VisionCaptioner provider（旧方式）**：在 `vision/` 中实现 `VisionCaptioner` ABC，在 `vision/factory.py` 注册。参考 `vision/stub.py`。匹配逻辑通过 `is_stub` 标志自动区分 fake 与真实字幕。
- **流水线暂停 / 恢复**：`--pause-at script|match` 在指定步骤后暂停；`mn resume <output_dir>` 继续。状态序列化到 `pipeline_state.json`。
- **远程推理**：`mn serve` 启动 worker daemon；`mn submit --remote <url>` 向远程 worker 提交任务；`register_remote_llm` / `register_remote_tts` 代理推理调用。
- **新增 CLI 命令**：在 `cli.py` 加 `@app.command()`。
- **前端 / WebUI**：请在 [`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web) 仓库内工作。web 包需要的任何新引擎能力都必须通过 `contract.py` 暴露（并相应升级 `CONTRACT_VERSION`）。见 `docs/CONTRIBUTING.md` → *Frontend Development*。

## 关键设计决策

| 决策 | 理由 |
|------|------|
| 扁平串联的 STEPS 列表 | 没有事件总线或 DI 容器；流程清晰、可直接审阅 |
| 软/硬步骤切分 | 可选依赖（PySceneDetect、WhisperX）不会破坏核心流水线 |
| 内容寻址 TTS 缓存 | 避免重复 API 调用；键包括 version + style_prompt 配置 |
| `PipelineStatus` 模型 | 每个软步骤的执行结果都可以在 `metadata.json` 中检查 |
| `--strict` 标志 | 把软步骤失败升级为硬错误（CI 或生产环境用） |
| 渲染时 `usable_clips` 过滤 | 忽略意外的 `source="fallback"` 行（构造时的默认） |
| `TaskQueue` 协议抽象 | 本地和远程部署共享同一 API 面 |
| 纯 stdlib REST API | 云端部署无需将 FastAPI/uvicorn 列为核心依赖 |
| `contract.py` 作为唯一导入边界 | web 包通过 `CONTRACT_VERSION` 独立版本管理，而非包版本号 |
