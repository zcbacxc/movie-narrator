"""Tests for TTS abstraction layer (v0.4).

Covers:
- TTSCacheKey: determinism, fan-out, key inequality
- cache_path_for: two-level fan-out, sha256 hash
- BaseTTSProvider: CI silent fallback, _real_synthesize routing
- EdgeTTSProvider: delegation to edge_tts
- OpenAITTSProvider: constructor validation, voice whitelist, lazy import
- factory: provider selection, ConfigError on unsupported
- is_ci: env var detection
- pipeline.tts.generate_voice: CI path (temp file isolation), non-CI cache path
"""

import asyncio
import base64
import json
import os
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from movie_narrator.config import get_settings
from movie_narrator.tts import (
    CACHE_SCHEMA_VERSION,
    PROVIDER_CACHE_VERSIONS,
    TTSCacheKey,
    cache_path_for,
    get_tts_provider,
    is_ci,
)
from movie_narrator.tts.base import BaseTTSProvider, _estimate_duration_s
from movie_narrator.tts.edge import EdgeTTSProvider
from movie_narrator.tts.factory import get_tts_provider as factory_fn
from movie_narrator.tts.protocol import TTSProvider
from movie_narrator.utils.errors import ConfigError

# ffmpeg on this machine may lack mp3/wav codecs (minimal build).
# Tests that actually export/import mp3 via pydub need a full ffmpeg.
_FFMPEG_OK = shutil.which("ffmpeg") is not None
try:
    from pydub import AudioSegment as _test_seg
    _tmp = Path(os.environ.get("TEMP", "/tmp")) / "_ffmpeg_probe.mp3"
    _test_seg.silent(duration=10).export(_tmp, format="mp3")
    _test_seg.from_mp3(_tmp)
    _tmp.unlink(missing_ok=True)
    _MP3_OK = True
except Exception:
    _MP3_OK = False

requires_mp3 = pytest.mark.skipif(
    not _MP3_OK, reason="ffmpeg lacks mp3/wav codec support"
)


# ─── TTSCacheKey & cache_path_for ───


class TestTTSCacheKey:
    def _make_key(self, **overrides):
        defaults = dict(
            schema_version=CACHE_SCHEMA_VERSION,
            provider="edge",
            provider_version=1,
            model="",
            voice="zh-CN-YunxiNeural",
            text="hello",
            pause_ms=300,
        )
        defaults.update(overrides)
        return TTSCacheKey(**defaults)

    def test_deterministic_hash(self, tmp_path):
        key = self._make_key()
        p1 = cache_path_for(tmp_path, key)
        p2 = cache_path_for(tmp_path, key)
        assert p1 == p2

    def test_different_text_produces_different_path(self, tmp_path):
        k1 = self._make_key(text="hello")
        k2 = self._make_key(text="world")
        assert cache_path_for(tmp_path, k1) != cache_path_for(tmp_path, k2)

    def test_different_provider_produces_different_path(self, tmp_path):
        k1 = self._make_key(provider="edge")
        k2 = self._make_key(provider="openai")
        assert cache_path_for(tmp_path, k1) != cache_path_for(tmp_path, k2)

    def test_different_model_produces_different_path(self, tmp_path):
        k1 = self._make_key(model="tts-1")
        k2 = self._make_key(model="tts-1-hd")
        assert cache_path_for(tmp_path, k1) != cache_path_for(tmp_path, k2)

    def test_fan_out_two_levels(self, tmp_path):
        key = self._make_key()
        p = cache_path_for(tmp_path, key)
        # p = root / hash[:2] / hash[2:4] / hash.mp3
        assert len(p.parent.parent.name) == 2
        assert len(p.parent.name) == 2
        assert p.suffix == ".mp3"
        assert len(p.stem) == 64  # sha256 hex

    def test_frozen_dataclass(self):
        key = self._make_key()
        with pytest.raises(AttributeError):
            key.text = "modified"

    def test_provider_cache_versions_dict(self):
        assert "edge" in PROVIDER_CACHE_VERSIONS
        assert "openai" in PROVIDER_CACHE_VERSIONS
        assert "mimo" in PROVIDER_CACHE_VERSIONS
        assert PROVIDER_CACHE_VERSIONS["edge"] >= 1
        assert PROVIDER_CACHE_VERSIONS["openai"] >= 1
        assert PROVIDER_CACHE_VERSIONS["mimo"] >= 1

    def test_schema_version_is_2(self):
        assert CACHE_SCHEMA_VERSION == 2


# ─── is_ci() ───


class TestIsCI:
    def test_false_without_env(self, monkeypatch):
        monkeypatch.delenv("CI", raising=False)
        assert is_ci() is False

    def test_true_with_env(self, monkeypatch):
        monkeypatch.setenv("CI", "1")
        assert is_ci() is True

    def test_true_with_empty_string(self, monkeypatch):
        monkeypatch.setenv("CI", "")
        # bool(os.getenv("CI")) → bool("") → False
        assert is_ci() is False


# ─── _estimate_duration_s ───


class TestEstimateDuration:
    def test_floor_one_second(self):
        assert _estimate_duration_s("") == 1.0

    def test_short_text_uses_floor(self):
        # 1 char / 10 = 0.1s → floored to 1.0
        assert _estimate_duration_s("x") == 1.0

    def test_long_text_calculates(self):
        # 50 chars / 10 = 5.0s
        assert _estimate_duration_s("x" * 50) == 5.0

    def test_chinese_text(self):
        # 20 Chinese chars / 10 = 2.0s
        assert _estimate_duration_s("你好世界" * 5) == 2.0


# ─── BaseTTSProvider ───


class _DummyProvider(BaseTTSProvider):
    """Records calls to _real_synthesize for testing."""

    def __init__(self):
        self.calls = []

    async def _real_synthesize(self, text: str, voice: str, output_path: Path) -> None:
        self.calls.append((text, voice, output_path))
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"fake mp3")


class TestBaseTTSProvider:
    def test_synthesize_routes_to_real_when_not_ci(self, monkeypatch, tmp_path):
        monkeypatch.delenv("CI", raising=False)
        provider = _DummyProvider()
        out = tmp_path / "out.mp3"
        asyncio.run(provider.synthesize("hello", "v", out))
        assert len(provider.calls) == 1
        assert provider.calls[0][0] == "hello"
        assert out.exists()

    @requires_mp3
    def test_synthesize_routes_to_silent_when_ci(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CI", "1")
        provider = _DummyProvider()
        out = tmp_path / "out.mp3"
        asyncio.run(provider.synthesize("hello", "v", out))
        # _real_synthesize should NOT be called in CI
        assert len(provider.calls) == 0
        # Silent audio file should exist
        assert out.exists()
        # File should be a valid mp3 (non-zero)
        assert out.stat().st_size > 0

    @requires_mp3
    def test_silent_synthesize_writes_valid_audio(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CI", "1")
        provider = _DummyProvider()
        out = tmp_path / "sub" / "out.mp3"
        asyncio.run(provider.synthesize("test text here", "v", out))
        # Silent audio file should exist and be non-empty
        assert out.exists()
        assert out.stat().st_size > 0

    def test_is_protocol_subclass(self):
        assert issubclass(BaseTTSProvider, TTSProvider)
        assert issubclass(EdgeTTSProvider, TTSProvider)


# ─── EdgeTTSProvider ───


class TestEdgeTTSProvider:
    def test_real_synthesize_delegates_to_edge_tts(self, tmp_path):
        provider = EdgeTTSProvider()
        out = tmp_path / "edge.mp3"

        # Mock edge_tts.Communicate
        mock_communicate = MagicMock()
        mock_communicate.save = AsyncMock()
        with patch("edge_tts.Communicate", return_value=mock_communicate):
            asyncio.run(provider._real_synthesize("hello", "zh-CN-YunxiNeural", out))

        mock_communicate.save.assert_called_once_with(str(out))
        # Parent dir should be created
        assert out.parent.exists()

    def test_no_voice_validation(self, tmp_path):
        """Edge-TTS does not validate voice — lets the API report errors."""
        provider = EdgeTTSProvider()
        out = tmp_path / "edge.mp3"
        mock_communicate = MagicMock()
        mock_communicate.save = AsyncMock()
        with patch("edge_tts.Communicate", return_value=mock_communicate):
            # Even with a weird voice, no ConfigError
            asyncio.run(provider._real_synthesize("hello", "nonexistent-voice", out))
        mock_communicate.save.assert_called_once()


# ─── OpenAITTSProvider ───


class TestOpenAITTSProvider:
    def _make_settings(self, **overrides):
        from movie_narrator.config import Settings, TTSProviderType
        defaults = dict(
            tts_provider=TTSProviderType.OPENAI,
            openai_tts_model="tts-1",
            openai_tts_api_key="sk-test",
            openai_tts_base_url="https://api.openai.com/v1",
            llm_api_key="llm-key",
            llm_base_url="http://localhost:11434/v1",
        )
        defaults.update(overrides)
        return Settings(**defaults)

    def test_constructor_uses_explicit_api_key(self):
        from movie_narrator.tts.openai_provider import OpenAITTSProvider
        settings = self._make_settings(openai_tts_api_key="sk-explicit")
        with patch("openai.OpenAI") as mock_openai:
            provider = OpenAITTSProvider(settings)
            mock_openai.assert_called_once()
            call_kwargs = mock_openai.call_args.kwargs
            assert call_kwargs["api_key"] == "sk-explicit"

    def test_constructor_falls_back_to_llm_api_key(self):
        from movie_narrator.tts.openai_provider import OpenAITTSProvider
        settings = self._make_settings(openai_tts_api_key=None, llm_api_key="llm-fallback")
        with patch("openai.OpenAI") as mock_openai:
            provider = OpenAITTSProvider(settings)
            call_kwargs = mock_openai.call_args.kwargs
            assert call_kwargs["api_key"] == "llm-fallback"

    def test_constructor_falls_back_to_llm_base_url(self):
        from movie_narrator.tts.openai_provider import OpenAITTSProvider
        settings = self._make_settings(
            openai_tts_base_url=None,
            llm_base_url="http://my-llm:8080/v1",
        )
        with patch("openai.OpenAI") as mock_openai:
            provider = OpenAITTSProvider(settings)
            call_kwargs = mock_openai.call_args.kwargs
            assert call_kwargs["base_url"] == "http://my-llm:8080/v1"

    def test_constructor_raises_on_missing_key(self):
        from movie_narrator.tts.openai_provider import OpenAITTSProvider
        settings = self._make_settings(openai_tts_api_key=None, llm_api_key="")
        with patch("openai.OpenAI"):
            with pytest.raises(ConfigError, match="requires.*API_KEY"):
                OpenAITTSProvider(settings)

    def test_real_synthesize_validates_voice(self, tmp_path):
        from movie_narrator.tts.openai_provider import OpenAITTSProvider
        settings = self._make_settings()
        with patch("openai.OpenAI"):
            provider = OpenAITTSProvider(settings)
        out = tmp_path / "openai.mp3"
        with pytest.raises(ConfigError, match="voice must be one of"):
            asyncio.run(provider._real_synthesize("hello", "bad-voice", out))

    def test_real_synthesize_accepts_valid_voice(self, tmp_path):
        from movie_narrator.tts.openai_provider import OpenAITTSProvider
        settings = self._make_settings()
        with patch("openai.OpenAI"):
            provider = OpenAITTSProvider(settings)

        # Mock the _write_audio method
        provider._write_audio = MagicMock()
        out = tmp_path / "openai.mp3"
        asyncio.run(provider._real_synthesize("hello", "alloy", out))
        provider._write_audio.assert_called_once_with("hello", "alloy", out)

    def test_all_whitelist_voices_accepted(self, tmp_path):
        from movie_narrator.tts.openai_provider import OpenAITTSProvider, OPENAI_TTS_VOICES
        settings = self._make_settings()
        with patch("openai.OpenAI"):
            provider = OpenAITTSProvider(settings)
        provider._write_audio = MagicMock()
        for voice in OPENAI_TTS_VOICES:
            out = tmp_path / f"{voice}.mp3"
            asyncio.run(provider._real_synthesize("hi", voice, out))
        assert provider._write_audio.call_count == len(OPENAI_TTS_VOICES)


# ─── MiMo TTSProvider ───


class TestMimoTTSProvider:
    def _make_settings(self, **overrides):
        from movie_narrator.config import Settings, TTSProviderType
        defaults = dict(
            tts_provider=TTSProviderType.MIMO,
            mimo_tts_model="mimo-v2.5-tts",
            mimo_api_key="mimo-test-key",
            mimo_base_url="https://api.xiaomimimo.com/v1",
            mimo_style_prompt="",
            llm_api_key="llm-key",
            llm_base_url="http://localhost:11434/v1",
        )
        defaults.update(overrides)
        return Settings(**defaults)

    def _make_mock_completion(self, audio_data=None):
        """Return a mock completion with valid WAV audio data.

        Default generates 100ms of silence as a real WAV file so that
        ``AudioSegment.from_file(..., format="wav")`` in the provider
        can actually decode it (CI ffmpeg has full codec support).
        """
        if audio_data is None:
            from io import BytesIO
            from pydub import AudioSegment as _seg
            seg = _seg.silent(duration=100, frame_rate=8000)
            buf = BytesIO()
            seg.export(buf, format="wav")
            audio_data = buf.getvalue()
        msg = MagicMock()
        msg.audio.data = base64.b64encode(audio_data).decode("utf-8")
        completion = MagicMock()
        completion.choices = [MagicMock(message=msg)]
        return completion

    def test_constructor_uses_explicit_api_key(self):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider
        settings = self._make_settings(mimo_api_key="mimo-explicit")
        with patch("openai.OpenAI") as mock_openai:
            MimoTTSProvider(settings)
            call_kwargs = mock_openai.call_args.kwargs
            assert call_kwargs["api_key"] == "mimo-explicit"

    def test_constructor_falls_back_to_llm_api_key(self):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider
        settings = self._make_settings(mimo_api_key=None, llm_api_key="llm-fallback")
        with patch("openai.OpenAI") as mock_openai:
            MimoTTSProvider(settings)
            call_kwargs = mock_openai.call_args.kwargs
            assert call_kwargs["api_key"] == "llm-fallback"

    def test_constructor_raises_on_missing_key(self):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider
        settings = self._make_settings(mimo_api_key=None, llm_api_key="")
        with patch("openai.OpenAI"):
            with pytest.raises(ConfigError, match="requires.*API_KEY"):
                MimoTTSProvider(settings)

    def test_constructor_uses_custom_base_url(self):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider
        settings = self._make_settings(mimo_base_url="https://custom.mimo.api/v1")
        with patch("openai.OpenAI") as mock_openai:
            MimoTTSProvider(settings)
            call_kwargs = mock_openai.call_args.kwargs
            assert call_kwargs["base_url"] == "https://custom.mimo.api/v1"

    @requires_mp3
    def test_named_voice_mode_calls_api_correctly(self, tmp_path):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider, MIMO_TTS
        settings = self._make_settings(
            mimo_tts_model=MIMO_TTS,
            mimo_style_prompt="Bright and bouncy tone.",
        )
        with patch("openai.OpenAI"):
            provider = MimoTTSProvider(settings)

        provider._call_api = MagicMock(return_value=self._make_mock_completion())
        out = tmp_path / "mimo.mp3"
        asyncio.run(provider._real_synthesize("Hello world", "Chloe", out))

        assert provider._call_api.call_count == 1
        args = provider._call_api.call_args.args
        assert args[0] == "Hello world"       # text
        assert args[1] == "Bright and bouncy tone."  # user_content (style prompt)
        audio_param = args[2]
        assert audio_param["voice"] == "Chloe"
        assert audio_param["format"] == "wav"
        assert out.exists()

    @requires_mp3
    def test_voiceclone_mode_encodes_audio_file(self, tmp_path):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider, MIMO_VOICECLONE
        # Create a fake voice file
        voice_file = tmp_path / "voice.wav"
        voice_file.write_bytes(b"fake wav header + data")

        settings = self._make_settings(mimo_tts_model=MIMO_VOICECLONE)
        with patch("openai.OpenAI"):
            provider = MimoTTSProvider(settings)

        provider._call_api = MagicMock(return_value=self._make_mock_completion())
        out = tmp_path / "clone.mp3"
        asyncio.run(provider._real_synthesize("Test text", str(voice_file), out))

        audio_param = provider._call_api.call_args.args[2]
        assert audio_param["voice"].startswith("data:audio/wav;base64,")
        # Verify the base64 content matches the file
        b64_part = audio_param["voice"].split(",", 1)[1]
        assert base64.b64decode(b64_part) == b"fake wav header + data"
        # User content should be empty for voiceclone
        assert provider._call_api.call_args.args[1] == ""

    def test_voiceclone_raises_on_missing_file(self, tmp_path):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider, MIMO_VOICECLONE
        settings = self._make_settings(mimo_tts_model=MIMO_VOICECLONE)
        with patch("openai.OpenAI"):
            provider = MimoTTSProvider(settings)

        with pytest.raises(ConfigError, match="Voice clone file not found"):
            asyncio.run(provider._real_synthesize("text", "/nonexistent/voice.wav", tmp_path / "out.mp3"))

    @requires_mp3
    def test_voicedesign_mode_uses_voice_as_description(self, tmp_path):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider, MIMO_VOICEDESIGN
        settings = self._make_settings(mimo_tts_model=MIMO_VOICEDESIGN)
        with patch("openai.OpenAI"):
            provider = MimoTTSProvider(settings)

        provider._call_api = MagicMock(return_value=self._make_mock_completion())
        out = tmp_path / "designed.mp3"
        asyncio.run(provider._real_synthesize("Hello", "young male tone", out))

        args = provider._call_api.call_args.args
        # voice description goes in user_content
        assert args[1] == "young male tone"
        audio_param = args[2]
        assert "voice" not in audio_param
        assert audio_param.get("optimize_text_preview") is True

    def test_unsupported_model_raises_config_error(self, tmp_path):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider
        settings = self._make_settings(mimo_tts_model="mimo-unsupported-model")
        with patch("openai.OpenAI"):
            provider = MimoTTSProvider(settings)

        with pytest.raises(ConfigError, match="Unsupported MiMo TTS model"):
            asyncio.run(provider._real_synthesize("text", "voice", tmp_path / "out.mp3"))

    def test_voice_b64_cache_avoids_reread(self, tmp_path):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider, MIMO_VOICECLONE
        voice_file = tmp_path / "voice.wav"
        voice_file.write_bytes(b"cache test data")

        settings = self._make_settings(mimo_tts_model=MIMO_VOICECLONE)
        with patch("openai.OpenAI"):
            provider = MimoTTSProvider(settings)

        # First call reads file
        uri1 = provider._encode_voice_file(str(voice_file))
        # Second call should use cache
        uri2 = provider._encode_voice_file(str(voice_file))
        assert uri1 == uri2
        assert len(provider._voice_b64_cache) == 1

    def test_is_protocol_subclass(self):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider
        assert issubclass(MimoTTSProvider, TTSProvider)


# ─── Factory ───


class TestFactory:
    def test_returns_edge_provider(self):
        from movie_narrator.config import Settings, TTSProviderType
        settings = Settings(tts_provider=TTSProviderType.EDGE)
        provider = get_tts_provider(settings)
        assert isinstance(provider, EdgeTTSProvider)

    def test_returns_openai_provider(self):
        from movie_narrator.config import Settings, TTSProviderType
        settings = Settings(
            tts_provider=TTSProviderType.OPENAI,
            openai_tts_api_key="sk-test",
        )
        with patch("openai.OpenAI"):
            provider = get_tts_provider(settings)
        from movie_narrator.tts.openai_provider import OpenAITTSProvider
        assert isinstance(provider, OpenAITTSProvider)

    def test_no_singleton_each_call_new_instance(self):
        from movie_narrator.config import Settings, TTSProviderType
        settings = Settings(tts_provider=TTSProviderType.EDGE)
        p1 = get_tts_provider(settings)
        p2 = get_tts_provider(settings)
        assert p1 is not p2

    def test_returns_mimo_provider(self):
        from movie_narrator.config import Settings, TTSProviderType
        settings = Settings(
            tts_provider=TTSProviderType.MIMO,
            mimo_api_key="mimo-test",
        )
        with patch("openai.OpenAI"):
            provider = get_tts_provider(settings)
        from movie_narrator.tts.mimo_provider import MimoTTSProvider
        assert isinstance(provider, MimoTTSProvider)


# ─── pipeline/tts.generate_voice (CI path) ───


@requires_mp3
class TestGenerateVoiceCI:
    def _make_ctx(self, tmp_path):
        from movie_narrator.models import Context, ScriptSegment
        ctx = Context(movie_name="Test", output_dir=str(tmp_path))
        ctx.segments = [
            ScriptSegment(text="Hello world this is a test", index=0),
            ScriptSegment(text="Second segment here", index=1),
        ]
        return ctx

    def test_ci_produces_narration_and_timed_segments(self, tmp_path, monkeypatch):
        from movie_narrator.models import Context, ScriptSegment
        from movie_narrator.pipeline.tts import generate_voice
        from movie_narrator.config import get_settings

        # Clear lru_cache so .env (MN_TTS_PROVIDER=mimo) doesn't leak in
        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")

        monkeypatch.setenv("CI", "1")
        ctx = self._make_ctx(tmp_path)

        ctx = generate_voice(ctx)

        assert Path(ctx.audio_path).exists()
        assert len(ctx.timed_segments) == 2
        # Duration should be > 0
        assert ctx.timed_segments[0].end > ctx.timed_segments[0].start
        assert ctx.timed_segments[1].start > ctx.timed_segments[0].end  # gap for pause

    def test_ci_does_not_write_to_cache(self, tmp_path, monkeypatch):
        from movie_narrator.pipeline.tts import generate_voice
        from movie_narrator.config import get_settings

        # Clear lru_cache so .env (MN_TTS_PROVIDER=mimo) doesn't leak in
        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")

        monkeypatch.setenv("CI", "1")
        ctx = self._make_ctx(tmp_path)
        generate_voice(ctx)

        cache_dir = tmp_path / "cache" / "tts" / "edge"
        if cache_dir.exists():
            # Should be empty or only have directories, no mp3 files
            mp3_files = list(cache_dir.rglob("*.mp3"))
            assert len(mp3_files) == 0, f"CI wrote {len(mp3_files)} files to cache"

    def test_ci_temp_files_cleaned_up(self, tmp_path, monkeypatch):
        from movie_narrator.pipeline.tts import generate_voice
        from movie_narrator.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")

        monkeypatch.setenv("CI", "1")
        ctx = self._make_ctx(tmp_path)
        generate_voice(ctx)

        ci_files = list(tmp_path.glob(".ci_*.mp3"))
        assert len(ci_files) == 0, f"{len(ci_files)} CI temp files not cleaned up"

    def test_ci_sets_tts_provider_metadata(self, tmp_path, monkeypatch):
        from movie_narrator.pipeline.tts import generate_voice
        from movie_narrator.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")

        monkeypatch.setenv("CI", "1")
        ctx = self._make_ctx(tmp_path)
        generate_voice(ctx)
        assert ctx.metadata.get("tts_provider") == "edge"

    def test_ci_sets_voice_used_metadata(self, tmp_path, monkeypatch):
        from movie_narrator.pipeline.tts import generate_voice
        from movie_narrator.config import get_settings

        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")

        monkeypatch.setenv("CI", "1")
        ctx = self._make_ctx(tmp_path)
        generate_voice(ctx)
        assert "voice_used" in ctx.metadata


# ─── pipeline/tts.generate_voice (non-CI cache path) ───


class TestGenerateVoiceCache:
    def _make_ctx(self, tmp_path):
        from movie_narrator.models import Context, ScriptSegment
        ctx = Context(movie_name="Test", output_dir=str(tmp_path))
        ctx.segments = [ScriptSegment(text="Cached segment", index=0)]
        return ctx

    @requires_mp3
    def test_cache_hit_skips_synthesize(self, tmp_path, monkeypatch):
        from movie_narrator.pipeline.tts import generate_voice
        from movie_narrator.config import get_settings

        # Clear lru_cache so .env (MN_TTS_PROVIDER=mimo) doesn't leak in
        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "edge")

        monkeypatch.delenv("CI", raising=False)
        ctx = self._make_ctx(tmp_path)

        # First call to populate cache
        with patch("edge_tts.Communicate") as mock_comm:
            mock_comm.return_value.save = AsyncMock(
                side_effect=lambda path: Path(path).parent.mkdir(parents=True, exist_ok=True)
                or _write_silent_mp3(Path(path))
            )
            generate_voice(ctx)

        cache_files = list((tmp_path / "cache" / "tts" / "edge").rglob("*.mp3"))
        assert len(cache_files) == 1

        # Second call should hit cache — Communicate should NOT be called again
        ctx2 = self._make_ctx(tmp_path)
        with patch("edge_tts.Communicate") as mock_comm2:
            mock_comm2.return_value.save = AsyncMock()
            generate_voice(ctx2)
            mock_comm2.assert_not_called()

    def test_cache_key_includes_provider(self, tmp_path, monkeypatch):
        """Verify cache path differs between providers."""
        from movie_narrator.tts.cache import TTSCacheKey, cache_path_for

        k_edge = TTSCacheKey(
            schema_version=2, provider="edge", provider_version=1,
            model="", voice="v", text="t", pause_ms=300,
        )
        k_openai = TTSCacheKey(
            schema_version=2, provider="openai", provider_version=1,
            model="tts-1", voice="v", text="t", pause_ms=300,
        )
        assert cache_path_for(tmp_path, k_edge) != cache_path_for(tmp_path, k_openai)


# ─── Settings ───


class TestSettingsTTS:
    def test_default_tts_provider_is_edge(self):
        from movie_narrator.config import Settings, TTSProviderType
        # Use _env_file=None to prevent .env from overriding defaults
        s = Settings(_env_file=None)
        assert s.tts_provider is TTSProviderType.EDGE

    def test_openai_tts_model_default(self):
        from movie_narrator.config import Settings
        s = Settings()
        assert s.openai_tts_model == "tts-1"

    def test_openai_tts_api_key_default_none(self):
        from movie_narrator.config import Settings
        s = Settings()
        assert s.openai_tts_api_key is None

    def test_env_prefix_tts_provider(self, monkeypatch):
        from movie_narrator.config import Settings, TTSProviderType
        # Clear lru_cache to pick up new env
        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "openai")
        s = Settings()
        assert s.tts_provider is TTSProviderType.OPENAI
        get_settings.cache_clear()

    def test_invalid_tts_provider_raises(self, monkeypatch):
        from pydantic import ValidationError
        from movie_narrator.config import Settings
        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "invalid-provider")
        with pytest.raises(ValidationError):
            Settings()
        get_settings.cache_clear()

    def test_mimo_defaults(self):
        from movie_narrator.config import Settings
        # Use _env_file=None to prevent .env from overriding defaults
        s = Settings(_env_file=None)
        assert s.mimo_tts_model == "mimo-v2.5-tts"
        assert s.mimo_api_key is None
        assert s.mimo_base_url == "https://api.xiaomimimo.com/v1"
        assert s.mimo_style_prompt == ""

    def test_env_prefix_mimo_provider(self, monkeypatch):
        from movie_narrator.config import Settings, TTSProviderType
        get_settings.cache_clear()
        monkeypatch.setenv("MN_TTS_PROVIDER", "mimo")
        s = Settings()
        assert s.tts_provider is TTSProviderType.MIMO
        get_settings.cache_clear()


# ─── ConfigError ───


class TestConfigError:
    def test_is_exception(self):
        assert issubclass(ConfigError, Exception)

    def test_message_preserved(self):
        err = ConfigError("test message")
        assert str(err) == "test message"


# ─── metadata_export ───


class TestMetadataExportTTS:
    def test_tts_provider_in_metadata(self, tmp_path):
        from movie_narrator.models import Context
        from movie_narrator.utils.metadata_export import build_metadata_json

        ctx = Context(movie_name="Test", output_dir=str(tmp_path))
        ctx.metadata["tts_provider"] = "edge"
        ctx.metadata["voice_used"] = "zh-CN-YunxiNeural"
        meta = build_metadata_json(ctx)
        assert meta["input"]["tts_provider"] == "edge"

    def test_tts_provider_none_when_not_set(self, tmp_path):
        from movie_narrator.models import Context
        from movie_narrator.utils.metadata_export import build_metadata_json

        ctx = Context(movie_name="Test", output_dir=str(tmp_path))
        meta = build_metadata_json(ctx)
        assert meta["input"]["tts_provider"] is None


# ─── Helpers ───


def _write_silent_mp3(path: Path):
    """Write a minimal silent mp3 file for test mocks."""
    from pydub import AudioSegment
    path.parent.mkdir(parents=True, exist_ok=True)
    AudioSegment.silent(duration=1000).export(path, format="mp3")
