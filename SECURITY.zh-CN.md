[![English](https://img.shields.io/badge/English-Security-blue)](SECURITY.md)
[![简体中文](https://img.shields.io/badge/简体中文-安全策略-green)](SECURITY.zh-CN.md)

# 安全策略

## 受支持的版本

仅最新发布版本接收安全更新。

| 版本 | 是否支持 |
|------|----------|
| latest | ✅ |
| < latest | ❌ |

## 报告漏洞

如果你发现了安全漏洞，请**不要**公开提交 Issue。

请通过私密渠道报告：

1. 前往 [Security Advisories](https://github.com/zcbacxc/movie-narrator/security/advisories/new) 页面
2. 点击 "Report a vulnerability"
3. 提供清晰的描述与复现步骤

你也可以发送邮件至：zcbacxc@users.noreply.github.com

### 响应时间线

- **确认收悉**：48 小时内
- **初步评估**：1 周内
- **修复或缓解**：关键问题目标 2 周

## 适用范围

本策略覆盖 `movie-narrator` 核心引擎包。对于 Web UI 包（[movie-narrator-web](https://github.com/zcbacxc/movie-narrator-web)），请在该仓库中报告问题。

## 不适用范围

- 用户配置中的 API 密钥泄露（用户责任）
- 第三方服务提供商的漏洞（OpenAI、Edge-TTS、TMDB 等）
- 通过 `[media]`、`[ml]` 或 `[full]` 额外依赖安装的可选组件的漏洞
