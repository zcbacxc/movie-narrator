# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the v0.9.6 language-aware i18n pipeline.

Covers:
- ``lang=en``: script generation emits the English language directive and
  records ``narration_lang`` / ``script_lang`` = "en".
- ``lang`` default ``zh``: backward-compatible behaviour (no English hint,
  metadata records "zh").
- Language-aware prompt templates (``SCRIPT_PROMPT_ZH`` / ``select_script_prompt``).
- translate step: records the i18n translation direction metadata.
- match step: matches against the target-language text (``translated_texts``)
  and records which language / text source was used.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from movie_narrator.models import (
    Context, Scene, Services, TimedSegment,
)
from movie_narrator.pipeline.script import generate_script
from movie_narrator.pipeline.translate import translate_subtitles
from movie_narrator.pipeline.match import match_clips, _resolve_match_texts


# ── shared helpers (mirror test_script.py conventions) ─────


def _make_ctx(tmp_path, **kw):
    defaults = dict(
        movie_name="test_movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )
    defaults.update(kw)
    return Context(**defaults)


def _mock_llm_response(json_str: str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json_str
    return resp


def _mock_llm_cm(response=None, side_effect=None):
    mock_llm = MagicMock()
    mock_llm.model = "test-model"
    if side_effect:
        mock_llm.client.chat.completions.create.side_effect = side_effect
    else:
        mock_llm.client.chat.completions.create.return_value = response
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_llm)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


def _mock_settings(**overrides):
    s = MagicMock()
    s.script_retries = overrides.get("script_retries", 3)
    s.script_retry_delay = overrides.get("script_retry_delay", 0)
    s.script_temperature = overrides.get("script_temperature", 0.7)
    s.script_expand_temperature = overrides.get("script_expand_temperature", 0.5)
    s.script_max_tokens = overrides.get("script_max_tokens", 2048)
    s.research_temperature = overrides.get("research_temperature", 0.3)
    s.research_max_tokens = overrides.get("research_max_tokens", 1024)
    return s


def _beats_json(n: int) -> str:
    beats = [f"剧情关键点{i+1}" for i in range(n)]
    return '{"beats": ' + str(beats).replace("'", '"') + '}'


def _segments_json(texts: list) -> str:
    segs = [{"text": t} for t in texts]
    return json.dumps({"segments": segs}, ensure_ascii=False)


# ── script step: lang-aware ────────────────────────────────


def test_script_lang_en_uses_english_prompt(tmp_path):
    """lang=en → both Phase 1 and Phase 2 prompts carry the English directive."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["lang"] = "en"
    ctx.metadata["prompt_target_sentences"] = 3

    beats_resp = _mock_llm_response(_beats_json(3))
    seg_resp = _mock_llm_response(_segments_json(["s1", "s2", "s3"]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    mock_llm = mock_cm.__enter__.return_value
    calls = mock_llm.client.chat.completions.create.call_args_list
    phase1_prompt = calls[0].kwargs["messages"][0]["content"]
    phase2_prompt = calls[1].kwargs["messages"][0]["content"]
    assert "Write ALL narration text in English" in phase1_prompt
    assert "Write ALL narration text in English" in phase2_prompt
    assert result.metadata["narration_lang"] == "en"
    assert result.metadata["script_lang"] == "en"


def test_script_lang_defaults_to_zh(tmp_path):
    """no lang → backward-compatible zh: no English hint, metadata records zh."""
    ctx = _make_ctx(tmp_path)  # lang unset
    ctx.metadata["prompt_target_sentences"] = 3

    beats_resp = _mock_llm_response(_beats_json(3))
    seg_resp = _mock_llm_response(_segments_json(["s1", "s2", "s3"]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    mock_llm = mock_cm.__enter__.return_value
    phase1_prompt = mock_llm.client.chat.completions.create.call_args_list[0].kwargs["messages"][0]["content"]
    assert "Write ALL narration text" not in phase1_prompt
    assert result.metadata["narration_lang"] == "zh"
    assert result.metadata["script_lang"] == "zh"


# ── language-aware prompt templates ────────────────────────


def test_select_script_prompt_is_language_aware():
    from movie_narrator.utils.prompts import (
        SCRIPT_PROMPT, SCRIPT_PROMPT_ZH, select_script_prompt,
    )
    assert select_script_prompt("zh") == SCRIPT_PROMPT_ZH
    assert select_script_prompt("en") == SCRIPT_PROMPT
    assert select_script_prompt("") == SCRIPT_PROMPT  # backward-compatible
    assert "电影" in SCRIPT_PROMPT_ZH
    assert "million-follower" in SCRIPT_PROMPT


# ── translate step: i18n metadata ──────────────────────────


def test_translate_records_i18n_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    ctx = _make_ctx(tmp_path)
    ctx.timed_segments = [
        TimedSegment(text="你好", start=0.0, end=1.0),
        TimedSegment(text="世界", start=1.0, end=2.0),
    ]
    ctx.metadata.update(subtitle_lang="en", lang="zh")

    from movie_narrator.pipeline import translate as t_mod
    monkeypatch.setattr(t_mod, "_call_llm_chunk", lambda **kw: ["hello", "world"])

    translate_subtitles(ctx)

    assert ctx.status.translate == "success"
    assert ctx.translated_texts == ["hello", "world"]
    assert ctx.metadata["translate_target_lang"] == "en"
    assert ctx.metadata["translate_source_lang"] == "zh"
    assert ctx.metadata["narration_lang"] == "zh"
    assert ctx.metadata["script_lang"] == "zh"


# ── match step: language-aware text selection ──────────────


def test_resolve_match_texts_uses_translated_when_aligned():
    ctx = _make_ctx("/tmp")
    ctx.timed_segments = [
        TimedSegment(text="a", start=0.0, end=1.0),
        TimedSegment(text="b", start=1.0, end=2.0),
    ]
    ctx.translated_texts = ["hello", "world"]
    assert _resolve_match_texts(ctx) == ["hello", "world"]


def test_resolve_match_texts_falls_back_to_narration():
    ctx = _make_ctx("/tmp")
    ctx.timed_segments = [
        TimedSegment(text="a", start=0.0, end=1.0),
        TimedSegment(text="b", start=1.0, end=2.0),
    ]
    # no translated_texts -> narration text
    assert _resolve_match_texts(ctx) == ["a", "b"]


def test_match_records_narration_lang(tmp_path):
    ctx = _make_ctx(tmp_path)
    ctx.source_video_path = str(tmp_path / "v.mp4")
    ctx.timed_segments = [
        TimedSegment(text="A", start=0.0, end=2.0),
        TimedSegment(text="B", start=2.5, end=5.0),
    ]
    ctx.scenes = [Scene(index=0, start=0.0, end=10.0)]
    ctx.status.scene = "success"
    ctx.metadata["lang"] = "en"
    (tmp_path / "v.mp4").write_bytes(b"00")

    match_clips(ctx)
    assert ctx.status.match == "success"
    assert ctx.metadata["match_text_source"] == "narration"
    assert ctx.metadata["match_lang"] == "en"


def test_match_records_translated_lang(tmp_path):
    ctx = _make_ctx(tmp_path)
    ctx.source_video_path = str(tmp_path / "v.mp4")
    ctx.timed_segments = [
        TimedSegment(text="A", start=0.0, end=2.0),
        TimedSegment(text="B", start=2.5, end=5.0),
    ]
    ctx.scenes = [Scene(index=0, start=0.0, end=10.0)]
    ctx.status.scene = "success"
    ctx.translated_texts = ["hello", "world"]
    ctx.metadata["lang"] = "zh"
    ctx.metadata["subtitle_lang"] = "en"
    (tmp_path / "v.mp4").write_bytes(b"00")

    match_clips(ctx)
    assert ctx.status.match == "success"
    assert ctx.metadata["match_text_source"] == "translated"
    assert ctx.metadata["match_lang"] == "en"