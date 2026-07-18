# AI Coding Assistant Guide

> 本指南面向 AI 编程工具（Claude Code、Codex、Cursor、Copilot 等），帮助快速理解项目结构并开始工作。

## 项目简介

`movie-narrator` — 一个 Python CLI 工具（`mn`），从一个提示词生成带解说的电影回顾视频。

**Pipeline**: Resolve → Assets → Research → Script → Script Export → TTS → Align → Scenes → Match → BGM → Translate → Subtitle → Render → QA → Export Clips（15 步）

## 安装

```bash
# 克隆
git clone https://github.com/zcbacxc/movie-narrator.git
cd movie-narrator

# 安装（开发模式）
pip install -e ".[dev]"

# 可选扩展（按需安装）
pip install "movie-narrator[media]"   # 场景检测（PySceneDetect）
pip install "movie-narrator[ml]"      # WhisperX + 语义搜索
pip install "movie-narrator[web]"     # Web UI（FastAPI + React）
pip install "movie-narrator[full]"    # 全部
```

**外部依赖**: FFmpeg（`ffmpeg -version` 验证）

## 配置

### LLM 配置

编辑 `~/.movie-narrator/.env`（首次运行自动创建）：

```env
MN_LLM_BASE_URL=http://localhost:11434/v1   # Ollama 默认
MN_LLM_API_KEY=ollama
MN_LLM_MODEL=qwen2.5:7b
MN_DEFAULT_VOICE=zh-CN-YunxiNeural
```

支持任何 OpenAI 兼容 LLM（Ollama、智谱、百炼、MiMo、硅基流动等），详见 [LLM 服务商导航](LLM_PROVIDERS.md)。

### Job YAML 配置

`examples/job.example.yaml` 包含全部 52 个行为参数的白名单和默认值。用法：

```bash
mn create --config examples/job.example.yaml
mn create --config examples/job.example.yaml --movie "其他电影" --no-clips
```

优先级：**CLI 参数 > YAML 配置 > 内置默认值**

## 常用命令

```bash
# 基础用法
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60

# 指定音色和比例
mn create --movie "飞驰人生" --voice "zh-CN-XiaoxiaoNeural" --format "9:16"

# 带源视频 + BGM + 调研
mn create --movie "飞驰人生" --video ./movie.mp4 --bgm ./bgm.mp3 --research

# 多语言字幕
mn create --movie "Inception" --subtitle-lang en --subtitle-mode bilingual

# 解说风格预设
mn create --movie "飞驰人生" --narration-preset mainstream-dry

# 离线演示（跳过 LLM 和 TTS，生成静音音频）
CI=1 mn create --movie "Demo" --duration 10

# Web UI
mn web

# 查看版本
mn version
```

## 测试

```bash
# 单元测试（无需网络和 LLM）
pytest -v

# 单个测试
pytest tests/test_context.py::test_format_time -v

# CI 冒烟测试（端到端，静音回退）
CI=1 mn create --movie "CI-Test" --style "测试" --duration 10 --keep-cache
```

## 项目结构

```text
src/movie_narrator/
├── cli.py               # Typer CLI 入口（mn 命令）
├── config.py            # Settings（pydantic-settings，MN_* 环境变量）
├── models.py            # Context、PipelineStatus、StepState 等数据模型
├── pipeline/
│   ├── runner.py        # 15 步流水线编排（STEPS 列表 + build_context）
│   ├── resolve.py       # 源视频查找
│   ├── script.py        # LLM 脚本生成（两阶段：beats → expansion）
│   ├── tts.py           # TTS 编排（缓存 + 并发）
│   ├── render.py        # MoviePy 视频渲染
│   ├── translate.py     # 多语言字幕翻译
│   ├── qa.py            # 成片质检（ffprobe）
│   └── errors.py        # PipelineStrictError, PipelineCancelled, StepAction
├── tts/                 # TTS 抽象层（edge / openai / mimo）
├── workflow/            # YAML 配置加载与合并
├── presets/             # 解说风格预设（douyin-fast / mainstream-dry / bilibili-long）
├── utils/               # 工具函数（llm、font、json_parser 等）
└── web_api/             # FastAPI + WebSocket 后端（Web UI）
```

## 关键设计

| 概念 | 说明 |
|------|------|
| `Context` | 共享可变状态，在所有步骤间传递 |
| `PipelineStatus` | 软步骤状态追踪（disabled / skipped / success / failed） |
| Soft vs Hard steps | 软步骤失败可跳过（`--strict` 改为中止），硬步骤失败中止流水线 |
| Content-addressable TTS cache | 7 维度 SHA256（schema_version, provider, provider_version, model, voice, text, pause_ms） |
| YAML auto-discovery | 未传 `--config` 时自动查找 `cwd/job.yaml` → 打包示例 |
| env/yaml boundary | `.env` = LLM + TTS 凭证（24 项）；`job.yaml` = 行为参数（52 项） |

## 添加新 Pipeline 步骤

1. 在 `src/movie_narrator/pipeline/` 新建模块，暴露 `def step_name(ctx: Context) -> Context`
2. 软步骤：设置 `ctx.status.<field>` + `ctx.step_state`，失败时追加 `metadata.warnings`
3. 在 `runner.py` 的 `STEPS` 中注册，软步骤加入 `SOFT_STATUS_STEPS`
4. 在 `models.py` 的 `PipelineStatus` 中添加状态字段
5. 在 `tests/` 添加测试覆盖 disabled / skipped / success / failure 四种路径

## 贡献规范

- 提交前缀：`feat:` / `fix:` / `docs:` / `chore:` / `refactor:`
- 分支模型：`main`（生产）+ `feature/*` + `hotfix/*`
- 发布流程：本地测试通过 → 更新版本号和 CHANGELOG → 打 tag → CI 自动发布
- 详见 [CONTRIBUTING.md](CONTRIBUTING.md)
