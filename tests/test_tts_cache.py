"""Tests for TTS cache key integrity and LRU eviction."""

from pathlib import Path

from movie_narrator.tts.cache import (
    CACHE_SCHEMA_VERSION,
    PROVIDER_CACHE_VERSIONS,
    TTSCacheKey,
    cache_path_for,
)


def test_provider_version_change_produces_different_key():
    """Changing provider_version must produce a different cache path.

    Regression guard: if PROVIDER_CACHE_VERSIONS is bumped but the key
    hash doesn't change, stale cache files from the old encoding would
    be served — silent cache poisoning.
    """
    root = Path("/tmp/cache_test")
    k1 = TTSCacheKey(
        schema_version=CACHE_SCHEMA_VERSION, provider="edge",
        provider_version=1, model="", voice="v", text="t", pause_ms=300,
    )
    k2 = TTSCacheKey(
        schema_version=CACHE_SCHEMA_VERSION, provider="edge",
        provider_version=2, model="", voice="v", text="t", pause_ms=300,
    )
    assert cache_path_for(root, k1) != cache_path_for(root, k2)


def test_text_change_produces_different_key():
    """Different text must produce a different cache path."""
    root = Path("/tmp/cache_test")
    k1 = TTSCacheKey(
        schema_version=CACHE_SCHEMA_VERSION, provider="edge",
        provider_version=1, model="", voice="v", text="hello", pause_ms=300,
    )
    k2 = TTSCacheKey(
        schema_version=CACHE_SCHEMA_VERSION, provider="edge",
        provider_version=1, model="", voice="v", text="world", pause_ms=300,
    )
    assert cache_path_for(root, k1) != cache_path_for(root, k2)


def test_provider_change_produces_different_key():
    """Switching providers must produce a different cache path."""
    root = Path("/tmp/cache_test")
    k1 = TTSCacheKey(
        schema_version=CACHE_SCHEMA_VERSION, provider="edge",
        provider_version=1, model="", voice="v", text="t", pause_ms=300,
    )
    k2 = TTSCacheKey(
        schema_version=CACHE_SCHEMA_VERSION, provider="openai",
        provider_version=1, model="tts-1", voice="v", text="t", pause_ms=300,
    )
    assert cache_path_for(root, k1) != cache_path_for(root, k2)


def test_provider_cache_versions_has_all_providers():
    """All three TTS providers must be present in PROVIDER_CACHE_VERSIONS."""
    assert set(PROVIDER_CACHE_VERSIONS.keys()) == {"edge", "openai", "mimo"}
