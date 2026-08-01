[![English](https://img.shields.io/badge/English-LLM_Providers-blue)](LLM_PROVIDERS.md)
[![简体中文](https://img.shields.io/badge/简体中文-LLM服务商-green)](LLM_PROVIDERS.zh-CN.md)

# LLM Providers Guide

Movie Narrator requires an LLM backend to generate plot research and narration scripts. All providers offer an **OpenAI-compatible API** — simply change three variables in `.env` to switch between them.

> On first run, `~/.movie-narrator/.env` is automatically created and populated with default configuration. Just modify the `MN_LLM_BASE_URL`, `MN_LLM_API_KEY`, and `MN_LLM_MODEL` entries.

## Provider Overview

| Provider | Free Tier | Recommended Model | TTS Support | Guide |
|----------|-----------|-------------------|-------------|-------|
| **Ollama** | Completely free | qwen2.5:7b | None | [View Guide](llm-providers/ollama.md) |
| **Zhipu AI** | glm-4-flash free forever + 20M Tokens | glm-4-flash | None | [View Guide](llm-providers/zhipu.md) |
| **Alibaba Cloud Bailian** | 1M Tokens per model | qwen-plus | None | [View Guide](llm-providers/alibaba-bailian.md) |
| **Xiaomi MiMo** | Limited-time free + invite code ¥10 | mimo-v2.5-7b | Yes (clone/design) | [View Guide](llm-providers/xiaomi-mimo.md) |
| **SiliconFlow** | Bonus credits + free models | Qwen2.5-7B-Instruct | None | [View Guide](llm-providers/siliconflow.md) |

## Quick Selection

### No GPU, want to get running fastest

**Zhipu GLM-4-flash** — Free and unlimited, ready to use upon registration, no installation required. Click → [Zhipu Guide](llm-providers/zhipu.md)

### Have a dedicated GPU (≥8GB VRAM)

**Ollama local deployment** — Completely free, works offline, data never leaves your machine. Click → [Ollama Guide](llm-providers/ollama.md)

### Want the best TTS experience (voice cloning/design)

**Xiaomi MiMo** — All-in-one LLM + TTS, TTS supports voice cloning and voice design, limited-time free. Click → [MiMo Guide](llm-providers/xiaomi-mimo.md)

### Want to use multiple models at once

**SiliconFlow** — One API Key to call dozens of models including DeepSeek/Qwen/GLM, with some models completely free. Click → [SiliconFlow Guide](llm-providers/siliconflow.md)

### Want to use the flagship Tongyi Qianwen models

**Alibaba Cloud Bailian** — Full series of qwen-max/qwen-plus/qwen-turbo, 1M Tokens free per model. Click → [Bailian Guide](llm-providers/alibaba-bailian.md)

## Recommended Combinations

| Scenario | LLM | TTS | Monthly Cost |
|----------|-----|-----|--------------|
| Zero-cost experience | Zhipu glm-4-flash | Edge TTS | ¥0 |
| Best free quality | Zhipu glm-4-flash | MiMo TTS (limited-time free) | ¥0 |
| Local offline | Ollama qwen2.5:7b | Edge TTS | ¥0 |
| Flagship model | Bailian qwen-max | MiMo TTS | ¥0 within quota |

## General Configuration

Regardless of which provider you choose, simply modify the three entries in `~/.movie-narrator/.env`:

```env
MN_LLM_BASE_URL=<provider's OpenAI-compatible endpoint>
MN_LLM_API_KEY=<your API Key>
MN_LLM_MODEL=<model name>
```

After modifying, run `mn create --movie "Full River Red"` to test. If the configuration is incorrect, the preflight check will report an error before the pipeline executes and suggest how to fix it.
