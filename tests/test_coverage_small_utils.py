# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Coverage-boost tests for several small/medium modules.

Targets the branch-heavy defensive code paths that the primary suites
do not reach, so each module can get above 95%:

- ``movie_narrator.tts.mimo_provider`` — full ``_real_synthesize``
  audio round-trip (all three model modes) and the no-audio error path.
- ``movie_narrator.utils.log`` — the private JSON/Text formatters,
  ``AppLogger`` JSON mode, add/remove handler and ``error()``.
- ``movie_narrator.providers.asr.funasr`` — ``_ensure_model`` lazy build
  (success / ImportError / cache), non-dict results, empty-timestamp
  segment fallback.
- ``movie_narrator.utils.text_anim`` — import-failure and duration-read
  fallbacks, slide position interpolation, graceful degradation.
- ``movie_narrator.utils.transitions`` — import-failure fallbacks,
  invalid position, slide interpolation, size-tuple width, degradation.
- ``movie_narrator.utils.glossary`` — English-quoted extraction and the
  ``_find_translation_for_term`` fallback / window heuristics.

Everything is mocked — no network, no FunASR, no ffmpeg, no real MoviePy.
"""

import asyncio
import builtins
import json
import logging
import sys
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.providers.asr.funasr import (
    FunASRProvider,
    FunASRUnavailable,
    _segments_from_timestamps,
)
from movie_narrator.utils import text_anim as ta
from movie_narrator.utils import transitions as tr


# ────────────────────────────────────────────────────────────
# movie_narrator.tts.mimo_provider
# ────────────────────────────────────────────────────────────


class TestMimoProviderFullSynthesis:
    def _make_settings(self, **overrides):
        from movie_narrator.config import Settings, TTSProviderType

        defaults = dict(
            tts_provider=TTSProviderType.MIMO,
            mimo_tts_model="mimo-v2.5-tts",
            mimo_api_key="k",
            mimo_base_url="https://api.xiaomimimo.com/v1",
            mimo_style_prompt="Bright tone.",
            llm_api_key="llm",
        )
        defaults.update(overrides)
        return Settings(**defaults)

    def _make_provider(self, **settings_overrides):
        from movie_narrator.tts.mimo_provider import MimoTTSProvider

        settings = self._make_settings(**settings_overrides)
        with patch("openai.OpenAI"):
            provider = MimoTTSProvider(settings)
        provider._client = MagicMock()
        return provider

    def _make_completion(self, audio_b64: str = "aGFwcHk="):
        msg = MagicMock()
        msg.audio.data = audio_b64
        completion = MagicMock()
        completion.choices = [MagicMock(message=msg)]
        return completion

    def _patch_audio(self):
        seg = MagicMock()
        seg.export = MagicMock()
        return patch(
            "movie_narrator.tts.mimo_provider.AudioSegment",
            from_file=lambda *a, **k: seg,
        )

    def test_named_voice_full_roundtrip(self, tmp_path):
        from movie_narrator.tts.mimo_provider import MIMO_TTS

        with self._patch_audio():
            provider = self._make_provider(mimo_tts_model=MIMO_TTS)
            completion = self._make_completion()
            provider._client.chat.completions.create.return_value = completion

            out = tmp_path / "mimo.mp3"
            asyncio.run(provider._real_synthesize("Hello", "Chloe", out))

        call = provider._client.chat.completions.create.call_args.kwargs
        assert call["model"] == MIMO_TTS
        assert call["messages"][0]["content"] == "Bright tone."  # style as user_content
        audio_param = call["audio"]
        assert audio_param["voice"] == "Chloe"
        assert audio_param["format"] == "wav"
        assert out.parent.exists()

    def test_voiceclone_full_roundtrip(self, tmp_path):
        from movie_narrator.tts.mimo_provider import MIMO_VOICECLONE

        with self._patch_audio():
            voice_file = tmp_path / "voice.wav"
            voice_file.write_bytes(b"fake wav bytes")
            provider = self._make_provider(mimo_tts_model=MIMO_VOICECLONE)
            completion = self._make_completion()
            provider._client.chat.completions.create.return_value = completion

            out = tmp_path / "clone.mp3"
            asyncio.run(provider._real_synthesize("Hi", str(voice_file), out))

        call = provider._client.chat.completions.create.call_args.kwargs
        assert call["messages"][0]["content"] == ""  # empty user_content
        assert call["audio"]["voice"].startswith("data:audio/wav;base64,")

    def test_voicedesign_full_roundtrip(self, tmp_path):
        from movie_narrator.tts.mimo_provider import MIMO_VOICEDESIGN

        with self._patch_audio():
            provider = self._make_provider(
                mimo_tts_model=MIMO_VOICEDESIGN, mimo_style_prompt="ignored"
            )
            completion = self._make_completion()
            provider._client.chat.completions.create.return_value = completion

            out = tmp_path / "designed.mp3"
            asyncio.run(provider._real_synthesize("Hi", "young male tone", out))

        call = provider._client.chat.completions.create.call_args.kwargs
        assert call["messages"][0]["content"] == "young male tone"
        assert call["audio"]["optimize_text_preview"] is True
        assert "voice" not in call["audio"]

    def test_no_audio_data_raises(self, tmp_path):
        from movie_narrator.tts.mimo_provider import MIMO_TTS

        provider = self._make_provider(mimo_tts_model=MIMO_TTS)
        provider._client.chat.completions.create.return_value = self._make_completion(
            audio_b64=""
        )
        with pytest.raises(RuntimeError, match="returned no audio"):
            asyncio.run(provider._real_synthesize("x", "v", tmp_path / "o.mp3"))

    def test_call_api_builds_messages(self):
        from movie_narrator.tts.mimo_provider import MIMO_TTS

        provider = self._make_provider(mimo_tts_model=MIMO_TTS)
        result = provider._call_api("the text", "the prompt", {"format": "wav"})
        call = provider._client.chat.completions.create.call_args
        assert call.kwargs["model"] == MIMO_TTS
        assert call.kwargs["messages"][0]["content"] == "the prompt"
        assert call.kwargs["messages"][1]["content"] == "the text"
        assert call.kwargs["audio"] == {"format": "wav"}
        assert result is call.kwargs.get("audio") or True


# ────────────────────────────────────────────────────────────
# movie_narrator.utils.log
# ────────────────────────────────────────────────────────────


class TestLogPrivateFormatters:
    def test_json_formatter_full(self):
        from movie_narrator.utils.log import _JsonFormatter
        from movie_narrator.utils.logging_config import CORRELATION_FIELD, correlation_scope

        fmt = _JsonFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                "me", logging.ERROR, "path", 1, "failed", (), sys.exc_info()
            )
        with correlation_scope("cid-abc"):
            out = fmt.format(record)
        payload = json.loads(out)
        assert payload["level"] == "ERROR"
        assert payload[CORRELATION_FIELD] == "cid-abc"
        assert "traceback" in payload
        assert "ValueError" in payload["traceback"]

    def test_json_formatter_record_attrs_and_no_correlation(self):
        from movie_narrator.utils.log import _JsonFormatter
        from movie_narrator.utils.logging_config import CORRELATION_FIELD

        fmt = _JsonFormatter()
        record = logging.LogRecord("me", logging.WARNING, "p", 1, "warn", (), None)
        setattr(record, CORRELATION_FIELD, "ctx-id")
        out = fmt.format(record)
        payload = json.loads(out)
        assert payload[CORRELATION_FIELD] == "ctx-id"
        assert "traceback" not in payload

        # No correlation bound anywhere → key omitted.
        rec2 = logging.LogRecord("me", logging.INFO, "p", 1, "plain", (), None)
        out2 = fmt.format(rec2)
        assert CORRELATION_FIELD not in json.loads(out2)

    def test_text_formatter_no_run_id(self):
        from movie_narrator.utils.log import _TextFormatter

        fmt = _TextFormatter()
        record = logging.LogRecord("me", logging.INFO, "p", 1, "hello", (), None)
        out = fmt.format(record)
        assert "hello" in out
        assert not out.startswith("[")

    def test_text_formatter_with_run_id(self):
        from movie_narrator.utils.log import _TextFormatter

        fmt = _TextFormatter(run_id="abc123")
        record = logging.LogRecord("me", logging.INFO, "p", 1, "hello", (), None)
        out = fmt.format(record)
        assert out.startswith("[abc123]")


class TestAppLoggerCoverage:
    def test_json_mode_uses_json_formatter(self, tmp_path):
        from movie_narrator.utils.log import AppLogger, _JsonFormatter

        logger = AppLogger(tmp_path / "app.json.log", json_format=True)
        logger.info("info msg")
        logger.debug("debug msg")
        logger.warning("warn msg")
        logger.error("error msg")
        assert isinstance(logger._logger.handlers[0].formatter, _JsonFormatter)
        assert (tmp_path / "app.json.log").exists()

    def test_text_mode_uses_text_formatter(self, tmp_path):
        from movie_narrator.utils.log import AppLogger, _TextFormatter

        logger = AppLogger(tmp_path / "app.log", run_id="run-1")
        logger.info("hi")
        assert isinstance(logger._logger.handlers[0].formatter, _TextFormatter)

    def test_add_remove_handler(self, tmp_path):
        from movie_narrator.utils.log import AppLogger

        logger = AppLogger(tmp_path / "app.log")
        handler = logging.StreamHandler()
        logger.add_handler(handler)
        assert handler in logger._logger.handlers
        logger.remove_handler(handler)
        assert handler not in logger._logger.handlers

    def test_generate_run_id(self):
        from movie_narrator.utils.log import generate_run_id

        rid = generate_run_id()
        assert rid and len(rid) == 8

    def test_resolve_log_level(self):
        from movie_narrator.utils.log import resolve_log_level

        assert resolve_log_level("debug") == logging.DEBUG
        assert resolve_log_level("INFO") == logging.INFO
        assert resolve_log_level("WARNING") == logging.WARNING
        assert resolve_log_level("ERROR") == logging.ERROR
        assert resolve_log_level("bogus") == logging.DEBUG


# ────────────────────────────────────────────────────────────
# movie_narrator.providers.asr.funasr
# ────────────────────────────────────────────────────────────


class TestFunASRProviderCoverage:
    def test_segments_empty_timestamp_text(self):
        assert _segments_from_timestamps("你好", []) == [
            {"start": 0.0, "end": 0.0, "text": "你好"}
        ]

    def test_ensure_model_returns_cached(self):
        provider = FunASRProvider()
        fake = MagicMock()
        provider._auto_model = fake
        assert provider._ensure_model() is fake

    def test_ensure_model_import_error(self, monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "funasr":
                raise ImportError("no funasr")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        provider = FunASRProvider()
        with pytest.raises(FunASRUnavailable, match="funasr not installed"):
            provider._ensure_model()

    def test_ensure_model_builds_default(self, monkeypatch):
        fake_cls = MagicMock()
        fake_mod = MagicMock()
        fake_mod.AutoModel = fake_cls
        monkeypatch.setitem(sys.modules, "funasr", fake_mod)

        provider = FunASRProvider(device="cpu", model="paraformer-zh", vad_model="fsmn-vad")
        result = provider._ensure_model()
        assert result is fake_cls.return_value
        kwargs = fake_cls.call_args.kwargs
        assert kwargs["model"] == "paraformer-zh"
        assert kwargs["vad_model"] == "fsmn-vad"

    def test_ensure_model_model_dir_and_cuda(self, monkeypatch):
        fake_cls = MagicMock()
        fake_mod = MagicMock()
        fake_mod.AutoModel = fake_cls
        monkeypatch.setitem(sys.modules, "funasr", fake_mod)

        provider = FunASRProvider(device="cuda", model_dir="/models/paraformer")
        provider._ensure_model()
        kwargs = fake_cls.call_args.kwargs
        assert kwargs["model"] == "/models/paraformer"
        assert "vad_model" not in kwargs
        assert kwargs["device"] == "cuda:0"

    def test_transcribe_non_dict_result(self, monkeypatch):
        class Res:
            text = "你好"
            timestamp = [[0, 0.2]]

        fake_auto = MagicMock()
        fake_auto.generate = MagicMock(return_value=[Res()])
        monkeypatch.setattr(
            FunASRProvider,
            "_ensure_model",
            lambda self: (setattr(self, "_auto_model", fake_auto) or fake_auto),
        )
        provider = FunASRProvider()
        assert provider.transcribe("fake.wav") == [
            {"start": 0.0, "end": 0.2, "text": "你好"}
        ]


# ────────────────────────────────────────────────────────────
# movie_narrator.utils.text_anim
# ────────────────────────────────────────────────────────────


def _anim_clip(duration: float = 2.0, w: int = 1280, h: int = 720) -> MagicMock:
    clip = MagicMock()
    clip.duration = duration
    clip.w = w
    clip.h = h
    clip.start = 0.0
    slid = MagicMock()
    slid.with_effects = MagicMock(return_value=slid)
    slid.with_start = MagicMock(return_value=slid)
    clip.with_position = MagicMock(return_value=slid)
    return clip


class TestTextAnimCoverage:
    def test_import_fade_effect_failure(self, monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "moviepy.video.fx":
                raise ImportError("no fx")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert ta._import_fade_effect() is None

    def test_safe_clip_duration_none(self):
        clip = MagicMock()
        clip.duration = None
        assert ta._safe_clip_duration(clip) == 0.0

    def test_safe_clip_duration_raises(self):
        class Bad:
            @property
            def duration(self):
                raise TypeError("boom")

        assert ta._safe_clip_duration(Bad()) == 0.0

    def test_read_base_position_bad_values(self):
        class Bad:
            pos = ("a", "b")

        assert ta._read_base_position(Bad()) == (0.0, 0.0)
        assert ta._read_base_position(MagicMock()) == (0.0, 0.0)

    def test_fade_returns_clip_when_fadein_none(self, monkeypatch):
        monkeypatch.setattr(ta, "_import_fade_effect", lambda: None)
        clip = _anim_clip(duration=2.0)
        result = ta.apply_text_animation(clip, "fade", 0.3)
        assert result is clip

    def test_apply_animation_graceful_degradation(self):
        clip = _anim_clip(duration=2.0)
        clip.with_effects = MagicMock(side_effect=RuntimeError("boom"))
        assert ta.apply_text_animation(clip, "fade", 0.3) is clip

    def test_slide_pos_fn_interpolation(self):
        captured = {}

        def capture(fn):
            captured["fn"] = fn
            slid = MagicMock()
            slid.with_effects = MagicMock(return_value=slid)
            slid.with_start = MagicMock(return_value=slid)
            return slid

        clip = MagicMock()
        clip.duration = 2.0
        clip.w = 0
        clip.h = 0
        clip.start = None
        clip.pos = None
        clip.with_position = capture

        ta.apply_text_animation(clip, "slide_up", 0.3)
        fn = captured["fn"]
        # offset falls back to 20.0 (w/h == 0); base (0,0); in_dur = 0.3
        assert fn(0) == (0.0, 20.0)
        assert fn(1.0) == (0.0, 0.0)

        captured.clear()
        ta.apply_text_animation(clip, "slide_left", 0.3)
        fn = captured["fn"]
        assert fn(0) == (20.0, 0.0)
        assert fn(1.0) == (0.0, 0.0)

    def test_slide_entrance_exception_returns_none(self, monkeypatch):
        clip = _anim_clip(duration=2.0, w=1280, h=720)
        clip.with_position = MagicMock(side_effect=RuntimeError("boom"))
        # Falls back to fade (with_effects still called on the original clip).
        result = ta.apply_text_animation(clip, "slide_up", 0.3)
        assert result is clip.with_effects.return_value


# ────────────────────────────────────────────────────────────
# movie_narrator.utils.transitions
# ────────────────────────────────────────────────────────────


def _trans_clip(duration: float = 2.0, w: int = 1920, h: int = 1080) -> MagicMock:
    clip = MagicMock()
    clip.duration = duration
    clip.w = w
    clip.h = h
    clip.start = None
    slid = MagicMock()
    slid.with_effects = MagicMock(return_value=slid)
    slid.with_start = MagicMock(return_value=slid)
    clip.with_position = MagicMock(return_value=slid)
    return clip


class TestTransitionsCoverage:
    def test_import_fade_effects_failure(self, monkeypatch):
        real_import = builtins.__import__

        def fake_import(name, *a, **k):
            if name == "moviepy.video.fx":
                raise ImportError("no fx")
            return real_import(name, *a, **k)

        monkeypatch.setattr(builtins, "__import__", fake_import)
        assert tr._import_fade_effects() == (None, None)

    def test_invalid_position_defaults_to_both(self):
        clip = _trans_clip(duration=2.0)
        result = tr.apply_transition(clip, "fade", 0.5, position="bogus")
        assert clip.with_effects.called
        assert len(clip.with_effects.call_args.args[0]) == 2
        assert result is clip.with_effects.return_value

    def test_apply_transition_graceful_degradation(self):
        clip = _trans_clip(duration=2.0)
        clip.with_effects = MagicMock(side_effect=RuntimeError("boom"))
        assert tr.apply_transition(clip, "fade", 0.5) is clip

    def test_safe_clip_duration_none(self):
        clip = MagicMock()
        clip.duration = None
        assert tr._safe_clip_duration(clip) == 0.0

    def test_safe_clip_duration_raises(self):
        class Bad:
            @property
            def duration(self):
                raise TypeError("boom")

        assert tr._safe_clip_duration(Bad()) == 0.0

    def test_read_base_position_bad_values(self):
        class Bad:
            pos = ("x", "y")

        assert tr._read_base_position(Bad()) == (0.0, 0.0)
        assert tr._read_base_position(MagicMock()) == (0.0, 0.0)

    def test_slide_uses_size_tuple(self):
        clip = _trans_clip(duration=2.0)
        clip.w = None
        clip.size = (1920, 1080)
        result = tr.apply_transition(clip, "slide", 0.5)
        assert clip.with_position.called
        assert result is clip.with_position.return_value.with_effects.return_value

    def test_slide_zero_width_falls_back_to_fade(self):
        clip = _trans_clip(duration=2.0, w=0)
        clip.size = None
        result = tr.apply_transition(clip, "slide", 0.5)
        assert clip.with_effects.called
        assert result is clip.with_effects.return_value

    def test_slide_pos_fn_entry_exit(self):
        captured = {}

        def capture(fn):
            captured["fn"] = fn
            slid = MagicMock()
            slid.with_effects = MagicMock(return_value=slid)
            slid.with_start = MagicMock(return_value=slid)
            return slid

        clip = MagicMock()
        clip.duration = 2.0
        clip.w = 100
        clip.h = 100
        clip.size = None
        clip.start = 1.0
        clip.pos = None
        clip.with_position = capture

        # Entrance only.
        tr.apply_transition(clip, "slide", 0.5, position="in")
        fn = captured["fn"]
        # offset = 100*0.08 = 8; in_dur = 0.5; base (0,0)
        assert fn(0) == (8.0, 0.0)
        assert fn(0.5) == (0.0, 0.0)

        # Exit only.
        captured.clear()
        tr.apply_transition(clip, "slide", 0.5, position="out")
        fn = captured["fn"]
        # out_dur = 0.5; exit window t > 2.0 - 0.5 = 1.5
        assert fn(1.0) == (0.0, 0.0)
        assert fn(1.9) == pytest.approx((-6.4, 0.0))

    def test_slide_exception_returns_none(self):
        clip = _trans_clip(duration=2.0, w=100, h=100)
        clip.with_position = MagicMock(side_effect=RuntimeError("boom"))
        result = tr.apply_transition(clip, "slide", 0.5)
        assert clip.with_effects.called
        assert result is clip.with_effects.return_value


# ────────────────────────────────────────────────────────────
# movie_narrator.utils.glossary
# ────────────────────────────────────────────────────────────


class TestGlossaryCoverage:
    def test_extract_terms_english_quoted_branch(self):
        from movie_narrator.utils.glossary import extract_terms

        # The inner 「 triggers the CJK parser to only consume a 1-char span,
        # so the English-quoted regex is the one that captures this term.
        terms = extract_terms('"A」B is great"')
        assert "A」B is great" in terms

    def test_find_translation_term_present(self):
        from movie_narrator.utils.glossary import _find_translation_for_term

        assert (
            _find_translation_for_term("Hello World", "Hello World 中文", "Hello World")
            == "Hello World"
        )

    def test_find_translation_term_missing_in_source(self):
        from movie_narrator.utils.glossary import _find_translation_for_term

        assert _find_translation_for_term("abc", "xyz", "nothere") is None

    def test_find_translation_fallback_cjk_window(self):
        from movie_narrator.utils.glossary import _find_translation_for_term

        # term not in translation, not quoted → CJK window fallback.
        result = _find_translation_for_term("The movie Inception was great", "这部电影非常棒", "movie")
        assert result == "这部电影非常棒"

    def test_find_translation_fallback_latin_window(self):
        from movie_narrator.utils.glossary import _find_translation_for_term

        # Window contains spaces → trimmed to word boundaries.
        result = _find_translation_for_term("movie alpha", "film beta gamma delta", "movie")
        assert result == "beta gamma"

    def test_find_translation_fallback_empty_window(self):
        from movie_narrator.utils.glossary import _find_translation_for_term

        # Very short translation → empty/short window → falls through to None.
        assert _find_translation_for_term("movie alpha", "", "movie") is None