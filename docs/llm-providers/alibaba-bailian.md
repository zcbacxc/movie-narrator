# 阿里云百炼（通义千问）— 新用户每个模型 100 万 Tokens

## 简介

阿里云百炼是阿里旗下 MaaS（模型即服务）平台，集成了通义千问全系列模型，完全兼容 OpenAI 接口协议。新用户每个模型可领取 **100 万 Tokens 免费额度**，有效期 3 个月，适合项目孵化。

## 注册流程

### 1. 访问平台

打开 [bailian.console.aliyun.com](https://bailian.console.aliyun.com)，使用阿里云账号登录。

> 如果没有阿里云账号，先注册一个（支持支付宝扫码登录）。

### 2. 开通百炼服务

首次进入会提示「开通模型服务」，点击确认即可（个人开发者免费开通，无需付费）。

### 3. 领取免费额度

在「模型广场」中找到需要的模型，点击「领取免费额度」：

- `qwen-plus` — 均衡型，100 万 Tokens 免费（最新版 qwen3.6-plus）
- `qwen-turbo` — 快速版，100 万 Tokens 免费
- `qwen-max` — 最强版本，100 万 Tokens 免费（最新版 qwen3-max）

> 推荐使用 `qwen-plus`，能力与速度均衡，适合 Movie Narrator 的剧本生成。模型名直接填 `qwen-plus` 即可，百炼会自动路由到最新版本。

### 4. 创建 API Key

1. 进入「API Key 管理」页面：[bailian.console.aliyun.com/?apiKey=1#/api-key](https://bailian.console.aliyun.com/?apiKey=1#/api-key)
2. 点击「创建 API Key」
3. 复制生成的 Key（格式形如 `sk-xxxxxxxx`）

## 配置 Movie Narrator

编辑 `~/.movie-narrator/.env`：

```env
MN_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MN_LLM_API_KEY=你的API Key
MN_LLM_MODEL=qwen-plus
```

> 注意 Base URL 路径包含 `/compatible-mode/v1`，这是百炼的 OpenAI 兼容端点。

## 免费额度说明

| 模型 | 免费额度 | 有效期 |
|------|---------|--------|
| qwen-plus | 100 万 Tokens | 3 个月 |
| qwen-turbo | 100 万 Tokens | 3 个月 |
| qwen-max | 100 万 Tokens | 3 个月 |

> 每个模型独立计算额度。如果 qwen-plus 用完了，可以切换到 qwen-turbo 继续。

## 优缺点

| 优点 | 缺点 |
|------|------|
| 通义千问中文能力强 | 免费额度有限（100 万 Tokens/模型） |
| OpenAI 兼容接口 | 3 个月有效期 |
| 阿里云生态稳定可靠 | 需要阿里云账号 |
| 模型选择丰富 | — |

## TTS 额外说明

阿里云百炼也提供 CosyVoice 语音合成服务，但 Movie Narrator 目前未集成。TTS 请使用 Edge TTS（免费）或 MiMo TTS。
