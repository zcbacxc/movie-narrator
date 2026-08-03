# INTEGRATION NOTES — v0.9.1 Reliability (Circuit Breaker + Retry Policy)

Branch: `feature/v0.9.1-reliability`
Scope: `Circuit Breaker（外部 API 熔断）` + `Retry Policy Framework（可配置重试策略）`

## 1. 改动文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `src/movie_narrator/reliability/__init__.py` | 新增 | 包导出 |
| `src/movie_narrator/reliability/circuit_breaker.py` | 新增 | 熔断器核心 |
| `src/movie_narrator/reliability/retry.py` | 新增 | 重试策略框架 |
| `src/movie_narrator/providers/tmdb.py` | 修改 | `_tmdb_get` 接入 `"tmdb"` 熔断器；网络请求抽到 `_tmdb_get_network` |
| `src/movie_narrator/vision/vlm.py` | 修改 | `_caption_frame` 接入 `"vlm"` 熔断器；发送层抽到 `_caption_frame_send` |
| `src/movie_narrator/utils/llm.py` | 修改 | `get_llm_client()` 改为受 `"llm"` 熔断器保护的 context manager |
| `src/movie_narrator/tts/base.py` | 修改 | `BaseTTSProvider.synthesize` 接入 `"tts"` 熔断器 |
| `src/movie_narrator/config.py` | 修改 | Settings 新增 3 个 `MN_CIRCUIT_*` 字段 |
| `src/movie_narrator/contract.py` | 修改 | `__all__` 新增 `Reliability (v0.9.1)` 分组（不改 `CONTRACT_VERSION`） |
| `.env.example` | 修改 | 新增 `Reliability (v0.9.1)` 分区 |
| `tests/test_v091_reliability.py` | 新增 | 37 个针对性测试 |
| `INTEGRATION_NOTES.md` | 新增 | 本文档 |

未触碰（其他子代理区域）：`cloud/queue.py`、`cloud/api.py`、`cloud/models.py`、`cloud/openapi.py`、`pipeline/runner.py`、`daemon.py`。`cloud/worker.py` 任务级重试保持不变（v0.9.4 集成点）。

## 2. 新增导出（contract.py）

`__all__` 新增 `Reliability (v0.9.1)` 分组（底部懒加载，向后兼容）：

```
CircuitState, CircuitBreaker, CircuitBreakerRegistry,
CircuitOpenError, RetryPolicy, with_retry, with_async_retry
```

- `CONTRACT_VERSION` 未改动（仍为 `(0, 8, 3)`，由集成时统一升级）。
- `pyproject.toml` version 未改动。

## 3. 熔断接入点

所有接入均使用全局 `reliability.CIRCUIT_REGISTRY[<service>]`（每个 service 一个共享熔断器，参数来自 Settings）。

| 服务名 | 位置 | 行为 |
|--------|------|------|
| `tmdb` | `providers/tmdb.py::_tmdb_get` | 熔断开启时抛 `CircuitOpenError`（retryable），不发起 urllib 请求；缓存命中不受影响；网络错误计入失败 |
| `vlm` | `vision/vlm.py::_caption_frame` | 熔断开启时抛 `CircuitOpenError`；`caption_scenes` 按场景捕获并降级为 fallback label |
| `llm` | `utils/llm.py::get_llm_client` | 熔断开启时进入 `with get_llm_client() as llm:` 即抛 `CircuitOpenError`，provider factory 不执行、不发网络请求 |
| `tts` | `tts/base.py::BaseTTSProvider.synthesize` | 熔断开启时抛 `ProviderError(retryable=True)`；CI 静默路径不受影响 |

- 熔断状态变化统一经标准 `logging` 门面 `logger.debug(...)` 记录（项目惯例，同 `workflow/errors.py`、`providers/tmdb.py`）。
- 熔断器为粗粒度：guard 内任何异常都计为一次失败；`CircuitOpenError`（含 HALF_OPEN 探测槽位占满）不计失败。

## 4. 配置项（Settings / .env.example）

| 字段 | 环境变量 | 默认值 | 说明 |
|------|----------|--------|------|
| `circuit_failure_threshold` | `MN_CIRCUIT_FAILURE_THRESHOLD` | `5` | 连续失败次数达到即 OPEN |
| `circuit_recovery_timeout` | `MN_CIRCUIT_RECOVERY_TIMEOUT` | `30.0` | OPEN 状态秒数，超时后进入 HALF_OPEN 放行探测请求 |
| `circuit_half_open_max_calls` | `MN_CIRCUIT_HALF_OPEN_MAX_CALLS` | `1` | HALF_OPEN 期间允许的并发探测请求数 |

## 5. 测试结果

- 针对性：`tests/test_v091_reliability.py` — **37 passed**（熔断状态机流转、恢复探测、探测槽位并发、并发安全、`CircuitOpenError` 传播与 retryable、`with_retry` 指数退避与耗尽、async 退避、`should_retry` 自定义、registry 按服务隔离、TMDB/VLM/LLM/TTS mock 接入）。
- 既有回归（逐文件通过）：`test_contract.py` 60、`test_settings.py` 4、`test_tmdb_provider.py` 41、`test_tts_providers.py` 61、`test_preflight.py`、`test_step_retry.py`。
- 全量回归由收尾脚本后台执行（`tests/` 全量，20+ 分钟）。

> 注意：共享 Python 环境（`.workbuddy/.../3.13.12`）在开发期间被并行子代理重装而破坏（pydantic/pip 文件缺失、editable 指向 wt-v092）。本项目测试基于独立 venv `.venv/`（gitignore 已含 `.venv`）完成，避免相互干扰。

## 6. 与其他版本可能的冲突点

1. **`utils/llm.py::get_llm_client` 签名语义**：从「普通函数返回 CM」改为「@contextmanager 生成器函数」。外部调用 `with get_llm_client() as llm:` 完全兼容；任何**不经过 `with` 而直接调用**并依赖 eager 执行的代码需注意 provider factory 现在在 `__enter__` 时执行。
2. **`providers/tmdb.py::_tmdb_get` 拆分**：网络部分移至 `_tmdb_get_network`。若其他版本/插件直接 import `_tmdb_get` 内部细节（下划线私有），有符号变化风险；公开调用契约不变。
3. **`vision/vlm.py::_caption_frame` 拆分**：发送层移至 `_caption_frame_send`；`_caption_frame` 现在可抛 `CircuitOpenError`（此前只有 `RuntimeError`）。`caption_scenes` 已按场景兜底，不受影响。
4. **contract `__all__`**：新增 7 个名字，向后兼容；`test_contract.py` 的 `__all__` 完整性断言仍通过（只校验子集）。`CONTRACT_VERSION` 保持 `(0, 8, 3)`，集成升版时再统一提升并同步 `test_contract.py` 断言。
5. **`tts/base.py::synthesize`**：新增 `CircuitOpenError → ProviderError(retryable=True)` 分支，原网络错误分类逻辑不变。
6. **熔断器全局状态**：`CIRCUIT_REGISTRY` 为进程级单例，测试间共享；测试通过 patch 各模块的 `CIRCUIT_REGISTRY` 隔离。生产环境如需按请求隔离需在进程边界处理。
