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
- [x] StepRegistry + ProviderRegistry，支持 `@register_step` / `@register_tts` / `@register_vision` / `@register_llm` / `@register_research` 装饰器
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

### M5 — 社区与打包

- [x] CLI 插件命令（`mn plugin list|discover|registries|version`）
- [x] 插件模板（`examples/plugins/template/`）含 README 快速上手指南
- [x] `check_version()` 辅助函数，供外部消费者在 import 时校验版本
- [x] `ProviderRegistry.info()` 方法，返回结构化 provider 元数据
- [x] 打包指南（`docs/PACKAGING.md`）—— 版本号、entry points、发布流程

> **设计备注**：SDK 与 Plugin API 是一起设计的 —— SDK 是 Plugin API 的主要使用者，所以两者必须在同一次发布稳定下来，避免兼容性压力。

### v0.5.3 — Hardening

- [x] SDK API 参考（`mkdocs.yml` + `docs/sdk/` —— 通过 mkdocstrings 从 docstring 自动生成）
- [x] 性能基准脚本（`benchmarks/profile_pipeline.py` —— CI 模式下逐步骤 profiling）
- [x] Quickstart 指南（`docs/QUICKSTART.md` —— 端到端插件开发教程）
- [x] Research provider 示例（`examples/plugins/research-wiki/` —— 基于 Wikipedia API 的调研 provider）
- [x] `ResearchInfo` 加入 SDK 导出（`contract.py` + `__init__.py`）
- [x] `PLUGIN_DEVELOPMENT.md` 补全 LLM 和 Research provider 章节
- [x] `docs` 可选依赖组（mkdocs + mkdocstrings）

### v0.5.4 — 质量提升

- [x] VLM caption provider（`vision/vlm.py` —— 云端 VLM API 生成真实视觉场景描述，Q-M5）
- [x] 多候选赛马（`race.py` + `mn race` CLI —— 同输入跑 N 套变体，打分选优，Q-P2）
- [x] 参考片模仿（`imitate.py` + `mn imitate` CLI —— 从爆款解说提取风格生成同风格新片，Q-P7）
- [x] Layer 0 runbook（`examples/l2/RUNBOOK.md` —— 零代码质量提升指南，Q-X1~X6）

### v0.5.5 — 日志改进

- [x] 可配置日志级别（`--log-level DEBUG|INFO|WARNING|ERROR`，适用于 `mn create`/`resume`/`imitate`）
- [x] 详细控制台模式（`--verbose` 标志，实时输出 DEBUG 级别日志）
- [x] RotatingFileHandler（10MB 轮转，5 个备份，防止日志无限增长）
- [x] JSON 格式日志（可选结构化 JSON 日志，适用于 ELK/Loki 聚合）
- [x] Run ID 关联（8 字符 ID 前缀 + metadata.json，便于交叉关联日志文件）
- [x] 子步骤计时（`step_timing`，用于 LLM/TTS/ffmpeg 调用性能分析）
- [x] Services.logger 集成（AppLogger 自动注入，供插件使用结构化日志）
- [x] 文档与示例对齐 v0.5.4 项目状态

### v0.5.6 — 叙事质量与外部数据

- [x] 叙事五原则 + 反 AI 腔调注入提示模板 (NA-M1-S1)
- [x] 平台语气适配 — `target_platform` 支持 douyin/bilibili/youtube (NA-M1-S2)
- [x] 节拍标注 rhythm_zone/emotion (NA-M1-S3)
- [x] 节拍区域影响匹配评分 (NA-M1-S3+)
- [x] 解说视角与角色锚定 — `narrator_perspective` / `focus_character` (NA-M1-S4)
- [x] CLI 视角参数 — `--narrator-perspective` / `--focus-character` (NA-M1-S4+)
- [x] 两阶段解说稿自检 judge + 重试 (NA-M1-S5)
- [x] Judge 反馈循环 — 将审查问题注入重试提示 (NA-M1-S5+)
- [x] 结构化电影卡片降低幻觉 (NA-M2-S1)
- [x] TMDB 外部数据源事实验证 (NA-M2-S1+)
- [x] BGM 基于情绪的选择 (NA-M4-S1)
- [x] 情绪加权 BGM 选择与能量对齐 (NA-M4-S1+)
- [x] 渲染模板系统 — 按 preset 自定义样式 (NA-M6-S1)
- [x] 语言链一致性 — 单一 `lang` 真相源 (R2-NA-LANG)
- [x] 可重试错误码 — 网络类故障分类 (R2-NA-ORCH)
- [x] TMDB provider 导入时注册修复（`pipeline/research.py`）
- [x] YAML 白名单注释同步 — 补全 12 个缺失参数文档（`examples/job.example.yaml`）
- [x] L2 YAML 注释修复 — WP1 短键已生效、prompt_target_segment_duration 为合法字段（`examples/l2/job.l2.douyin.yaml`）
- [x] README / cli-usage.sh / ROADMAP 与当前代码库对齐

### v0.5.7 — 质量加固

- [x] TMDB provider 内存缓存（`_TMDB_CACHE`）— 所有 GET 请求复用结果
- [x] TMDB 限流重试 — HTTP 429 解析 Retry-After 头 + 指数退避（3 次尝试）
- [x] TMDB 优雅降级 — 重试耗尽后返回原始 LLM 卡片，不崩溃
- [x] 渲染模板测试 — 13 个测试覆盖 `{movie}` 占位符替换与渲染逻辑
- [x] v0.5.6 边界用例测试 — 35 个测试覆盖视角、平台调性、语言链、错误分类
- [x] 特性级基准性能分析 — judge LLM、TMDB、BGM 情感选择独立计时
- [x] 测试总数：876 → 921（+45 个新测试，0 失败）

### v0.5.8 — 脚本质量深化（2026-07-29）

- [x] 修复 `_score_bgm_candidate` 多功能性奖励 bug（分支条件 `emo == mood` 恒为 False）
- [x] 多语言反 AI 腔 — 扩展禁用词列表至中文以外（英文、日文、韩文）
- [x] Judge 五维度评审 — 新增反 AI 腔遵守度、叙事原则符合度
- [x] Beat 去重 — Phase 2 扩展前检测并合并重复剧情节点
- [x] 内置 Hook 模板库 — 用户未提供 `hook_templates` 时的后备方案
- [x] 脚本级 QA 门 — TTS 前校验脚本（长度、多样性、hook 存在性）

### v0.5.9 — 语音与音频质量（2026-07-29）

- [x] TTS 时长反馈 v2 — v1 pause 调整后仍溢出时按速度调整（上限 1.15x）
- [x] TTS 输出质量验证 — 削波检测、SNR 估算、静音检查
- [x] 情感感知 TTS — 基于 beat 情感标签的逐段 prosody 速度/音高调整（intense/suspense/calm/twist/laughter）
- [x] BGM 动态过渡 — 情绪区间变化时增益调整与淡入淡出
- [x] 音频质量聚合 — 每段音频指标写入 metadata.json

### v0.5.10 — 字幕与翻译质量（2026-07-29）

- [x] 字幕 QA — CPS（字符/秒）、重叠检测、行长度合规
- [x] 翻译术语一致性 — 跨分块术语表提取与强制统一
- [x] 字幕显示长度校验 — 确保翻译后文本适配渲染区域
- [x] 双语字幕优化 — 双语模式行平衡
- [x] 标记未翻译行 — 输出元数据中标注回退原文的行

### v0.5.11 — 匹配与对齐精度（规划中）

- [ ] 词级对齐 — 利用 WhisperX 词时间戳实现子段精度
- [ ] 匹配质量评分聚合 — 跨 embedding + 节奏 + 多样性复合评分
- [ ] 场景去重检查 — 检测并惩罚跨段重复场景使用
- [ ] 收紧对齐漂移阈值 — 从 0.5 收紧至 0.3（基于 L2 测试数据）
- [ ] 对齐置信度评分 — 标记低置信度段供审查

### v0.5.12 — 全链路 QA 与质量总览（规划中）

- [ ] 中间产物 QA 门 — 渲染前检查音频、字幕、脚本质量
- [ ] 质量评分聚合 — metadata.json 中跨步骤质量总览
- [ ] 质量回归基线 — 跨运行追踪质量指标趋势
- [ ] 视频编码质量检查 — 码率、编码格式、分辨率验证
- [ ] QA 报告导出 — 随交付物输出结构化质量报告

## v0.6.x — Cloud

### v0.6.0 — 任务队列与异步 Job 系统 (2026-07-29)

- [x] 任务队列（异步 job 提交、进度轮询、重试）— `LocalTaskQueue` + `TaskQueue` 协议
- [x] 任务模型 — `TaskStatus`、`TaskPriority`、`TaskRequest`、`TaskProgress`、`TaskResult`、`Task` 完整生命周期
- [x] 任务持久化 — JSON 存储 `TaskStorage`，原子写入，线程安全，支持进程重启
- [x] 协作式取消 — `CancelController` 实现 `RunController` 协议，可中断重试休眠
- [x] 进度跟踪 — `ProgressConsole` 包装器拦截步骤事件，实时进度更新
- [x] 指数退避重试 — 错误类型匹配检测可重试错误，可中断休眠
- [x] CLI 任务队列命令 — `mn submit`、`mn status`、`mn tasks`、`mn cancel`、`mn wait`、`mn cleanup`
- [x] Contract 导出 — `CONTRACT_VERSION` 升至 `(0, 6, 0)`，cloud 类型通过 SDK 导出

### v0.6.1 — 远程推理 (2026-07-29)

- [x] REST API 服务器 — `TaskAPIServer`，基于 stdlib `http.server`，任务 CRUD + 产物下载端点
- [x] 远程任务队列客户端 — `RemoteTaskQueue` 通过 HTTP 实现 `TaskQueue` 协议，仅依赖 stdlib
- [x] Worker 守护进程 — `WorkerDaemon` / `run_daemon()` 组合 `LocalTaskQueue` + `TaskAPIServer`，优雅关闭
- [x] 产物管理 — `list_artifacts()`、`download_artifact()`、`download_all_artifacts()`，路径遍历保护
- [x] 远程 Provider 代理 — `register_remote_llm()` / `register_remote_tts()` 推理 offload 到远程 worker
- [x] CLI 远程命令 — `mn serve`、`mn download`、`--remote` 标志
- [x] Contract 导出 — `CONTRACT_VERSION` 升至 `(0, 6, 1)`，远程推理类型通过 SDK 导出

- [ ] 分布式渲染（将视频段分散到多节点）
- [ ] Web 服务部署（REST API、鉴权、多租户）—— 注：Web UI 本身现已是独立包（[movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)）；本条目关注云端部署/托管，而非 UI 代码库
