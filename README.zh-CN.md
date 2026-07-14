[![English](https://img.shields.io/badge/English-README-blue)](README.md)
[![简体中文](https://img.shields.io/badge/简体中文-README-green)](README.zh-CN.md)

# 🎬 Movie Narrator

![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License](https://img.shields.io/github/license/zcbacxc/movie-narrator)
![CI](https://github.com/zcbacxc/movie-narrator/actions/workflows/ci.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/movie-narrator)
![Downloads](https://img.shields.io/pypi/dm/movie-narrator)

> 一个提示 → 一个带解说的电影视频

Movie Narrator 是一个开源工具包，可通过简单命令自动生成带有解说、字幕和渲染输出的电影解说视频。

---

## 功能特性

- 🎬 使用 LLM 生成电影解说脚本
- 🔊 文字转语音解说（默认使用 Edge-TTS）
- 💬 自动生成 SRT 字幕文件
- 🌐 多语言字幕（`--subtitle-lang en` 通过 LLM 翻译解说文案，输出 `subtitle.<lang>.srt` + `subtitle.bilingual.srt`）
- 🖥️ Web UI（`mn web` — 本地 Gradio 浏览器应用，支持表单输入、协作式取消、产物下载）
- 🎞️ 使用 MoviePy 和 FFmpeg 渲染视频
- 📝 脚本 Markdown 导出（`script.md`）
- 🎵 背景音乐集成（BGM 混音）
- 🎬 场景级片段导出
- 📦 元数据导出
- 🔌 可扩展的流水线架构
- 🐍 纯 Python 实现

---

## 安装

### 环境要求

- Python 3.10+
- FFmpeg

### 安装 FFmpeg

#### macOS

```bash
brew install ffmpeg
```

#### Ubuntu / Debian

```bash
sudo apt install ffmpeg
```

#### Windows

```bash
# 方式一：winget
winget install Gyan.FFmpeg

# 方式二：chocolatey
choco install ffmpeg

# 方式三：从官网下载 https://ffmpeg.org/
```

验证安装：

```bash
ffmpeg -version
```

---

## 安装 Movie Narrator

### 从 PyPI 安装

```bash
pip install movie-narrator
```

### 从源码安装

```bash
git clone https://github.com/zcbacxc/movie-narrator.git
cd movie-narrator
pip install -e .
```

#### 可选扩展

```bash
# 场景检测（PySceneDetect）
pip install "movie-narrator[media]"

# WhisperX + 语义搜索（需要 PyTorch，Python < 3.14）
pip install "movie-narrator[ml]"

# Web UI（Gradio）
pip install "movie-narrator[web]"

# 全部
pip install "movie-narrator[full]"
```

开发模式安装：

```bash
pip install -e ".[dev]"
```

---

## 快速开始

### 前置条件

- **LLM**: 默认使用本地 Ollama（先运行 `ollama serve`）。也可通过 `.env` 文件配置远程 LLM。
- **FFmpeg**: 视频渲染必需。

### 基本用法

```bash
# 生成带解说的电影视频
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60

# 自定义音色和视频比例
mn create --movie "飞驰人生" --voice "zh-CN-XiaoxiaoNeural" --format "9:16"

# 保留 TTS 缓存用于调试
mn create --movie "飞驰人生" --keep-cache
```

### CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--movie, -m` | 电影名称（必填） | - |
| `--style, -s` | 解说风格 | `热血搞笑` |
| `--duration, -d` | 目标时长（秒） | `60` |
| `--voice, -v` | Edge-TTS 音色 | `zh-CN-YunxiNeural` |
| `--format, -f` | 视频比例（`16:9` 或 `9:16`） | `16:9` |
| `--video, -V` | 源电影文件路径 | - |
| `--library-dir` | 电影库目录 | - |
| `--research` | 启用 LLM 剧情调研 | `false` |
| `--no-research` | 禁用剧情调研 | - |
| `--bgm` | 背景音乐文件路径 | - |
| `--no-bgm` | 禁用 BGM | `false` |
| `--no-clips` | 跳过场景片段导出 | `false` |
| `--strict` | 软步骤失败时中止 | `false` |
| `--keep-cache` | 保留 TTS 缓存文件 | `false` |
| `--subtitle-lang` | 目标语言标签（`en`、`ja`、`zh-TW`...），留空 = 关闭翻译 | - |
| `--subtitle-mode` | 渲染叠加模式：`original` / `translated` / `bilingual` | `original` |
| `--config` | Job YAML 配置文件路径（movie/steps/params）；CLI 参数覆盖 YAML | - |

### Job YAML 配置（v0.3）

```bash
# 通过 YAML 文件驱动任务（movie 可只写在 YAML 中）
mn create --config examples/job.example.yaml

# CLI 参数优先级高于 YAML
mn create --config examples/job.example.yaml --movie "其他电影" --no-clips
```

详细白名单请参考 [`examples/job.example.yaml`](examples/job.example.yaml)：软步骤开关（`steps:` 下的 `research` / `align` / `scene` / `match` / `bgm` / `export` / `translate`）、参数（`params:` 下的 `scene_threshold` / `match_min_score` / `research_provider` / `translate_provider` / `translate_retries` / `translate_chunk_chars` / `translate_chunk_size`），以及多语言字幕顶层键 `subtitle_lang` / `subtitle_mode`。相对路径 `video` / `bgm` / `library_dir` 相对于 YAML 所在目录解析。LLM 凭据请保留在 `.env` / `MN_*` 环境变量中。

### 多语言字幕

```bash
# 将解说文案翻译为英文并叠加到视频画面
mn create --movie "Inception" --subtitle-lang en --subtitle-mode bilingual

# 或仅生成翻译版 SRT 文件（不改变画面字幕）
mn create --movie "Inception" --subtitle-lang en
```

设置 `--subtitle-lang` 后，`generate_subtitle` 始终会输出三个 SRT 文件：

- `subtitle.srt` —— 原版解说（始终存在，`subtitle_path` 不变）
- `subtitle.<lang>.srt` —— 翻译版（如 `subtitle.en.srt`）
- `subtitle.bilingual.srt` —— 双语版（cue 主体 `f"{原文}\n{译文}"`，LF 分隔）

`--subtitle-mode` 决定 `render_video` 读哪个文件：

| 模式 | 叠加文本源 |
|------|-----------|
| `original`（默认） | `subtitle.srt` |
| `translated` | `subtitle.<lang>.srt`（缺失时降级到 `subtitle.srt` 并告警） |
| `bilingual` | `subtitle.bilingual.srt`（缺失时同样降级） |

设置 `subtitle_mode=translated|bilingual` 但未指定 `subtitle_lang` 时，会在 merge 阶段抛 `JobConfigError`。失败策略：LLM 重试 `MN_TRANSLATE_RETRIES` 次后软降级 —— 用原文填到翻译轨并把警告写入 `metadata.warnings`。

### Web UI（v0.3.5）

```bash
# 安装 web 扩展
pip install "movie-narrator[web]"

# 启动本地浏览器应用
mn web

# 自定义主机和端口
mn web --host 0.0.0.0 --port 8080

# 创建临时公开链接（Gradio share）
mn web --share
```

Web UI 提供表单界面，覆盖所有 CLI 参数：电影名、风格、时长、音色、比例、视频/BGM 上传、字幕设置、高级参数。Cancel 按钮支持在步骤边界协作式取消。所有终态（成功/失败/取消）均可下载已生成的产物（视频、字幕、脚本、元数据）。

**空值 = 不覆盖**：高级表单字段留空时不会覆盖 Settings（`.env` / `MN_*`）默认值，只有显式填写时才生效。

### 离线演示（无需 LLM）

```bash
# CI=1 使用静音回退音频，跳过 LLM 和 Edge-TTS
CI=1 mn create --movie "Demo" --duration 10
```

### 其他命令

```bash
mn version   # 查看版本
mn --help    # 查看帮助
```

---

## 配置

所有配置项使用 `MN_` 前缀，避免与其他工具冲突。

### 通过 `.env` 文件（推荐）

在项目目录创建 `.env`（或 `~/.movie-narrator/.env` 作为全局配置——该文件在包目录之外，`pip install/upgrade/uninstall` 均不会触碰）：

```bash
MN_LLM_BASE_URL=http://localhost:11434/v1
MN_LLM_API_KEY=ollama
MN_LLM_MODEL=qwen2.5:7b
MN_DEFAULT_VOICE=zh-CN-YunxiNeural
MN_DEFAULT_FORMAT=16:9
```

### 通过环境变量

```powershell
# PowerShell
$env:MN_LLM_BASE_URL="http://localhost:11434/v1"
$env:MN_LLM_MODEL="qwen2.5:7b"
mn create --movie "飞驰人生" --duration 60
```

```bash
# Linux / macOS
export MN_LLM_BASE_URL=http://localhost:11434/v1
export MN_LLM_MODEL=qwen2.5:7b
mn create --movie "飞驰人生" --duration 60
```

### 配置查找顺序

| 优先级 | 位置 | 说明 |
|--------|------|------|
| 1 | 环境变量（`MN_*`） | 最高优先 |
| 2 | `当前目录/.env` | 项目级 |
| 3 | `~/.movie-narrator/.env` | 用户级，pip install/upgrade/uninstall 均不会丢失 |
| 4 | 内置默认值 | 本地 Ollama |

### 完整配置项

| 变量 | 说明 | 默认值 |
|------|------|--------|
| `MN_LLM_BASE_URL` | LLM API 地址 | `http://localhost:11434/v1` |
| `MN_LLM_API_KEY` | LLM API 密钥 | `ollama` |
| `MN_LLM_MODEL` | LLM 模型名称 | `qwen2.5:7b` |
| `MN_DEFAULT_VOICE` | 当前 TTS Provider 的默认音色（由 Provider 解释） | `zh-CN-YunxiNeural` |
| `MN_DEFAULT_FORMAT` | 视频比例 | `16:9` |
| `MN_LIBRARY_DIR` | 电影库路径 | - |
| `MN_DEFAULT_BGM` | 默认背景音乐 | - |
| `MN_RESEARCH_ENABLED` | 自动启用调研 | `false` |
| `MN_RESEARCH_PROVIDER` | 调研后端 | `llm` |
| `MN_SCENE_THRESHOLD` | PySceneDetect 阈值 | `27.0` |
| `MN_SCENE_FRAME_SKIP` | 场景检测跳帧数 | `10` |
| `MN_MATCH_MIN_SCORE` | 最低匹配分数 | `0.25` |
| `MN_EXPORT_CLIPS_DEFAULT` | 自动导出片段 | `true` |
| `MN_SUBTITLE_LANG` | 默认目标语言标签；留空 = 关闭翻译 | - |
| `MN_SUBTITLE_MODE` | 默认叠加模式（`original` / `translated` / `bilingual`） | `original` |
| `MN_TRANSLATE_PROVIDER` | 翻译后端（v0.3 仅支持 `llm`） | `llm` |
| `MN_TRANSLATE_RETRIES` | LLM 翻译失败重试次数，超出后软降级 | `3` |
| `MN_TTS_PROVIDER` | TTS 后端：`edge`（默认）、`openai` 或 `mimo` | `edge` |
| `MN_OPENAI_TTS_MODEL` | OpenAI TTS 模型（`MN_TTS_PROVIDER=openai` 时生效） | `tts-1` |
| `MN_OPENAI_TTS_API_KEY` | OpenAI TTS API 密钥（回退到 `MN_LLM_API_KEY`） | - |
| `MN_OPENAI_TTS_BASE_URL` | OpenAI TTS 基地址（回退到 `MN_LLM_BASE_URL`） | - |
| `MN_MIMO_TTS_MODEL` | MiMo TTS 模型（`mimo-v2.5-tts`、`mimo-v2.5-tts-voiceclone`、`mimo-v2.5-tts-voicedesign`） | `mimo-v2.5-tts` |
| `MN_MIMO_API_KEY` | MiMo API 密钥（回退到 `MN_LLM_API_KEY`） | - |
| `MN_MIMO_BASE_URL` | MiMo 基地址 | `https://api.xiaomimimo.com/v1` |
| `MN_MIMO_STYLE_PROMPT` | `mimo-v2.5-tts` 风格描述（可选） | - |

---

## 输出结构

```text
output/
└── 飞驰人生/
    ├── narration.mp3       # TTS 解说音频
    ├── mixed.mp3            # 解说 + BGM 混音（启用 BGM 时）
    ├── subtitle.srt
    ├── subtitle.<lang>.srt    # 设置 --subtitle-lang 时输出（如 subtitle.en.srt）
    ├── subtitle.bilingual.srt # 设置 --subtitle-lang 时输出（原文 + LF + 译文，每行 cue）
    ├── script.md
    ├── script.json
    ├── research.json        # （使用 --research 时）
    ├── scenes.json          # （提供视频时）
    ├── matches.json         # （提供视频时）
    ├── metadata.json
    ├── final.mp4
    └── clips/               # （未设置 --no-clips 时）
```

| 文件 | 说明 |
|------|------|
| `narration.mp3` | AI 生成的解说音频 |
| `mixed.mp3` | 解说 + BGM 混音（启用 BGM 时；否则直接使用 `narration.mp3`） |
| `subtitle.srt` | 同步字幕文件（原版解说） |
| `subtitle.<lang>.srt` | 翻译字幕（设置 `--subtitle-lang` 时输出） |
| `subtitle.bilingual.srt` | 双语字幕（设置 `--subtitle-lang` 时输出；cue 主体 `f"{原文}\n{译文}"`） |
| `script.md` | 人类可读的脚本 |
| `script.json` | 机器可读的脚本分段 |
| `research.json` | 电影调研数据（使用 `--research` 时） |
| `scenes.json` | 检测到的场景边界（提供视频时） |
| `metadata.json` | 片段时间戳、流水线状态、配置 |
| `final.mp4` | 渲染的视频（16:9 或 9:16） |
| `matches.json` | 场景-片段匹配结果（提供视频时） |
| `clips/` | 逐片段剪辑 .mp4 文件（未设置 `--no-clips` 时） |

---

## 流水线

14 步顺序流水线（详见[架构设计](docs/ARCHITECTURE.md)）：

```text
resolve_video → prepare_assets → research_plot → generate_script →
export_script_md → generate_voice → align_audio → detect_scenes →
match_clips → mix_bgm → translate_subtitles → generate_subtitle →
render_video → export_clips
```

**软步骤**（research、align、scene detect、scene match、BGM、translate、clip export）在缺少可选依赖或上游数据缺失时**优雅跳过**或**软降级**。使用 `--strict` 改为直接中断。

---

## 项目结构

```text
movie-narrator/
├── src/movie_narrator/
│   ├── __init__.py          # 包元数据（__version__）
│   ├── cli.py               # Typer CLI 入口
│   ├── config.py            # Pydantic 配置
│   ├── models.py            # 数据模型（Context、Status 等）
│   ├── pipeline/
│   │   ├── runner.py        # 14 步流水线协调器
│   │   ├── resolve.py       # 源视频解析
│   │   ├── assets.py        # 素材验证
│   │   ├── research.py      # LLM 电影调研
│   │   ├── script.py        # LLM 脚本生成
│   │   ├── script_export.py # 脚本 Markdown 导出
│   │   ├── tts.py           # TTS 编排（使用 tts/ 包；缓存 + 并发）
│   │   ├── align.py         # WhisperX 音频对齐
│   │   ├── scenes.py        # PySceneDetect 场景检测
│   │   ├── match.py         # 启发式片段匹配
│   │   ├── bgm.py           # 背景音乐混音
│   │   ├── translate.py     # 多语言字幕翻译（LLM）
│   │   ├── subtitle.py      # SRT 生成（原版 / 翻译 / 双语）
│   │   ├── render.py        # MoviePy 视频渲染
│   │   ├── export_clips.py  # 场景片段导出
│   │   └── errors.py        # PipelineStrictError
│   ├── workflow/
│   │   ├── schema.py        # JobConfig / JobSteps / JobParams
│   │   ├── load.py          # YAML 加载与验证
│   │   ├── merge.py         # CLI > YAML > Settings 合并
│   │   └── errors.py        # JobConfigError
│   ├── tts/                     # TTS 抽象层（v0.4）
│   │   ├── __init__.py          # 导出公共 API
│   │   ├── protocol.py          # TTSProvider ABC
│   │   ├── base.py              # BaseTTSProvider（CI 静音回退）、is_ci()
│   │   ├── edge.py              # EdgeTTSProvider
│   │   ├── openai_provider.py   # OpenAITTSProvider（voice 白名单、延迟 SDK 导入）
│   │   ├── mimo_provider.py     # MimoTTSProvider（3 模型：指定音色、声音克隆、声音设计）
│   │   ├── factory.py           # get_tts_provider(settings)
│   │   └── cache.py             # TTSCacheKey、cache_path_for、PROVIDER_CACHE_VERSIONS
│   ├── utils/
│   │   ├── async_utils.py   # 同步/异步桥接
│   │   ├── console.py       # Console Protocol + PlainConsole + build_console
│   │   ├── environment.py   # 环境信息采集
│   │   ├── errors.py        # ConfigError（横切配置错误类）
│   │   ├── font.py          # CJK 字体回退
│   │   ├── json_parser.py   # LLM JSON 抽取
│   │   ├── llm.py           # OpenAI 客户端封装
│   │   ├── log.py           # AppLogger（文件日志层）
│   │   ├── metadata_export.py # metadata.json 构造器
│   │   ├── optional_deps.py # 可选依赖探测
│   │   ├── prompts.py       # 提示词模板
│   │   └── retention.py     # 日志文件保留策略
│   └── web/                     # Gradio 浏览器 UI（v0.3.5；需安装 [web] extra）
│       ├── __init__.py          # 延迟导出 launch_web
│       ├── __main__.py          # python -m movie_narrator.web
│       ├── app.py               # Gradio Blocks 布局 + 事件处理
│       ├── bridge.py            # 表单 → 后台线程 → yield UI 更新
│       ├── form.py              # FormData + validate_form + form_to_context_args
│       ├── console.py           # GradioConsole（threading.Lock 线程安全）
│       ├── controller.py        # GradioController（协作式取消标志）
│       ├── models.py            # RunStatus 枚举 + WebRun 会话状态
│       └── utils.py             # 上传处理 + collect_artifacts + 文件名清洗
├── tests/
│   ├── test_context.py
│   ├── test_settings.py
│   ├── test_errors.py
│   ├── test_align.py
│   ├── test_assets.py
│   ├── test_bgm.py
│   ├── test_cli_config.py
│   ├── test_cli_resolve.py
│   ├── test_match.py
│   ├── test_optional_deps.py
│   ├── test_render_real.py
│   ├── test_research.py
│   ├── test_resolve.py
│   ├── test_runner_strict.py
│   ├── test_runner_workflow_metadata.py
│   ├── test_scenes.py
│   ├── test_script_export.py
│   ├── test_translate.py
│   ├── test_json_parser.py
│   ├── test_pipeline_cancel.py
│   ├── test_web_console.py
│   ├── test_web_controller.py
│   ├── test_web_form.py
│   └── test_workflow_steps.py
├── docs/
├── assets/
└── .github/workflows/
```

---

## 路线图

### v0.1.x — 核心流水线 ✅

- [x] CLI 接口（`mn create`、`mn version`）
- [x] LLM 脚本生成（JSON 输出）
- [x] Edge-TTS 语音合成（并发生成）
- [x] SRT 字幕生成（毫秒精度）
- [x] MoviePy 视频渲染（16:9 / 9:16）
- [x] TTS 结果缓存（内容寻址）
- [x] 元数据导出（JSON）
- [x] CI 流水线（单元测试 + 冒烟测试）

### v0.2.x — 场景与媒体 ✅

- [x] 电影剧情调研 Agent（`--research`）
- [x] WhisperX 音频-文本对齐
- [x] 电影视频场景检测
- [x] 基于脚本的自动素材匹配
- [x] 语义化场景搜索（Embedding，需要 `[ml]`）
- [x] 背景音乐集成（BGM 混音）
- [x] 脚本 Markdown 导出（`script.md`）
- [x] 场景级片段输出（`clips/`）

### v0.3.x — 平台与工作流 ✅

- [x] 声明式工作流配置（软步骤开关 + 参数调节）
- [x] YAML 任务配置文件（`mn create --config`）
- [x] 控制台 / 结构化 StepState 日志重构（`ctx.services.console`、`StepState`）
- [x] 多语言字幕（`--subtitle-lang` / `--subtitle-mode`；LLM 翻译 + 重试软降级；输出 `subtitle.<lang>.srt` + `subtitle.bilingual.srt`）
- [x] Web UI（Gradio 本地浏览器应用，`mn web`；协作式取消；需安装 `[web]` extra）

### v0.4.x — TTS 抽象与基础设施 ✅

- [x] TTS Provider 抽象（`TTSProvider` 协议、Edge + OpenAI + MiMo 后端）
- [x] 通过 `MN_TTS_PROVIDER` 选择后端（`edge` / `openai` / `mimo`）
- [x] OpenAI TTS 支持（voice 白名单、凭证回退、延迟 SDK 导入）
- [x] MiMo TTS 支持（3 模型：指定音色、声音克隆、声音设计；限时免费）
- [x] 缓存键升级（sha256、7 维度、两级散列、per-provider 版本映射）
- [x] CI 临时文件隔离（静音音频不进入缓存）
- [x] `is_ci()` CI 检测单一来源
- [x] `ConfigError` 横切配置错误类

### v0.5.x — 生态系统（规划中）

> **目标**：在 Cloud 功能依赖之前，冻结公开 API 表面（Pipeline、Workflow、Plugin、SDK）。

- [ ] 插件 API（自定义流水线步骤：步骤注册、生命周期钩子、依赖声明）
- [ ] Python SDK（`from movie_narrator import ...`）
- [ ] 自定义步骤注册（`@register_step`）
- [ ] 第三方 Provider 扩展（TTS、LLM、研究后端，通过插件 API）
- [ ] 社区扩展发现与打包规范

> SDK 与 Plugin API 一起设计——两者必须在同一版本中稳定。

### v0.6.x — 云端（规划中）

- [ ] 远程推理（将 LLM / TTS / 渲染卸载到云端 Worker）
- [ ] 分布式渲染（跨节点拆分视频片段）
- [ ] 任务队列（异步任务提交、进度轮询、重试）
- [ ] Web 服务部署（REST API、认证、多租户）

---

## 文档

- [路线图](docs/ROADMAP.md)
- [架构设计](docs/ARCHITECTURE.md)
- [贡献指南](docs/CONTRIBUTING.md)

---

## 许可证

基于 [AGPL-3.0](LICENSE) 许可证发布。
