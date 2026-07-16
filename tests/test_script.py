"""Tests for the generate_script pipeline step.

Covers: LLM success path, retry-then-hard-fail, research context
injection, empty-response handling, and script_source metadata.
"""

from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Context, ResearchInfo, Services, ScriptSegment
from movie_narrator.pipeline.script import generate_script


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


# ── 1. LLM success ──────────────────────────────────────────


def test_generate_script_llm_success(tmp_path):
    """Valid JSON from LLM → segments set, script_source = 'llm'."""
    ctx = _make_ctx(tmp_path)
    json_resp = '{"segments": [{"text": "一段精彩的旁白。"}, {"text": "另一段旁白。"}]}'
    mock_cm = _mock_llm_cm(response=_mock_llm_response(json_resp))

    with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
        result = generate_script(ctx)

    assert len(result.segments) == 2
    assert result.segments[0].text == "一段精彩的旁白。"
    assert result.metadata["script_source"] == "llm"


# ── 2. LLM failure → hard fail ─────────────────────────────


def test_generate_script_hard_fails_on_all_retries(tmp_path, monkeypatch):
    """3 consecutive LLM failures → RuntimeError (no fake mock fallback)."""
    monkeypatch.delenv("CI", raising=False)  # ensure not in CI mode
    ctx = _make_ctx(tmp_path)
    mock_cm = _mock_llm_cm(side_effect=ConnectionError("unreachable"))

    with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
        with patch("movie_narrator.pipeline.script.sleep", return_value=None):
            with pytest.raises(RuntimeError, match="LLM script generation failed"):
                generate_script(ctx)


# ── 3. Research context injection ───────────────────────────


def test_generate_script_includes_research_context(tmp_path):
    """When ctx.research has a summary, it should be included in the prompt."""
    ctx = _make_ctx(tmp_path)
    ctx.research = ResearchInfo(
        title="测试电影",
        year="2024",
        summary="一部关于勇气的电影",
        genres=["动作"],
        cast=["演员A"],
        keywords=["勇气"],
    )
    json_resp = '{"segments": [{"text": "旁白"}]}'
    mock_cm = _mock_llm_cm(response=_mock_llm_response(json_resp))

    with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm) as m:
        generate_script(ctx)
        # Verify the LLM was called
        mock_llm = mock_cm.__enter__.return_value
        call_args = mock_llm.client.chat.completions.create.call_args
        prompt_content = call_args.kwargs["messages"][0]["content"]
        assert "一部关于勇气的电影" in prompt_content


def test_generate_script_without_research_context(tmp_path):
    """When ctx.research is empty, no research block is added to prompt."""
    ctx = _make_ctx(tmp_path)
    json_resp = '{"segments": [{"text": "旁白"}]}'
    mock_cm = _mock_llm_cm(response=_mock_llm_response(json_resp))

    with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
        generate_script(ctx)
        mock_llm = mock_cm.__enter__.return_value
        call_args = mock_llm.client.chat.completions.create.call_args
        prompt_content = call_args.kwargs["messages"][0]["content"]
        assert "Research context" not in prompt_content


# ── 4. Empty segments triggers retry → hard fail ───────────


def test_generate_script_empty_segments_triggers_retry(tmp_path, monkeypatch):
    """LLM returns empty segments → ValueError → retry → eventually hard fail."""
    monkeypatch.delenv("CI", raising=False)  # ensure not in CI mode
    ctx = _make_ctx(tmp_path)
    empty_resp = _mock_llm_response('{"segments": []}')
    mock_cm = _mock_llm_cm(side_effect=[
        empty_resp,
        empty_resp,
        empty_resp,
    ])

    with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
        with patch("movie_narrator.pipeline.script.sleep", return_value=None):
            with pytest.raises(RuntimeError, match="LLM script generation failed"):
                generate_script(ctx)


# ── 5. Retry succeeds on second attempt ─────────────────────


def test_generate_script_retry_succeeds(tmp_path):
    """First attempt fails, second succeeds → script_source = 'llm'."""
    ctx = _make_ctx(tmp_path)
    good_resp = _mock_llm_response('{"segments": [{"text": "成功的旁白"}]}')
    mock_cm = _mock_llm_cm(side_effect=[
        ConnectionError("timeout"),
        good_resp,
    ])

    with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
        with patch("movie_narrator.pipeline.script.sleep", return_value=None):
            result = generate_script(ctx)

    assert result.metadata["script_source"] == "llm"
    assert result.segments[0].text == "成功的旁白"
