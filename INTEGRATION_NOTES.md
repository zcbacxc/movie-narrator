# INTEGRATION_NOTES — v0.9.2 Task Lifecycle

> 分支：`feature/v0.9.2-lifecycle` ｜ 工作树：`wt-v092` ｜ 提交前缀：`feat(lifecycle):`

本文件记录 v0.9.2 小版本（**Task Checkpointing + Graceful Shutdown**）的改动范围、
模型字段、恢复/停机流程、配置项、测试结果与潜在冲突点，供集成方（主工作树）逐
版本合并时参考。

## 1. 改动文件

### 新增
| 文件 | 说明 |
|---|---|
| `src/movie_narrator/cloud/checkpoint.py` | `TaskCheckpoint` / `ResumePlan` / `CheckpointStore`（检查点持久化、原子写、resume 解析） |
| `tests/test_v092_lifecycle.py` | v0.9.2 生命周期测试（26 个用例） |
| `INTEGRATION_NOTES.md` | 本文档 |

### 修改
| 文件 | 改动摘要 |
|---|---|
| `src/movie_narrator/cloud/worker.py` | `ProgressConsole` 增加 `on_step_complete` 回调；`_execute_task` 增加 `resume/attempt/checkpoint_store`；`run_task` 入口解析检查点、成功删除检查点；新增 `_extract_result/_restore_context/_step_index_of` |
| `src/movie_narrator/cloud/queue.py` | 新增 `QueueShutdownError`；`shutdown(wait, timeout)` drain 语义；`submit()` 停机后拒绝；`is_shutting_down`/`checkpoint_store` 属性；`_cancel_inflight`；协议签名同步 |
| `src/movie_narrator/cloud/api.py` | 新增 `begin_drain()`；`stop(drain_timeout)` drain 语义；`POST /tasks` 停机期间 503；`/info` 增加 `shutting_down` 字段 |
| `src/movie_narrator/cloud/daemon.py` | 新增 `graceful_shutdown_timeout()`、`drain_inflight()`；信号处理改为 drain→`os._exit(0)` |
| `src/movie_narrator/cloud/models.py` | `TaskProgress` 增加 `latest_checkpoint_step` / `checkpoint_updated_at` |
| `src/movie_narrator/cloud/remote_queue.py` | `shutdown(wait, timeout)` 协议签名（no-op） |
| `src/movie_narrator/config.py` | Settings 新增 `graceful_shutdown_timeout`（env `MN_GRACEFUL_SHUTDOWN_TIMEOUT`，默认 30.0） |
| `src/movie_narrator/contract.py` | `__all__` 追加分组 **Task Lifecycle (v0.9.2)**：`TaskCheckpoint`、`CheckpointStore`、`ResumePlan`、`QueueShutdownError`。**未改 `CONTRACT_VERSION`** |
| `src/movie_narrator/cloud/__init__.py` | 导出上述 4 个新符号 |
| `.env.example` | 新增 `MN_GRACEFUL_SHUTDOWN_TIMEOUT` 说明块 |

### 明确未改
`CONTRACT_VERSION`（仍 `(0,8,3)`）、`pyproject.toml` version（仍 `0.8.4`）、
`tests/test_contract.py` 版本断言、`cloud/openapi.py`、`pipeline/runner.py` 内部逻辑
（仅复用 `run_pipeline(start_step=)` 与 `_next_step_after`）、`utils/llm.py` 等。

## 2. TaskCheckpoint 模型字段

```python
class TaskCheckpoint(BaseModel):
    task_id: str
    completed_step: str                      # 最后完成的 pipeline 步骤名
    context_dump: Dict[str, Any]             # Context model_dump(mode="json")
    saved_at: str                            # UTC ISO-8601
    attempt: int = 0                         # 写检查点时的重试次数
```

配套 `ResumePlan`：`completed_step` / `start_step` / `context_dump` / `done`。
`done=True` 表示检查点已完成最后一步（`STEPS[-1]`），任务实质已跑完、只差结果提取。

**注意**：`context_dump` 复用 runner 的 model_dump 逻辑（`exclude={"services"}`），
但额外排除 `cost_tracker`——其内部含 `threading.Lock`，pydantic 无法序列化（这是
既有 `pipeline_state.json` pause/resume 机制的一个潜在隐患，本分支用排除字段规避，
未改动 runner 源码）。恢复时重新注入 `services` 与全新 `CostTracker`。

## 3. 恢复流程说明（Task checkpointing）

- **写入**：worker 每完成一个 pipeline 步骤（`step_ok` / `step_skip` / `step_warn`）
  通过 `ProgressConsole.on_step_complete` 回调把当前 `Context` 快照写入
  `CheckpointStore`（路径 `<storage_dir>/checkpoints/<task_id>.json`，tmp+`os.replace`
  原子写，参照 `TaskStorage._flush`）。
- **恢复**：`run_task` 每次 attempt 入口调用 `CheckpointStore.resolve_resume(task_id)`：
  - 无检查点 → 从头跑（`start_step=None`）。
  - 有检查点且 `completed_step` 非末步 → `start_step=_next_step_after(completed_step)`，
    用 `context_dump` 重建 Context（保留已产出的 segments/clips/audio 等），再调用
    `run_pipeline(ctx, controller=..., start_step=...)`。
  - `completed_step` 为末步 → `done=True`，跳过 `run_pipeline`，直接从 context_dump
    重建结果标记 COMPLETED（崩溃发生在“最后一步完成之后、结果落库之前”的场景）。
- **清理**：任务 COMPLETED 后删除检查点；FAILED/CANCELLED 保留（供未来重跑复用）。
- **与 `mn resume --state` 的关系**：互不干扰。CLI 手动恢复仍是 `pipeline_state.json`
  路径；任务级检查点是 worker 自动行为，仅当 `run_task` 被传入 `checkpoint_store`
  （`LocalTaskQueue` 默认接线）时生效。`run_task`/`_execute_task` 直接调用且不传
  store 时行为与 v0.9.1 完全一致（向后兼容）。
- **已知边界**：daemon 崩溃重启后，遗留 RUNNING 任务不会自动重投（队列 `start()`
  只统计 active 计数，不重跑）。本版本恢复机制作用于「重试」与「同一任务被再次
  执行」两个路径；自动重投建议留给 v0.9.4（DLQ/分布式）一并设计。

## 4. Shutdown 语义变化（Graceful shutdown）

### `LocalTaskQueue.shutdown(wait=True, timeout=None)`
- `wait=True`：executor 停止接收新任务 → join 所有 in-flight future，`timeout` 为预算
  （`None`=无限等待，即 v0.9.1 行为）；超时后剩余在飞任务由 `_cancel_inflight`
  请求协作取消 + 持久化状态置 `CANCELLED`。
- `wait=False`：`cancel_futures=True` 立即返回，在飞任务标记 CANCELLED。
- shutdown 之后 `submit()` 抛 `QueueShutdownError`（区别于未启动的 `RuntimeError`）。
- `start()` 清除 `_shutting_down`，允许重启。

### `TaskAPIServer.stop(drain_timeout=None)` / `begin_drain()`
顺序：① 置 `_shutting_down` Event（`POST /tasks` 返 503，`/ready` 503，`/info`
报告 `shutting_down=true`）→ ② 停 artifact sweeper → ③ 若 `_owns_queue` 则
`queue.shutdown(wait=True, timeout=...)`（timeout 取 `drain_timeout` →
构造参数 → `MN_GRACEFUL_SHUTDOWN_TIMEOUT`）→ ④ `server.shutdown()` 停 HTTP。
`begin_drain` 不碰 HTTP 循环，供信号路径在探针仍响应时排空。

### daemon 信号处理（SIGINT/SIGTERM）
`drain_inflight(server, queue, timeout)`：`begin_drain` → `queue.shutdown(wait=True,
timeout=timeout)` → `os._exit(0)`。**必须用 `os._exit`**：worker 线程非 daemon，
`sys.exit` 会在解释器退出时 join 卡死的 render 线程；且 handler 与 `serve_forever`
同线程，`server.stop()` 会死锁（既有隐患），故用 `begin_drain` 替代。取消语义不变：
用户主动 `cancel` 仍走 `CancelController` 立即停。

## 5. 配置项

- `Settings.graceful_shutdown_timeout: float = 30.0`（env `MN_GRACEFUL_SHUTDOWN_TIMEOUT`）。
  字段名按 pydantic-settings `MN_` 前缀惯例去掉了 `mn_` 前缀（若字段名为
  `mn_graceful_shutdown_timeout`，env 会变成 `MN_MN_...`，故未照字面命名）。
- `.env.example` 新增 Task lifecycle 说明块。

## 6. 测试结果

- `tests/test_v092_lifecycle.py`：**27 passed**（检查点读写/原子写/resume 解析、
  崩溃恢复 `start_step` 传递与 context 恢复、done 分支、成功删检查点、queue
  shutdown join/超时取消/拒收/排队任务排空、API drain 拒新任务与探针状态、
  daemon drain 顺序、队列端到端检查点接线）。
- 既有相关套件回归：`test_v060_task_queue.py` + `test_v061_remote.py` +
  `test_v082_health.py` + `test_v083_lifecycle.py` = **250 passed**；
  `test_contract.py` + `test_pipeline_pause.py` + `test_v080_api_auth.py` = **102 passed**。
- 全量 `pytest tests/` 逐文件运行：**1876 passed**（80/82 文件 EXIT=0）。
  唯一失败 2 个文件 6 个用例（`test_runner_workflow_metadata.py` 5 个 +
  `test_script.py` 1 个），**与本分支改动无关**——已用 `git show HEAD:…`
  临时还原改动文件后复跑，失败完全一致。根因是环境层：① `run_pipeline`
  preflight 探测真实 LLM 端点 `http://43.136.177.248:12580/v1` 返回 503
  model_not_found；② 新 venv 依赖版本（openai 2.52.0 等）与基线不同导致
  `test_script` 重试计数 5!=4。
- `mypy src/movie_narrator/cloud/ src/movie_narrator/config.py`：**Success**。
- `ruff check`（改动文件）：**All checks passed**。

## 7. 环境说明（集成方注意）

- 共享 Python 3.13.12 的 `site-packages` 出现**跨包文件丢失**（pydantic/pip/yaml/
  openai/httpx/moviepy/numpy 等大量包缺文件），且沙箱禁止 pip 的 safe-delete
  （recycle-bin），`pip install --force-reinstall` 无法完成。本任务改用全新 venv：
  `C:\Users\HXT-PC\WorkBuddy\movie-narrator-project\.venv-v092`
  （`pip install -e ".[dev]"`，openai 装到 2.52.0）。全量测试命令相应改为：
  `.venv-v092/Scripts/python.exe -m pytest tests/ -q --timeout=300`。
- 曾把全局 `.pth` 从 wt-v091 切到 wt-v092 并留 `.bak` 备份；venv 建好后全局路径
  是否还原由集成方决定（各分支子代理并行时建议还原，避免相互污染）。
- 工作树 `wt-v092/.git/refs/heads/` 在 `git stash` 尝试过程中被意外删除，已手工
  重建 `refs/heads/feature/v0.9.2-lifecycle`（指向 `05d2661`，与 HEAD reflog
  一致）；stash 未生效，无残留 stash 条目，工作树内容完好。后续集成方如需 stash
  请留意此仓库的 git 状态。
- `daemon.py` 历史上有 6 行 LF 混合换行，本次整文件统一为 CRLF（diff 中 docstring
  示例有 4 行纯换行符归一化，无内容变化）。

## 8. 潜在冲突点

| 文件 | 冲突风险 | 说明 |
|---|---|---|
| `cloud/worker.py` | **高** | v0.9.4（DLQ/分布式）会继续改；本次改 `run_task` 签名（新增 `checkpoint_store`）、`_execute_task`、`ProgressConsole`、新增辅助函数 |
| `cloud/queue.py` | **高** | `shutdown` 语义/签名、`submit` 拒收、`_cancel_inflight` 与 v0.9.3/0.9.4 批处理/队列改动的交集 |
| `cloud/api.py` | 中 | 只改了 `stop()/begin_drain()` 与既有 handler（未加路由）；v0.9.3/0.9.4 加路由时按块合并 |
| `cloud/models.py` | 中 | 只在 `TaskProgress` 尾部加字段，未动 `TaskRequest`/`Batch` |
| `cloud/daemon.py` | 低 | 信号 handler 与 drain 辅助函数 |
| `config.py` / `.env.example` | 低 | 新增 `graceful_shutdown_timeout` 配置块 |
| `contract.py` | 低 | 只追加 `__all__` 与 import，未动 `CONTRACT_VERSION`（集成时按 V09_PLAN 逐版本升 MINOR） |

## 9. 遗留风险

1. **恢复只覆盖 run_task 重试/再执行路径**：daemon 崩溃重启后不会自动重投 RUNNING
   任务，检查点可能在重启后闲置（建议 v0.9.4 结合 DLQ/分布式补充自动恢复）。
2. **resume 时 `TaskProgress` 只统计本轮执行的步骤**：百分比从 `start_step` 起算
   （已做 initial_step_index 校正），`steps_completed` 不包含检查点之前的步骤；
   属展示层小瑕疵，不影响恢复正确性。
3. **`step_warn` 也写检查点**：软步骤降级后 pipeline 继续，检查点按实际推进位置
   记录，行为正确；但“降级重跑”语义需在文档/API 侧说明。
4. **`cost_tracker` 不随检查点落盘**：恢复后成本统计从 0 重新累计（步骤内
   `cost_tracker is not None` 均有防护），仅影响成本报告准确性。
5. 环境损坏的根因未查明（疑病毒式文件删除/AV 隔离），venv 方案仅规避不根治。
