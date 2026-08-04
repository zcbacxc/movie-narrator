# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Language → default TTS voice mapping (v0.9.6).

Resolves the narration voice from the narration language so each language
gets a sensible default speaker, while still allowing the user to override
via an explicit per-job ``voice`` (metadata) or a per-language config option
(``voice_zh`` / ``voice_en``).

Mapping is provider-aware: keys are the provider names used by
``settings.tts_provider`` ("edge" / "openai" / "mimo"). Each language maps
to a provider-native voice id:
  - edge  → Microsoft Edge-TTS voice name (e.g. zh-CN-XiaoxiaoNeural)
  - openai → OpenAI voice (alloy / echo / fable / onyx / nova / shimmer)
  - mimo  → Xiaomi MiMo named voice (e.g. "Chloe")
"""

from __future__ import annotations

from typing import Optional

from ..config import Settings

# Language → provider → default voice id.
# ``lang`` is normalized to the ISO 639-1 two-letter code (lowercase).
DEFAULT_VOICE_MAP: dict[str, dict[str, str]] = {
    "zh": {
        "edge": "zh-CN-XiaoxiaoNeural",
        "openai": "nova",
        "mimo": "Chloe",
    },
    "en": {
        "edge": "en-US-AriaNeural",
        "openai": "nova",
        "mimo": "Chloe",
    },
}

# Provider names that take part in the language→voice map.
SUPPORTED_PROVIDERS: tuple[str, ...] = ("edge", "openai", "mimo")


def _norm_lang(lang: str) -> str:
    """Normalize a language tag to its ISO 639-1 two-letter code.

    Handles full tags like "zh-CN", "zh-Hans", "en-US" as well as bare
    "zh" / "en". Returns "" for empty or unrecognized input.
    """
    if not lang:
        return ""
    code = str(lang).strip().lower()
    return code.split("-")[0][:2] if code else ""


def resolve_voice(
    lang: str,
    provider: str,
    explicit_voice: Optional[str] = None,
    settings: Optional[Settings] = None,
) -> Optional[str]:
    """Resolve the TTS voice for a narration language.

    Priority (highest first):
      1. ``explicit_voice`` — an explicit per-job voice (metadata "voice").
      2. Per-language config override — ``settings.voice_zh`` / ``voice_en``
         (MN_VOICE_ZH / MN_VOICE_EN). Provider-agnostic, like ``default_voice``.
      3. Language default map — ``DEFAULT_VOICE_MAP[lang][provider]``.
      4. ``None`` — caller falls back to ``settings.default_voice``.

    Args:
        lang: narration language tag (e.g. "zh", "en", "zh-CN").
        provider: TTS provider name ("edge" / "openai" / "mimo").
        explicit_voice: optional per-job voice override.
        settings: optional Settings to consult for per-language config
            overrides.

    Returns:
        The resolved voice id, or ``None`` when no mapping applies.
    """
    if explicit_voice:
        return explicit_voice

    code = _norm_lang(lang)

    if settings is not None:
        if code == "zh" and settings.voice_zh:
            return settings.voice_zh
        if code == "en" and settings.voice_en:
            return settings.voice_en

    provider_map = DEFAULT_VOICE_MAP.get(code)
    if provider_map:
        return provider_map.get(provider)

    return None
