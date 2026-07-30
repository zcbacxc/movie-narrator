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

> **规划原则**：用户可感知的改善与基础设施交替交付。v1.0 目标用户：本地 CLI 创作者 + 可选单租户服务部署。

### v0.6.x 规划调整说明

原 v0.6.2–v0.6.4（分布式渲染、API 网关与鉴权、云存储）经重新评估后调整：

- **分布式渲染** — 降级为 v0.9.0 条件特性（触发条件：单机渲染 > 10 分钟且有多节点可用）
- **API Key 鉴权 + S3 存储** — 并入 v0.8.0 服务化基础
- **JWT / 多租户隔离 / 令牌桶限流** — 推迟到 v1.0 后，视社区需求决定

---

### v0.7.0 — 出片体验（规划中）

> **目标**：让本地用户升级后立即感受到"出片更快、更好看"。

- [ ] 场景转场 — 片段间的交叉淡入、硬切、擦除
- [ ] 文字动画 — 钩子句和标题的动态排版
- [ ] 多音轨混音 — 旁白 + BGM + 音效分轨混合
- [ ] GPU 编码加速 — 自动检测 NVENC / VAAPI / VideoToolbox 可用性并切换编码器（`render_encoder` 参数）
- [ ] 渲染并行化 — 并发片段编码，缩短渲染总时长
- [ ] 预览模式 — 先渲染前 N 秒样片确认效果，再跑全片（`mn create --preview`）
- [ ] 内存优化 — 大视频的流式处理，避免 OOM
- [ ] 单次运行成本统计 — LLM token + TTS 调用费用汇总，写入 `metadata.json`
- [ ] CONTRACT_VERSION → `(0, 7, 0)` — 渲染与预览类型通过 SDK 导出

### v0.8.0 — 服务化基础（规划中）

> **目标**：能部署成一个靠谱的单租户服务，不过度设计。

- [ ] Dockerfile — 多阶段构建（builder + runtime），支持 GPU
- [ ] docker-compose.yml — 本地集群（API + N workers + 存储）
- [ ] API Key 鉴权 — 服务端 `X-API-Key` 校验中间件（客户端 header 已由 `RemoteTaskQueue` / `remote_provider` 预留发送）
- [ ] 存储后端抽象 — `StorageBackend` 协议（local / S3）
- [ ] 产物生命周期 — 基于 TTL 的清理
- [ ] 结构化日志 — JSON 格式，带关联 ID
- [ ] Prometheus 指标 — `/metrics` 端点（任务数、队列深度、渲染时长、错误率）
- [ ] 健康/就绪探针 — `/ready` 端点 + 带依赖连通性的深度健康检查（`/health` 已在 v0.6.1 实现）
- [ ] OpenAPI 规范 — 自动生成的 API 文档
- [ ] CONTRACT_VERSION → `(0, 8, 0)` — 服务化类型通过 SDK 导出

### v0.9.0 — 可靠性与批量（规划中）

> **目标**：长时间运行不丢任务，批量出片有调度。

- [ ] 熔断器 — 针对外部 API（LLM、TTS、TMDB、VLM）
- [ ] 任务检查点 — 为长时间运行的渲染保存中间状态，支持断点续跑
- [ ] 优雅关闭 — 退出前排空在途任务
- [ ] 重试策略框架 — 可配置的逐步骤重试策略（任务级指数退避已在 `cloud/worker.py` 实现）
- [ ] 批量任务提交 — 一次 API 请求提交 N 部影片
- [ ] 定时任务 — 基于 cron 的周期性任务提交
- [ ] 批量进度跟踪 — 跨子任务的聚合进度
- [ ] 死信队列 — 失败任务转入 DLQ 供检查和重放
- [ ] 分布式渲染（条件特性） — 触发条件：单机渲染 > 10 分钟且有多节点；基于 v0.8.0 容器化构建
- [ ] CONTRACT_VERSION → `(0, 9, 0)` — 可靠性与批量类型通过 SDK 导出

### v0.9.1 — 打磨与补全（规划中）

> **目标**：安全、国际化、文档全面就绪，为 v1.0 扫清障碍。

- [ ] 输入净化 — 所有 API 输入的全面校验
- [ ] 安全扫描 — 依赖审计、CI 中的 SAST
- [ ] 完整 i18n 流水线 — 语言感知的脚本生成与匹配
- [ ] 本地化 TTS 语音 — 按语言选择语音及回退
- [ ] Web UI 本地化 — movie-narrator-web 的 i18n 支持
- [ ] 教程系列 — 从入门到高级的完整走查
- [ ] 架构决策记录 (ADRs) — 关键设计决策文档化
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

---

### v1.0 后 — 社区生态（视需求决定）

以下特性不在 v1.0 范围内，根据社区反馈和企业需求决定是否推进：

- 社区预设分享 — `mn presets install <url>` 机制（依赖 contract 冻结后的稳定 API）
- Helm chart / K8s 部署模板 — 面向真正跑在 K8s 上的团队
- 多租户隔离 — 租户隔离的任务存储与产物（仅当有多用户部署需求时）
- OAuth2 认证 — 面向 Web 客户端的完整认证流程（仅当有 SaaS 需求时）
- 令牌桶限流 — 按租户的请求限速（仅当有多用户部署需求时）
