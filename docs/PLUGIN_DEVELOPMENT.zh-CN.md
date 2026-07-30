# 插件开发指南

本指南介绍如何使用 v0.6 插件 SDK (Plugin SDK) 为 movie-narrator
编写、打包和分发插件 (Plugin)。

## 概述

movie-narrator v0.5+ 包含一个插件系统 (Plugin System)，允许外部
代码在不修改核心源码的情况下扩展流水线 (Pipeline)。插件可以：

- **添加流水线步骤 (Pipeline Step)** — 在任意位置注入自定义处理逻辑
- **注册 TTS 服务商 (TTS Provider)** — 添加新的文本转语音后端
- **注册 Vision 字幕器 (Vision Captioner)** — 添加新的场景字幕后端
- **注册 LLM/Research 服务商 (LLM/Research Provider)** — 切换推理后端 (v0.5+)
- **注册远程服务商 (Remote Provider)** — 将 LLM/TTS 调用代理到远程 worker (v0.6+)

所有扩展都使用相同的注册表模式 (Registry Pattern)：在导入时注册，
在运行时发现。

## 快速开始

### 1. 创建插件类

插件是任何带有 `name` 属性、且拥有接受 `PluginContext` 的 `register`
方法的对象：

```python
from movie_narrator import PluginContext, register_step, Context

class MyPlugin:
    name = "my-plugin"

    def register(self, ctx: PluginContext) -> None:
        ctx.steps.register(
            "my_step",
            my_step_func,
            after="render_video",
            soft=True,
        )

def my_step_func(ctx: Context) -> Context:
    # Your custom logic here
    return ctx
```

### 2. 通过入口点 (Entry Point) 打包

在你的 `pyproject.toml` 中：

```toml
[project.entry-points."movie_narrator.plugins"]
my-plugin = "my_package:MyPlugin"
```

### 3. 安装并发现

```bash
pip install my-plugin-package
```

```python
from movie_narrator import discover_plugins
discover_plugins()  # auto-loads all installed plugins
```

## SDK 表面 (SDK Surface)

公共 SDK 可从 `movie_narrator` 导入：

| 符号 (Symbol) | 用途 (Purpose) |
|--------|---------|
| `Context` | 流水线上下文模型（在步骤中读取/修改） |
| `Services` | 基础设施容器（console、logger） |
| `Plugin` | 你的插件类必须满足的协议 (Protocol) |
| `PluginContext` | 传递给 `register()`，提供注册表访问 |
| `load_plugin()` | 手动加载一个插件实例 |
| `discover_plugins()` | 通过 entry_points 自动发现 |
| `list_available_plugins()` | 列出可用插件但不加载 |
| `register_step` | 注册流水线步骤的装饰器 |
| `register_tts` | 注册 TTS 服务商工厂的装饰器 |
| `register_vision` | 注册 Vision 字幕器工厂的装饰器 |
| `register_llm` | 注册 LLM 服务商工厂的装饰器 |
| `register_research` | 注册 research 服务商工厂的装饰器 |
| `register_remote_llm` | 注册远程 LLM 代理服务商 (v0.6+) |
| `register_remote_tts` | 注册远程 TTS 代理服务商 (v0.6+) |
| `step_registry` | 全局步骤注册表 (StepRegistry) 实例 |
| `tts_registry` | 全局 TTS 服务商注册表实例 |
| `vision_registry` | 全局 Vision 服务商注册表实例 |
| `llm_registry` | 全局 LLM 服务商注册表实例 |
| `research_registry` | 全局 research 服务商注册表实例 |
| `ResearchInfo` | research 服务商返回的模型 |
| `check_version` | 导入时合约版本 (Contract Version) 校验 |

## 流水线步骤 (Pipeline Steps)

### 注册

```python
from movie_narrator import register_step, Context

@register_step("my_step", after="render_video", soft=True)
def my_step(ctx: Context) -> Context:
    ctx.metadata["my_step_ran"] = True
    return ctx
```

### 排序 (Ordering)

插件步骤必须声明插入点：

- `after="render_video"` — 插入到指定步骤之后
- `before="validate_deliverable"` — 插入到指定步骤之前

内置步骤（无 `after`/`before`）保持其固定顺序。没有插入点的插件
步骤会被追加到末尾。

### 软步骤与硬步骤 (Soft vs Hard Steps)

- **软步骤 (Soft Steps)**（`soft=True`）：异常被捕获并渲染为
  警告。流水线继续执行并输出降级结果。需要 `status_field` 名称
  和 `consequence` 消息。
- **硬步骤 (Hard Steps)**（`soft=False`，默认）：异常会中止流水线。

### 内置步骤名称

16 个内置步骤按执行顺序：

1. `resolve_video`
2. `prepare_assets`
3. `research_plot` (soft)
4. `generate_script`
5. `export_script_md`
6. `generate_voice`
7. `align_audio` (soft)
8. `detect_scenes` (soft)
9. `match_clips` (soft)
10. `mix_bgm` (soft)
11. `translate_subtitles` (soft)
12. `generate_subtitle`
13. `run_qa_gate` (soft)
14. `render_video`
15. `validate_deliverable`
16. `export_clips` (soft)

## TTS 服务商

```python
from movie_narrator import register_tts, Settings

@register_tts("elevenlabs")
def make_elevenlabs(settings: Settings) -> TTSProvider:
    from .elevenlabs import ElevenLabsProvider
    return ElevenLabsProvider(settings)
```

工厂接收项目 `Settings`，必须返回满足 `TTSProvider`
协议（带有 `synthesize` 方法）的对象。

## Vision 字幕器

```python
from movie_narrator import register_vision

@register_vision("blip")
def make_blip(**kwargs) -> VisionCaptioner:
    from .blip import BlipCaptioner
    return BlipCaptioner(**kwargs)
```

工厂接收关键字参数，必须返回满足 `VisionCaptioner`
协议（带有 `caption_frame` 和 `caption_scenes` 方法）的对象。

## LLM 服务商

```python
from contextlib import contextmanager
from movie_narrator import register_llm

@register_llm("anthropic")
def make_anthropic():
    @contextmanager
    def _cm():
        from .anthropic_client import AnthropicLLMClient
        yield AnthropicLLMClient(model="claude-3-opus", api_key=...)
    return _cm()
```

工厂不接受参数，必须返回一个**上下文管理器 (Context Manager)**，
产出 (yield) 一个兼容 `LLMClient` 的对象（带有 `.client` 和
`.model` 属性）。上下文管理器模式确保正确的资源清理。

通过 `.env` 或设置中的 `llm_provider` 选择服务商。

## Research 服务商

```python
from movie_narrator import Context, ResearchInfo, register_research

@register_research("web_search")
def make_web_search(ctx: Context, settings) -> ResearchInfo:
    # Fetch from your data source (API, database, web scraper, etc.)
    return ResearchInfo(
        title=ctx.movie_name,
        year=2024,
        summary="A custom summary from my provider.",
        genres=["Action", "Drama"],
        cast=["Actor 1", "Actor 2"],
        keywords=["keyword1", "keyword2"],
    )
```

工厂接收 `(ctx, settings)`，必须返回一个 `ResearchInfo`
实例。流水线的 `research_plot` 步骤会调用此工厂，并将结果写入
`research.json`。

在你的任务配置 (job config) 中选择服务商：

```yaml
params:
  research_provider: web_search
```

完整参考实现（使用 Wikipedia REST API）参见
`examples/plugins/research-wiki/`。

## 服务 (Services)

`Services` 容器为步骤提供基础设施：

```python
def my_step(ctx: Context) -> Context:
    # Console output (always available)
    ctx.services.console.info("Processing...")

    # Logger (optional, may be None)
    if ctx.services.logger:
        ctx.services.logger.info("my_step started")

    return ctx
```

`Services` 字段：

| 字段 (Field) | 类型 (Type) | 默认值 (Default) | 说明 (Description) |
|-------|------|---------|-------------|
| `console` | `Console` | 必填 (required) | 控制台输出抽象 |
| `logger` | `Optional[Any]` | `None` | 鸭子类型日志器（`.info/.warning/.error`） |

## 入口点 (Entry Points)

插件通过 `movie_narrator.plugins` 入口点组被发现。
在 `pyproject.toml` 中声明：

```toml
[project.entry-points."movie_narrator.plugins"]
my-plugin = "my_package:MyPlugin"
```

入口点值可以是：

- 类路径 (`my_package:MyPlugin`) — 无参数实例化
- 模块路径 (`my_package`) — 必须有顶层 `plugin` 或 `Plugin` 属性

## 兼容性策略 (Compatibility Strategy)

### 稳定的内容 (v0.6.1)

- `Plugin` 协议 (`name` + `register(ctx)`)
- `PluginContext` 接口 (`steps`, `tts`, `vision`, `llm`, `research`)
- `register_step` 装饰器签名
- `register_tts` / `register_vision` / `register_llm` / `register_research` 装饰器签名
- 用于远程推理代理的 `register_remote_llm` / `register_remote_tts` (v0.6+)
- 内置步骤名称和执行顺序
- `discover_plugins()` / `load_plugin()` 函数签名
- `Services` 字段名 (`console`, `logger`)
- `ResearchInfo` 模型字段 (`title`, `year`, `summary`, `genres`, `cast`, `keywords`)
- 用于导入时兼容性校验的 `check_version()` 函数

### 未来版本可能变化的内容

- 可能添加新的 `Services` 字段（总是可选的，永不破坏现有代码）
- 可能添加新的注册表类别（例如 `subtitles_registry`）
- 内置步骤列表可能增长（插入新步骤，保留现有顺序）
- `PluginContext` 可能增加新字段（增量添加，不破坏）

### 不会变化的内容

- 现有内置步骤名称不会被重命名
- `Context -> Context` 步骤函数签名不会变化
- `register(name, func, *, soft, ...)` 签名不会变化
