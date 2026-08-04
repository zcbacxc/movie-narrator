[![English](https://img.shields.io/badge/English-Roadmap-blue)](ROADMAP.md)
[![简体中文](https://img.shields.io/badge/简体中文-路线图-green)](ROADMAP.zh-CN.md)

# 路线图

> 逐版本明细见 [CHANGELOG.md](../CHANGELOG.md)。配置参考见 [`.env.example`](../.env.example) 和 [`job.example.yaml`](../examples/job.example.yaml)。

## 已完成

| 版本 | 主题 | 摘要 |
|------|------|------|
| v0.1.x | 核心流水线 | CLI / LLM 解说稿 / Edge-TTS / SRT 字幕 / MoviePy 渲染 / TTS 缓存 / CI |
| v0.2.x | 场景与媒体 | 研究 agent / WhisperX 对齐 / 场景检测 / 片段匹配 / BGM / 优雅降级 |
| v0.3.x | 平台与工作流 | YAML job 配置 / 多语言字幕 / Gradio WebUI（后被取代） |
| v0.4.x | TTS 抽象与基础设施 | TTS provider 抽象 / 配置体系重做 / FastAPI + React WebUI / 渲染质量 / 匹配智能 / 效果组合 / 契约层 |
| v0.5.x | 生态 | Plugin API / SDK 冻结 / 插件发现 / VLM 视觉 Provider / 叙事预设 / 场景过滤 / WebUI 拆分 / QA 仪表盘。`CONTRACT_VERSION` → `(0, 5, 1)` |
| v0.6.x | 任务队列与远程推理 | 异步 job / 持久化 / 取消 / 进度 / 重试 / REST API 服务器 / Worker 守护进程 / 产物管理 / 远程代理。`CONTRACT_VERSION` → `(0, 6, 1)` |
| v0.7.x | 出片体验 | GPU 编码 / 成本统计 / 预览模式 / 场景转场 / 文字动画 / 多音轨混音 / 安全加固。`CONTRACT_VERSION` → `(0, 7, 2)` |
| v0.8.x | 服务化基础 | API Key 鉴权 / video_format 重命名 / 渲染模板 / 异常收窄 / 代码检查工具链 / 队列死锁修复。`CONTRACT_VERSION` → `(0, 8, 0)` |
| v0.9.x | 可靠、批量与文档 | 熔断器 / 检查点 / 优雅关闭 / 重试策略 / 批量任务 / cron / 死信队列 / 分布式渲染 / 输入净化 / SAST / 覆盖率门 / 集成测试 / i18n / 语音映射 / 教程 / ADR / 迁移指南。`CONTRACT_VERSION` → `(0, 9, 5)` |

---

## 当前与规划

> **规划原则**：用户可感知的改善与基础设施交替交付。v1.0 目标用户：本地 CLI 创作者 + 可选单租户服务部署。

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
