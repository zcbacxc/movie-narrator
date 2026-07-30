# Alibaba Cloud Bailian — Tongyi Qianwen Series (1M Tokens Free Per Model)

> **Note**: This is the English version. For the Chinese version, see [阿里云百炼](alibaba-bailian.zh-CN.md).

## Introduction

Alibaba Cloud Bailian is Alibaba's MaaS (Model-as-a-Service) platform, integrating the full Tongyi Qianwen (Qwen) model series and fully compatible with the OpenAI interface protocol. New users can claim **1 million Tokens of free quota per model**, valid for 3 months — suitable for project incubation.

## Registration Process

### 1. Visit the Platform

Open [bailian.console.aliyun.com](https://bailian.console.aliyun.com) and log in with an Alibaba Cloud account.

> If you don't have an Alibaba Cloud account, register one first (Alipay scan-to-login is supported).

### 2. Activate Bailian Service

On first entry, you will be prompted to "Activate Model Service"; click to confirm (free activation for individual developers, no payment required).

### 3. Claim Free Quota

Find the desired model in "Model Square" and click "Claim Free Quota":

- `qwen-plus` — balanced tier, 1M Tokens free (latest version qwen3.6-plus)
- `qwen-turbo` — fast tier, 1M Tokens free
- `qwen-max` — most powerful tier, 1M Tokens free (latest version qwen3-max)

> `qwen-plus` is recommended — a balance of capability and speed, well-suited for Movie Narrator's script generation. Just enter `qwen-plus` as the model name; Bailian will automatically route to the latest version.

### 4. Create an API Key

1. Go to the "API Key Management" page: [bailian.console.aliyun.com/?apiKey=1#/api-key](https://bailian.console.aliyun.com/?apiKey=1#/api-key)
2. Click "Create API Key"
3. Copy the generated Key (format like `sk-xxxxxxxx`)

## Configure Movie Narrator

Edit `~/.movie-narrator/.env`:

```env
MN_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MN_LLM_API_KEY=你的API Key
MN_LLM_MODEL=qwen-plus
```

> Note that the Base URL path includes `/compatible-mode/v1`; this is Bailian's OpenAI-compatible endpoint.

## Free Quota Details

| Model | Free Quota | Validity |
|------|---------|--------|
| qwen-plus | 1M Tokens | 3 months |
| qwen-turbo | 1M Tokens | 3 months |
| qwen-max | 1M Tokens | 3 months |

> Each model's quota is calculated independently. If qwen-plus runs out, you can switch to qwen-turbo to continue.

## Pros & Cons

| Pros | Cons |
|------|------|
| Strong Chinese language capabilities of Tongyi Qianwen | Limited free quota (1M Tokens/model) |
| OpenAI-compatible interface | 3-month validity |
| Stable and reliable Alibaba Cloud ecosystem | Requires an Alibaba Cloud account |
| Rich model selection | — |

## TTS Note

Alibaba Cloud Bailian also offers the CosyVoice speech synthesis service, but Movie Narrator does not currently integrate it. For TTS, use Edge TTS (free) or MiMo TTS.
