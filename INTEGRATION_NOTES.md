# INTEGRATION_NOTES — v0.9.3 (Batch Job Submission + Scheduled Jobs)

> 分支：`feature/v0.9.3-batch-schedule`
> 交付范围：批量提交（Batch）+ 聚合进度（BatchProgress）+ cron 定时任务（Scheduler）
> 本文件为集成联调用，非正式文档；正式文档归入 `docs/`（集成时统一整理）。

## 1. 改动文件

### 新增
| 文件 | 说明 |
|------|------|
| `src/movie_narrator/cloud/scheduler.py` | cron 解析器 + `JobScheduler` + `ScheduleRequest`/`ScheduleRun`/`ScheduleError` |
| `tests/test_v093_batch_schedule.py` | v0.9.3 针对性测试（48 项） |

### 修改
| 文件 | 改动内容 |
|------|---------|
| `src/movie_narrator/cloud/models.py` | 新增 `BatchStatus`、`BatchProgress`、`BatchRequest`、`Batch` |
| `src/movie_narrator/cloud/queue.py` | `TaskQueue` Protocol 新增 4 个 batch 方法；`LocalTaskQueue` 实现 + `_refresh_batch` 聚合逻辑 + `JsonModelStore` 持久化 batches.json |
| `src/movie_narrator/cloud/storage.py` | 新增通用 `JsonModelStore`（批量记录/调度记录持久化，原子写 tmp+replace） |
| `src/movie_narrator/cloud/api.py` | 新路由：`POST /tasks/batch`、`GET /batches`、`GET/DELETE /batches/{id}`、`POST/GET /schedules`、`DELETE /schedules/{id}`、`GET /schedules/{id}/runs`；`_BATCH_PATTERN`/`_SCHEDULE_PATTERN`/`_SCHEDULE_RUNS_PATTERN` 正则 + 路由模板 + 静态路径 |
| `src/movie_narrator/cloud/openapi.py` | `/tasks/batch`、`/batches`、`/batches/{batch_id}`、`/schedules`、`/schedules/{schedule_id}`、`/schedules/{schedule_id}/runs` 路径段；组件 schema：`Batch`/`BatchRequest`/`BatchProgress`/`BatchStatus`/`ScheduleRequest`/`ScheduleRun` 及手动响应 schema |
| `src/movie_narrator/cloud/remote_queue.py` | `RemoteTaskQueue` 实现 4 个 batch 方法（HTTP 客户端） |
| `src/movie_narrator/cloud/daemon.py` | `run_daemon`/`WorkerDaemon` 创建 JobScheduler；`mn_scheduler_enabled` 时启动调度循环；优雅停机由 `TaskAPIServer.stop()` 触发 |
| `src/movie_narrator/config.py` | Settings 新增 `scheduler_enabled`（默认 True）、`scheduler_poll_interval`（默认 15.0） |
| `.env.example` | 同步两个配置项注释块 |
| `src/movie_narrator/contract.py` | `__all__` 新增分组 `Batch & Schedule (v0.9.3)` + 底部懒加载 re-export |
| `src/movie_narrator/cloud/__init__.py`、`src/movie_narrator/__init__.py` | 导出新符号 |
| `tests/test_v082_openapi.py` | `EXPECTED_PATHS` 补充新路径 |

### 未改动（硬性边界，遵守）
`cloud/worker.py`（run_task 重试核心）、`cloud/queue.py` 的 shutdown 语义（仅在其上加 batch）、`pipeline/runner.py`、`utils/llm.py`、`providers/tmdb.py`、`vision/vlm.py`、`reliability/`、`cloud/checkpoint.py`。
`CONTRACT_VERSION`（保持 (0,8,3)）、`pyproject.toml` version、`tests/test_contract.py` 版本断言均未动。

## 2. 新增模型字段

- `BatchRequest`：`requests: list[TaskRequest]`（1 ≤ n ≤ 50，pydantic min/max_length）、`name: str | None`、`metadata: dict | None`
- `Batch`：`batch_id`、`name`、`task_ids: list[str]`、`status: BatchStatus`、`created_at`、`completed_at`、`progress: BatchProgress`、`success_count: int`、`failure_ids: list[str]`、`metadata`
- `BatchProgress`：`total/completed/failed/cancelled/running: int`、`percentage: float`（子任务等权聚合）
- `BatchStatus`：`pending | running | completed | partial_failed | failed`
- `ScheduleRequest`：`schedule_id`、`cron`、`task_request: TaskRequest`、`enabled`、`next_run_at`、`created_at`
- `ScheduleRun`：`run_id`、`schedule_id`、`run_at`、`task_id`、`status`（submitted/failed）、`error`

`Batch.status` 语义：
- `pending`：刚创建，成员尚未开始
- `running`：至少一个成员仍活跃
- `completed`：全部成员成功
- `partial_failed`：部分成员失败（或未提交成功）——**注意**：存在未成功提交成员时，即使其余仍在跑，状态也会立即置为 `partial_failed`（因为不可能全成功）
- `failed`：全部成员失败（含全部提交失败）

聚合规则：终态任务按 100% 计，运行中任务按各自 progress.percentage，pending/未提交按 0%，取算术平均（等权）。`failure_ids` 仅含实际 FAILED 的成员任务；未提交成功的成员计入 `progress.failed`（作为 missing），但不在 `failure_ids`（无 task id）。提交失败明细记在 `batch.metadata["submission_failures"]`。

## 3. 路由清单

| 方法 | 路径 | 认证 | 说明 |
|------|------|------|------|
| POST | `/tasks/batch` | ✓ | 批量提交，201 返回 `{batch_id, status, task_ids}` |
| GET | `/batches` | ✓ | 批量列表（`?limit=`），含实时聚合进度 |
| GET | `/batches/{batch_id}` | ✓ | 单个批量 + 聚合进度 + 结果汇总 |
| DELETE | `/batches/{batch_id}` | ✓ | 取消批量内所有活跃任务 |
| POST | `/schedules` | ✓ | 创建定时任务，400 拒绝非法 cron |
| GET | `/schedules` | ✓ | 定时任务列表 |
| DELETE | `/schedules/{schedule_id}` | ✓ | 删除定时任务 |
| GET | `/schedules/{schedule_id}/runs` | ✓ | 最近触发记录（按 schedule 保留 50 条） |

认证语义与既有路由一致：`X-API-Key`（hmac.compare_digest）+ `X-Correlation-ID` 回显，全部经 `_dispatch` 进入 correlation scope。`/tasks/batch` 与 `/tasks/{id}` 路由无冲突（`batch` 含非 [a-f0-9] 字符）。

## 4. cron 语法支持范围

标准 5 段：`minute hour day-of-month month day-of-week`（0=周日，与标准 cron 一致）。
每段支持：
- `*`（通配）
- `*/n`（步进）
- `a,b,c`（列表）
- `a-b`（范围）、`a-b/n`（带步进范围）
- 单值 `a`

校验：字段数必须为 5；数值必须落在段界内（minute 0-59、hour 0-23、dom 1-31、month 1-12、dow 0-6）；步进必须为正整数；`a-b` 不允许反转；非法表达式抛 `ScheduleError`（POST /schedules 映射为 400）。
DOM/DOW 语义：两者都被限定时取「或」（经典 cron）；仅一个被限定时另一个视为 `*`。
`next_after`：按分钟精度扫描未来 ≤367 天；`Feb 30` 之类永远无匹配的表达式抛 `ScheduleError`。
未实现：秒/年字段、`@daily` 等宏、时区（统一按 UTC 计算 `next_run_at`）。

## 5. 配置项

| 键 | 类型 | 默认 | 说明 |
|----|------|------|------|
| `MN_SCHEDULER_ENABLED` | bool | `1` | 关闭后 `mn serve` 不启动调度循环；`/schedules` CRUD 路由仍可用 |
| `MN_SCHEDULER_POLL_INTERVAL` | float | `15.0` | 调度循环两次 due-check 之间的秒数 |

持久化位置：批量记录 → `<storage_dir>/batches.json`；调度记录 → `<storage_dir>/schedules.json`；触发记录 → `<storage_dir>/schedule_runs.json`。`storage_dir` 缺省为 `~/.mn_tasks`。所有写入均经 `JsonModelStore` 原子写（tmp 文件 + `os.replace`）。

## 6. 测试结果

针对性：`pytest tests/test_v093_batch_schedule.py -q --timeout=300` → **48 passed**（BatchRequest 校验、submit_batch 成功/部分失败/全失败/取消/持久化、BatchProgress 聚合百分比、cron 解析（通配/步进/范围/列表/非法）、JobScheduler due 触发（mock submit）、API 路由、OpenAPI 新路径、contract 导出）。

回归：`test_v082_openapi.py`、`test_v060_task_queue.py`、`test_v080_api_auth.py`、`test_contract.py`、`test_settings.py`、`test_v061_remote.py`（除下述既存失败外全部通过）。mypy（cloud/）0 错误，ruff 0 告警。

⚠️ 已知既存失败（**与 v0.9.3 无关**，均在基线版本验证复现）：
1. `tests/test_v061_remote.py::TestRemoteIntegration::test_full_lifecycle` — Windows 沙箱拦截 `Path.unlink()`（`utils/retention.py::cleanup_logs` 删除日志文件），报 `SAFE_DELETE_FAIL_CLOSED / windows-sandbox-recycle-bin-unavailable`，导致 worker 线程异常、任务被判失败。会话开始前 `_pytest_out.txt` 已有同样堆栈。
2. `tests/test_runner_workflow_metadata.py`（5 项）— 测试未 mock LLM，直连 `~/.movie-narrator/.env` 配置的真实端点 `http://43.136.177.248:12580/v1`，返回 `503 model_not_found`（`qwen/qwen3-next-80b-a3b-instruct` 无可用渠道）。属外部服务/配置环境问题。
3. `tests/test_script.py::test_generate_script_phase1_ok_phase2_fail_then_retry` — `assert mock_llm...call_count == 4` 实际 5 次，位于未改动的 `pipeline/script.py` 重试路径；已通过 `git apply -R` 恢复基线版本复现同样失败，确认既存。

以上均为环境/基线问题，CI 或正常环境可复现与否需在集成时确认。本特性全部 48 项测试 + 相关回归全部通过。

全量结果：`pytest tests/ -q --timeout=300` → **7 failed, 1953 passed, 5 skipped**（见 `test-full.log`），7 项失败均为上述既存/环境问题，无一是 v0.9.3 引入。

## 7. 潜在冲突点（集成时关注）

1. **`queue.py` 与 v0.9.4（queue-distributed）**：`LocalTaskQueue.submit_batch` 依赖 `submit()` 返回值与 `_storage`；若 v0.9.4 改造了队列后端，`_refresh_batch` 中 `self._storage.load(tid)` 与 `submit()` 语义需同步适配。
2. **`_STATIC_PATHS` / `_ROUTE_TEMPLATES`**：新路径已登记到 metrics 路由折叠表，若后续新增 `/schedules/{id}/enable` 之类子路由需更新该表与 openapi。
3. **`TaskQueue` Protocol 变更**：新增了 4 个方法（submit_batch/get_batch/list_batches/cancel_batch）。任何鸭子类型实现（如测试中的 mock queue）若未实现这些方法，在 Protocol 名义类型检查下会不完整；`JobScheduler` 只依赖 `submit()`，不依赖 batch 方法。
4. **调度时区**：`next_run_at` 一律按 UTC 计算，与服务器本地时区无关；部署在不同时区的 worker 行为需在文档中明确。
5. **`get_settings()` 缓存**：`daemon._build_scheduler` 用 `get_settings()`（lru_cache），若集成时希望热重载配置需调整。
6. **`Batch` 与 `JsonModelStore.list()`**：默认按 `created_at` 降序；`Batch` 有 `created_at` 字段，排序正常。
7. **`.env.example` 同步**：`config.py` 与 `.env.example` 已同步；首次运行 `ensure_user_config()` 会以 `.env.example` 生成用户配置，已包含新配置项注释。
8. **`test_v082_openapi.py::EXPECTED_PATHS`** 已补充新路径；集成时若并行改 openapi，注意 merge 冲突。

## 8. 交付检查

- [x] Batch 模型 + 校验（1~50）
- [x] submit_batch / cancel_batch / 聚合进度 / 结果汇总
- [x] RemoteTaskQueue 客户端批量支持
- [x] cron 解析器（无第三方依赖）
- [x] JobScheduler 线程 + due 检查 + 持久化
- [x] API 路由 + 认证 + 路由模板/metrics
- [x] OpenAPI 路径 + 组件 schema
- [x] daemon 拉起/停机调度线程
- [x] Settings 两个新配置项 + .env.example
- [x] contract 导出（版本号未动）
- [x] 测试（48 项针对性通过，回归通过，mypy/ruff 干净）
