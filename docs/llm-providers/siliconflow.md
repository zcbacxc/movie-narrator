[![English](https://img.shields.io/badge/English-SiliconFlow-blue)](siliconflow.md)
[![简体中文](https://img.shields.io/badge/简体中文-硅基流动-green)](siliconflow.zh-CN.md)

# SiliconFlow — Multi-Model Aggregator with Signup Credits

> **Note**: This is the English version. For the Chinese version, see [硅基流动](siliconflow.zh-CN.md).

## Introduction

SiliconFlow is a leading Chinese AI model aggregation platform. A single API Key lets you call dozens of open-source large models (DeepSeek, Qwen, GLM, etc.). It is fully OpenAI-compatible. New users receive **voucher credits** after registration and real-name verification, and some models are completely free. Suitable for users who need to flexibly switch between different models.

## Registration Process

### 1. Visit the Platform

Open [cloud.siliconflow.cn](https://cloud.siliconflow.cn) and click "Register".

### 2. Complete Registration

- Supports phone number / WeChat / GitHub registration
- Complete real-name verification to receive bonus credits

### 3. Create an API Key

1. After logging in, go to the "API Keys" page
2. Click "Create API Key"
3. Copy the generated Key (format like `sk-xxxxxxxx`)

### 4. Select a Model

SiliconFlow aggregates dozens of models. The following free or low-cost models are recommended:

**Free models** (do not consume credits, subject to platform updates):
- `Qwen/Qwen2.5-7B-Instruct` — Qwen 7B
- `deepseek-ai/DeepSeek-V2.5` — DeepSeek

**Paid models** (consume bonus credits):
- `Qwen/Qwen3.5-397B-A17B` — Qwen flagship
- `deepseek-ai/DeepSeek-V3.2` — DeepSeek flagship

> Free models do not consume credits and can be used long-term. Paid models can be tried using the bonus voucher credits. The model list is continuously updated; refer to [cloud.siliconflow.cn/models](https://cloud.siliconflow.cn/models) for the latest.

## Configure Movie Narrator

Edit `~/.movie-narrator/.env`:

```env
MN_LLM_BASE_URL=https://api.siliconflow.cn/v1
MN_LLM_API_KEY=你的API Key
MN_LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
```

> Model names must include the organization prefix (e.g., `Qwen/Qwen2.5-7B-Instruct`); this is SiliconFlow's naming convention.

## Free Quota Details

| Type | Quota | Validity |
|------|------|--------|
| New user bonus | Voucher credits | Per platform announcements |
| Free models (Qwen2.5-7B, etc.) | Unlimited | Free long-term |
| Paid models (flagship tier) | Consumes bonus credits | Until credits are exhausted |

## Pros & Cons

| Pros | Cons |
|------|------|
| One Key to call dozens of models | Free models have limited capability (7B tier) |
| Some models are completely free | Flagship models require payment |
| OpenAI-compatible interface | Model names require organization prefix |
| No need to register with each platform separately | — |

## TTS Note

SiliconFlow does not currently offer TTS services. For TTS, use Edge TTS (free) or MiMo TTS.
