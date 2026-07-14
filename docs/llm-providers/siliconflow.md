# 硅基流动（SiliconFlow）— 多模型聚合，新用户赠送额度

## 简介

硅基流动是国内领先的 AI 模型聚合平台，一个 API Key 即可调用数十个开源大模型（DeepSeek、Qwen、GLM 等）。完全兼容 OpenAI 接口，新用户注册并完成实名认证后赠送**代金券额度**，部分模型完全免费。适合需要灵活切换不同模型的用户。

## 注册流程

### 1. 访问平台

打开 [cloud.siliconflow.cn](https://cloud.siliconflow.cn)，点击「注册」。

### 2. 完成注册

- 支持手机号 / 微信 / GitHub 注册
- 完成实名认证后获得赠送额度

### 3. 创建 API Key

1. 登录后进入「API 密钥」页面
2. 点击「新建 API 密钥」
3. 复制生成的 Key（格式形如 `sk-xxxxxxxx`）

### 4. 选择模型

硅基流动聚合了数十个模型，推荐以下免费或低价模型：

**免费模型**（不消耗额度，具体以平台为准）：
- `Qwen/Qwen2.5-7B-Instruct` — 通义千问 7B
- `deepseek-ai/DeepSeek-V2.5` — DeepSeek

**付费模型**（消耗赠送额度）：
- `Qwen/Qwen3.5-397B-A17B` — 通义千问旗舰
- `deepseek-ai/DeepSeek-V3.2` — DeepSeek 旗舰

> 免费模型不消耗额度，可长期使用。付费模型用赠送的代金券额度体验。模型列表会持续更新，以 [cloud.siliconflow.cn/models](https://cloud.siliconflow.cn/models) 为准。

## 配置 Movie Narrator

编辑 `~/.movie-narrator/.env`：

```env
MN_LLM_BASE_URL=https://api.siliconflow.cn/v1
MN_LLM_API_KEY=你的API Key
MN_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
```

> 模型名需要带组织前缀（如 `Qwen/Qwen2.5-7B-Instruct`），这是硅基流动的命名规范。

## 免费额度说明

| 类型 | 额度 | 有效期 |
|------|------|--------|
| 新用户赠送 | 代金券额度 | 以平台公告为准 |
| 免费模型（Qwen2.5-7B 等） | 不限量 | 长期免费 |
| 付费模型（旗舰级） | 消耗赠送额度 | 额度用完即止 |

## 优缺点

| 优点 | 缺点 |
|------|------|
| 一个 Key 调用数十个模型 | 免费模型能力有限（7B 级） |
| 部分模型完全免费 | 旗舰模型需付费 |
| OpenAI 兼容接口 | 模型名需带组织前缀 |
| 无需分别注册各家平台 | — |

## 推荐场景

如果你想同时体验 DeepSeek、通义千问、GLM 等多个模型，不想分别注册各家平台，硅基流动是最方便的聚合入口。
