[![English](https://img.shields.io/badge/English-Quickstart-blue)](QUICKSTART.md)
[![简体中文](https://img.shields.io/badge/简体中文-快速开始-green)](QUICKSTART.zh-CN.md)

# 快速开始：插件开发

本指南带你用不到 10 分钟从零开始创建、打包并分发一个
movie-narrator 插件。

## 前置条件 (Prerequisites)

```bash
pip install movie-narrator
```

验证安装：

```bash
mn version
# movie-narrator 1.0.0 (contract 1.0.0)
```

## 步骤 1：创建插件包

为你的插件创建一个新目录：

```
my-plugin/
├── pyproject.toml
└── my_plugin/
    └── __init__.py
```

### `pyproject.toml`

```toml
[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[project]
name = "my-plugin"
version = "0.1.0"
description = "My custom movie-narrator plugin."
requires-python = ">=3.10"
dependencies = ["movie-narrator>=0.6.0"]

[tool.setuptools.packages.find]
where = ["."]
```

`[project.entry-points]` 部分为自动发现注册你的插件。权威的 TOML 片段 — 包括 `[project.entry-points."movie_narrator.plugins"]` 块 — 定义在[插件开发指南](PLUGIN_DEVELOPMENT.zh-CN.md#entry-points)，此处链接过去而非重复。

### `my_plugin/__init__.py`

```python
from movie_narrator import Context, PluginContext, register_step


class MyPlugin:
    name = "my-plugin"

    def register(self, ctx: PluginContext) -> None:
        ctx.steps.register(
            "my_step",
            _my_step,
            soft=True,
            status_field="my_step",
            consequence="my step skipped — output unaffected",
            after="render_video",
        )


def _my_step(ctx: Context) -> Context:
    """Add a custom processing step after video rendering."""
    console = ctx.services.console
    console.info(f"MyPlugin: processing {ctx.video_path}")

    # Your custom logic here
    ctx.metadata["my_step_ran"] = True

    ctx.step_state.result = ctx.step_state.result.__class__("success")
    ctx.step_state.message = "my step completed"
    return ctx
```

## 步骤 2：本地安装并测试

```bash
cd my-plugin
pip install -e .
```

验证插件已被发现：

```bash
mn plugin list
# my-plugin (entry_point: my_plugin:MyPlugin)
```

加载并验证注册：

```bash
mn plugin discover
# Discovered: my-plugin
# Registered steps: my_step (after render_video, soft)
```

## 步骤 3：在插件激活的情况下运行

当调用 `discover_plugins()` 时插件会被自动加载。在
CLI 中，这会自动发生：

```bash
mn create --movie "Inception" --video movie.mp4 --output-dir output/
```

你的步骤会在 `render_video` 之后执行并记录其消息。

## 步骤 4：选择你的扩展类型

插件 SDK 支持五种扩展点 (Extension Point)。选择与你的目标
匹配的那一种：

| 目标 (Goal) | 装饰器 (Decorator) | 工厂签名 (Factory signature) | 示例 (Example) |
|------|-----------|-------------------|---------|
| 添加流水线步骤 | `register_step` | `(ctx: Context) -> Context` | `examples/plugins/watermark/` |
| 添加 TTS 服务商 | `register_tts` | `(settings) -> TTSProvider` | 内置 `edge`、`openai`、`mimo` |
| 添加 LLM 服务商 | `register_llm` | `() -> ContextManager[LLMClient]` | 内置 `openai` |
| 添加 research 服务商 | `register_research` | `(ctx, settings) -> ResearchInfo` | `examples/plugins/research-wiki/` |
| 添加 Vision 字幕器 | `register_vision` | `(**kwargs) -> VisionCaptioner` | 内置 `stub` |

### Research 服务商示例

```python
from movie_narrator import Context, ResearchInfo, register_research

@register_research("my_research")
def _research(ctx: Context, settings) -> ResearchInfo:
    # Fetch data from your source (API, database, etc.)
    return ResearchInfo(
        title=ctx.movie_name,
        summary="A custom summary from my provider.",
        genres=["Action"],
        cast=[],
        keywords=["custom"],
    )
```

在你的任务配置中选择它：

```yaml
params:
  research_provider: my_research
```

### TTS 服务商示例

```python
from movie_narrator import register_tts

@register_tts("my_tts")
def _make_tts(settings) -> "TTSProvider":
    from my_tts_impl import MyTTSProvider
    return MyTTSProvider(settings)
```

通过环境变量选择它：

```bash
MN_TTS_PROVIDER=my_tts mn create ...
```

## 步骤 5：打包并分发

### 构建 wheel

```bash
pip install build
python -m build
```

这会生成 `dist/my_plugin-0.1.0-py3-none-any.whl`。

### 发布到 PyPI

```bash
pip install twine
twine upload dist/*
```

### 用户安装并使用

```bash
pip install my-plugin
mn plugin list          # confirms discovery
mn create --movie ...   # plugin auto-loads
```

## 调试 (Debugging)

### 插件未被发现？

检查：
1. 入口点组恰好是 `movie_narrator.plugins`
2. 入口点值匹配 `package:ClassName`
3. 包已安装（`pip show my-plugin`）
4. 运行 `mn plugin list` 查看所有发现的入口点

### 步骤未执行？

检查：
1. `mn plugin registries` 显示你的步骤在注册表中
2. `after`/`before` 插入点匹配某个内置步骤名称
3. 步骤未在 `job.yaml` 的 `steps:` 下被禁用
4. 对于软步骤，如果静默失败，检查 metadata 中的 `_degraded_steps`

### 协议校验错误？

TTS 和 Vision 服务商必须返回满足
协议 ABC (`TTSProvider` / `VisionCaptioner`) 的实例。如果你看到
来自 `create()` 的 `TypeError`，请确保你的服务商类继承或
实现了所有必需方法。

## 参考示例

| 插件 (Plugin) | 扩展类型 (Extension type) | 位置 (Location) |
|--------|---------------|----------|
| Watermark | 流水线步骤 (Pipeline step) | `examples/plugins/watermark/` |
| Template | 流水线步骤骨架 (Pipeline step skeleton) | `examples/plugins/template/` |
| Research Wiki | Research 服务商 (Research provider) | `examples/plugins/research-wiki/` |

## 后续步骤

- 阅读 [插件开发指南](PLUGIN_DEVELOPMENT.zh-CN.md) 获取
  完整 API 参考
- 查看 [架构文档](ARCHITECTURE.zh-CN.md) 了解流水线流程
- 阅读 [PACKAGING.md](PACKAGING.zh-CN.md) 了解发布最佳实践
