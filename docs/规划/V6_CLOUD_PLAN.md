# v0.6 Cloud — 技术规划

> **状态**：规划文档（尚未实现）
> **日期**：2026-07-27
> **版本锚点**：`v0.5.3` + main `378d182`
> **北极星**：将本地 CLI 管道升级为可远程调用、可分布式渲染、可多租户部署的云服务
> **关联**：[QUALITY_UPLIFT_METHODS](./QUALITY_UPLIFT_METHODS.md) §7 明确不做云端分布式渲染（属 0.6 范畴）

---

## 0. 前置条件

v0.5.x Ecosystem 已全部落地，以下基础设施可直接复用：

| 已有能力 | 复用点 |
|----------|--------|
| `contract.py` — 39 symbols, `CONTRACT_VERSION = (0, 5, 1)` | C1/C2/C3/C4 的 API 边界 |
| Plugin API — `@register_step` / `@register_*` 四类 provider | 远程 provider 注册复用同一套 registry |
| EP9 `--pause-at` / `mn resume` / `pipeline_state.json` | C1 断点续做基础 |
| `--retry` flag — soft/hard step 机制 | C1 retry 策略 |
| `match_summary` 21+ fields | C2/C3 任务分发的数据基础 |
| `movie-narrator-web` 独立包 — WebSocket 实时进度 | C4 前端已就绪，需后端 task queue 对接 |

---

## 1. 工作包总览

```text
C1  Task Queue — 异步作业模型          [基础设施]     3–4d
C2  Remote Inference — 远程计算卸载    [provider]     2–3d
C3  Distributed Rendering — 分布式渲染 [render拆分]   3–4d
C4  Web Service Deployment — 云端部署  [部署/多租户]   2–3d
```

依赖链：**C1 → (C2 ∥ C3) → C4**

| WP | 目标 | 依赖 | 预估 |
|----|------|------|------|
| **C1** | `run_pipeline` 异步化，支持 job 提交/轮询/回调 | 无 | 3–4d |
| **C2** | LLM/TTS 远程 provider + 渲染子任务卸载 | C1 | 2–3d |
| **C3** | 按 segment 拆分渲染，多节点并行合并 | C1 | 3–4d |
| **C4** | REST 认证、多租户隔离、弹性伸缩 | C2 + C3 | 2–3d |

---

## 2. C1 — Task Queue

### 2.1 问题

当前 `run_pipeline` 是同步阻塞调用。CLI 启动后用户必须等待完成，无法提交后离开。Web 包的 WebSocket 进度推送也依赖同步阻塞期间的 console 回调，没有真正的异步作业层。

### 2.2 设计

#### 2.2.1 作业模型

```python
# contract.py 新增
@dataclass
class JobRequest:
    movie: str
    style: str
    duration: int
    preset: str
    config_path: str | None = None
    video_path: str | None = None
    bgm_path: str | None = None
    extra_params: dict = field(default_factory=dict)

@dataclass
class JobStatus:
    job_id: str
    state: Literal["queued", "running", "paused", "completed", "failed"]
    progress: float  # 0.0–1.0
    current_step: str | None
    output_dir: str | None
    error: str | None
    created_at: str
    updated_at: str
```

#### 2.2.2 作业生命周期

```text
submit(job_request) → job_id
  ├─ state=queued → 入队等待 worker 拉取
  ├─ state=running → worker 调用 run_pipeline
  │   ├─ progress 通过 step 回调实时更新
  │   ├─ state=paused → 命中 --pause-at 或手动暂停
  │   └─ state=completed/failed
  └─ 结果落盘 output_dir/，metadata.json 含 job_id
```

#### 2.2.3 实现策略

复用 EP9 的 `pipeline_state.json` 序列化机制。不引入 Celery/Redis 等重依赖，v1 用进程级队列：

| 层 | 实现 |
|----|------|
| Queue | `asyncio.Queue` 或 `threading.Queue`（v1 单进程） |
| Worker | `ThreadPoolExecutor` 或 `asyncio.Task` |
| State | `pipeline_state.json` + 内存 dict |
| Progress | 复用 `ctx.services.console` 回调 → WebSocket |

v2 可替换为 Redis/RQ 或 Celery，接口不变。

#### 2.2.4 契约扩展

```python
# contract.py 新增导出
def submit_job(request: JobRequest) -> str: ...
def get_job_status(job_id: str) -> JobStatus: ...
def cancel_job(job_id: str) -> bool: ...
def resume_job(job_id: str) -> str: ...
def list_jobs(limit: int = 20) -> list[JobStatus]: ...
```

`CONTRACT_VERSION` bump 到 `(0, 6, 0)` — 新增导出，向后兼容（MINOR bump 而非 MAJOR，因为不破坏现有 API）。

### 2.3 改动落点

| 文件 | 改动 |
|------|------|
| `contract.py` | 新增 `JobRequest`/`JobStatus` + 5 个作业函数 |
| `pipeline/job_queue.py` | 新建 — 队列 + worker 实现 |
| `pipeline/runner.py` | `run_pipeline` 增加 `job_id` 参数，progress 回调写入 job state |
| `cli.py` | 新增 `mn job submit` / `mn job status` / `mn job cancel` 子命令 |
| `__init__.py` | 导出作业相关符号 |
| `tests/test_job_queue.py` | 新建 — 提交/轮询/取消/恢复 |

### 2.4 退出条件

- [ ] `mn job submit` 返回 job_id，`mn job status` 查到状态
- [ ] 作业失败后 `mn job resume` 可从断点继续
- [ ] WebSocket 进度推送对接 job state
- [ ] 单测覆盖 queued/running/paused/completed/failed 五态

---

## 3. C2 — Remote Inference

### 3.1 问题

LLM 和 TTS 调用在本地执行，大模型推理耗时且占用本地资源。远程化可将计算卸载到 GPU 节点。

### 3.2 设计

#### 3.2.1 LLM/TTS 远程化

registry 模式已就绪（v0.5.1），只需注册新的远程 provider：

```python
@register_llm("remote")
class RemoteLLMProvider:
    """通过 HTTP 调用远程 LLM 服务"""
    def __init__(self, endpoint: str, api_key: str, model: str): ...
    def generate(self, prompt: str, **kwargs) -> str:
        # POST endpoint/v1/chat/completions
        ...

@register_tts("remote")
class RemoteTTSProvider:
    """通过 HTTP 调用远程 TTS 服务"""
    def __init__(self, endpoint: str, api_key: str): ...
    def synthesize(self, text: str, voice: str, **kwargs) -> bytes:
        # POST endpoint/v1/tts
        ...
```

#### 3.2.2 渲染子任务卸载

将 `render_video` 拆分为可远程执行的单元：

```text
render_video(segments, clips, ...)
  ├─ prepare (本地) — 收集 segments + matched clips 元数据
  ├─ render_segments (可远程) — 每个 segment 独立渲染为 tmp.mp4
  ├─ concat (本地或远程) — ffmpeg concat
  └─ mux (本地) — 音频 + 字幕 + 封装
```

### 3.3 改动落点

| 文件 | 改动 |
|------|------|
| `providers/remote_llm.py` | 新建 — RemoteLLMProvider |
| `providers/remote_tts.py` | 新建 — RemoteTTSProvider |
| `pipeline/render.py` | 拆分 `render_video` 为 prepare/render_segments/concat/mux |
| `workflow/schema.py` | 新增 `llm_endpoint`/`tts_endpoint` 参数 |
| `tests/test_remote_providers.py` | 新建 — mock HTTP 调用 |

### 3.4 退出条件

- [ ] `MN_LLM_PROVIDER=remote` + `MN_LLM_ENDPOINT=...` 可正常生成脚本
- [ ] `MN_TTS_PROVIDER=remote` + `MN_TTS_ENDPOINT=...` 可正常合成语音
- [ ] 渲染子任务可拆分为独立单元执行
- [ ] 单测覆盖远程调用 + 超时 + 重试

---

## 4. C3 — Distributed Rendering

### 4.1 问题

长视频（120s+）渲染耗时 5–10 分钟，单机串行成为瓶颈。`MatchedClip[]` 已是天然的分片单元。

### 4.2 设计

```text
C1 submit → C3 dispatcher
  ├─ 将 N 个 segments 分为 K 组（K = worker 数）
  ├─ 每组生成 render_subtask(segments[], clips[], params)
  ├─ 分发到 C1 队列的 worker 池
  ├─ 各 worker 并行渲染 → 上传 tmp_{group_id}.mp4
  └─ dispatcher 收集所有 tmp → concat → mux → final.mp4
```

分组策略：

| 策略 | 说明 |
|------|------|
| 均分 | N/K 个 segment 一组，简单 |
| 时长均衡 | 按 segment duration 总和分组，避免某组过重 |
| 场景不重叠 | 同一 scene 的多个 segment 尽量同组（减少重复读源片） |

### 4.3 改动落点

| 文件 | 改动 |
|------|------|
| `pipeline/distributed_render.py` | 新建 — 分组 + 分发 + 收集 + 合并 |
| `pipeline/render.py` | 提取 `render_segment_group` 可独立调用函数 |
| `pipeline/job_queue.py` | 支持 subtask 概念 |
| `tests/test_distributed_render.py` | 新建 — 分组正确性 + 部分失败处理 |

### 4.4 退出条件

- [ ] 120s 视频在 2 worker 下渲染时间 < 单机的 60%
- [ ] 某个 worker 失败时可重试该组
- [ ] 最终输出与单机渲染结果一致（帧级对比）
- [ ] 单测覆盖分组 + 合并 + 失败重试

---

## 5. C4 — Web Service Deployment

### 5.1 问题

`movie-narrator-web` 已是独立包，但部署时缺乏认证、多租户隔离和弹性伸缩。

### 5.2 设计

| 层 | 实现 |
|----|------|
| 认证 | JWT token，`POST /api/auth/login` → token |
| 多租户 | job_id 绑定 user_id，查询时校验归属 |
| API 网关 | REST: `POST /api/jobs` / `GET /api/jobs/{id}` / `DELETE /api/jobs/{id}` |
| WebSocket | `WS /ws/jobs/{id}` — 复用 web 包已有实现，对接 C1 job state |
| 弹性 | C1 队列 worker 数可配；v2 对接 K8s HPA |

### 5.3 改动落点

| 文件 | 改动 |
|------|------|
| `movie-narrator-web` 包 | 新增 auth middleware + multi-tenant job API |
| `pipeline/job_queue.py` | job 绑定 user_id |
| `contract.py` | `submit_job` 增加可选 `user_id` 参数 |
| 部署 | Dockerfile + docker-compose（core + web + worker） |

### 5.4 退出条件

- [ ] 未认证请求返回 401
- [ ] 用户 A 无法查询用户 B 的 job
- [ ] `docker-compose up` 一键启动完整服务
- [ ] WebSocket 进度推送正常

---

## 6. 发版建议

```text
v0.6.0  C1 Task Queue + CLI job 子命令
v0.6.1  C2 Remote Inference（LLM + TTS 远程 provider）
v0.6.2  C3 Distributed Rendering（segment 并行）
v0.6.3  C4 Web Service Deployment（auth + multi-tenant + Docker）
```

`CONTRACT_VERSION` 变更：

- v0.6.0: `(0, 6, 0)` — 新增 Job API（MINOR bump）
- v0.6.1–v0.6.3: 视 API 变更决定，无新导出则不 bump

---

## 7. 风险与缓解

| 风险 | 缓解 |
|------|------|
| 异步化破坏现有同步 CLI | `mn create` 保持同步；`mn job submit` 是新路径 |
| 远程 provider 超时 | 复用 `--retry` 机制 + 可配超时 |
| 分布式渲染帧不一致 | 统一 CRF/preset/编码参数；concat 前校验分辨率/帧率 |
| 多租户数据泄漏 | job_id 绑定 user_id，查询层强制校验 |
| 队列积压 | v1 单进程够用；v2 可换 Redis/RQ 无需改接口 |

---

## 8. 明确不做（v0.6 边界）

- 实时协作编辑（远超当前需求）
- 自动视频审核（依赖平台 API，非引擎职责）
- CDN 分发（由发布平台处理）
- 多语言引擎同时运行（主路径仍中文优先）
- K8s 原生部署（v0.7+ 考虑）

---

*本方案只定义「怎么规划」；实现、测试、发版由维护者按 C1–C4 推进。*
