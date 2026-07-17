import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from movie_narrator.models import Context
from movie_narrator.pipeline.research import research_plot


def test_research_skipped_no_file(tmp_path):
    ctx = Context(movie_name="X", output_dir=str(tmp_path))
    ctx.metadata["research_enabled"] = False
    research_plot(ctx)
    assert ctx.status.research == "skipped"
    assert not (tmp_path / "research.json").exists()


def test_research_unknown_provider_failed(tmp_path):
    ctx = Context(movie_name="X", output_dir=str(tmp_path))
    ctx.metadata["research_enabled"] = True
    with patch("movie_narrator.pipeline.research.get_settings") as gs:
        gs.return_value.research_provider = "tmdb"
        gs.return_value.research_retries = 3
        gs.return_value.research_retry_delay = 0
        research_plot(ctx)
    assert ctx.status.research == "failed"
    assert (tmp_path / "research.json").exists()
    envelope = json.loads((tmp_path / "research.json").read_text(encoding="utf-8"))
    assert envelope["status"] == "failed"
    assert envelope["error"] is not None
    assert isinstance(envelope["research"], dict)


def test_research_llm_success(tmp_path):
    ctx = Context(movie_name="Inception", output_dir=str(tmp_path))
    ctx.metadata["research_enabled"] = True

    mock_response = MagicMock()
    mock_response.choices[0].message.content = (
        '{"title": "Inception", "year": 2010, "summary": "A thief who steals secrets.",'
        ' "genres": ["Sci-Fi"], "cast": ["DiCaprio"], "keywords": ["dreams"]}'
    )

    with patch("movie_narrator.pipeline.research.get_settings") as gs, \
         patch("movie_narrator.pipeline.research.get_llm_client") as gl:
        gs.return_value.research_provider = "llm"
        gs.return_value.research_retries = 3
        gs.return_value.research_retry_delay = 0
        gs.return_value.research_temperature = 0.3
        gs.return_value.research_max_tokens = 1024
        gl.return_value.__enter__.return_value = gl.return_value
        gl.return_value.client.chat.completions.create.return_value = mock_response
        research_plot(ctx)

    assert ctx.status.research == "success"
    assert ctx.research.title == "Inception"
    assert ctx.research.year == 2010
    assert (tmp_path / "research.json").exists()
    envelope = json.loads((tmp_path / "research.json").read_text(encoding="utf-8"))
    assert envelope["status"] == "success"
    assert envelope["error"] is None
    assert isinstance(envelope["research"], dict)
    assert envelope["research"]["title"] == "Inception"
    assert envelope["research"]["year"] == 2010
    assert envelope["research"]["summary"]
    assert isinstance(envelope["research"]["genres"], list)
    assert isinstance(envelope["research"]["cast"], list)
    assert isinstance(envelope["research"]["keywords"], list)
