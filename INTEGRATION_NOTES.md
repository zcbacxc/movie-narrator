# v0.9.4 集成说明 — Dead Letter Queue + Conditional Distributed Rendering + Contract 收口

分支：`feature/v0.9.4-queue-distributed`（本地子代理开发，尚未合并到 main）

## 交付内容

1. **Dead Letter Queue（死信队列）**
   - `TaskStatus` 新增 `DEAD`，`TERMINAL_STATES` 加入 `DEAD`。
   - `TaskRequest.enable_dlq: bool = True`（默认开启）。
   - 新模块 `src/movie_narrator/cloud/dlq.py`：`DeadLetterRecord`（pydantic）、
     `DeadLetterStore`（JSON 原子写 + 线程锁）、`replay_dead_letter()`、
     `get_default_store()` / `set_default_store()`。
   - `worker.run_task`：可重试错误持续到 `max_retries` 耗尽且 `enable_dlq` →
     写 DLQ 记录 → 任务置 `DEAD`（替代原 `FAILED`）。
   - REST 路由：`GET /deadletters`、`GET /deadletters/{id}`、
     `POST /deadletters/{id}/replay`、`DELETE /deadletters/{id}`。
   - OpenAPI：补 `/deadletters*` 路径段 + `DeadLetterRecord` /
     `DeadLetterList` / `DeadLetterReplayed` / `DeadLetterRemoved` 组件。

2. **Conditional Distributed Rendering（条件性分布式渲染，基础框架）**
   - Settings 新增：`mn_distributed_enabled`(False)、
     `mn_distributed_nodes`("")、`mn_distributed_min_render_seconds`(600)、
     `mn_distributed_node_health_timeout`(5.0)；`.env.example` 已同步。
   - 新模块 `src/movie_narrator/cloud/distributed.py`：`NodeRegistry`、
     `DistributedRenderPlanner`、`estimate_render_seconds`、
     `render_task_dispatcher`、`DistributedRenderError`。
   - `worker._execute_task` 软钩子：渲染阶段满足条件时先尝试远端分发，
     成功则产物回传本地并续跑 render 之后的步骤；任何失败自动回退本地
     （`console.debug`），非分布式路径完全不变。
   - docstring 已注明：本期为「条件触发基础框架」，不强制 SLA；
     远端节点需自行打通渲染输入（共享存储 / S3 后端），属部署侧范围。

3. **Contract 导出**
   - `contract.py` 新增分组 `Queue & Distributed (v0.9.4)`：`DeadLetterRecord`、
     `DeadLetterStore`、`replay_dead_letter`、`NodeRegistry`、
     `DistributedRenderPlanner`、`DistributedRenderError`、
     `render_task_dispatcher`。`TaskStatus` 确认已在 v0.6.0 分组导出。
   - **未改** `CONTRACT_VERSION`（仍为 (0,8,3)）、**未改** `pyproject.toml`
     version、**未改** `test_contract.py` 版本断言（集成时统一升 (0,9,4)）。

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `src/movie_narrator/cloud/models.py` | TaskStatus.DEAD、TERMINAL_STATES、TaskRequest.enable_dlq |
| `src/movie_narrator/cloud/dlq.py` | **新增**：DeadLetterRecord / DeadLetterStore / replay / default-store |
| `src/movie_narrator/cloud/distributed.py` | **新增**：NodeRegistry / Planner / dispatcher / 时长估算 |
| `src/movie_narrator/cloud/worker.py` | DLQ 路由 + 分布式软钩子 + ProgressConsole.set_step_index |
| `src/movie_narrator/cloud/api.py` | /deadletters 四组路由 + TaskAPIServer.dead_letter_store |
| `src/movie_narrator/cloud/openapi.py` | 路径段 + 组件 schema |
| `src/movie_narrator/cloud/__init__.py` | 导出新符号 |
| `src/movie_narrator/cloud/storage.py` | clear_terminal 纳入 DEAD（终端清理一致性） |
| `src/movie_narrator/config.py` | mn_distributed_* 四个配置项 |
| `src/movie_narrator/contract.py` | __all__ + 底部懒加载导入 |
| `.env.example` | 分布式配置段 |
| `tests/test_v094_dlq_distributed.py` | **新增** 28 个测试 |
| `tests/test_v060_task_queue.py` | `test_retry_exhausted` 断言 FAILED→DEAD（v0.9.4 行为变化） |
| `tests/test_v082_openapi.py` | EXPECTED_PATHS 补 3 条 /deadletters 路径 |

## TaskStatus.DEAD 影响面

- **状态枚举**：`TaskStatus.DEAD = "dead"`，加入 `TERMINAL_STATES`。
  `wait()` / `is_terminal` / API 结果轮询 / `storage.count` 全部沿用
  TERMINAL_STATES 逻辑，无需改动 → API 兼容。
- **触发条件（刻意收窄）**：仅当「最后一次失败为可重试错误 **且** 重试预算
  耗尽（`attempt >= max_retries`）」且 `enable_dlq=True` 时进 DLQ 并置 DEAD。
  非可重试错误（首试即 FAILED）保持原 `FAILED` 行为 → 既有消费方不受影响。
  `enable_dlq=False` 完全保持 v0.9.4 之前行为。
- **指标**：`record_task_terminal("dead")` 随通用终端统计走；`_record_task_outcome`
  仅对 FAILED 累加错误计数，DEAD 不重复计（可接受，见遗留风险）。
- **清理**：`storage.clear_terminal` / `queue.cleanup_terminal` 现会一并清 DEAD
  任务；DLQ 记录独立存放，不受影响。

## DLQ 存储路径

- 默认：`~/.mn_tasks/deadletters/<task_id>.json`（TaskStorage 的 sibling 目录）。
- `DeadLetterStore(storage_dir=...)` 可注入；`set_default_store()` 可重定向
  进程级默认存储（worker 与 API 共用同一默认，`GET /deadletters` 与 worker
  写入天然一致）。
- 重放语义：从记录重建 `TaskRequest`（深拷贝）以**新 task_id** 重新 submit，
  原记录保留并 `replay_count += 1`。

## 分布式配置项与触发逻辑

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| `MN_DISTRIBUTED_ENABLED` | `0` | 总开关，默认关闭 |
| `MN_DISTRIBUTED_NODES` | 空 | 逗号分隔的远端节点 base_url 列表 |
| `MN_DISTRIBUTED_MIN_RENDER_SECONDS` | `600` | 预计渲染时长 ≥ 此值才考虑分发 |
| `MN_DISTRIBUTED_NODE_HEALTH_TIMEOUT` | `5.0` | 节点 `/ready` 探测超时 |

触发判定（`DistributedRenderPlanner.should_distribute`）：enabled 且健康节点 ≥1
且 `estimate_render_seconds(request, progress, history)` ≥ 阈值。
时长估算：优先 history → progress.step_elapsed_seconds → `duration * 1.0` 兜底。

分发流程（`render_task_dispatcher`）：把请求深拷贝成「仅 render_video 的
workflow_steps」子任务 → `RemoteTaskQueue.submit` 到节点 → `wait` 轮询 →
`download_artifact` 把 final.mp4/音频回传本地 → 返回本地路径的 TaskResult。
失败（提交/等待/下载任一环节）抛 `DistributedRenderError` → worker 回退本地。

## 测试结果（独立 venv `.venv-v094`）

- `tests/test_v094_dlq_distributed.py`：**28 passed**（隔离 + 全量均通过）。
- 受影响的既有测试：`test_v060_task_queue`、`test_v061_remote`、
  `test_contract`、`test_settings`、`test_v080_format_rename`、
  `test_m5_community` 等 **231 passed**。
- `test_v082_openapi`（补路径后）及其余 v0.8x 相关文件全部通过。
- mypy（cloud/）与 ruff 全部通过。
- **全量回归**（`pytest tests/`，沙箱 + 默认 basetemp，8m40s）：
  **1930 passed, 7 failed, 5 skipped**。其中 7 个失败均为环境问题，与
  v0.9.4 改动无关：
  - `test_runner_workflow_metadata`（5 个）+ `test_script.py`（1 个）：
    本机 `.env` 指向的 LLM 端点 `http://43.136.177.248:12580/v1` 返回
    503 `model_not_found`，preflight 探测失败；`CI=1` 下全部通过。
  - `test_v061_remote.py::test_full_lifecycle`（1 个）：WorkBuddy
    safe-delete 防护（`SAFE_DELETE_FAIL_CLOSED`）拦截了
    `output/LifecycleTest/logs/` 内旧日志文件的删除（遗留文件为先前
    测试运行产生，且该测试在本改动前的 231 个用例批次中通过）。
  - 注：全量在「项目目录 basetemp」下运行时 safe-delete 会拦截大量
    unlink 导致更多环境性失败；改用默认 basetemp 后仅剩上述 7 个。

## 潜在冲突点

1. **test_v060_task_queue.py::test_retry_exhausted**：重试耗尽行为由 FAILED
   改为 DEAD（v0.9.4 预期变化），已同步断言；集成审查时注意此语义变更。
2. **test_v082_openapi.py::EXPECTED_PATHS**：新增 3 条 /deadletters 路径。
3. **DEAD 不记录错误指标**：`_record_task_outcome` 只对 FAILED 调
   `record_error`；若产品要求 DEAD 计入错误计数，需在集成时补。
4. **本地分支 ref 曾因一次失败的 `git stash push -u`（沙箱 CRLF 警告）被清空**：
   已重建 `refs/heads/feature/v0.9.4-queue-distributed` → 05d2661（与
   origin 相同），并验证 `git status` / `git log` 正常。提交前请复核 reflog。
5. 并行子代理若同时改 `cloud/worker.py` / `api.py` / `openapi.py` /
   `contract.py`，需以集成分支为准做合并。
6. 环境遗留：`output/`（gitignored）含历次测试残留，本地跑
   `test_v061_remote::test_full_lifecycle` 可能被 WorkBuddy safe-delete
   防护拦截（与 v0.9.4 无关）。
