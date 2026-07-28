"""Edge-case tests for v0.5.6 features.

Covers boundary behaviour of the four v0.5.6 additions:

1. **Narrator perspective** (``build_perspective_hint``) — default
   omniscient, character-without-focus degradation, invalid values,
   and focus_character-only without perspective.
2. **Platform tone** (``build_platform_tone_hint``) — douyin, invalid,
   and None platform values.
3. **Language chain** (``merge_job``) — lang=None backward compat and
   lang="en" propagation.
4. **Retryable error classification** (``is_network_error`` /
   ``ProviderError.retryable``) — network vs non-network errors.

All tests use mocks — no real LLM or network calls are made.
"""

from unittest.mock import MagicMock

import pytest

from movie_narrator.config import Settings
from movie_narrator.utils.prompts import (
    PLATFORM_TONE,
    build_perspective_hint,
    build_platform_tone_hint,
)
from movie_narrator.workflow.errors import (
    ProviderError,
    is_network_error,
)
from movie_narrator.workflow.merge import merge_job
from movie_narrator.workflow.schema import JobConfig, JobParams


# ── Shared helper (mirrors test_workflow_merge.py style) ───


def _cli(**overrides):
    """Build a CLI args dict matching merge_job's expected shape."""
    base = {
        "movie": None,
        "style": "热血搞笑",
        "duration": 60,
        "voice": None,
        "format": "16:9",
        "keep_cache": False,
        "video": None,
        "library_dir": None,
        "research": None,
        "bgm": None,
        "no_bgm": False,
        "no_clips": False,
        "strict": False,
        "config_path": None,
    }
    base.update(overrides)
    return base


# ────────────────────────────────────────────────────────────
# 1. Narrator Perspective
# ────────────────────────────────────────────────────────────


class TestNarratorPerspective:
    """Edge cases for build_perspective_hint (NA-M1-S4)."""

    def test_perspective_omniscient_default(self):
        """Default 'omniscient' perspective injects no extra hint."""
        hint = build_perspective_hint("omniscient")
        assert hint == ""

    def test_perspective_omniscient_explicit_with_focus(self):
        """Even with a focus_character, omniscient injects no hint."""
        hint = build_perspective_hint("omniscient", "Alice")
        assert hint == ""

    def test_perspective_empty_string_no_hint(self):
        """Empty perspective string is treated as omniscient (no hint)."""
        assert build_perspective_hint("") == ""

    def test_perspective_character_without_focus(self):
        """character mode without focus_character degrades to protagonist hint."""
        hint = build_perspective_hint("character", "")
        assert hint != ""
        assert "character" in hint.lower()
        assert "protagonist" in hint.lower()

    def test_perspective_character_with_focus(self):
        """character mode with focus_character anchors to that character."""
        hint = build_perspective_hint("character", "Wang")
        assert "Wang" in hint
        assert "viewpoint" in hint.lower()

    def test_perspective_invalid_value(self):
        """Invalid perspective value returns empty string (backward compat)."""
        hint = build_perspective_hint("first-person")
        assert hint == ""

    def test_perspective_detective(self):
        """detective perspective produces a mystery-style hint."""
        hint = build_perspective_hint("detective")
        assert hint != ""
        assert "mystery" in hint.lower() or "clue" in hint.lower()

    def test_focus_character_only_without_perspective(self):
        """focus_character without a character perspective is ignored."""
        hint = build_perspective_hint("", "John")
        assert hint == ""

    def test_focus_character_only_with_omniscient(self):
        """focus_character with omniscient perspective is also ignored."""
        hint = build_perspective_hint("omniscient", "John")
        assert hint == ""

    def test_perspective_none_input(self):
        """None perspective is handled gracefully (treated as default)."""
        assert build_perspective_hint(None) == ""

    def test_perspective_case_insensitive(self):
        """Perspective matching is case-insensitive."""
        hint = build_perspective_hint("CHARACTER", "Bob")
        assert "Bob" in hint

    def test_perspective_whitespace_trimmed(self):
        """Perspective with surrounding whitespace is trimmed."""
        hint = build_perspective_hint("  detective  ")
        assert hint != ""
        assert "mystery" in hint.lower() or "clue" in hint.lower()


# ────────────────────────────────────────────────────────────
# 2. Platform Tone
# ────────────────────────────────────────────────────────────


class TestPlatformTone:
    """Edge cases for build_platform_tone_hint (NA-M1-S2)."""

    def test_platform_douyin(self):
        """target_platform=douyin produces the douyin tone hint."""
        hint = build_platform_tone_hint("douyin")
        assert hint != ""
        assert "douyin" in hint.lower() or "抖音" in hint
        # Verify it matches the PLATFORM_TONE dict entry
        assert hint == PLATFORM_TONE["douyin"]

    def test_platform_bilibili(self):
        """target_platform=bilibili produces the bilibili tone hint."""
        hint = build_platform_tone_hint("bilibili")
        assert hint != ""
        assert "bilibili" in hint.lower() or "b站" in hint.lower()

    def test_platform_youtube(self):
        """target_platform=youtube produces the youtube tone hint."""
        hint = build_platform_tone_hint("youtube")
        assert hint != ""
        assert "youtube" in hint.lower()

    def test_platform_invalid(self):
        """Invalid platform value returns empty string (graceful degradation)."""
        hint = build_platform_tone_hint("tiktok")
        assert hint == ""

    def test_platform_none(self):
        """platform=None returns empty string (default behaviour)."""
        assert build_platform_tone_hint(None) == ""

    def test_platform_empty_string(self):
        """Empty platform string returns empty string."""
        assert build_platform_tone_hint("") == ""

    def test_platform_all_known_platforms_non_empty(self):
        """All keys in PLATFORM_TONE produce non-empty hints."""
        for platform in PLATFORM_TONE:
            hint = build_platform_tone_hint(platform)
            assert hint != "", f"Platform '{platform}' produced empty hint"


# ────────────────────────────────────────────────────────────
# 3. Language Chain
# ────────────────────────────────────────────────────────────


class TestLanguageChain:
    """Edge cases for lang parameter merging (R2-NA-LANG)."""

    def test_lang_not_set_when_none(self):
        """lang=None (neither CLI nor YAML) does not write to params.

        Backward compatibility: the default 'zh' is handled by
        build_context's parameter default, not by inserting 'lang'
        into the params dict.
        """
        r = merge_job(_cli(movie="M"), None, Settings())
        assert "lang" not in r.params
        # ResolvedJob.lang always has a value (default "zh")
        assert r.lang == "zh"

    def test_lang_set_propagates(self):
        """lang='en' from CLI propagates to both params and ResolvedJob.lang."""
        r = merge_job(_cli(movie="M", lang="en"), None, Settings())
        assert r.params.get("lang") == "en"
        assert r.lang == "en"

    def test_lang_yaml_propagates(self):
        """lang from YAML propagates to both params and ResolvedJob.lang."""
        job = JobConfig(movie="M", lang="ja")
        r = merge_job(_cli(config_path="job.yaml"), job, Settings())
        assert r.params.get("lang") == "ja"
        assert r.lang == "ja"

    def test_lang_cli_overrides_yaml(self):
        """CLI lang overrides YAML lang."""
        job = JobConfig(movie="M", lang="ja")
        r = merge_job(_cli(lang="en", config_path="job.yaml"), job, Settings())
        assert r.lang == "en"
        assert r.params.get("lang") == "en"

    def test_lang_uppercase_normalized(self):
        """lang is lowercased before storage."""
        r = merge_job(_cli(movie="M", lang="EN"), None, Settings())
        assert r.params.get("lang") == "en"
        assert r.lang == "en"

    def test_lang_whitespace_stripped(self):
        """lang with surrounding whitespace is stripped."""
        r = merge_job(_cli(movie="M", lang="  en  "), None, Settings())
        assert r.params.get("lang") == "en"

    def test_lang_empty_string_falls_back_to_zh(self):
        """Empty lang string falls back to 'zh' default and is not in params."""
        r = merge_job(_cli(movie="M", lang=""), None, Settings())
        assert "lang" not in r.params
        assert r.lang == "zh"


# ────────────────────────────────────────────────────────────
# 4. Retryable Error Classification
# ────────────────────────────────────────────────────────────


class TestRetryableError:
    """Edge cases for retryable error classification (R2-NA-ORCH)."""

    def test_retryable_error_classification(self):
        """Network errors are classified as retryable by is_network_error."""
        assert is_network_error(ConnectionError("connection refused")) is True
        assert is_network_error(TimeoutError("timed out")) is True
        # ConnectionRefusedError is a subclass of ConnectionError
        assert is_network_error(ConnectionRefusedError()) is True
        # ConnectionResetError is also a subclass
        assert is_network_error(ConnectionResetError()) is True

    def test_non_retryable_not_classified(self):
        """Non-network errors are not classified as retryable."""
        assert is_network_error(ValueError("bad value")) is False
        assert is_network_error(RuntimeError("generic error")) is False
        assert is_network_error(KeyError("missing")) is False
        assert is_network_error(TypeError("wrong type")) is False

    def test_provider_error_retryable_flag_true(self):
        """ProviderError with retryable=True sets the flag correctly."""
        err = ProviderError("network timeout", retryable=True)
        assert err.retryable is True
        assert "network timeout" in str(err)

    def test_provider_error_retryable_flag_default_false(self):
        """ProviderError defaults to retryable=False (backward compat)."""
        err = ProviderError("bad config")
        assert err.retryable is False

    def test_provider_error_not_classified_as_network(self):
        """ProviderError itself is not a network-type error by isinstance."""
        err = ProviderError("some error", retryable=True)
        # is_network_error checks the exception TYPE, not the retryable flag
        assert is_network_error(err) is False

    def test_job_config_error_not_retryable(self):
        """JobConfigError inherits retryable=False from ProviderError."""
        from movie_narrator.workflow.errors import JobConfigError

        err = JobConfigError("invalid subtitle_mode")
        assert err.retryable is False
        assert isinstance(err, ProviderError)

    def test_openai_transient_errors_classified(self):
        """OpenAI SDK transient errors are classified as retryable.

        The openai package is a required dependency, so this test
        verifies that APITimeoutError, APIConnectionError, and
        RateLimitError are all recognised by is_network_error.
        """
        try:
            from openai import (
                APITimeoutError,
                APIConnectionError,
                RateLimitError,
            )
        except ImportError:
            pytest.skip("openai SDK not available")

        # APITimeoutError requires a request argument
        timeout_err = APITimeoutError(request=MagicMock())
        assert is_network_error(timeout_err) is True

        # APIConnectionError
        conn_err = APIConnectionError(request=MagicMock())
        assert is_network_error(conn_err) is True

    def test_openai_rate_limit_classified(self):
        """OpenAI RateLimitError is classified as retryable."""
        try:
            from openai import RateLimitError
        except ImportError:
            pytest.skip("openai SDK not available")

        # Construct a minimal RateLimitError
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {}
        rate_err = RateLimitError(
            message="rate limit exceeded",
            response=mock_response,
            body=None,
        )
        assert is_network_error(rate_err) is True

    def test_wrapped_runtime_error_retryable_attribute(self):
        """A RuntimeError with .retryable attribute simulates script.py wrapping.

        In script.py, when all retries are exhausted in non-CI mode, the
        original exception is wrapped in a RuntimeError with
        ``.retryable = is_network_error(e)``. This test verifies that
        pattern works correctly.
        """
        # Simulate a network error being wrapped
        original = ConnectionError("dns failure")
        wrapped = RuntimeError("LLM script generation failed: dns failure")
        wrapped.retryable = is_network_error(original)
        assert wrapped.retryable is True

        # Simulate a config error being wrapped
        original = ValueError("bad api key")
        wrapped = RuntimeError("LLM script generation failed: bad api key")
        wrapped.retryable = is_network_error(original)
        assert wrapped.retryable is False
