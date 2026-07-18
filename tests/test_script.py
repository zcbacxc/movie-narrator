"""Tests for the generate_script pipeline step (two-phase v0.4.16+).

Covers: Phase 1 beat extraction, Phase 2 expansion, fallback trim,
retry/fail behavior, research context injection, CI mock fallback,
and preset tag propagation.
"""

from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Context, ResearchInfo, Services, ScriptSegment
from movie_narrator.pipeline.script import (
    generate_script,
    _trim_segments,
    _generate_plot_beats,
    _expand_beats_to_script,
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


def _mock_llm_response(json_str: str):
    """Build a mock OpenAI response whose content is *json_str*."""
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json_str
    return resp


def _mock_llm_cm(response=None, side_effect=None):
    """Build a mock context manager for get_llm_client."""
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
    """Build a mock Settings object with sensible defaults."""
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
    """Build a valid Phase 1 response with n beats."""
    beats = [f"剧情关键点{i+1}" for i in range(n)]
    return '{"beats": ' + str(beats).replace("'", '"') + '}'


def _segments_json(texts: list) -> str:
    """Build a valid Phase 2 response with segments."""
    segs = [{"text": t} for t in texts]
    import json
    return json.dumps({"segments": segs}, ensure_ascii=False)


# ── 1. Two-phase success ────────────────────────────────────


def test_generate_script_two_phase_success(tmp_path):
    """Phase 1 returns N beats, Phase 2 returns N segments → success."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 5

    beats_resp = _mock_llm_response(_beats_json(5))
    seg_resp = _mock_llm_response(_segments_json([f"旁白{i}" for i in range(5)]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    assert len(result.segments) == 5
    assert result.metadata["script_source"] == "llm"
    assert result.metadata["script_phase"] == "two-phase"
    assert result.metadata["script_beat_count"] == 5
    assert result.metadata["script_segment_count"] == 5


def test_generate_script_no_preset_defaults_to_18(tmp_path):
    """Without prompt_target_sentences, default target is 18."""
    ctx = _make_ctx(tmp_path)

    beats_resp = _mock_llm_response(_beats_json(18))
    seg_resp = _mock_llm_response(_segments_json([f"s{i}" for i in range(18)]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    assert len(result.segments) == 18
    # Verify Phase 1 prompt asked for 18 beats
    mock_llm = mock_cm.__enter__.return_value
    phase1_call = mock_llm.client.chat.completions.create.call_args_list[0]
    assert "18" in phase1_call.kwargs["messages"][0]["content"]


# ── 2. Fallback trim ────────────────────────────────────────


def test_trim_segments_no_trim_when_under_target():
    """If segments <= target, return as-is."""
    segs = [ScriptSegment(text=f"s{i}") for i in range(3)]
    result = _trim_segments(segs, 5)
    assert len(result) == 3
    assert result == segs


def test_trim_segments_exact_match():
    """If segments == target, return as-is."""
    segs = [ScriptSegment(text=f"s{i}") for i in range(5)]
    result = _trim_segments(segs, 5)
    assert len(result) == 5


def test_trim_segments_trims_to_exact_target():
    """If segments > target, trim to exactly target."""
    segs = [ScriptSegment(text=f"s{i}") for i in range(10)]
    result = _trim_segments(segs, 5)
    assert len(result) == 5


def test_trim_preserves_first_three_hooks():
    """First 3 segments (hooks) must be preserved during trim."""
    segs = [ScriptSegment(text=f"hook{i}") for i in range(3)] + \
           [ScriptSegment(text=f"body{i}") for i in range(7)]
    result = _trim_segments(segs, 5)
    assert len(result) == 5
    # First 3 must be the hooks
    assert result[0].text == "hook0"
    assert result[1].text == "hook1"
    assert result[2].text == "hook2"


def test_trim_preserves_chronological_order():
    """After trim, segments should be in original chronological order."""
    segs = [ScriptSegment(text=f"s{i:02d}") for i in range(8)]
    result = _trim_segments(segs, 4)
    assert len(result) == 4
    # The result should be a subsequence of the original, in order
    original_texts = [s.text for s in segs]
    result_texts = [s.text for s in result]
    # All result texts must appear in original in the same relative order
    idx = 0
    for rt in result_texts:
        found = original_texts.index(rt, idx)
        idx = found + 1


def test_trim_with_small_target():
    """target=2 should keep first 2 (hook_count=min(3,2)=2)."""
    segs = [ScriptSegment(text=f"s{i}") for i in range(6)]
    result = _trim_segments(segs, 2)
    assert len(result) == 2
    assert result[0].text == "s0"
    assert result[1].text == "s1"


# ── 3. Phase 1 beat extraction ─────────────────────────────


def test_phase1_wrong_beat_count_raises(tmp_path):
    """Phase 1 returning wrong number of beats → ValueError."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 8

    # LLM returns 5 beats but we asked for 8
    beats_resp = _mock_llm_response(_beats_json(5))
    mock_cm = _mock_llm_cm(response=beats_resp)
    mock_llm = mock_cm.__enter__.return_value

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with pytest.raises(ValueError, match="expected 8 beats, got 5"):
            _generate_plot_beats(ctx, _mock_settings(), mock_llm, 8)


def test_phase1_zero_beats_raises(tmp_path):
    """Phase 1 returning zero beats → ValueError."""
    ctx = _make_ctx(tmp_path)
    beats_resp = _mock_llm_response('{"beats": []}')
    mock_cm = _mock_llm_cm(response=beats_resp)
    mock_llm = mock_cm.__enter__.return_value

    with pytest.raises(ValueError, match="zero beats"):
        _generate_plot_beats(ctx, _mock_settings(), mock_llm, 5)


def test_phase1_includes_research_context(tmp_path):
    """Research summary should appear in Phase 1 prompt."""
    ctx = _make_ctx(tmp_path)
    ctx.research = ResearchInfo(
        title="测试电影", year="2024", summary="一部关于勇气的电影",
        genres=["动作"], cast=["演员A"], keywords=["勇气"],
    )
    beats_resp = _mock_llm_response(_beats_json(3))
    mock_cm = _mock_llm_cm(response=beats_resp)
    mock_llm = mock_cm.__enter__.return_value

    _generate_plot_beats(ctx, _mock_settings(), mock_llm, 3)
    call_args = mock_llm.client.chat.completions.create.call_args
    prompt = call_args.kwargs["messages"][0]["content"]
    assert "一部关于勇气的电影" in prompt


def test_phase1_uses_low_temperature(tmp_path):
    """Phase 1 should use research_temperature (0.3), not script_temperature."""
    ctx = _make_ctx(tmp_path)
    beats_resp = _mock_llm_response(_beats_json(3))
    mock_cm = _mock_llm_cm(response=beats_resp)
    mock_llm = mock_cm.__enter__.return_value

    settings = _mock_settings()
    _generate_plot_beats(ctx, settings, mock_llm, 3)
    call_args = mock_llm.client.chat.completions.create.call_args
    assert call_args.kwargs["temperature"] == 0.3


# ── 4. Phase 2 beat expansion ───────────────────────────────


def test_phase2_returns_segments(tmp_path):
    """Phase 2 expands beats into segments."""
    ctx = _make_ctx(tmp_path)
    beats = ["关键点1", "关键点2", "关键点3"]
    seg_resp = _mock_llm_response(_segments_json(["旁白1", "旁白2", "旁白3"]))
    mock_cm = _mock_llm_cm(response=seg_resp)
    mock_llm = mock_cm.__enter__.return_value

    segments = _expand_beats_to_script(ctx, _mock_settings(), mock_llm, beats, 3)
    assert len(segments) == 3
    assert segments[0].text == "旁白1"


def test_phase2_uses_expand_temperature(tmp_path):
    """Phase 2 should use script_expand_temperature (0.5)."""
    ctx = _make_ctx(tmp_path)
    beats = ["b1", "b2"]
    seg_resp = _mock_llm_response(_segments_json(["s1", "s2"]))
    mock_cm = _mock_llm_cm(response=seg_resp)
    mock_llm = mock_cm.__enter__.return_value

    settings = _mock_settings()
    _expand_beats_to_script(ctx, settings, mock_llm, beats, 2)
    call_args = mock_llm.client.chat.completions.create.call_args
    assert call_args.kwargs["temperature"] == 0.5


def test_phase2_zero_segments_raises(tmp_path):
    """Phase 2 returning zero segments → ValueError."""
    ctx = _make_ctx(tmp_path)
    beats = ["b1", "b2"]
    seg_resp = _mock_llm_response('{"segments": []}')
    mock_cm = _mock_llm_cm(response=seg_resp)
    mock_llm = mock_cm.__enter__.return_value

    with pytest.raises(ValueError, match="zero segments"):
        _expand_beats_to_script(ctx, _mock_settings(), mock_llm, beats, 2)


def test_phase2_preset_tags_in_prompt(tmp_path):
    """Preset tags should appear in Phase 2 prompt via cadence_hint."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["narration_preset_tags"] = {
        "prompt_cadence": "brisk",
        "prompt_connectors": "interjection",
        "prompt_register": "spoken",
    }
    beats = ["b1"]
    seg_resp = _mock_llm_response(_segments_json(["s1"]))
    mock_cm = _mock_llm_cm(response=seg_resp)
    mock_llm = mock_cm.__enter__.return_value

    _expand_beats_to_script(ctx, _mock_settings(), mock_llm, beats, 1)
    call_args = mock_llm.client.chat.completions.create.call_args
    prompt = call_args.kwargs["messages"][0]["content"]
    assert "brisk" in prompt
    assert "spoken" in prompt


def test_phase2_beats_formatted_as_numbered_list(tmp_path):
    """Beats should be formatted as a numbered list in the prompt."""
    ctx = _make_ctx(tmp_path)
    beats = ["第一点", "第二点", "第三点"]
    seg_resp = _mock_llm_response(_segments_json(["s1", "s2", "s3"]))
    mock_cm = _mock_llm_cm(response=seg_resp)
    mock_llm = mock_cm.__enter__.return_value

    _expand_beats_to_script(ctx, _mock_settings(), mock_llm, beats, 3)
    call_args = mock_llm.client.chat.completions.create.call_args
    prompt = call_args.kwargs["messages"][0]["content"]
    assert "1. 第一点" in prompt
    assert "2. 第二点" in prompt
    assert "3. 第三点" in prompt


# ── 5. End-to-end with trim ─────────────────────────────────


def test_generate_script_trims_overshoot(tmp_path):
    """Phase 2 returns more segments than target → trim to exact."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 5

    beats_resp = _mock_llm_response(_beats_json(5))
    # Phase 2 returns 8 segments but target is 5
    seg_resp = _mock_llm_response(_segments_json([f"s{i}" for i in range(8)]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    assert len(result.segments) == 5
    assert result.metadata["script_segment_count"] == 5


# ── 6. Retry behavior ───────────────────────────────────────


def test_generate_script_hard_fails_on_all_retries(tmp_path, monkeypatch):
    """3 consecutive failures → RuntimeError (no mock fallback)."""
    monkeypatch.delenv("CI", raising=False)
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 3

    mock_cm = _mock_llm_cm(side_effect=[
        ConnectionError("unreachable"),  # attempt 1 Phase 1
        ConnectionError("unreachable"),  # attempt 2 Phase 1
        ConnectionError("unreachable"),  # attempt 3 Phase 1
    ])

    with patch("movie_narrator.pipeline.script.is_ci", return_value=False):
        with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
            with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
                with pytest.raises(RuntimeError, match="LLM script generation failed"):
                    generate_script(ctx)


def test_generate_script_retry_succeeds(tmp_path):
    """First attempt fails, second succeeds."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 3

    beats_resp = _mock_llm_response(_beats_json(3))
    seg_resp = _mock_llm_response(_segments_json(["s1", "s2", "s3"]))
    mock_cm = _mock_llm_cm(side_effect=[
        ConnectionError("timeout"),  # attempt 1 Phase 1 fails
        beats_resp,                   # attempt 2 Phase 1
        seg_resp,                     # attempt 2 Phase 2
    ])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    assert result.metadata["script_source"] == "llm"
    assert len(result.segments) == 3


def test_generate_script_phase1_count_mismatch_triggers_retry(tmp_path, monkeypatch):
    """Phase 1 returns wrong count → retry → eventually hard fail."""
    monkeypatch.delenv("CI", raising=False)
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 8

    # All 3 attempts: Phase 1 returns wrong count
    wrong_resp = _mock_llm_response(_beats_json(5))
    mock_cm = _mock_llm_cm(side_effect=[wrong_resp, wrong_resp, wrong_resp])

    with patch("movie_narrator.pipeline.script.is_ci", return_value=False):
        with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
            with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
                with pytest.raises(RuntimeError, match="LLM script generation failed"):
                    generate_script(ctx)


# ── 7. CI mode mock fallback ────────────────────────────────


def test_generate_script_ci_mock_fallback(tmp_path, monkeypatch):
    """CI=1 + LLM failure → mock script with inline_warn."""
    ctx = _make_ctx(tmp_path)
    mock_cm = _mock_llm_cm(side_effect=ConnectionError("unreachable"))

    with patch("movie_narrator.pipeline.script.is_ci", return_value=True):
        with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
            with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
                with patch("movie_narrator.pipeline.script.sleep", return_value=None):
                    result = generate_script(ctx)

    assert result.metadata["script_source"] == "ci_mock"
    assert result.metadata["script_degraded"] is True
    assert len(result.segments) == 4
    assert "test_movie" in result.segments[0].text
    ctx.services.console.inline_warn.assert_called()


def test_generate_script_ci_mock_not_used_in_production(tmp_path, monkeypatch):
    """Non-CI + LLM failure → RuntimeError (no mock fallback)."""
    monkeypatch.delenv("CI", raising=False)
    ctx = _make_ctx(tmp_path)
    mock_cm = _mock_llm_cm(side_effect=ConnectionError("unreachable"))

    with patch("movie_narrator.pipeline.script.is_ci", return_value=False):
        with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
            with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
                with pytest.raises(RuntimeError, match="LLM script generation failed"):
                    generate_script(ctx)


# ── 8. Research context propagation ─────────────────────────


def test_generate_script_research_in_phase1(tmp_path):
    """Research context should appear in Phase 1 prompt, not Phase 2."""
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 3
    ctx.research = ResearchInfo(
        title="测试电影", year="2024", summary="一部关于勇气的电影",
        genres=["动作"], cast=["演员A"], keywords=["勇气"],
    )

    beats_resp = _mock_llm_response(_beats_json(3))
    seg_resp = _mock_llm_response(_segments_json(["s1", "s2", "s3"]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            generate_script(ctx)

    mock_llm = mock_cm.__enter__.return_value
    calls = mock_llm.client.chat.completions.create.call_args_list
    phase1_prompt = calls[0].kwargs["messages"][0]["content"]
    phase2_prompt = calls[1].kwargs["messages"][0]["content"]
    assert "一部关于勇气的电影" in phase1_prompt
    assert "一部关于勇气的电影" not in phase2_prompt


# ── 9. Cross-phase retry ────────────────────────────────────


def test_generate_script_phase1_ok_phase2_fail_then_retry(tmp_path):
    """Phase 1 succeeds but Phase 2 fails → retry both → success.

    This verifies the retry loop wraps both phases together: a Phase 2
    failure triggers a full retry (Phase 1 + Phase 2), not just Phase 2.
    """
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 3

    beats_resp = _mock_llm_response(_beats_json(3))
    seg_resp = _mock_llm_response(_segments_json(["s1", "s2", "s3"]))
    # Attempt 1: Phase 1 OK, Phase 2 fails
    # Attempt 2: Phase 1 OK, Phase 2 OK
    mock_cm = _mock_llm_cm(side_effect=[
        beats_resp,               # attempt 1 Phase 1
        ConnectionError("phase2 fail"),  # attempt 1 Phase 2
        beats_resp,               # attempt 2 Phase 1
        seg_resp,                 # attempt 2 Phase 2
    ])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    assert result.metadata["script_source"] == "llm"
    assert len(result.segments) == 3
    # Verify 4 LLM calls: 2 Phase 1 + 2 Phase 2
    mock_llm = mock_cm.__enter__.return_value
    assert mock_llm.client.chat.completions.create.call_count == 4


def test_generate_script_phase1_ok_phase2_fail_all_retries(tmp_path, monkeypatch):
    """Phase 1 always OK, Phase 2 always fails → hard fail after 3 attempts."""
    monkeypatch.delenv("CI", raising=False)
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = 3

    beats_resp = _mock_llm_response(_beats_json(3))
    # 3 attempts: each Phase 1 OK, Phase 2 fails
    mock_cm = _mock_llm_cm(side_effect=[
        beats_resp, ConnectionError("p2 fail"),
        beats_resp, ConnectionError("p2 fail"),
        beats_resp, ConnectionError("p2 fail"),
    ])

    with patch("movie_narrator.pipeline.script.is_ci", return_value=False):
        with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
            with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
                with pytest.raises(RuntimeError, match="LLM script generation failed"):
                    generate_script(ctx)


# ── 10. None / empty beat filtering ─────────────────────────


def test_phase1_filters_none_beats(tmp_path):
    """None entries in beats list should be filtered, not converted to "None"."""
    ctx = _make_ctx(tmp_path)
    # LLM returns [None, "point1", "point2"] — None should be dropped
    import json
    bad_resp = _mock_llm_response(json.dumps({"beats": [None, "point1", "point2"]}))
    mock_cm = _mock_llm_cm(response=bad_resp)
    mock_llm = mock_cm.__enter__.return_value

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with pytest.raises(ValueError, match="after filtering"):
            _generate_plot_beats(ctx, _mock_settings(), mock_llm, 3)


def test_phase1_filters_string_none_beats(tmp_path):
    """The string "None" should also be filtered (case-insensitive)."""
    ctx = _make_ctx(tmp_path)
    import json
    bad_resp = _mock_llm_response(json.dumps({"beats": ["none", "point1", "point2"]}))
    mock_cm = _mock_llm_cm(response=bad_resp)
    mock_llm = mock_cm.__enter__.return_value

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with pytest.raises(ValueError, match="after filtering"):
            _generate_plot_beats(ctx, _mock_settings(), mock_llm, 3)


def test_phase1_accepts_integer_beats(tmp_path):
    """Integer beats should be converted to strings (not dropped)."""
    ctx = _make_ctx(tmp_path)
    import json
    resp = _mock_llm_response(json.dumps({"beats": [1, 2, 3]}))
    mock_cm = _mock_llm_cm(response=resp)
    mock_llm = mock_cm.__enter__.return_value

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        beats = _generate_plot_beats(ctx, _mock_settings(), mock_llm, 3)
    assert beats == ["1", "2", "3"]


# ── 11. Phase 2 empty text filtering ─────────────────────────


def test_phase2_filters_empty_text_segments(tmp_path):
    """Empty / whitespace-only segments should be dropped."""
    ctx = _make_ctx(tmp_path)
    beats = ["b1", "b2", "b3"]
    # Mix of valid, empty string, whitespace-only, and None text
    seg_resp = _mock_llm_response(
        _segments_json(["valid", "", "   "])
    )
    mock_cm = _mock_llm_cm(response=seg_resp)
    mock_llm = mock_cm.__enter__.return_value

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        segments = _expand_beats_to_script(ctx, _mock_settings(), mock_llm, beats, 3)
    # Only "valid" survives
    assert len(segments) == 1
    assert segments[0].text == "valid"


def test_phase2_all_empty_raises(tmp_path):
    """If all segments are empty → ValueError."""
    ctx = _make_ctx(tmp_path)
    beats = ["b1", "b2"]
    seg_resp = _mock_llm_response(_segments_json(["", "  "]))
    mock_cm = _mock_llm_cm(response=seg_resp)
    mock_llm = mock_cm.__enter__.return_value

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with pytest.raises(ValueError, match="zero segments"):
            _expand_beats_to_script(ctx, _mock_settings(), mock_llm, beats, 2)


# ── 12. Preset end-to-end sentence count (R4 regression guard) ──
#
# These tests固化 R4 的核心发现: 三个 preset 的 prompt_target_sentences
# 必须被两阶段生成严格遵守.  R1-R3 中 LLM 系统性忽略 12/8 目标(总是产出
# 18 句)的回归必须被 CI 永久拦截.
#
# 测试策略: mock LLM 返回恰好目标数量的 beats + segments, 验证
# generate_script 产出的 segment 数量严格等于 preset 目标.


import json


_PRESET_TARGETS = [
    ("douyin-fast", 18),
    ("mainstream-dry", 12),
    ("bilibili-long", 8),
]


@pytest.mark.parametrize("preset_name,expected_count", _PRESET_TARGETS)
def test_preset_sentence_count_enforced(preset_name, expected_count, tmp_path):
    """Each preset's prompt_target_sentences must be strictly enforced.

    This is the CI smoke test for the R4 finding: two-phase generation
    makes sentence count deterministic.  If this test fails, it means
    the prompt_target_sentences contract is broken — the LLM is no
    longer respecting the count constraint.
    """
    from movie_narrator.presets import get_preset

    preset = get_preset(preset_name)
    target = preset.param_dict.get("prompt_target_sentences")
    assert target == expected_count, (
        f"{preset_name} preset has prompt_target_sentences={target}, "
        f"expected {expected_count}"
    )

    # Build context with preset params applied
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = target

    # Mock LLM: Phase 1 returns exactly target beats, Phase 2 returns
    # exactly target segments.  This is the "happy path" — if the
    # two-phase pipeline is working correctly, output count == target.
    beats_resp = _mock_llm_response(_beats_json(target))
    seg_resp = _mock_llm_response(_segments_json([f"s{i}" for i in range(target)]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    assert len(result.segments) == target, (
        f"{preset_name}: expected {target} segments, got {len(result.segments)}. "
        f"prompt_target_sentences contract is broken — R1-R3 style regression."
    )
    assert result.metadata["script_source"] == "llm"
    assert result.metadata["script_beat_count"] == target


def test_preset_sentence_count_with_overshoot_trim(tmp_path):
    """Even if Phase 2 overshoots, trim must enforce the target count.

    This tests the fallback trim path: Phase 2 returns more segments
    than target, but _trim_segments brings it back to exact target.
    Uses bilibili-long (target=8) as the test case.
    """
    from movie_narrator.presets import get_preset

    preset_name = "bilibili-long"
    expected_count = 8
    preset = get_preset(preset_name)
    target = preset.param_dict.get("prompt_target_sentences")
    assert target == expected_count

    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = target

    # Phase 1 returns target beats, Phase 2 returns target+5 segments
    beats_resp = _mock_llm_response(_beats_json(target))
    overshoot = target + 5
    seg_resp = _mock_llm_response(_segments_json([f"s{i}" for i in range(overshoot)]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    assert len(result.segments) == target, (
        f"{preset_name}: overshoot not trimmed — expected {target}, got {len(result.segments)}"
    )


def test_preset_sentence_count_with_undershoot_fails(tmp_path, monkeypatch):
    """If Phase 1 returns fewer beats than target, it must fail (not silently produce fewer).

    This guards against the opposite regression: silently producing
    fewer segments than the target.  Phase 1's count validation should
    raise ValueError, triggering retry.
    """
    monkeypatch.delenv("CI", raising=False)
    target = 12  # mainstream-dry
    ctx = _make_ctx(tmp_path)
    ctx.metadata["prompt_target_sentences"] = target

    # Phase 1 returns only 5 beats (less than target 12)
    beats_resp = _mock_llm_response(_beats_json(5))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, beats_resp, beats_resp])

    with patch("movie_narrator.pipeline.script.is_ci", return_value=False):
        with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
            with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
                with patch("movie_narrator.pipeline.script.sleep", return_value=None):
                    with pytest.raises(RuntimeError, match="LLM script generation failed"):
                        generate_script(ctx)


def test_no_preset_defaults_to_18_sentences(tmp_path):
    """Without prompt_target_sentences, default target is 18 (douyin-fast equivalent).

    This guards backward compatibility: users without a preset still
    get a reasonable default, not zero or an error.
    """
    ctx = _make_ctx(tmp_path)
    # No prompt_target_sentences in metadata — simulates no preset

    beats_resp = _mock_llm_response(_beats_json(18))
    seg_resp = _mock_llm_response(_segments_json([f"s{i}" for i in range(18)]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    assert len(result.segments) == 18
    assert result.metadata["script_segment_count"] == 18


# ── 13. Dynamic sentence count (duration-aware) ────────────
#
# R5b 发现: 方案 B — 按时长动态调整句数, 保持每段字数自然.
# 句数 = round(duration / target_segment_duration)
# 60s 时与 preset 的 prompt_target_sentences 一致 (向后兼容).
# 非 60s 时句数按比例缩放, max_chars 不变.


_DYNAMIC_CASES = [
    # (preset_name, duration, expected_count)
    # 60s baseline — matches preset's prompt_target_sentences
    ("douyin-fast", 60, 18),      # 60 / 3.3 = 18.2 → 18
    ("mainstream-dry", 60, 12),   # 60 / 5.0 = 12.0 → 12
    ("bilibili-long", 60, 8),     # 60 / 7.5 = 8.0 → 8
    # 120s — double the sentences, same per-sentence length
    ("douyin-fast", 120, 36),     # 120 / 3.3 = 36.4 → 36
    ("mainstream-dry", 120, 24),  # 120 / 5.0 = 24.0 → 24
    ("bilibili-long", 120, 16),   # 120 / 7.5 = 16.0 → 16
    # 90s — 1.5x sentences
    ("bilibili-long", 90, 12),    # 90 / 7.5 = 12.0 → 12
    # 30s — half sentences
    ("douyin-fast", 30, 9),       # 30 / 3.3 = 9.1 → 9
    ("bilibili-long", 30, 4),     # 30 / 7.5 = 4.0 → 4
]


@pytest.mark.parametrize("preset_name,duration,expected_count", _DYNAMIC_CASES)
def test_dynamic_sentence_count_by_duration(preset_name, duration, expected_count, tmp_path):
    """Sentence count must scale with duration, keeping per-sentence length fixed.

    This is the core of 方案 B: longer videos get more sentences,
    not longer sentences. The per-sentence max_chars stays constant
    (defined by preset), but the total count adjusts to fill the duration.

    Formula: n = round(duration / target_segment_duration)
    """
    from movie_narrator.presets import get_preset

    preset = get_preset(preset_name)
    seg_dur = preset.param_dict.get("prompt_target_segment_duration")
    assert seg_dur is not None, f"{preset_name} missing prompt_target_segment_duration"

    # Verify formula matches expected
    calculated = max(1, round(duration / seg_dur))
    assert calculated == expected_count, (
        f"{preset_name} duration={duration}: "
        f"round({duration}/{seg_dur}) = {calculated}, expected {expected_count}"
    )

    # End-to-end: generate_script with this duration should produce expected count
    ctx = _make_ctx(tmp_path, duration=duration)
    ctx.metadata["prompt_target_sentences"] = preset.param_dict.get("prompt_target_sentences")
    ctx.metadata["prompt_target_segment_duration"] = seg_dur

    beats_resp = _mock_llm_response(_beats_json(expected_count))
    seg_resp = _mock_llm_response(_segments_json([f"s{i}" for i in range(expected_count)]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            result = generate_script(ctx)

    assert len(result.segments) == expected_count, (
        f"{preset_name} duration={duration}s: expected {expected_count} segments, "
        f"got {len(result.segments)}. Dynamic count formula is broken."
    )


def test_dynamic_count_60s_matches_preset_baseline(tmp_path):
    """At 60s, dynamic count must equal preset's prompt_target_sentences.

    This is the backward compatibility guarantee: existing 60s videos
    behave exactly as before.
    """
    from movie_narrator.presets import get_preset

    for name in ("douyin-fast", "mainstream-dry", "bilibili-long"):
        preset = get_preset(name)
        base_count = preset.param_dict.get("prompt_target_sentences")
        seg_dur = preset.param_dict.get("prompt_target_segment_duration")
        dynamic_count = round(60 / seg_dur)
        assert dynamic_count == base_count, (
            f"{name}: 60s dynamic count {dynamic_count} != "
            f"preset baseline {base_count}"
        )
