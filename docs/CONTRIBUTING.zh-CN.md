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
```

> **Web UI 在独立仓库中开发。** FastAPI + React 技术栈现位于 [`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web)；本核心 repo 是纯 CLI 引擎，不含 `web_api/` 或 `webui/` 目录。如需参与 Web UI 开发，请克隆该仓库并遵循其贡献指南。

## 运行测试

```bash
pytest -v
```

## 项目结构

```
movie-narrator/
├── src/movie_narrator/
│   ├── pipeline/        # 16 步 pipeline、preflight、tts/render/match 等 step 模块
│   ├── pipeline/scene_filter.py  # 场景过滤（片头跳过、黑帧检测、高亮窗口）
│   ├── pipeline/registry.py      # StepRegistry 与 runner 集成
│   ├── tts/             # TTS provider 抽象层（edge、openai、mimo、factory、cache）
│   ├── providers/       # ProviderRegistry（register_tts、register_vision、register_llm、register_research）
│   ├── vision/          # VisionCaptioner 抽象（stub，通过 Plugin API 可扩展）
│   ├── presets/         # 解说预设（douyin-fast、mainstream-dry、bilibili-long）
│   ├── utils/           # llm.py、errors.py、共享辅助
│   ├── plugin_loader.py # 插件发现（entry_points）、StepRegistry、Plugin protocol
│   ├── models.py        # Context、PipelineStatus、StepState、Services 等
│   ├── contract.py      # 稳定 API 边界（CONTRACT_VERSION = (1, 0, 0)）
│   ├── cli.py           # `mn` Typer 入口（create、version 等）
│   └── workflow/        # job.yaml 加载与合并（schema.py、load.py、merge.py、errors.py）
├── tests/               # pytest 套件（单元 + 烟雾测试）
├── docs/                # ARCHITECTURE、ROADMAP、CONTRIBUTING、PACKAGING、ADR、sdk/、llm-providers/
└── examples/            # job.example.yaml、ci-test.yaml、plugins/（watermark、template、timeline_export、research-wiki）
```

Web UI 在独立仓库 [`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web) 中开发；它只通过 `contract.py` 定义的契约面消费核心引擎。本 repo 不含 `web_api/` 或 `webui/` 目录树。

## 如何贡献 Web UI

Web UI（FastAPI + React 18 SPA，安装 `pip install movie-narrator-web` 后通过独立的 `mn-web` 命令启动）位于其专属仓库：[`movie-narrator-web`](https://github.com/zcbacxc/movie-narrator-web)。前端与 web 后端的改动 —— 包括 `npm install` / `npm run dev` / `npm run build` 等流程 —— 都应在该仓库进行，而非本核心 repo。本 repo 仅维护 web 包所依赖的稳定 `contract.py` API 面。

## 代码风格

- 遵循各模块已有的代码风格
- 新增的 pipeline 步骤请补齐测试
- 新增功能时同步更新 `docs/ROADMAP.md`
- 禁止在代码注释或文档字符串中引入内部追踪代号（如 EP*、WP*、NA-*）。请使用简明的技术说明。

## 许可禁区

在添加任何依赖或复制参考代码之前，请核对下面的禁区清单。以下内容**不得**引入核心或捆绑发行包 —— 完整理由见 [ADR-011](ADR.zh-CN.md#adr-011许可禁区与-ffmpeg-捆绑策略)。

- **Remotion** —— 自定义许可（公司超 3 人需付费）。请改用 MIT 替代方案（如 revideo）或 HTML + 无头截图。
- **TypeTale 源码** —— 许可未声明，不得复制。请自行实现，或使用明确 MIT 许可的参考并在 `NOTICE` 文件中声明。
- **素材爬取爬虫**（针对流媒体平台的 yt-dlp / Bilibili / Playwright）—— 平台服务条款 + 版权。请坚持"用户自备素材"路线；空镜素材仅可来自公有领域来源（Archive.org、NASA、Wikimedia、Pexels）。
- **声音克隆**（针对任意声音的 IndexTTS / CosyVoice）—— 声音权法律风险。若未来接入，仅可克隆用户本人/已授权声音。

**FFmpeg**：请勿自行将 FFmpeg 二进制捆绑进发行包。务必通过共享的 `utils/ffmpeg_bin.ffmpeg_bin()` 策略解析二进制（优先捆绑的 imageio-ffmpeg 构建，其次 `PATH` 上的系统二进制，最后 `"ffmpeg"`）——切勿临时用 `shutil.which("ffmpeg")`。若确需真正在发行层捆绑，必须先经 [ADR-011](ADR.zh-CN.md#adr-011许可禁区与-ffmpeg-捆绑策略) 决策，并附带 `THIRD_PARTY_NOTICES` 文件（优先 LGPL 构建）。

> **CI 强制检查**：上述禁区依赖清单已由 CI 中的 `scripts/check_forbidden_deps.py` 自动检查；构建产物是否误捆绑 FFmpeg/FFprobe 二进制由 `scripts/check_no_ffmpeg_bundle.py` 检查。若新增禁区包，请同步更新本清单与脚本的守卫清单。

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

### 推荐：插件 API（v0.5+）

使用 `@register_step` 装饰器添加步骤，无需修改 runner：

1. 创建一个 Python 包，包含步骤函数 `def my_step(ctx: Context) -> Context`
2. 使用 `@register_step("my_step", soft=True, after="render_video")` 注册
3. 在 `pyproject.toml` 中声明 entry point（格式见[插件开发指南](PLUGIN_DEVELOPMENT.md#quick-start)）
4. 安装你的包后步骤会被自动发现

完整参考实现见 `examples/plugins/watermark/`。

### 旧方式：直接修改 STEPS

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

## 开发插件

插件通过自定义步骤和 provider 扩展流水线。完整指南见
[插件开发指南](PLUGIN_DEVELOPMENT.md)，包含 entry point 声明、SDK 接口和参考实现。

参考实现：`examples/plugins/watermark/`

插件的打包、版本管理与 PyPI 发布，详见 [PACKAGING.md](PACKAGING.md)。
