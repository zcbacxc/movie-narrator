[![English](https://img.shields.io/badge/English-Tutorial-blue)](TUTORIAL.md)
[![简体中文](https://img.shields.io/badge/简体中文-教程-green)](TUTORIAL.zh-CN.md)

# 教程

这是 **movie-narrator** 从零到进阶的完整上手指南。movie-narrator 是一个 Python 引擎，可将单个电影片名转化为一段带旁白解说的回顾视频。本教程面向**内容创作者**——你无需具备开发经验即可跟随学习。如果你是插件作者，请阅读 [QUICKSTART.md](QUICKSTART.md)。

> **兼容性说明。** 本文档描述的是 **1.0.0** 版本的治理（governance）机制。引擎本身为 **1.0.0** 版本，`CONTRACT_VERSION=(1,0,0)`。运行 `mn version` 可查看你当前安装的构建版本。

---

## 目录

- [前置条件](#前置条件)
- [教程 1 —— 快速创建你的第一个视频](#教程-1--快速创建你的第一个视频)
- [教程 2 —— 配置 `job.yaml`](#教程-2--配置-jobyaml)
- [教程 3 —— Presets 与样式](#教程-3--presets-与样式)
- [教程 4 —— 多语言与配音](#教程-4--多语言与配音)
- [教程 5 —— 高级 pipeline 控制](#教程-5--高级-pipeline-控制)
- [教程 6 —— 异步任务与远程服务](#教程-6--异步任务与远程服务)
- [教程 7 —— 批量与可靠性](#教程-7--批量与可靠性)
- [教程 8 —— 插件](#教程-8--插件)
- [下一步](#下一步)

---

## 前置条件

开始之前，请确保你已具备以下条件：

1. 已安装 **Python 3.10+**。
2. 已安装 **movie-narrator**，且 `mn` CLI 可在 `PATH` 中访问。
3. 已在 `.env` 文件中配置 **LLM + TTS 凭据**（见下文）。这些是驱动脚本生成与旁白配音的基础设施。

验证 CLI 是否已安装：

```bash
mn --help
```

检查版本与契约版本：

```bash
mn version
```

### 环境变量（`.env`）

`.env` 文件存放 **LLM + TTS 基础设施** 配置。它与 `job.yaml` 相互独立，后者控制 pipeline 行为。至少你需要配置一个 LLM provider。

```bash
# 复制示例文件并填写你的密钥
cp .env.example .env
```

参见 `docs/llm-providers/` 中的 provider 文档（例如 `alibaba-bailian.md`、`zhipu.md`、`ollama.md`），选择你使用的 provider。

---

## 教程 1 —— 快速创建你的第一个视频

得到一段成片回顾视频的最快路径，是单条 `mn create` 命令。唯一必需参数是电影名称。

```bash
mn create -m "The Matrix"
```

就这么简单。`mn` 将：

1. 解析电影（查找其元数据）。
2. 调研剧情。
3. 生成旁白脚本。
4. 生成配音。
5. 检测场景、匹配片段、渲染视频，并导出交付文件。

新建视频默认采用 `热血搞笑`（hot & funny）样式，时长 **60 秒**，以 **16:9** 横屏渲染。

### 首次运行的关键参数

```bash
# 修改时长（秒）
mn create -m "Inception" -d 120

# 选择不同的样式
mn create -m "Spirited Away" -s "mainstream-dry"

# 选择竖屏 9:16 格式（非常适合短视频）
mn create -m "Dune" -f 9:16

# 选择配音
mn create -m "Titanic" -v "zh-CN-YunxiNeural"
```

### 输出位置

默认情况下，交付文件写入工作目录下的 `output/`。你可以随时更改目标位置：

```bash
mn create -m "Parasite" -o "./my-videos"
```

### 保留缓存以加速重跑

完整重跑会重新计算所有内容。如果你希望快速迭代，请保留中间缓存：

```bash
mn create -m "Coco" --keep-cache
```

---

## 教程 2 —— 配置 `job.yaml`

`job.yaml` 是用于控制单次运行 **pipeline 行为** 的唯一文件。借助它，你可以实现自己的“家族风格”，而无需每次都重复输入参数。

### 配置优先级

```
CLI arguments  >  job.yaml  >  inline defaults
```

任何你在命令行传入的内容都优先于 `job.yaml`；`job.yaml` 优先于内置默认值。这使得你可以轻松地为单次运行覆盖某个选项，同时保留其余配置。

### 一个极简的 `job.yaml`

```yaml
movie: "Interstellar"
duration: 60
style: "mainstream-dry"
video_format: "16:9"
subtitle_lang: "zh"
subtitle_mode: "burned"
```

### 让 `mn` 指向你的配置

```bash
mn create --config ./job.yaml
```

或者在单次运行时从 CLI 覆盖单个字段：

```bash
mn create --config ./job.yaml -d 90
```

> `CLI` 优先：`-d 90` 仅对本次运行覆盖文件中的 `duration: 60`。

### `job.yaml` 不能做什么

`job.yaml` 关注的是 pipeline 行为。它**不**存放 API 密钥或秘密基础设施设置——这些应属于 `.env`。请将两者关注点分开。

---

## 教程 3 —— Presets 与样式

Presets 将一整套样式、配音、节奏与格式选择打包在一起，使你可以用一个词应用一致的外观。

### 内置 presets

| Preset | 描述 |
| --- | --- |
| `douyin-fast` | 快节奏、短式，为短视频平台优化 |
| `mainstream-dry` | 克制、主流、克制的旁白 |
| `bilibili-long` | 长篇幅、剧情驱动，适合深度解说 |

### 列出 presets

```bash
mn preset
```

### 查看单个 preset

```bash
mn preset douyin-fast
```

### 应用 preset

```bash
# 应用一个旁白 preset
mn create -m "Knives Out" -p douyin-fast
```

### 样式与旁白 preset 的区别

- `-s/--style` 控制整体旁白样式（例如 `热血搞笑`、`历史悬疑`、`温情催泪`）。
- `-p/--narration-preset` 应用打包好的旁白 preset（`douyin-fast`、`mainstream-dry`、`bilibili-long`）。

它们作用于 pipeline 的不同维度，可组合使用。

### 细粒度旁白控制

在 presets 之外，你还可以直接调优旁白：

```bash
mn create -m "The Godfather" \
  --narrator-perspective "first-person" \
  --focus-character "Michael Corleone"
```

---

## 教程 4 —— 多语言与配音

`movie-narrator` 开箱即用地支持**中文**和**英文**旁白。

### 语言基础

默认语言为 `zh`（中文）。要切换到英文，请在 `job.yaml` 中设置 `lang` 参数：

```yaml
# job.yaml
params:
  lang: en
```

```bash
mn create -m "The Dark Knight" --config ./job.yaml
```

### 字幕模式

字幕可以是原始、翻译或双语：

```bash
# 在视频中显示原始字幕
mn create -m "Lalaland" --subtitle-lang en --subtitle-mode original

# 在视频中显示翻译字幕
mn create -m "Lalaland" --subtitle-lang en --subtitle-mode translated

# 同时显示原始与翻译字幕（并排）
mn create -m "Lalaland" --subtitle-lang en --subtitle-mode bilingual
```

`--subtitle-lang` 启用翻译（空 = 关闭）。`--subtitle-mode` 接受 `original`、`translated` 或 `bilingual` 之一。

### 配音选择

直接选择配音：

```bash
mn create -m "Frozen" -v "zh-CN-YunxiNeural"
```

### 通过环境变量进行配音映射

为获得一致的跨语言配音行为，请在 `.env` 中设置映射：

```bash
# 未设置其他内容时的默认配音
MN_DEFAULT_VOICE="zh-CN-YunxiNeural"

# 覆盖中文 TTS 配音
MN_VOICE_ZH="zh-CN-YunxiNeural"

# 覆盖英文 TTS 配音
MN_VOICE_EN="en-US-AriaNeural"
```

`MN_VOICE_ZH` / `MN_VOICE_EN` 变量会覆盖各语言的配音。当未配置任何特定语言配音时，`MN_DEFAULT_VOICE` 作为回退项。

---

## 教程 5 —— 高级 pipeline 控制

在底层，一次运行会执行一个 **16 步 pipeline**。大多数步骤是 *soft*（软）的——如果它们不可用（例如缺少可选依赖），会被优雅地跳过，而不是使运行失败。

### 16 个 pipeline 步骤

```
resolve_video        -> prepared
prepare_assets
research_plot        (soft)
generate_script
export_script_md
generate_voice
align_audio          (soft)
detect_scenes        (soft)
match_clips          (soft)
mix_bgm              (soft)
translate_subtitles  (soft)
generate_subtitle
run_qa_gate          (soft)
render_video
validate_deliverable
export_clips         (soft)
```

### 将 soft 步骤设为严格

默认情况下，soft 步骤会优雅地失败。如果你需要硬性保证每个步骤都完成，请以严格模式运行：

```bash
mn create -m "Blade Runner" --strict
```

这会把这些 soft 步骤变成硬性失败——如果其中任何一个无法运行，整个 pipeline 会以错误停止。

### 暂停与恢复

你可以在特定步骤暂停 pipeline，并从检查点稍后恢复。这对于运行时间长或不可靠的运行很有用。

```bash
# 在生成脚本后暂停
mn create -m "Her" --pause-at generate_script
```

从已保存状态恢复：

```bash
mn resume --state ./pipeline_state.json
```

### 检查中间结果

使用专用子命令逐步调试 pipeline：

```bash
# 解析电影而不运行所有内容
mn resolve -m "Joker"

# 运行调研
mn research -m "Joker"

# 检测场景（带阈值，默认 27.0）
mn scenes --video /path/to/joker.mp4 --threshold 27.0

# 使用脚本对齐音频
mn align --audio /path/to/voice.mp3 --script script.md

# 从 scenes.json 导出片段
mn clips --video /path/to/joker.mp4 --scenes ./output/scenes.json
```

这些非常适合在投入到完整渲染之前，排查特定阶段的问题。

---

## 教程 6 —— 异步任务与远程服务

对于长时间渲染，或当你希望将引擎作为服务运行时，请使用异步任务模型。

### 提交任务（异步）

`mn submit` 会立即返回一个任务 ID，而不是阻塞：

```bash
mn submit -m "The Shawshank Redemption" -p douyin-fast --lang zh
```

### 管理任务队列

```bash
# 检查单个任务的状态
mn status <job-id>

# 列出所有任务（可选按状态过滤）
mn tasks
mn tasks --status running
mn tasks -n 20

# 取消任务
mn cancel <job-id>

# 等待任务完成
mn wait <job-id>
```

### 控制重试

```bash
# 提交并将重试次数上限定为 5
mn submit -m "Whiplash" -p douyin-fast --max-retries 5

# 内联等待完成
mn submit -m "Whiplash" -p douyin-fast --wait
```

### 运行远程服务

启动服务器：

```bash
# 默认值：127.0.0.1:8765
mn serve

# 绑定到特定端口
mn serve --port 9000

# 对外公开（请谨慎使用）
mn serve --public

# 要求 API 密钥
mn serve --api-key "your-secret-key"
```

### 使用服务 API

```bash
# 下载已完成的产物
mn download <job-id>

# 打印服务的 OpenAPI 规范
mn api-spec
```

---

## 教程 7 —— 批量与可靠性

当你需要大量视频，或需要信任 pipeline 能够无人值守地运行，请使用可靠性功能。

### 批量提交

一次提交多个任务。引擎通过 `mn submit` 或 HTTP API 接受 **1 到 50** 个任务的批量。

```bash
# 提交多个任务
mn submit -m "Movie A" -p douyin-fast
mn submit -m "Movie B" -p douyin-fast
mn submit -m "Movie C" -p douyin-fast
```

### Cron 调度

引擎支持调度周期性任务（v0.9.3）。配置一个 cron 表达式，让视频按计划生成——例如每天 08:00。

```yaml
# 在你的任务配置 / 调度器中
schedule: "0 8 * * *"
```

### 熔断器 + 重试

网络与 provider 调用受到 **带重试的熔断器（circuit breaker）** 保护（v0.9.1）。瞬时故障会被自动重试；如果某个 provider 持续失败，熔断器会断开，使 pipeline 快速失败而不是挂起。

### 检查点 + 恢复

检查点（v0.9.2）让你可以从最后一个成功步骤恢复。结合 `--pause-at` 与 `mn resume`，可确保长时间运行的安全。

### 死信队列（DLQ）

失败的任务会被路由到死信队列（v0.9.4），以便你稍后检查并重试它们，而不会丢失负载内容。DLQ 通过 HTTP API 管理（`GET/DELETE/REPLAY /deadletters`）。

### 清理已完成的任务

```bash
# 从本地队列中移除已完成的任务
mn cleanup
```

### 分布式渲染

当存在多个节点可用时，渲染可以分发到各 worker 上。分布式渲染是**条件性**激活的——仅当环境与队列支持时才会启用。

### 交互式重试

某些交互式失败会提供一个 `R/S/A` 提示——**R**etry（重试）、**S**kip（跳过）、**A**bort（中止）：

```bash
mn create -m "The Prestige" --retry
```

---

## 教程 8 —— 插件

插件用于扩展引擎。你可以从 CLI 发现、安装和管理它们。

```bash
# 列出已安装的插件
mn plugin list

# 从注册中心发现插件
mn plugin discover

# 管理注册中心
mn plugin registries

# 显示插件版本信息
mn plugin version
```

如需深入编写你自己的插件，请阅读 [QUICKSTART.md](QUICKSTART.md)。

### 产物（Artifacts）

管理生成的产物：

```bash
# 列出产物
mn artifacts list

# 清理旧的产物
mn artifacts cleanup
```

---

## 下一步

- 阅读 [QUICKSTART.md](QUICKSTART.md)，了解如何构建插件。
- 探索 `docs/llm-providers/` 中的 provider 文档，调优你的 LLM 配置。
- 查看 [BEST_PRACTICES.md](BEST_PRACTICES.md)，获取生产环境建议。
- 当你希望规模化地将引擎作为服务运行时，参考 [DEPLOYMENT.md](DEPLOYMENT.md)。
- 查看 [ROADMAP.md](ROADMAP.md)，了解接下来会提供什么。

现在，去创造一些很棒的内容吧。运行你的第一条命令：

```bash
mn create -m "Your Favorite Movie"
```