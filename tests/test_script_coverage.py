# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Additional coverage for pipeline.script.

Focuses on branches not exercised by test_script.py: movie_card injection,
LLM cost tracking, dict-typed beats parsing, judge_script (non-CI), beat
deduplication, the script-level QA gate, and the judge verdict retry paths.
All LLM interactions are fully mocked — no network / real LLM calls.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import (
    Context,
    MovieCard,
    ScriptSegment,
    Services,
)
from movie_narrator.pipeline.script import (
    _char_bigrams,
    _deduplicate_beats,
    _expand_beats_to_script,
    _generate_plot_beats,
    _jaccard_similarity,
    generate_script,
    judge_script,
    validate_script_quality,
)


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


def _resp(json_str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json_str
    return resp


def _llm_cm(response=None, side_effect=None):
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


def _settings(**overrides):
    s = MagicMock()
    s.script_retries = overrides.get("script_retries", 3)
    s.script_retry_delay = overrides.get("script_retry_delay", 0)
    s.script_expand_temperature = overrides.get("script_expand_temperature", 0.5)
    s.script_max_tokens = overrides.get("script_max_tokens", 2048)
    s.research_temperature = overrides.get("research_temperature", 0.3)
    s.research_max_tokens = overrides.get("research_max_tokens", 1024)
    return s


def _beats_json(beats):
    return json.dumps({"beats": beats}, ensure_ascii=False)


def _segments_json(items):
    return json.dumps({"segments": items}, ensure_ascii=False)


# ── _generate_plot_beats: movie_card + cost tracking + dict beats ──


def test_generate_plot_beats_movie_card(tmp_path):
    """movie_card is injected into the prompt and set_pieces propagated."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["movie_card"] = MovieCard(
        title="t",
        director="王导",
        cast=["演员A"],
        genres=["动作"],
        set_pieces=["名场面"],
    )
    resp = _resp(_beats_json(["b1", "b2", "b3"]))
    mock_cm = _llm_cm(response=resp)
    mock_llm = mock_cm.__enter__.return_value

    beats = _generate_plot_beats(ctx, _settings(), mock_llm, 3)
    assert beats == ["b1", "b2", "b3"]
    assert ctx.metadata.get("set_pieces") == ["名场面"]
    prompt = mock_llm.client.chat.completions.create.call_args.kwargs["messages"][0]["content"]
    assert "Director: 王导" in prompt
    assert "Cast: 演员A" in prompt
    assert "Genres: 动作" in prompt


def test_generate_plot_beats_movie_card_no_override_set_pieces(tmp_path):
    """Explicit set_pieces in metadata is not overwritten by the card."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["set_pieces"] = ["已有"]
    ctx.metadata["movie_card"] = MovieCard(title="t", director="d", set_pieces=["卡片"])
    resp = _resp(_beats_json(["b1"]))
    mock_cm = _llm_cm(response=resp)
    mock_llm = mock_cm.__enter__.return_value

    _generate_plot_beats(ctx, _settings(), mock_llm, 1)
    assert ctx.metadata["set_pieces"] == ["已有"]


def test_generate_plot_beats_cost_track(tmp_path):
    """Phase 1 records LLM token usage when a cost_tracker is present."""
    ctx = _make_ctx(tmp_path)
    ctx.cost_tracker = MagicMock()
    resp = _resp(_beats_json(["b1"]))
    resp.usage = MagicMock()
    mock_cm = _llm_cm(response=resp)
    mock_llm = mock_cm.__enter__.return_value

    _generate_plot_beats(ctx, _settings(), mock_llm, 1)
    ctx.cost_tracker.record_llm_call.assert_called_once()


def test_generate_plot_beats_beats_not_list(tmp_path):
    """beats key holding a non-list raises ValueError."""
    ctx = _make_ctx(tmp_path)
    resp = _resp(json.dumps({"beats": "nope"}))
    mock_cm = _llm_cm(response=resp)
    mock_llm = mock_cm.__enter__.return_value

    with pytest.raises(ValueError, match="not a list"):
        _generate_plot_beats(ctx, _settings(), mock_llm, 3)


def test_generate_plot_beats_dict_beats(tmp_path):
    """Dict beats parse act/approx_ratio/rhythm_zone/emotion with validation."""
    ctx = _make_ctx(tmp_path)
    beats = [
        {"text": "第一幕", "act": 1, "approx_ratio": 0.2, "rhythm_zone": "hook", "emotion": "suspense"},
        {"text": "第二幕", "act": "2", "approx_ratio": 2.0, "rhythm_zone": "bogus", "emotion": "bogus"},
        {"text": "第三幕", "act": "abc", "approx_ratio": "x", "rhythm_zone": None, "emotion": None},
    ]
    resp = _resp(_beats_json(beats))
    mock_cm = _llm_cm(response=resp)
    mock_llm = mock_cm.__enter__.return_value

    out = _generate_plot_beats(ctx, _settings(), mock_llm, 3)
    assert out == ["第一幕", "第二幕", "第三幕"]
    meta = ctx.metadata["beats_meta"]
    assert meta[0]["act"] == 1 and meta[0]["approx_ratio"] == 0.2
    assert meta[0]["rhythm_zone"] == "hook" and meta[0]["emotion"] == "suspense"
    # ratio clamped to [0,1]; invalid zone/emotion fall back to None
    assert meta[1]["approx_ratio"] == 1.0
    assert meta[1]["rhythm_zone"] is None and meta[1]["emotion"] is None
    # non-numeric act/ratio fall back to None
    assert meta[2]["act"] is None and meta[2]["approx_ratio"] is None


def test_generate_plot_beats_dict_empty_filtered(tmp_path):
    """Dict beats with empty / 'None' text are filtered out."""
    ctx = _make_ctx(tmp_path)
    beats = [{"text": "  "}, {"text": "none"}, {"text": "ok"}]
    resp = _resp(_beats_json(beats))
    mock_cm = _llm_cm(response=resp)
    mock_llm = mock_cm.__enter__.return_value

    with pytest.raises(ValueError, match="after filtering"):
        _generate_plot_beats(ctx, _settings(), mock_llm, 3)


def test_generate_plot_beats_dict_act_out_of_range(tmp_path):
    """An act outside the 1-4 range is clamped to None."""
    ctx = _make_ctx(tmp_path)
    beats = [{"text": "第一幕", "act": 5, "approx_ratio": 0.5}]
    resp = _resp(_beats_json(beats))
    mock_cm = _llm_cm(response=resp)
    mock_llm = mock_cm.__enter__.return_value

    out = _generate_plot_beats(ctx, _settings(), mock_llm, 1)
    assert out == ["第一幕"]
    assert ctx.metadata["beats_meta"][0]["act"] is None


# ── _expand_beats_to_script: cost tracking + mixed segments + truncation ──


def test_expand_cost_track_mixed_segments_and_truncation(tmp_path):
    """Phase 2 skips non-text items, hard-truncates long text, records cost."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_max_chars_per_sentence"] = 5
    ctx.cost_tracker = MagicMock()
    resp = _resp(_segments_json([{"text": "一二十三四五六七八九十"}, 123, {"text": "OK"}]))
    resp.usage = MagicMock()
    mock_cm = _llm_cm(response=resp)
    mock_llm = mock_cm.__enter__.return_value

    segments = _expand_beats_to_script(ctx, _settings(), mock_llm, ["b1"], 2)
    # int item dropped; long text truncated to 5 chars; dict OK kept
    assert [s.text for s in segments] == ["一二十三四", "OK"]
    assert ctx.metadata["script_truncated"]["count"] == 1
    assert ctx.metadata["script_truncated"]["max_chars"] == 5
    ctx.cost_tracker.record_llm_call.assert_called_once()


# ── judge_script (non-CI path) ─────────────────────────────


def test_judge_script_pass_and_cost_track(tmp_path):
    """Judge returns a pass verdict and records LLM cost."""
    ctx = _make_ctx(tmp_path)
    ctx.cost_tracker = MagicMock()
    llm = MagicMock()
    llm.model = "m"
    resp = _resp(
        json.dumps({
            "hook_strength": 8, "spoiler_level": 3, "plot_accuracy": 9,
            "anti_ai_compliance": 8, "narrative_adherence": 7, "issues": [],
        })
    )
    resp.usage = MagicMock()
    llm.client.chat.completions.create.return_value = resp
    segs = [ScriptSegment(text="不错的一段话")]

    with patch("movie_narrator.pipeline.script.is_ci", return_value=False):
        scores = judge_script(segs, "m", llm, ctx=ctx)

    assert scores["verdict"] == "pass"
    ctx.cost_tracker.record_llm_call.assert_called_once()


def test_judge_script_retry_malformed(tmp_path):
    """Non-coercible scores force retry and default missing keys are filled."""
    ctx = _make_ctx(tmp_path)
    llm = MagicMock()
    llm.model = "m"
    resp = _resp(json.dumps({"hook_strength": "abc", "spoiler_level": "x"}))
    llm.client.chat.completions.create.return_value = resp
    segs = [ScriptSegment(text="一段话")]

    with patch("movie_narrator.pipeline.script.is_ci", return_value=False):
        scores = judge_script(segs, "m", llm)

    assert scores["verdict"] == "retry"
    assert scores["plot_accuracy"] == 0  # setdefault default
    assert scores["narrative_adherence"] == 0
    assert isinstance(scores["issues"], list)


def test_judge_script_retry_low_score(tmp_path):
    """A low integer score yields a retry verdict."""
    llm = MagicMock()
    llm.model = "m"
    resp = _resp(
        json.dumps({
            "hook_strength": 2, "spoiler_level": 1, "plot_accuracy": 3,
            "anti_ai_compliance": 4, "narrative_adherence": 2, "issues": ["x"],
        })
    )
    llm.client.chat.completions.create.return_value = resp
    segs = [ScriptSegment(text="一段话")]

    with patch("movie_narrator.pipeline.script.is_ci", return_value=False):
        scores = judge_script(segs, "m", llm)

    assert scores["verdict"] == "retry"
    assert scores["issues"] == ["x"]


def test_judge_script_retry_noncoercible_spoiler(tmp_path):
    """A non-coercible spoiler_level (while hook passes) drives _is_int_le's except."""
    llm = MagicMock()
    llm.model = "m"
    resp = _resp(
        json.dumps({
            "hook_strength": 8, "spoiler_level": "x", "plot_accuracy": 9,
            "anti_ai_compliance": 8, "narrative_adherence": 7, "issues": [],
        })
    )
    llm.client.chat.completions.create.return_value = resp
    segs = [ScriptSegment(text="一段话")]

    with patch("movie_narrator.pipeline.script.is_ci", return_value=False):
        scores = judge_script(segs, "m", llm)

    assert scores["verdict"] == "retry"


# ── Beat deduplication helpers ─────────────────────────────


def test_char_bigrams_single_char():
    assert _char_bigrams("a") == frozenset({"a"})
    assert _char_bigrams("ab") == frozenset({"ab"})


def test_jaccard_similarity():
    a = frozenset({"ab", "bc"})
    b = frozenset({"ab", "cd"})
    assert _jaccard_similarity(a, b) == pytest.approx(1 / 3)
    assert _jaccard_similarity(frozenset(), frozenset({"x"})) == 0.0


def test_deduplicate_beats_single():
    b = ["x"]
    assert _deduplicate_beats(b) == (b, None)


def test_deduplicate_beats_no_duplicate():
    beats = ["内容甲", "内容乙"]
    out, meta = _deduplicate_beats(beats, None)
    assert out == beats
    assert meta is None


def test_deduplicate_beats_finds_duplicate():
    beats = ["同样内容", "同样内容", "不同内容"]
    meta = [{"text": t} for t in beats]
    deduped, deduped_meta = _deduplicate_beats(beats, meta)
    assert deduped == ["同样内容", "不同内容"]
    assert len(deduped_meta) == 2


def test_deduplicate_beats_skips_already_dropped_inner():
    """An inner position already marked duplicate is skipped (keep[j] False)."""
    beats = ["内容甲", "不同乙", "内容甲"]
    deduped, _ = _deduplicate_beats(beats, None)
    assert deduped == ["内容甲", "不同乙"]


# ── validate_script_quality (QA gate) ──────────────────────


def test_validate_script_quality_issues(tmp_path):
    ctx = _make_ctx(tmp_path)
    segs = [
        ScriptSegment(text="a"),                  # too short + hook too short
        ScriptSegment(text="x" * 100),            # too long (max 10*1.5=15)
        ScriptSegment(text="重重复复的相同句子"),     # near-duplicate pair
        ScriptSegment(text="重重复复的相同句子"),
    ]
    issues = validate_script_quality(segs, target_count=4, max_chars=10, ctx=ctx)
    assert any("too short" in i for i in issues)
    assert any("exceeds length" in i for i in issues)
    assert any("near-duplicates" in i for i in issues)
    assert any("hook" in i and "too short" in i for i in issues)
    assert ctx.metadata["script_qa"]["total_issues"] == len(issues)


def test_validate_script_quality_no_issues(tmp_path):
    ctx = _make_ctx(tmp_path)
    segs = [ScriptSegment(text="一段质量良好的合适句子内容")]
    issues = validate_script_quality(segs, target_count=1, max_chars=15, ctx=ctx)
    assert issues == []
    assert ctx.metadata["script_qa"]["total_issues"] == 0


# ── generate_script: dedup count adjustment + judge verdict paths ──


def test_generate_script_dedup_adjusts_count(tmp_path):
    """Duplicate beats are removed and n is adjusted before Phase 2."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 5
    beats = ["独有内容甲", "独有内容甲", "独有内容甲", "其他素材乙", "其他素材丙"]
    beats_resp = _resp(_beats_json(beats))
    seg_resp = _resp(_segments_json(["s1", "s2", "s3"]))
    mock_cm = _llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            with patch("movie_narrator.pipeline.script.is_ci", return_value=True):
                result = generate_script(ctx)

    assert len(result.segments) == 3
    assert result.metadata["script_beat_count"] == 3
    assert result.metadata["script_target_count"] == 3


def test_generate_script_judge_failure_treated_as_pass(tmp_path):
    """A judge exception is caught and treated as pass (never breaks pipeline)."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 3
    beats_resp = _resp(_beats_json(["b1", "b2", "b3"]))
    seg_resp = _resp(_segments_json(["s1", "s2", "s3"]))
    mock_cm = _llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            with patch("movie_narrator.pipeline.script.judge_script", side_effect=RuntimeError("boom")):
                result = generate_script(ctx)

    assert result.metadata["script_source"] == "llm"
    assert len(result.segments) == 3
    assert result.metadata["script_judge"]["verdict"] == "pass"


def test_generate_script_judge_retry_then_pass(tmp_path):
    """Verdict=retry triggers a retry with feedback; subsequent pass succeeds."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 3
    beats_resp = _resp(_beats_json(["b1", "b2", "b3"]))
    seg_resp = _resp(_segments_json(["s1", "s2", "s3"]))
    retry = {"verdict": "retry", "issues": ["too long"]}
    passed = {"verdict": "pass", "issues": []}
    mock_cm = _llm_cm(side_effect=[beats_resp, seg_resp, beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            with patch("movie_narrator.pipeline.script.judge_script", side_effect=[retry, passed]):
                with patch("movie_narrator.pipeline.script.sleep", return_value=None):
                    result = generate_script(ctx)

    assert result.metadata["script_source"] == "llm"
    assert len(result.segments) == 3


def test_generate_script_judge_retry_all_exhausted(tmp_path):
    """Verdict=retry on every attempt → uses the last script with a warning."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 3
    beats_resp = _resp(_beats_json(["b1", "b2", "b3"]))
    seg_resp = _resp(_segments_json(["s1", "s2", "s3"]))
    retry = {"verdict": "retry", "issues": ["bad"]}
    mock_cm = _llm_cm(
        side_effect=[beats_resp, seg_resp, beats_resp, seg_resp, beats_resp, seg_resp]
    )

    with patch("movie_narrator.pipeline.script.get_settings",
               return_value=_settings(script_retries=3)):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            with patch("movie_narrator.pipeline.script.judge_script",
                       side_effect=[retry, retry, retry]):
                with patch("movie_narrator.pipeline.script.sleep", return_value=None):
                    result = generate_script(ctx)

    assert result.metadata["script_source"] == "llm"
    assert len(result.segments) == 3


def test_generate_script_retry_sleeps_between_attempts(tmp_path):
    """A non-final attempt failure sleeps (retry delay) before the next attempt."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 3
    beats_resp = _resp(_beats_json(["b1", "b2", "b3"]))
    seg_resp = _resp(_segments_json(["s1", "s2", "s3"]))
    mock_cm = _llm_cm(side_effect=[ConnectionError("fail"), beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            with patch("movie_narrator.pipeline.script.sleep") as slp:
                with patch("movie_narrator.pipeline.script.is_ci", return_value=True):
                    result = generate_script(ctx)

    assert result.metadata["script_source"] == "llm"
    assert len(result.segments) == 3
    slp.assert_called()