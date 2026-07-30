# AI Coding Assistant Guide

> 本页是面向 AI 编程工具（Claude Code、Codex、Cursor、Copilot 等）的中文导航索引。所有内容由对应权威文档维护，此处仅提供快速跳转。

## 快速入门

| 主题 | 文档 |
|------|------|
| 项目简介与安装 | [README](../README.md) / [README.zh-CN](../README.zh-CN.md) |
| 5 分钟快速开始 | [QUICKSTART.md](QUICKSTART.md) |
| LLM 服务商配置 | [LLM_PROVIDERS.md](LLM_PROVIDERS.md) |

## 架构与设计

| 主题 | 文档 |
|------|------|
| 系统架构与组件关系 | [ARCHITECTURE.zh-CN.md](ARCHITECTURE.zh-CN.md) |
| 元数据字段参考 | [METADATA_SCHEMA.md](METADATA_SCHEMA.md) |
| 16 步流水线与步骤职责 | [ARCHITECTURE.zh-CN.md § 流水线总览](ARCHITECTURE.zh-CN.md#流水线总览) |

## 插件与扩展

| 主题 | 文档 |
|------|------|
| 插件开发完整指南 | [PLUGIN_DEVELOPMENT.md](PLUGIN_DEVELOPMENT.md) |
| 打包与发布 | [PACKAGING.md](PACKAGING.md) |
| SDK API 参考 | [sdk/](sdk/contract.md) |

## 贡献与发布

| 主题 | 文档 |
|------|------|
| 贡献指南 | [CONTRIBUTING.zh-CN.md](CONTRIBUTING.zh-CN.md) |
| 版本路线图 | [ROADMAP.zh-CN.md](ROADMAP.zh-CN.md) |

## CLI 命令速查

```bash
mn create --movie "飞驰人生" --style "热血搞笑" --duration 60
mn create --config examples/job.example.yaml
CI=1 mn create --movie "CI-Test" --duration 10    # 离线冒烟测试
mn version                                         # 查看版本
mn --help                                          # 完整命令列表
```

> 任务队列命令（`mn submit` / `mn serve` / `mn status` 等）详见 `mn --help`。
