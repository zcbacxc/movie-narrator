# Zhipu AI (GLM) — GLM-4-Flash Free Forever + 20M Tokens for New Users

> **Note**: This is the English version. For the Chinese version, see [智谱 AI](zhipu.zh-CN.md).

## Introduction

Zhipu AI is a Tsinghua-origin large model team. The GLM series ranks among China's top-tier models in coding and reasoning capabilities. Its open platform BigModel provides an OpenAI-compatible interface. New users receive **20 million free Tokens** upon registration, and the GLM-4-Flash model is **free forever with unlimited calls**.

## Registration Process

### 1. Visit the Platform

Open [open.bigmodel.cn](https://open.bigmodel.cn) and click "Register" in the top-right corner.

### 2. Complete Registration

- Supports phone number / email registration
- Complete real-name verification (individual developers select "Personal Verification")
- Free quota is granted automatically after verification

### 3. Create an API Key

1. After logging in, go to the "API Keys" page
2. Click "Add New API Key"
3. Copy the generated Key (format like `xxxxxxxx.xxxxxxxx`)

### 4. Enable Models

Search for and enable the desired models in "Model Square" (the free quota covers the following models):

- `glm-4-plus` — flagship model, strongest capabilities
- `glm-4-flash` — lightweight and fast version, **free forever, unlimited**
- `glm-4-air` — cost-effective version

> `glm-4-flash` is recommended — free forever and unlimited, extremely fast, and well-suited for Movie Narrator's script generation scenarios.

## Configure Movie Narrator

Edit `~/.movie-narrator/.env`:

```env
MN_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
MN_LLM_API_KEY=你的API Key
MN_LLM_MODEL=glm-4-flash
```

## Free Quota Details

| Model | Free Quota | Validity |
|------|---------|--------|
| glm-4-flash | Unlimited | Free forever |
| glm-4-plus | 20M Tokens | Registration bonus |
| glm-4-air | 20M Tokens | Registration bonus |

> glm-4-flash is currently the only flagship-level model in China that is permanently free and unlimited. It is highly recommended as the default LLM for Movie Narrator.

## Pros & Cons

| Pros | Cons |
|------|------|
| glm-4-flash is free forever and unlimited | Requires real-name verification |
| 20M Tokens bonus for new users | Flagship glm-4-plus is pay-per-use after quota is exhausted |
| OpenAI-compatible interface | — |
| Strong Chinese language capabilities | — |

## TTS Note

Zhipu does not currently offer TTS services. For TTS, use Edge TTS (free) or MiMo TTS.
