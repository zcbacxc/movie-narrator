# LLM 服务商导航

Movie Narrator 需要一个 LLM 后端来生成剧情研究和旁白剧本。所有服务商均提供 **OpenAI 兼容接口**，只需修改 `.env` 中的三个变量即可切换。

> 首次运行时，`~/.movie-narrator/.env` 会自动创建并填入默认配置。修改其中的 `MN_LLM_BASE_URL`、`MN_LLM_API_KEY`、`MN_LLM_MODEL` 三项即可。

## 服务商总览

| 服务商 | 免费额度 | 推荐模型 | TTS 支持 | 详情 |
|--------|---------|----------|---------|------|
| **Ollama** | 完全免费 | qwen2.5:7b | 无 | [查看指南](llm-providers/ollama.md) |
| **智谱 AI** | glm-4-flash 永久免费 + 2000 万 Tokens | glm-4-flash | 无 | [查看指南](llm-providers/zhipu.md) |
| **阿里云百炼** | 每模型 100 万 Tokens | qwen-plus | 无 | [查看指南](llm-providers/alibaba-bailian.md) |
| **小米 MiMo** | 限时免费 + 邀请码 ¥10 | mimo-v2.5-7b | 有（克隆/设计） | [查看指南](llm-providers/xiaomi-mimo.md) |
| **硅基流动** | 赠送额度 + 免费模型 | Qwen2.5-7B-Instruct | 无 | [查看指南](llm-providers/siliconflow.md) |

## 快速选择

### 没有显卡，想最快跑起来

**智谱 GLM-4-flash** — 免费不限量，注册即用，无需安装任何东西。点击 → [智谱指南](llm-providers/zhipu.md)

### 有独立显卡（≥8GB 显存）

**Ollama 本地部署** — 完全免费、离线可用、数据不出本机。点击 → [Ollama 指南](llm-providers/ollama.md)

### 想要最好的 TTS 效果（声音克隆/设计）

**小米 MiMo** — LLM + TTS 一站式，TTS 支持声音克隆和声音设计，限时免费。点击 → [MiMo 指南](llm-providers/xiaomi-mimo.md)

### 想同时用多个模型

**硅基流动** — 一个 API Key 调用 DeepSeek/Qwen/GLM 等数十个模型，部分模型完全免费。点击 → [硅基流动指南](llm-providers/siliconflow.md)

### 想用通义千问旗舰模型

**阿里云百炼** — qwen-max/qwen-plus/qwen-turbo 全系列，每模型 100 万 Tokens 免费。点击 → [百炼指南](llm-providers/alibaba-bailian.md)

## 推荐组合

| 场景 | LLM | TTS | 月成本 |
|------|-----|-----|--------|
| 零成本体验 | 智谱 glm-4-flash | Edge TTS | 0 元 |
| 最佳免费效果 | 智谱 glm-4-flash | MiMo TTS（限时免费） | 0 元 |
| 本地离线 | Ollama qwen2.5:7b | Edge TTS | 0 元 |
| 旗舰模型 | 百炼 qwen-max | MiMo TTS | 额度内 0 元 |

## 通用配置方法

无论选择哪个服务商，只需修改 `~/.movie-narrator/.env` 中的三项：

```env
MN_LLM_BASE_URL=<服务商的 OpenAI 兼容端点>
MN_LLM_API_KEY=<你的 API Key>
MN_LLM_MODEL=<模型名称>
```

修改后运行 `mn create --movie "满江红"` 即可测试。如果配置有误，preflight 预检会在流水线执行前报错并提示修复方向。
