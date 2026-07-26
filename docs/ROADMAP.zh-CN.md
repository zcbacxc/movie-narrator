[![English](https://img.shields.io/badge/English-Roadmap-blue)](ROADMAP.md)
[![简体中文](https://img.shields.io/badge/简体中文-路线图-green)](ROADMAP.zh-CN.md)

# 路线图

> 逐版本明细见 [CHANGELOG.md](../CHANGELOG.md)。配置参考见 [`.env.example`](../.env.example) 和 [`job.example.yaml`](../examples/job.example.yaml)。

## v0.1.x — 核心流水线

- [x] CLI 接口（`mn create`、`mn version`）
- [x] 基于 LLM 的解说稿生成，输出 JSON
- [x] Edge-TTS 旁白，支持并发合成
- [x] SRT 字幕生成，毫秒精度
- [x] MoviePy 视频渲染（16:9 / 9:16）
- [x] TTS 结果缓存，使用内容寻址键
- [x] 元数据导出（JSON）
- [x] CI 流水线（单元测试 + smoke test）

## v0.2.x — 场景与媒体

- [x] 影片资料研究 agent（`--research`）
- [x] WhisperX 音字对齐
- [x] 从影片视频检测分镜
- [x] 基于解说稿的自动素材片段匹配
- [x] 语义化场景检索（基于 embedding）
- [x] 背景音乐（BGM）混音
- [x] 解说稿 markdown 导出（`script.md`）
- [x] 场景级片段输出（`clips/`）
- [x] 优雅降级 —— 可选依赖缺失时软步骤静默跳过

## v0.3.x — 平台与工作流

- [x] 软步骤开关 + 参数的声明式工作流配置
- [x] 基于 YAML 的 job 配置（`mn create --config`）
- [x] 控制台 / 结构化步骤状态日志重构（`ctx.services.console`、`StepState`）
- [x] 多语言字幕支持（`--subtitle-lang` / `--subtitle-mode`；LLM 翻译，重试 → 软降级；三文件 SRT 输出）
- [x] Web UI（Gradio；在 v0.4.x 由 FastAPI + React 重构后取代；随后拆分为独立 repo [movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)）

## v0.4.x — TTS 抽象与基础设施

> 28 个 patch 版本（v0.4.0 – v0.4.27）。主线：TTS provider 抽象、配置体系重做、WebUI 重构、核心引擎生产级质量、L2 就绪并通过手测、匹配智能、效果组合、可扩展性、契约层。

### 基础设施

- [x] TTS provider 抽象（Edge / OpenAI / MiMo，通过 `MN_TTS_PROVIDER` 选择）
- [x] 内容寻址缓存（sha256，7 维，按 provider 版本表）
- [x] 配置体系重做 —— 严格的 `.env` / `job.yaml` 边界
- [x] MoviePy 1.x → 2.x 升级（兼容 Python 3.13+）
- [x] 流水线执行前的 LLM/TTS Preflight 校验
- [x] 步骤级重试机制（`--retry` 标志、`StepAction` 枚举）

### Web UI

> 以下 Web UI 工作随后已拆分为独立 repo [movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)。核心 repo 不再包含 `web_api/` 或 `webui/`；以独立包形式安装 Web UI：`pip install movie-narrator-web`，通过 `mn-web` 启动。

- [x] Gradio → FastAPI + React SPA 重构（Vite + TypeScript + shadcn/ui）
- [x] WebSocket 实时进度推送（`/ws/task/{task_id}`）
- [x] pip 可安装的 WebUI 打包 —— 现由独立的 `movie-narrator-web` repo 发布（`pip install movie-narrator-web`，命令 `mn-web`）

### 核心引擎质量

- [x] 渲染后产物 QA 步骤（`validate_deliverable`）
- [x] 音频归一化 + BGM ducking（带 attack/release 平滑）
- [x] 视频 cover/contain 布局；底部安全字幕布局（CJK 换行 + 半透明衬底条）
- [x] 渲染编码质量 —— CRF 18、preset `slow`、`+faststart`
- [x] 解说预设系统（`douyin-fast` / `mainstream-dry` / `bilibili-long`）
- [x] 两段式解说稿生成，按时长动态决定句数
- [x] 草稿模式快速迭代（`render_profile: draft`）

### L2 就绪与验证

- [x] `metadata.json` 中的 `match_summary` 完整 schema（21+ 字段），供 L2 jq 查询
- [x] 降级可见性 —— `_degraded_steps` + CLI 摘要覆盖所有软步骤失败
- [x] faster-whisper 后端（Windows CPU 兼容；解锁 embedding re-rank）
- [x] L2 手测通过 —— O1-O10 100%（G1 满江红 + G3 飞驰人生3）
- [x] L2 G2 跨片验证 —— 西虹市首富
- [x] L2+ 手测工具包（checklist + `compare_runs.py` + SOP）

### 匹配智能

- [x] EP1 幕加权时间线分区（四幕戏剧化节奏）
- [x] EP3 top-K 重排，带顺序回溯复用惩罚
- [x] EP2 节拍时间锚（结构化 beats 带 `act` + `approx_ratio`）
- [x] 多样性后处理（滑动窗口场景复用限制）
- [x] 素材覆盖率门控（低于阈值时仅告警）

### 效果组合

- [x] EP4 钩子模板与名场面注入（按类型的开场钩子句 + 命名场景注入 beats）
- [x] EP5 标题卡 + cover.jpg 导出 + 竖屏安全区（9:16 字幕边距收紧）
- [x] EP6 duck 曲线 + 基于 RMS 的响度归一化

### 可扩展性与流水线

- [x] EP8 VisionCaptioner 抽象（`vision/` 包；stub provider，通过 Plugin API 可扩展）
- [x] EP9 暂停/恢复（`--pause-at` + `mn resume` + `pipeline_state.json`）
- [x] 契约层（`contract.py` —— 稳定 API 边界；现由外部 [movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web) 包消费，`CONTRACT_VERSION = (0, 5, 1)`）
- [x] Stage E 产品化（CLI 匹配摘要 + RS-07/08/09 渲染修复）

## v0.5.x — 生态

> **目标**：在 Cloud 功能依赖之前，先冻结公开 API 表面（Pipeline、Workflow、Plugin、SDK）。

### M1 — 插件注册基础设施 (#91)

- [x] 自定义流水线步骤的 Plugin API（步骤注册、生命周期钩子、依赖声明）
- [x] StepRegistry + ProviderRegistry，支持 `@register_step` / `@register_provider` 装饰器
- [x] UnifiedParamSchema —— `PARAM_WHITELIST` 由 `JobParams` 模型字段自动派生
- [x] SDK 公开导出（`list_presets`、`get_preset`）在 `contract.py` 和 `__init__.py` 中

### M2 — SDK 冻结 (#92)

- [x] 编程式使用的 Python SDK（`from movie_narrator import ...`）
- [x] 自定义流水线步骤的注册（`@register_step`）
- [x] 通过 `importlib.metadata` entry points 发现插件（`movie_narrator.plugins` 组）
- [x] 第三方 provider 扩展（TTS、LLM、资料 backend，通过 Plugin API）
- [x] `Services.logger` 可选字段，供插件结构化日志使用
- [x] 树外示例插件（`examples/plugins/watermark/`）

### WP6 — 场景过滤 (#93)

- [x] 片头跳过 —— 通过亮度 + 运动分析自动检测并跳过 intro/logo 序列
- [x] 黑帧检测 —— 过滤浪费解说预算的近黑帧
- [x] 高亮窗口 —— 可配置的基于时间窗口的场景优先级

### WebUI 拆分 —— 双仓库分离 (#94, #95)

- [x] WebUI（FastAPI + React）拆分为独立 repo [movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)
- [x] 核心引擎现为纯 CLI 包，无 web 依赖
- [x] 契约版本号（`CONTRACT_VERSION = (0, 5, 0)`），支持导入时兼容性检查

### M4 — Provider 迁移

- [x] LLM 注册表（`llm_registry`）—— `utils/llm.py` 迁移到注册表模式，内置 `openai` provider
- [x] Research 注册表（`research_registry`）—— `pipeline/research.py` 迁移到注册表模式，内置 `llm` provider
- [x] TTS/Vision 工厂清理 legacy fallback —— 移除死代码，仅通过注册表分发
- [x] Protocol 校验 —— `tts_registry` 和 `vision_registry` 在 `create()` 时强制 ABC 一致性
- [x] `PluginContext` 扩展 `llm` 和 `research` 字段
- [x] `CONTRACT_VERSION` 升至 `(0, 5, 1)` —— 向后兼容（仅新增导出）
- [x] SDK 导出：`register_llm`、`register_research`、`llm_registry`、`research_registry` 加入 `contract.py` 和 `__init__.py`

> **设计备注**：SDK 与 Plugin API 是一起设计的 —— SDK 是 Plugin API 的主要使用者，所以两者必须在同一次发布稳定下来，避免兼容性压力。

## v0.6.x — Cloud

- [ ] 远程推理（offload LLM / TTS / 渲染到云 worker）
- [ ] 分布式渲染（将视频段分散到多节点）
- [ ] 任务队列（异步 job 提交、进度轮询、重试）
- [ ] Web 服务部署（REST API、鉴权、多租户）—— 注：Web UI 本身现已是独立包（[movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)）；本条目关注云端部署/托管，而非 UI 代码库
