# Xiaomi MiMo — All-in-One LLM + TTS (Limited-Time Free)

> **Note**: This is the English version. For the Chinese version, see [小米 MiMo](xiaomi-mimo.zh-CN.md).

## Introduction

Xiaomi MiMo is Xiaomi's AI open platform, offering both LLM (large language model) and TTS (text-to-speech) services. MiMo TTS supports voice cloning and voice design, and is one of the few platforms that provides advanced TTS capabilities for free. Both LLM and TTS are currently available for free on a limited-time basis.

Movie Narrator has natively integrated MiMo TTS (three modes: named voice / voice cloning / voice design).

## Registration Process

### 1. Visit the Platform

Open [platform.xiaomimimo.com](https://platform.xiaomimimo.com?ref=5MG8AD) and click "Register".

> Register with invite code **5MG8AD** — both you and the referrer receive ¥10 API trial credit + 10% off your first order (trial credit valid for 40 days). Registering via the link above fills in the invite code automatically.

### 2. Complete Registration

- Register with a phone number
- Complete real-name verification (individual developers select "Personal Verification")

### 3. Create an API Key

1. After logging in, go to the console
2. On the "API Keys" page, click "Create API Key"
3. Copy the generated Key (format like `sk-xxxxxxxx`)

### 4. View Available Models

The MiMo platform offers the following models (all limited-time free):

**LLM (large language model)**:
- `mimo-v2.5-7b` — base conversational model

**TTS (text-to-speech)**:
- `mimo-v2.5-tts` — named voice (e.g., Chloe, Alice, etc.)
- `mimo-v2.5-tts-voiceclone` — voice cloning (upload audio to generate a matching voice)
- `mimo-v2.5-tts-voicedesign` — voice design (generate a voice from a text description)

## Configure Movie Narrator

### As an LLM

Edit `~/.movie-narrator/.env`:

```env
MN_LLM_BASE_URL=https://api.xiaomimimo.com/v1
MN_LLM_API_KEY=你的API Key
MN_LLM_MODEL=mimo-v2.5-7b
```

### As TTS (Recommended)

```env
MN_TTS_PROVIDER=mimo
MN_MIMO_TTS_MODEL=mimo-v2.5-tts
MN_MIMO_API_KEY=你的API Key
MN_MIMO_BASE_URL=https://api.xiaomimimo.com/v1
MN_MIMO_STYLE_PROMPT=Bright, bouncy, slightly sing-song tone.
MN_DEFAULT_VOICE=Chloe
```

> If LLM and TTS use the same MiMo account, `MN_MIMO_API_KEY` can be omitted; it automatically falls back to `MN_LLM_API_KEY`.

### Voice Cloning Mode

Switch to voice cloning:

```env
MN_MIMO_TTS_MODEL=mimo-v2.5-tts-voiceclone
```

Then pass an audio file path to the `--voice` argument, and MiMo will automatically clone the voice characteristics of that audio.

### Voice Design Mode

Switch to voice design:

```env
MN_MIMO_TTS_MODEL=mimo-v2.5-tts-voicedesign
```

Pass a voice description as text to the `--voice` argument (e.g., "gentle female voice, slower pace"), and MiMo will generate a corresponding voice based on the description.

## Free Quota Details

| Service | Free Quota | Validity |
|------|---------|--------|
| LLM (mimo-v2.5-7b) | Limited-time free | Per official announcements |
| TTS (mimo-v2.5-tts) | Limited-time free | Per official announcements |
| TTS (voiceclone) | Limited-time free | Per official announcements |
| TTS (voicedesign) | Limited-time free | Per official announcements |
| Invite code reward (5MG8AD) | ¥10 trial credit + 10% off first order | 40 days |

> MiMo is currently in a limited-time free phase. For the exact quota and end date, refer to the official announcements on [platform.xiaomimimo.com](https://platform.xiaomimimo.com?ref=5MG8AD).

## Pros & Cons

| Pros | Cons |
|------|------|
| All-in-one LLM + TTS | Limited-time free; may charge in the future |
| TTS supports voice cloning and design | LLM capability is weaker than Zhipu/Bailian flagships |
| OpenAI-compatible interface | Platform is relatively new; stability to be verified |
| Excellent Chinese TTS quality | — |

## TTS Note

MiMo provides both LLM and TTS services. To pair it with another LLM, see the recommended combinations in the [LLM Providers Guide](../LLM_PROVIDERS.md#recommended-combinations).
