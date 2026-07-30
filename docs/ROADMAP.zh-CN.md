[![English](https://img.shields.io/badge/English-Roadmap-blue)](ROADMAP.md)
[![简体中文](https://img.shields.io/badge/简体中文-路线图-green)](ROADMAP.zh-CN.md)

# 路线图

> 逐版本明细见 [CHANGELOG.md](../CHANGELOG.md)。配置参考见 [`.env.example`](../.env.example) 和 [`job.example.yaml`](../examples/job.example.yaml)。

## 已完成

| 版本 | 主题 | 摘要 |
|------|------|------|
| v0.1.x | 核心流水线 | CLI、LLM 解说稿、Edge-TTS、SRT 字幕、MoviePy 渲染、TTS 缓存、CI |
| v0.2.x | 场景与媒体 | 研究 agent、WhisperX 对齐、场景检测、片段匹配、BGM、优雅降级 |
| v0.3.x | 平台与工作流 | YAML job 配置、多语言字幕、Gradio WebUI（后被取代） |
| v0.4.x | TTS 抽象与基础设施 | TTS provider 抽象、配置体系重做、FastAPI + React WebUI、渲染质量、L2 手测通过、匹配智能、效果组合、契约层 |
| v0.5.x | 生态 | Plugin API / SDK 冻结 / 插件发现（entry_points）/ VLM 视觉 Provider / 叙事预设（3 种风格）/ 场景过滤 / WebUI 拆分 / 叙事与音频质量 / 字幕 QA / 全链路 QA 仪表盘。`CONTRACT_VERSION` → `(0, 5, 1)` |
| v0.6.0 | 任务队列 | 异步 job 系统、任务持久化、取消、进度跟踪、重试、CLI 命令。`CONTRACT_VERSION` → `(0, 6, 0)` |
| v0.6.1 | 远程推理 | REST API 服务器、远程任务队列、Worker 守护进程、产物管理、远程 Provider 代理、CLI 命令。`CONTRACT_VERSION` → `(0, 6, 1)` |

---

## 当前与规划

### v0.6.x — Cloud（续）

#### v0.6.2 — 分布式渲染（规划中）

- [ ] 视频分段切分 — 将渲染时间线切分为 N 个独立片段
- [ ] Worker 分发 — 通过任务队列将片段分发给可用 worker 节点
- [ ] 并行渲染 — 每个 worker 独立渲染各自分到的片段
- [ ] 结果拼接 — 使用 ffmpeg concat demuxer 将各片段输出拼接为最终视频
- [ ] `mn render-distributed` CLI 命令 — 通过 `--workers` 标志触发分布式渲染
- [ ] CONTRACT_VERSION → `(0, 6, 2)` — 分布式渲染类型通过 SDK 导出

#### v0.6.3 — API 网关与鉴权（规划中）

- [ ] API Key 鉴权 — 服务端 `X-API-Key` 请求头校验中间件（客户端 header 已由 `RemoteTaskQueue` / `remote_provider` 预留发送）
- [ ] JWT 令牌支持 — 为认证会话签发令牌
- [ ] 多租户隔离 — 租户隔离的任务存储与产物
- [ ] 限流 — 按租户的请求限速（令牌桶算法）
- [ ] API 版本控制 — `/api/v1/` 前缀用于稳定端点
- [ ] CONTRACT_VERSION → `(0, 6, 3)` — 鉴权中间件类型通过 SDK 导出

#### v0.6.4 — 云存储与产物管理（规划中）

- [ ] 存储后端抽象 — `StorageBackend` 协议（local / S3 / GCS）
- [ ] S3 兼容存储 — 产物上传/下载至 S3 存储桶
- [ ] 产物生命周期 — 基于 TTL 的清理、按租户的存储配额
- [ ] 预签名 URL — 支持 CDN 直链下载
- [ ] 存储迁移工具 — 本地 → 云端传输工具
- [ ] CONTRACT_VERSION → `(0, 6, 4)` — 存储后端类型通过 SDK 导出

### v0.7.x — 生产部署

> **目标**：通过容器化、可观测性和容错机制使引擎达到生产可用。
>
> **架构迁移说明**：当前云层使用 Python 标准库 `ThreadingHTTPServer` + `ThreadPoolExecutor`。在 v0.7.0 前需要决策是否为 K8s 部署目标引入 FastAPI（替换标准库 HTTP）和/或 Redis/Celery（替换 `ThreadPoolExecutor`）。此决策不需要单独版本号变更 — 属于 v0.7.0 内的实现选择。

#### v0.7.0 — 容器化与编排（规划中）

- [ ] Dockerfile — 多阶段构建（builder + runtime），支持 GPU
- [ ] docker-compose.yml — 本地集群（API + N workers + 存储）
- [ ] Helm chart — K8s 部署模板（worker deployment、API deployment、存储）
- [ ] Worker 自动伸缩 — 基于队列深度的 HPA
- [ ] ConfigMap/Secret 管理 — 从 K8s secrets 注入环境变量
- [ ] 健康/就绪探针 — `/ready` 端点 + 带依赖连通性的深度健康检查（`/health` 已在 v0.6.1 `TaskAPIServer` 中实现）

#### v0.7.1 — 可观测性与监控（规划中）

- [ ] Prometheus 指标 — `/metrics` 端点（任务数、队列深度、渲染时长、错误率）
- [ ] Grafana 仪表盘 — 预置仪表盘 JSON 模板
- [ ] 分布式追踪 — OpenTelemetry spans 用于跨节点操作
- [ ] 结构化日志聚合 — Loki/ELK 就绪的 JSON 日志，带关联 ID
- [ ] 告警规则 — 队列积压、worker 故障率、渲染超时

#### v0.7.2 — 可靠性与容错（规划中）

- [ ] 熔断器 — 针对外部 API（LLM、TTS、TMDB、VLM）
- [ ] 死信队列 — 失败任务转入 DLQ 供检查和重放
- [ ] 优雅关闭 — 退出前排空在途任务
- [ ] 任务检查点 — 为长时间运行的任务保存中间状态
- [ ] 重试策略框架 — 可配置的逐步骤重试策略（任务级带指数退避的重试已在 `cloud/worker.py` 中实现）
- [ ] 健康检查框架 — 依赖健康（LLM、TTS、存储）

### v0.8.x — 高级功能

> **目标**：为高级用户添加批量处理、高级渲染和多语言支持。

#### v0.8.0 — 批量处理与工作流编排（规划中）

- [ ] 批量任务提交 — 一次 API 请求提交 N 部影片
- [ ] 批量模板 — 系列、播放列表、主题合集
- [ ] 任务依赖 — 基于 DAG 的任务链（研究 → 脚本 → 渲染）
- [ ] 定时任务 — 基于 cron 的周期性任务提交
- [ ] 批量进度跟踪 — 跨子任务的聚合进度
- [ ] CONTRACT_VERSION → `(0, 8, 0)` — 批量工作流类型通过 SDK 导出

#### v0.8.1 — 高级渲染与特效（规划中）

- [ ] 场景转场 — 片段间的交叉淡入、硬切、擦除
- [ ] 多音轨 — 旁白 + BGM + 音效混音
- [ ] 画中画 — 反应/解说风格的叠加布局
- [ ] 文字动画 — 钩子句和标题的动态排版
- [ ] 自定义品牌 — 水印、Logo、片头/片尾卡
- [ ] 渲染预设分享格式 — 可分享的渲染配置

#### v0.8.2 — 多语言与国际化（规划中）

- [ ] 完整 i18n 流水线 — 语言感知的脚本生成与匹配
- [ ] 本地化 TTS 语音 — 按语言选择语音及回退
- [ ] 跨语言片段匹配 — 不论音频语言匹配片段
- [ ] 字幕翻译链 — 源语言 → 中间语言 → 目标语言
- [ ] Web UI 本地化 — movie-narrator-web 的 i18n 支持

### v0.9.x — 稳定化与打磨

> **目标**：优化性能、加固安全、完善文档，为 v1.0 就绪做准备。

#### v0.9.0 — 性能优化（规划中）

- [ ] 渲染流水线并行化 — 并发片段编码
- [ ] 内存优化 — 大视频的流式处理
- [ ] 缓存策略优化 — LLM 响应缓存、场景 embedding 缓存
- [ ] Worker 预热 — worker 启动时预加载模型
- [ ] 冷启动优化 — 重依赖的延迟初始化
- [ ] 基准测试套件 — 自动化性能回归测试

#### v0.9.1 — 安全加固（规划中）

- [ ] OAuth2 认证 — 面向 Web 客户端的完整 OAuth2 流程
- [ ] 输入净化 — 所有 API 输入的全面校验
- [ ] 租户隔离加固 — 存储路径隔离、资源配额
- [ ] 审计日志 — 所有 API 操作记录用于合规
- [ ] 密钥管理 — Vault / Sealed Secrets 集成
- [ ] 安全扫描 — 依赖审计、CI 中的 SAST

#### v0.9.2 — 文档与开发者体验（规划中）

- [ ] OpenAPI/Swagger 规范 — 自动生成的 API 文档
- [ ] 部署指南 — Docker、K8s、裸机教程
- [ ] 教程系列 — 从入门到高级的完整走查
- [ ] 架构决策记录 (ADR) — 关键设计决策文档化
- [ ] 迁移指南 — v0.x → v1.0 升级路径
- [ ] 集成测试套件 — 跨模块和端到端测试
- [ ] 测试覆盖率门 — CI 中强制 >95% 覆盖率

### v1.0.0 — 稳定发布

> **目标**：API 稳定性保证、生产就绪、目标用例功能完整。

- [ ] **CONTRACT_VERSION 冻结** → `(1, 0, 0)` — API 表面声明稳定
- [ ] **API 稳定性保证** — v1.x 不做破坏性变更，除非 v2.0
- [ ] **最终文档审查** — 所有文档审阅并保持最新
- [ ] **发布公告** — 变更日志、迁移指南、博客文章
- [ ] **长期支持策略** — v1.x 维护分支和补丁回溯规则
