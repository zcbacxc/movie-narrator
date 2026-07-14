# 小米 MiMo — LLM + TTS 一站式（限时免费）

## 简介

小米 MiMo 是小米旗下 AI 开放平台，同时提供 LLM（大语言模型）和 TTS（语音合成）服务。MiMo TTS 支持声音克隆和声音设计，是目前少数免费提供高级 TTS 能力的平台。LLM 和 TTS 均限时免费中。

Movie Narrator 已原生集成 MiMo TTS（三种模式：命名音色 / 声音克隆 / 声音设计）。

## 注册流程

### 1. 访问平台

打开 [platform.xiaomimimo.com](https://platform.xiaomimimo.com?ref=5MG8AD)，点击「注册」。

> 使用邀请码 **5MG8AD** 注册，双方各得 ¥10 API 体验金 + 首单 9 折（体验金 40 天有效）。通过上方链接注册会自动填入邀请码。

### 2. 完成注册

- 手机号注册
- 完成实名认证（个人开发者选「个人认证」）

### 3. 创建 API Key

1. 登录后进入控制台
2. 在「API Keys」页面点击「创建 API Key」
3. 复制生成的 Key（格式形如 `sk-xxxxxxxx`）

### 4. 查看可用模型

MiMo 平台提供以下模型（均为限时免费）：

**LLM（大语言模型）**：
- `mimo-v2.5-7b` — 基础对话模型

**TTS（语音合成）**：
- `mimo-v2.5-tts` — 命名音色（如 Chloe、Alice 等）
- `mimo-v2.5-tts-voiceclone` — 声音克隆（上传音频生成同款声音）
- `mimo-v2.5-tts-voicedesign` — 声音设计（文字描述生成音色）

## 配置 Movie Narrator

### 作为 LLM

编辑 `~/.movie-narrator/.env`：

```env
MN_LLM_BASE_URL=https://api.xiaomimimo.com/v1
MN_LLM_API_KEY=你的API Key
MN_LLM_MODEL=mimo-v2.5-7b
```

### 作为 TTS（推荐）

```env
MN_TTS_PROVIDER=mimo
MN_MIMO_TTS_MODEL=mimo-v2.5-tts
MN_MIMO_API_KEY=你的API Key
MN_MIMO_BASE_URL=https://api.xiaomimimo.com/v1
MN_MIMO_STYLE_PROMPT=Bright, bouncy, slightly sing-song tone.
MN_DEFAULT_VOICE=Chloe
```

> 如果 LLM 和 TTS 使用同一个 MiMo 账号，`MN_MIMO_API_KEY` 可省略，自动回退到 `MN_LLM_API_KEY`。

### 声音克隆模式

切换到声音克隆：

```env
MN_MIMO_TTS_MODEL=mimo-v2.5-tts-voiceclone
```

然后在 `--voice` 参数中传入音频文件路径，MiMo 会自动克隆该音频的声音特征。

### 声音设计模式

切换到声音设计：

```env
MN_MIMO_TTS_MODEL=mimo-v2.5-tts-voicedesign
```

在 `--voice` 参数中传入声音描述文字（如 "温柔女声，语速偏慢"），MiMo 会根据描述生成对应音色。

## 免费额度说明

| 服务 | 免费额度 | 有效期 |
|------|---------|--------|
| LLM (mimo-v2.5-7b) | 限时免费 | 官方公告为准 |
| TTS (mimo-v2.5-tts) | 限时免费 | 官方公告为准 |
| TTS (voiceclone) | 限时免费 | 官方公告为准 |
| TTS (voicedesign) | 限时免费 | 官方公告为准 |
| 邀请码奖励 (5MG8AD) | ¥10 体验金 + 首单 9 折 | 40 天 |

> MiMo 目前处于限时免费阶段，具体额度和截止时间请以 [platform.xiaomimimo.com](https://platform.xiaomimimo.com?ref=5MG8AD) 官方公告为准。

## 优缺点

| 优点 | 缺点 |
|------|------|
| LLM + TTS 一站式 | 限时免费，未来可能收费 |
| TTS 支持声音克隆和设计 | LLM 能力弱于智谱/百炼旗舰 |
| OpenAI 兼容接口 | 平台较新，稳定性待验证 |
| 中文 TTS 效果优秀 | — |

## 推荐组合

**最佳免费组合**：智谱 GLM-4-flash（LLM，免费不限量）+ MiMo TTS（语音合成，限时免费）
