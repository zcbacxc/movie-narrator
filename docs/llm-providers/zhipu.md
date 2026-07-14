# 智谱 AI（GLM）— GLM-4-Flash 永久免费 + 新用户 2000 万 Tokens

## 简介

智谱 AI 是清华系大模型团队，GLM 系列是国内编码和推理能力第一梯队的模型。开放平台 BigModel 提供 OpenAI 兼容接口，新用户注册即送 **2000 万免费 Tokens**，且 GLM-4-Flash 模型**永久免费、不限量调用**。

## 注册流程

### 1. 访问平台

打开 [open.bigmodel.cn](https://open.bigmodel.cn)，点击右上角「注册」。

### 2. 完成注册

- 支持手机号 / 邮箱注册
- 完成实名认证（个人开发者选「个人认证」即可）
- 认证后自动获得免费额度

### 3. 创建 API Key

1. 登录后进入「API Keys」页面
2. 点击「添加新的 API Key」
3. 复制生成的 Key（格式形如 `xxxxxxxx.xxxxxxxx`）

### 4. 开通模型

在「模型广场」搜索并开通需要的模型（免费额度覆盖以下模型）：

- `glm-4-plus` — 旗舰模型，能力最强
- `glm-4-flash` — 轻量快速版，**永久免费、不限量**
- `glm-4-air` — 性价比版

> 推荐 `glm-4-flash`，永久免费且不限量，速度极快，适合 Movie Narrator 的剧本生成场景。

## 配置 Movie Narrator

编辑 `~/.movie-narrator/.env`：

```env
MN_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
MN_LLM_API_KEY=你的API Key
MN_LLM_MODEL=glm-4-flash
```

## 免费额度说明

| 模型 | 免费额度 | 有效期 |
|------|---------|--------|
| glm-4-flash | 不限量 | 永久免费 |
| glm-4-plus | 2000 万 Tokens | 注册赠送 |
| glm-4-air | 2000 万 Tokens | 注册赠送 |

> glm-4-flash 是目前国内唯一永久免费且不限量的旗舰级模型，强烈推荐作为 Movie Narrator 的默认 LLM。

## 优缺点

| 优点 | 缺点 |
|------|------|
| glm-4-flash 永久免费不限量 | 需要实名认证 |
| 2000 万 Tokens 新用户赠送 | 旗舰 glm-4-plus 额度用完后按量付费 |
| OpenAI 兼容接口 | — |
| 中文能力强 | — |

## TTS 额外说明

智谱目前不提供 TTS 服务。如需 TTS，请搭配 Edge TTS（免费）或 MiMo TTS 使用。
