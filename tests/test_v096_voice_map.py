# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the language→voice mapping (v0.9.6).

Covers:
  - per-language default mapping across providers (edge / openai / mimo)
  - explicit voice override (metadata "voice")
  - provider distinction
  - per-language config override (voice_zh / voice_en)
  - fallback to settings.default_voice when no mapping applies
"""

from movie_narrator.config import Settings
from movie_narrator.tts.voice_map import (
    DEFAULT_VOICE_MAP,
    resolve_voice,
)


# ── Default mapping per language & provider ────────────────
def test_default_mapping_zh():
    assert resolve_voice("zh", "edge") == "zh-CN-XiaoxiaoNeural"
    assert resolve_voice("zh", "openai") == "nova"
    assert resolve_voice("zh", "mimo") == "Chloe"


def test_default_mapping_en():
    assert resolve_voice("en", "edge") == "en-US-AriaNeural"
    assert resolve_voice("en", "openai") == "nova"
    assert resolve_voice("en", "mimo") == "Chloe"


def test_default_mapping_has_expected_zh_edge_voice():
    # Recommended mapping from the task: zh → edge zh-CN-XiaoxiaoNeural.
    assert DEFAULT_VOICE_MAP["zh"]["edge"] == "zh-CN-XiaoxiaoNeural"
    assert DEFAULT_VOICE_MAP["en"]["edge"] == "en-US-AriaNeural"


def test_language_tag_normalization():
    # Full tags normalize to the ISO 639-1 two-letter code.
    assert resolve_voice("zh-CN", "edge") == "zh-CN-XiaoxiaoNeural"
    assert resolve_voice("ZH-Hans", "edge") == "zh-CN-XiaoxiaoNeural"
    assert resolve_voice("en-US", "edge") == "en-US-AriaNeural"


# ── Provider distinction ───────────────────────────────────
def test_provider_distinction_for_zh():
    # Same language selects a different provider-native voice id.
    assert resolve_voice("zh", "edge") != resolve_voice("zh", "openai")
    assert resolve_voice("zh", "edge") == "zh-CN-XiaoxiaoNeural"
    assert resolve_voice("zh", "openai") == "nova"


def test_provider_distinction_for_en():
    assert resolve_voice("en", "edge") != resolve_voice("en", "openai")
    assert resolve_voice("en", "edge") == "en-US-AriaNeural"
    assert resolve_voice("en", "openai") == "nova"


# ── Explicit voice override ────────────────────────────────
def test_explicit_voice_overrides_default_map():
    assert resolve_voice("zh", "edge", explicit_voice="custom-voice") == "custom-voice"
    assert resolve_voice("en", "edge", explicit_voice="custom-voice") == "custom-voice"


def test_explicit_voice_overrides_config_override():
    settings = Settings(voice_zh="zh-CN-QingyangNeural")
    assert resolve_voice("zh", "edge", explicit_voice="custom", settings=settings) == "custom"


# ── Config override ────────────────────────────────────────
def test_config_override_beats_default_map():
    settings = Settings(voice_zh="zh-CN-QingyangNeural")
    assert resolve_voice("zh", "edge", settings=settings) == "zh-CN-QingyangNeural"
    assert resolve_voice("zh", "openai", settings=settings) == "zh-CN-QingyangNeural"


def test_config_override_en():
    settings = Settings(voice_en="en-GB-SoniaNeural")
    assert resolve_voice("en", "edge", settings=settings) == "en-GB-SoniaNeural"


def test_config_override_unset_uses_default_map():
    settings = Settings()
    assert resolve_voice("zh", "edge", settings=settings) == "zh-CN-XiaoxiaoNeural"
    assert resolve_voice("en", "edge", settings=settings) == "en-US-AriaNeural"


# ── Fallback to default_voice ──────────────────────────────
def test_unknown_language_returns_none():
    assert resolve_voice("fr", "edge") is None
    assert resolve_voice("ja", "openai") is None
    assert resolve_voice("", "edge") is None


def test_unknown_provider_returns_none():
    assert resolve_voice("zh", "untested-provider") is None


def test_fallback_to_default_voice():
    # Mirrors the pipeline expression: resolve_voice(...) or settings.default_voice.
    settings = Settings(default_voice="zh-CN-YunxiNeural")
    voice = resolve_voice("fr", "edge", settings=settings) or settings.default_voice
    assert voice == "zh-CN-YunxiNeural"
