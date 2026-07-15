"""Tests for preflight LLM/TTS validation."""

import os
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.config import Settings, TTSProviderType
from movie_narrator.models import Context, Services
from movie_narrator.pipeline.preflight import (
    PreflightError,
    _check_llm,
    _check_tts,
    run_preflight,
)


def _make_ctx(tmp_path):
    return Context(
        movie_name="test",
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )


def test_check_llm_skipped_in_ci(tmp_path, monkeypatch):
    """CI mode skips LLM probe entirely."""
    monkeypatch.setenv("CI", "1")
    ctx = _make_ctx(tmp_path)
    _check_llm(ctx)  # should not raise


def test_check_llm_raises_on_connection_error(tmp_path, monkeypatch):
    """Non-CI LLM probe raises PreflightError on connection failure."""
    monkeypatch.delenv("CI", raising=False)
    ctx = _make_ctx(tmp_path)

    mock_llm = MagicMock()
    mock_llm.client.chat.completions.create.side_effect = ConnectionError("refused")
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_llm)
    mock_cm.__exit__ = MagicMock(return_value=False)

    with patch("movie_narrator.pipeline.preflight.get_llm_client", return_value=mock_cm):
        with patch("movie_narrator.pipeline.preflight.is_ci", return_value=False):
            with pytest.raises(PreflightError) as exc_info:
                _check_llm(ctx)
    assert "LLM not reachable" in str(exc_info.value)
    assert "MN_LLM_BASE_URL" in str(exc_info.value)


def test_check_llm_passes_on_success(tmp_path, monkeypatch):
    """Non-CI LLM probe passes when the API call succeeds."""
    monkeypatch.delenv("CI", raising=False)
    ctx = _make_ctx(tmp_path)

    mock_llm = MagicMock()
    mock_llm.client.chat.completions.create.return_value = MagicMock()
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_llm)
    mock_cm.__exit__ = MagicMock(return_value=False)

    with patch("movie_narrator.pipeline.preflight.get_llm_client", return_value=mock_cm):
        with patch("movie_narrator.pipeline.preflight.is_ci", return_value=False):
            _check_llm(ctx)  # should not raise


def test_check_tts_edge_no_error(tmp_path):
    """Edge TTS requires no probe — should always pass."""
    ctx = _make_ctx(tmp_path)
    settings = MagicMock()
    settings.tts_provider = TTSProviderType.EDGE
    with patch("movie_narrator.pipeline.preflight.get_settings", return_value=settings):
        _check_tts(ctx)  # should not raise


def test_check_tts_openai_missing_key_raises(tmp_path):
    """OpenAI TTS with missing credentials raises PreflightError."""
    from movie_narrator.utils.errors import ConfigError

    ctx = _make_ctx(tmp_path)
    settings = MagicMock()
    settings.tts_provider = TTSProviderType.OPENAI

    with patch("movie_narrator.pipeline.preflight.get_settings", return_value=settings):
        with patch("movie_narrator.tts.get_tts_provider", side_effect=ConfigError("missing key")):
            with pytest.raises(PreflightError) as exc_info:
                _check_tts(ctx)
    assert "not properly configured" in str(exc_info.value)


def test_check_tts_mimo_ok(tmp_path):
    """MiMo TTS with valid credentials passes."""
    ctx = _make_ctx(tmp_path)
    settings = MagicMock()
    settings.tts_provider = TTSProviderType.MIMO

    mock_provider = MagicMock()
    with patch("movie_narrator.pipeline.preflight.get_settings", return_value=settings):
        with patch("movie_narrator.tts.get_tts_provider", return_value=mock_provider):
            _check_tts(ctx)  # should not raise


def test_run_preflight_integration_ci(tmp_path, monkeypatch):
    """Full preflight in CI mode: both checks skipped/passed."""
    monkeypatch.setenv("CI", "1")
    ctx = _make_ctx(tmp_path)
    settings = MagicMock()
    settings.tts_provider = TTSProviderType.EDGE

    with patch("movie_narrator.pipeline.preflight.get_settings", return_value=settings):
        run_preflight(ctx)  # should not raise

    ctx.services.console.step.assert_called_with("preflight")
    ctx.services.console.step_ok.assert_called_once()
