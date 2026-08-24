[![English](https://img.shields.io/badge/English-Roadmap-blue)](ROADMAP.md)
[![简体中文](https://img.shields.io/badge/简体中文-路线图-green)](ROADMAP.zh-CN.md)

# 路线图

> 逐版本明细见 [CHANGELOG.md](../CHANGELOG.md)。配置参考见 [`.env.example`](../.env.example) 和 [`job.example.yaml`](../examples/job.example.yaml)。

## 已完成

| 版本     | 关键主题                                                                                                                                          |
| ------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| v0.1.x | 核心流水线 / CLI / LLM 解说稿 / Edge-TTS / SRT 字幕 / MoviePy 渲染 / TTS 缓存 / CI                                                                          |
| v0.2.x | 场景与媒体 / 研究 agent / WhisperX 对齐 / 场景检测 / 片段匹配 / BGM / 优雅降级                                                                                     |
| v0.3.x | 平台与工作流 / YAML job 配置 / 多语言字幕 / Gradio WebUI（后被取代）                                                                                             |
| v0.4.x | TTS 抽象与基础设施 / TTS provider 抽象 / 配置体系重做 / FastAPI + React WebUI / 渲染质量 / 匹配智能 / 效果组合 / 契约层                                                     |
| v0.5.x | 生态 / Plugin API / SDK 冻结 / 插件发现 / VLM 视觉 Provider / 叙事预设 / 场景过滤 / WebUI 拆分 / QA 仪表盘                                                           |
| v0.6.x | 任务队列与远程推理 / 异步 job / 持久化 / 取消 / 进度 / 重试 / REST API 服务器 / Worker 守护进程 / 产物管理 / 远程代理                                                            |
| v0.7.x | 出片体验 / GPU 编码 / 成本统计 / 预览模式 / 场景转场 / 文字动画 / 多音轨混音 / 安全加固                                                                                      |
| v0.8.x | 服务化基础 / API Key 鉴权 / video\_format 重命名 / 渲染模板 / 异常收窄 / 代码检查工具链 / 队列死锁修复                                                                       |
| v0.9.x | 可靠 / 批量 / 文档 / 熔断器 / 检查点 / 优雅关闭 / 重试策略 / 批量任务 / cron / 死信队列 / 分布式渲染 / 输入净化 / SAST / 覆盖率门禁 / 集成测试 / i18n / 语音映射 / 教程 / ADR / 迁移指南              |
| v1.0.x | **稳定发布** / API 冻结 / 稳定性保障 / 发布清单 / 最终文档审查 / 长期支持策略                                                                                            |
| v1.1.x | FunASR 中文 ASR / `mn doctor` / QA 幻灯片与黑场检测 / EmotionTrack / SQLite 任务存储 / 视觉嵌入 match 骨架 / timeline_export 插件 / 合规（edge-tts + TMDB）/ 90% 覆盖率门禁 |
| v1.2.x | 渲染子进程治理 / 原子产物发布 / 检查点指纹 / 孤儿任务恢复 / 非 loopback 强制鉴权 / 任务准入限制 / 结构化步骤日志 / 执行清单 / 生成 dry-run / Provider 重试治理 / TTS 缓存核算 / 竖版 QA 修复 |
| v1.2.1 | 持久化 GPU 能力缓存 / 编码器回退原因上报（含运行时 GPU→CPU 降级审计）                            |

`CONTRACT_VERSION`（当前）：`(1, 0, 0)`（v1.2 未变——无新增契约导出）

***

## 当前与规划

> **规划原则**：用户可感知的改善与基础设施交替交付。v1.0 目标用户：本地 CLI 创作者 + 可选单租户服务部署。1.x 系列引擎定位：可靠的单机 / 轻量服务化视频引擎 — 线性流水线 + 可恢复检查点 + 明确的产物契约 + 资源受控的渲染。分布式工作流引擎（Temporal / Celery）刻意后置，直到实测的队列延迟、渲染耗时、恢复成功率与重复 Provider 调用足以证明迁移成本的合理性。

### v1.3 — 工作流语义与产品化基础（下一版本）

> 主题：在保持线性执行的同时，为选择性重跑与产品级服务语义做准备。预计 `CONTRACT_VERSION` MINOR 提升（新增契约导出）。

#### 自 v1.2 转入（延后项）

- OpenTelemetry 追踪 — v1.2 改为交付结构化步骤日志；真正的 span 级追踪（任务 → 步骤/Provider/子进程）仍待推进。
- 硬件编码产品化 — v1.2 已统一 ffmpeg 探测；能力缓存与回退原因上报已随 v1.2.1 补丁交付，基准测试仍待推进。
- 提示词/脚本缓存 — 以规范化主题 / 风格 / 语言 / 提示模板版本 / 模型为键，记录命中来源。
- 资源感知准入 — 临时磁盘 / CPU / GPU / 分辨率检查，以及 GPU 与 CPU 队列分离。
- Provider 幂等键 — v1.2 因 LLM 输出非确定性决定不引入强幂等（已记录）；仅在重复计费可度量后重新评估。

#### v1.3 范围

- 选择性重跑 — 只重跑 `generate_voice` 或 `render_video`，自动失效下游步骤，无需重新研究。
- 线性兼容 DAG 契约 — 显式步骤输入 / 输出 / 产物键 / 依赖声明与线性适配器（暂不并行）。
- 版本化交付清单 — `deliverable_manifest.json` 声明 MP4 / 音频 / SRT / 脚本 / 片段 / 时间线 / 校验和 / 兼容版本。
- 看板契约 — 为外部 `movie-narrator-web` UI 提供稳定的清单 / API 面。
- Principal 与租户基础 — tenant/principal 贯穿任务、产物、缓存引用、审计与生命周期；全部路由鉴权。
- 套餐与权益 — 最大时长 / 分辨率 / 水印 / GPU 与 Provider 权限 / 输出格式 / TTL 建模。
- Webhook MVP — 签名事件、投递重试、幂等事件 ID 与投递记录（替代纯轮询）。
- 时间线导出收口 — `timeline_export_backend` 接入核心白名单并补充集成测试。
- 参考媒体输入契约 — `reference_media[]` 记录视频/图片类型、用途、版权来源与风格特征；基于 VLM Provider 的图像参考风格提示。

### 长期 — 架构延展（需求驱动）

仅依据真实指标（队列延迟、渲染耗时、恢复成功率、重复 Provider 调用、缓存命中率、磁盘/GPU 使用率）做承诺：

- Temporal 试点（Celery 备选）— 仅当多节点 worker、持久定时器、心跳、人工审批步骤或可重放执行历史成为真实需求时启动。
- HDR / 4K 管线 — 10-bit pix_fmt、profile、色彩 primaries/transfer/mastering 元数据、显存预算与 4K QA 基线（不只是改 `video_sizes`）。
- 可选软字幕 — `subtitle_delivery=burned|sidecar|muxed`，并进行 `mov_text` 兼容性测试。
- 扩展时间线适配器 — 在当前 OTIO + 剪映支持之外增加 Premiere XML。
- 素材缓存池 — 面向未来外部素材接入的 content-hash + TTL + 许可元数据缓存。

### 社区与 SaaS 生态（需求驱动）

以下特性保持在 v1.3 范围之外，仅在社区反馈和企业需求明确后推进：

- 社区预设分享 — `mn presets install <url>` 机制（依赖 contract 冻结后的稳定 API）
- Helm chart / K8s 部署模板 — 面向真正跑在 Kubernetes 上的团队
- 完整多租户隔离 — 租户隔离的任务存储与产物（基础由 v1.3 铺设）
- OAuth2 认证 — 面向 Web 客户端的完整认证流程（仅当有 SaaS 需求时）
- 令牌桶限流 — 按租户的请求限速（仅当有多用户部署需求时）

