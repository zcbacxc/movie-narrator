# 控制台日志打印重构设计

> 状态：设计完成，待评审
> 日期：2026-07-13
> 版本：v0.3.1（infra 重构，不改变外部行为）

## 动机

当前项目**没有任何日志系统**，所有输出都是裸 `print()` 调用，分散在约 40+ 处：
- `runner.py` 用 ANSI 颜色码输出步骤状态（▶/✓/✗）
- 各 pipeline step 各自 `print()` 跳过/失败/成功信息（⏭/✗/✓）
- `cli.py` 用 `typer.echo()` 输出结果和错误
- `render.py` 有一个 tqdm 进度条

问题：
1. **无法调试**：LLM 调用、FFmpeg 命令、中间数据全部丢失，只能靠重现
2. **输出入口分散**：`print` / `typer.echo` / tqdm 三套体系各自为政
3. **隐式约定**：step 内部自己覆盖 runner 的 `▶` 线，语义靠 \r 技巧维持
4. **测试困难**：验证输出只能靠 `capsys`，状态无结构化记录

## 目标

1. **控制台只展示进度**：步骤状态（▶/✓/✗/⏭/⚠）、tqdm 进度条、最终结果
2. **所有详情写入日志文件**：报错 traceback、LLM 调用、FFmpeg 命令、中间数据
3. **日志文件位置**：`output/<movie>/logs/<timestamp>.log`，自动保留最近 3 次
4. **输出入口统一**：所有输出走 `ctx.services.console`，不再裸 `print()`
5. **状态与渲染分离**：step 产生状态，runner 渲染状态

## 架构

三层分离，单一出口：

```text
Pipeline Step
    ↓
ctx.services.console  ←── 唯一的输出入口
    ↓
Console (Protocol)     ←── UI 层：控制台渲染 + 状态分发
    ↓
AppLogger              ←── 日志层：结构化写入
    ↓
logging.FileHandler    ←── 标准库：文件落地
```

### 模块清单

```
src/movie_narrator/
├── utils/
│   ├── console.py    ← 新建：Console Protocol + PlainConsole + build_console()
│   ├── log.py        ← 新建：AppLogger（日志层，操作 logging.FileHandler）
│   └── retention.py  ← 新建：cleanup_logs()（保留最近 N 次）
├── models.py         ← 修改：新增 Services / StepState
└── pipeline/
    └── runner.py     ← 修改：统一状态渲染，引入 Console
```

## 核心组件

### StepState — 步骤回传状态

```python
# models.py 新增

from enum import Enum

class StepResult(Enum):
    SUCCESS = "success"
    SKIPPED = "skipped"
    WARNING = "warning"

@dataclass
class StepState:
    result: StepResult = StepResult.SUCCESS
    message: str | None = None  # skip reason / warning detail
```

`Context` 新增字段 `step_state: StepState`，step 在返回前写入：

```python
# step 内部
if output.exists():
    ctx.step_state.result = StepResult.SKIPPED
    ctx.step_state.message = "already exists"
    return ctx
```

### Services — 基础设施容器

```python
# models.py 新增

from typing import Protocol, runtime_checkable

@runtime_checkable
class Console(Protocol):
    """输出抽象 — 控制台渲染 + 日志分发"""
    def step(self, name: str) -> None: ...
    def step_ok(self, name: str, elapsed: float) -> None: ...
    def step_skip(self, name: str, reason: str) -> None: ...
    def step_warn(self, name: str, reason: str) -> None: ...
    def step_err(self, name: str, exc: Exception, elapsed: float) -> None: ...
    def debug(self, msg: str) -> None: ...
    def inline_warn(self, msg: str) -> None: ...
    def final(self, msg: str) -> None: ...
    def progress(self, *args, **kwargs): ...  # → tqdm

@dataclass
class Services:
    console: Console
```

`Context` 新增字段 `services: Services`。

使用方式：`ctx.services.console.step_skip(...)` / `ctx.services.console.debug(...)`。

### AppLogger — 日志层

```python
# utils/log.py

import logging
from pathlib import Path

class AppLogger:
    """纯文件日志层。不负责 UI，不接触控制台。"""

    def __init__(self, log_file: Path, level: int = logging.DEBUG):
        self._logger = logging.getLogger("movie_narrator")
        self._logger.setLevel(level)
        self._logger.handlers.clear()
        handler = logging.FileHandler(log_file, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        self._logger.addHandler(handler)

    def debug(self, msg: str) -> None:
        self._logger.debug(msg)

    def info(self, msg: str) -> None:
        self._logger.info(msg)

    def warning(self, msg: str) -> None:
        self._logger.warning(msg)

    def error(self, msg: str, exc_info: bool = False) -> None:
        self._logger.error(msg, exc_info=exc_info)
```

### PlainConsole — UI 层

```python
# utils/console.py

from pathlib import Path
from .log import AppLogger

class PlainConsole:
    """标准 Console 实现：ANSI + print + tqdm + typer.echo"""

    def __init__(self, logger: AppLogger):
        self._log = logger

    # ── 生命周期事件（由 runner 调用）────────────────────

    def step(self, name: str) -> None:
        print(f"\033[94m▶\033[0m {name}", end="", flush=True)
        self._log.info(f"STEP_START {name}")

    def step_ok(self, name: str, elapsed: float) -> None:
        t = _fmt_time(elapsed)
        print(f"\r\033[92m✓\033[0m {name}  \033[1m{t}\033[0m")
        self._log.info(f"STEP_OK {name} elapsed={elapsed:.3f}s")

    def step_skip(self, name: str, reason: str) -> None:
        print(f"\r\033[93m⏭\033[0m {name}: {reason}")
        self._log.info(f"STEP_SKIP {name} reason={reason}")

    def step_warn(self, name: str, reason: str) -> None:
        print(f"\r\033[93m⚠\033[0m {name}: {reason}")
        self._log.warning(f"STEP_WARN {name} reason={reason}")

    def step_err(self, name: str, exc: Exception, elapsed: float) -> None:
        t = _fmt_time(elapsed)
        # 控制台只显示简短摘要
        print(f"\r\033[91m✗\033[0m {name}: {exc} \033[93m({t})\033[0m")
        # 日志记录完整 traceback
        self._log.error(f"STEP_ERR {name}", exc_info=True)

    # ── 过程信息（step 内部直接调用）────────────────────

    def debug(self, msg: str) -> None:
        # 不进控制台
        self._log.debug(msg)

    def inline_warn(self, msg: str) -> None:
        """过程警告 — step 内部用于非致命警告（如 metadata 部分缺失）。
        与 step_warn 不同：inline_warn 是过程信息，step_warn 是步骤最终状态。"""
        print(f"\033[93m⚠\033[0m {msg}")
        self._log.warning(msg)

    # ── 最终结果（cli 层调用）──────────────────────────

    def final(self, msg: str) -> None:
        import typer
        typer.echo(msg)
        self._log.info(msg)

    # ── 进度条（透传给 tqdm）───────────────────────────

    def progress(self, *args, **kwargs):
        from tqdm import tqdm
        return tqdm(*args, **kwargs)
```

### 日志文件管理

```python
# utils/retention.py

from pathlib import Path

def cleanup_logs(logs_dir: Path, keep: int = 3) -> None:
    """保留最近 `keep` 个 .log 文件，删除更早的。"""
    logs = sorted(
        logs_dir.glob("*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for old in logs[keep:]:
        old.unlink()
```

### build_console — 工厂函数

```python
# utils/console.py

from datetime import datetime
from pathlib import Path
from .log import AppLogger
from .retention import cleanup_logs

def build_console(output_dir: Path) -> PlainConsole:
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = logs_dir / f"{timestamp}.log"

    # latest 副本（Windows 兼容：直接双写而非 symlink）
    latest = logs_dir / "latest.log"
    if latest.exists():
        latest.unlink()

    logger = AppLogger(log_file)
    # 双写：时间戳文件 + latest 副本
    latest_handler = logging.FileHandler(latest, encoding="utf-8")
    latest_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    logger._logger.addHandler(latest_handler)

    cleanup_logs(logs_dir, keep=3)

    return PlainConsole(logger)
```

## Runner 改造

### 新流程

```
For each step in STEPS:
  1. console.step(name)              ← 输出 ▶  + 写 STEP_START 日志
  2. 执行 step(ctx)
  3. On exception:
     - console.step_err(name, e, elapsed)  ← 输出 ✗ 摘要 + 写 STEP_ERR + traceback
     - re-raise (hard stop)
  4. 读取 ctx.step_state.result:
     - SUCCESS → console.step_ok(name, elapsed)
     - SKIPPED → console.step_skip(name, ctx.step_state.message)
     - WARNING → console.step_warn(name, ctx.step_state.message)
  5. 重置 ctx.step_state 为默认值（SUCCESS, message=None）
  6. _check_strict(ctx, name)
After loop:
  console.final(f"✅ 视频已生成: {ctx.video_path}")
```

### 关键变化

- 不再区分 `SOFT_STATUS_STEPS` — 所有 step 统一走 `StepState` 回传
- 不再有 `print()` 裸调用
- ANSI 颜色码收敛到 PlainConsole 内部
- `_fmt_time` 收敛到 PlainConsole 内部（或保留为 console 的静态方法）
- `PipelineStatus`（软步骤状态）与 `StepState`（单次运行状态）共存：
  - `PipelineStatus` 保留给跨 step 的软状态追踪（research/align/scene/match/bgm/export），值域不变：`disabled | skipped | success | failed`
  - `StepState` 是每个 step 返回时的即时状态，由 runner 消费后重置
- **软步骤失败处理**：soft step 内部 catch 异常后设置 `PipelineStatus.<field> = "failed"` 和 `StepState.result = WARNING`，runner 渲染 ⚠。`--strict` 模式下 `_check_strict` 检查的是 `PipelineStatus` 中的 `"failed"`（与现状一致），不会因为引入 StepState 而丢失 strict 行为。
- **硬步骤失败处理**：hard step 抛出异常 → runner catch → `console.step_err()` → re-raise（与现状一致），StepState 不参与。

## Step 改造

### 规则

1. **所有 `print()` 调用** → 替换为 `ctx.services.console` 对应方法
2. **⏭/✗/✓ 状态输出** → 不再直接输出，改为设置 `ctx.step_state`
3. **LLM 调用的 prompt/response** → `ctx.services.console.debug(...)`
4. **FFmpeg/subprocess 调用** → `ctx.services.console.debug(...)`
5. **进度条** → `ctx.services.console.progress(...)`（目前只有 render.py 用到）

### 示例：research.py 改造前后

**改造前**：
```python
if not research_enabled:
    print("⏭ research_plot: research disabled")
    ctx.status.research = "skipped"
    return ctx

try:
    info = _fetch_research(ctx)
    ctx.research = info
    print("✓ research_plot")
    ctx.status.research = "success"
except Exception as e:
    print(f"✗ research_plot: {e}")
    ctx.status.research = "failed"
```

**改造后**：
```python
if not research_enabled:
    ctx.step_state.result = StepResult.SKIPPED
    ctx.step_state.message = "research disabled"
    ctx.status.research = "skipped"
    return ctx

try:
    info = _fetch_research(ctx)
    ctx.research = info
    # step_state 默认是 SUCCESS，无需显式设置
    ctx.status.research = "success"
except Exception as e:
    ctx.step_state.result = StepResult.WARNING
    ctx.step_state.message = str(e)
    ctx.status.research = "failed"
    # runner 渲染 ⚠，日志记 STEP_WARN
    # 注意：研究失败不应阻塞 pipeline，所以用 WARNING 而非 raise
```

## 职责边界（设计约束）

> **步骤状态（SUCCESS/SKIPPED/WARNING）只能由 step 产生（写入 StepState），只能由 runner 渲染（调用 Console 对应方法）；step 不直接输出最终状态符号（✓/✗/⏭/⚠）。**

| 角色 | 可以做的事 | 不能做的事 |
|------|-----------|-----------|
| **step** | 修改业务数据、设置 StepState、`console.debug()`、`console.inline_warn()`、`console.progress()` | 调用 `console.step()` / `console.step_ok()` / `console.step_err()` / `console.step_skip()` / `console.step_warn()`；裸 `print()` 状态符号 |
| **runner** | `console.step()` / `console.step_ok()` / `console.step_err()` / `console.step_skip()` / `console.step_warn()`；读取 StepState 并渲染 | 修改 StepState |
| **cli** | `console.final()`、异常处理 | `print()` / `typer.echo()` 绕过 console |

例外：step 内部遇到需要用户注意但不改变步骤结果的事情（如 "ffmpeg metadata 部分缺失"），走 `console.inline_warn()` — 这是过程警告，不是步骤状态。

## Context 字段变更

```python
# models.py — 新增

class Context(BaseModel):
    # ... 现有字段不变 ...

    # 新增：基础设施依赖（不是业务数据）
    services: Services  # 由 build_context() 注入

    # 新增：单步回传状态（runner 消费后重置）
    step_state: StepState = Field(default_factory=StepState)
```

注意：`Services` 和 `StepState` 是 `@dataclass`（非 Pydantic），因为 `Console` 是 Protocol 无法被 Pydantic 校验。`Context` 需调整 model_config 允许任意类型，或将 services/step_state 作为非 Pydantic 属性挂载。

具体实现方式：利用 Pydantic 的 `model_config = {"arbitrary_types_allowed": True}`，或者将这两个字段放在 `Context.__init__` 中手动赋值（Pydantic 会忽略未声明的 attribute）。

## 日志文件示例

```
2026-07-13 10:15:01 [INFO] STEP_START resolve_video
2026-07-13 10:15:01 [INFO] STEP_OK resolve_video elapsed=0.002s
2026-07-13 10:15:01 [INFO] STEP_START prepare_assets
2026-07-13 10:15:01 [INFO] STEP_OK prepare_assets elapsed=0.001s
2026-07-13 10:15:01 [INFO] STEP_START research_plot
2026-07-13 10:15:01 [INFO] STEP_SKIP research_plot reason=research disabled
2026-07-13 10:15:01 [INFO] STEP_START generate_script
2026-07-13 10:15:01 [DEBUG] LLM prompt: 你是一个电影解说视频的...
2026-07-13 10:15:05 [DEBUG] LLM response: {"segments":[{"text":"..."}]}
2026-07-13 10:15:05 [INFO] STEP_OK generate_script elapsed=4.123s
2026-07-13 10:15:05 [INFO] STEP_START generate_voice
2026-07-13 10:15:05 [DEBUG] TTS: 15 segments, voice=zh-CN-YunxiNeural
2026-07-13 10:15:05 [DEBUG] TTS cache hit: segment 0 (md5=abc123)
2026-07-13 10:15:12 [INFO] STEP_OK generate_voice elapsed=7.456s
...
2026-07-13 10:16:30 [INFO] STEP_START render_video
2026-07-13 10:17:15 [DEBUG] MoviePy: CompositeVideoClip 15 layers, 1920x1080
2026-07-13 10:17:15 [DEBUG] FFmpeg: ffmpeg -i ... -c:v libx264 ...
2026-07-13 10:18:45 [INFO] STEP_OK render_video elapsed=135.200s
2026-07-13 10:18:45 [INFO] ✅ 视频已生成: output/飞驰人生/final.mp4
```

## CLI 层改造

`cli.py` 中的 `typer.echo()` 调用：
- 最终结果输出（如文件路径）→ `console.final(msg)`
- 错误信息 → `console.warn(msg)` + `raise typer.Exit(1)`
- debug 命令的输出（如 `mn resolve --json`）→ 暂保留 `typer.echo`。原因：这些命令的 stdout 是**数据输出**（可能被管道消费、被脚本解析），不是进度或日志。例如 `mn resolve --json | jq .source_video_path` 依赖 stdout 为纯 JSON。用 `console.final()` 会多写一份日志（这倒无妨），但核心是 stdout 的语义是"数据"而非"消息"。

## 测试策略

### Console / AppLogger 单元测试

- `AppLogger`：验证日志文件写入，不同级别的消息
- `PlainConsole`：mock AppLogger，验证 step_ok/step_err/step_skip 等方法的控制台输出格式
- `cleanup_logs`：验证保留最近 N 次的逻辑
- `build_console`：验证目录创建、文件命名、双写

### Step 单元测试

- 不再依赖 `capsys` 验证输出
- 验证 `ctx.step_state.result` 和 `ctx.step_state.message`
- 验证 `ctx.status.*` 软状态字段

### Runner 单元测试

- Mock Console，验证 step 状态到 Console 方法的映射
- 验证 StepState 在每次 step 后被重置
- 验证 strict 模式下 failed 状态触发 PipelineStrictError

### E2E Smoke Test

- `CI=1 mn create ...` 验证日志文件生成 + 控制台输出不退化

## 不做的事

- **不引入 Rich**：当前无明显 TUI 需求，未来可随时将 PlainConsole 替换为 RichConsole
- **不改 step 函数签名**：保持 `step(ctx) -> ctx`
- **不用模块级单例**：Console 通过 Services 注入，可测试、可替换
- **不动 tqdm**：透传即可，不与 logging 融合
- **不把日志写入数据库/远程**：YAGNI

## 实现顺序

1. **新建 `utils/log.py`** — AppLogger + FileHandler 配置
2. **新建 `utils/retention.py`** — cleanup_logs 函数
3. **新建 `utils/console.py`** — Console Protocol + PlainConsole + build_console
4. **修改 `models.py`** — 新增 StepResult / StepState / Services / Console Protocol
5. **修改 `pipeline/runner.py`** — 统一状态渲染，引入 Console
6. **逐个迁移 pipeline step** — 替换 print()，设置 StepState
7. **迁移 `cli.py`** — 替换 typer.echo 为 console.final / console.warn
8. **补充单元测试** — AppLogger / Console / retention / 各 step 的 StepState 验证
9. **E2E smoke test** — 验证不退化