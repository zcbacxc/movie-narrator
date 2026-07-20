[![English](https://img.shields.io/badge/English-Contributing-blue)](CONTRIBUTING.md)
[![简体中文](https://img.shields.io/badge/简体中文-贡献指南-green)](CONTRIBUTING.zh-CN.md)

# 贡献指南

## 开发环境搭建

```bash
git clone https://github.com/zcbacxc/movie-narrator.git
cd movie-narrator
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -e ".[dev]"

# 前端（用于 WebUI 开发）
cd webui && npm install && cd ..
```

## 运行测试

```bash
pytest -v
```

### 前端校验

当你改动 `webui/` 目录下的任何文件时，请确认 SPA 仍然能通过类型检查并构建成功：

```bash
cd webui && npm run build
```

该命令会先后执行 `tsc`（TypeScript 类型检查）和 Vite 生产构建。只有构建干净的 bundle 才能被 FastAPI 服务。

## 项目结构

```
movie-narrator/
├── src/movie_narrator/
│   ├── pipeline/        # 15 步 pipeline、preflight、tts/render/match 等 step 模块
│   ├── tts/             # TTS provider 抽象层（edge、openai、mimo、factory、cache）
│   ├── web_api/         # FastAPI + WebSocket 后端（默认 WebUI，端口 8760）
│   ├── utils/           # llm.py、errors.py、共享辅助
│   ├── models.py        # Context、PipelineStatus、StepState 等
│   ├── cli.py           # `mn` Typer 入口（create、web、version 等）
│   └── workflow.py      # job.yaml 加载与合并（JobConfig、merge_job）
├── webui/               # React 18 SPA — Vite + TypeScript + shadcn/ui + Tailwind
├── tests/               # pytest 套件（单元 + 烟雾测试）
├── docs/                # ARCHITECTURE、ROADMAP、CONTRIBUTING、specs/
└── examples/            # job.example.yaml
```

WebUI 由两部分组成：`src/movie_narrator/web_api/`（Python 后端）和 `webui/`（React 前端）。生产环境下 FastAPI 直接服务由 Vite 构建出来的 bundle，因此并不需要单独的 frontend server。

## 前端开发

### 开发模式（两个终端）

开发期间需要同时跑 API 和 Vite 开发服务器，这样才能享受热更新：

```bash
# 终端 1 — FastAPI 后端（API 服务在 :8760）
mn web

# 终端 2 — Vite 开发服务器（HMR；把 /api 和 /ws 代理到 :8760）
cd webui && npm run dev
```

打开终端 2 打印出来的 Vite URL 即可访问。Vite 开发服务器会把 REST 和 WebSocket 请求代理到 FastAPI 后端，这样 SPA 与真实 API 通信时不需要每次都重新打包。

### 生产构建

在发布前端改动之前，请重新打包 bundle，让 FastAPI 服务的静态资源保持最新：

```bash
cd webui && npm run build
```

构建成功后，仅靠 `mn web` 一条命令就能同时提供 API 和最新构建的 SPA，无需启动第二个进程。

## 代码风格

- 遵循各模块已有的代码风格
- 新增的 pipeline 步骤请补齐测试
- 新增功能时同步更新 `docs/ROADMAP.md`

## 提交规范

- `feat:` 新功能
- `fix:` Bug 修复
- `docs:` 仅文档变更
- `chore:` 维护、CI、工具相关
- `refactor:` 既不修 Bug 也不加新功能的代码重构

## 提交改动

1. Fork 本仓库并基于 `main` 拉出 feature 分支（`feature/<short-name>`）
2. 带上测试提交你的改动
3. 执行 `pytest -v` 确保全部测试通过
4. 如果新增的是功能，更新 `docs/ROADMAP.md`
5. 在 `[Unreleased]` 段添加 CHANGELOG 条目（Keep a Changelog 格式）
6. 提 PR 时目标分支选 `main`。本项目使用简化的 Gitflow：`feature/*` 和 `hotfix/*` 分支合并回 `main`；不使用 `release/*` 分支。

## 新增一个 Pipeline 步骤

1. 在 `src/movie_narrator/pipeline/` 下新增一个模块，导出
   `def <step_name>(ctx: Context) -> Context`
2. 对 soft 步骤，请在 `ctx.status.<field>`、`ctx.step_state`（使用
   `StepResult.{SKIPPED,WARNING}`）中记录状态，并在失败时往 `metadata.warnings`
   追加告警 —— 可参考 `pipeline/translate.py` 和 `pipeline/match.py` 中的规范实现
3. 在 `pipeline/runner.py` 中把该步骤注册进 `STEPS`、`SOFT_STATUS_STEPS`（若是 soft 步骤）
   以及 `STATUS_FIELD_FOR_STEP`
4. 给 `models.py` 中的 `PipelineStatus` 加上对应的状态字段（默认值 `disabled`，
   但 `translate` 例外，默认 `skipped`）
5. 在 `tests/test_<step>.py` 下写覆盖决策矩阵（disabled / skipped / success / failure）
   以及 CLI/YAML 集成的测试
