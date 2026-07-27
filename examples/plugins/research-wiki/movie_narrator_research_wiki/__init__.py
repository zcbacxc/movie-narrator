"""WikiResearchPlugin — example research provider plugin.

Registers a custom research provider ``wiki`` that fetches movie
information from Wikipedia's REST API instead of using an LLM.

This demonstrates the ``@register_research`` decorator and the
ResearchInfo return contract. The provider is selected by setting
``research_provider: wiki`` in job.yaml params.
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any

from movie_narrator import Context, PluginContext, ResearchInfo, register_research

WIKI_API = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"
WIKI_SEARCH = "https://en.wikipedia.org/w/api.php"


def _search_wikipedia(movie: str) -> str | None:
    """Search Wikipedia for a movie title and return the best-matching page title."""
    params = urllib.parse.urlencode({
        "action": "query",
        "list": "search",
        "srsearch": f"{movie} film",
        "srlimit": "1",
        "format": "json",
        "origin": "*",
    })
    url = f"{WIKI_SEARCH}?{params}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            results = data.get("query", {}).get("search", [])
            if results:
                return results[0]["title"]
    except Exception:
        pass
    return None


def _fetch_summary(page_title: str) -> dict[str, Any]:
    """Fetch the Wikipedia summary for a page title."""
    encoded = urllib.parse.quote(page_title, safe="")
    url = WIKI_API.format(title=encoded)
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _extract_year(extract: str) -> int | None:
    """Best-effort extraction of a 4-digit year from the summary text."""
    import re
    match = re.search(r"\b(19\d{2}|20\d{2})\b", extract)
    return int(match.group(1)) if match else None


def _extract_genres(extract: str) -> list[str]:
    """Best-effort extraction of genre keywords from the summary text."""
    genre_words = [
        "action", "adventure", "comedy", "drama", "horror", "thriller",
        "romance", "sci-fi", "science fiction", "fantasy", "mystery",
        "crime", "animation", "documentary", "biographical", "musical",
        "war", "western", "family", "sports",
    ]
    lower = extract.lower()
    return list(dict.fromkeys(g.title() for g in genre_words if g in lower))


class WikiResearchPlugin:
    """Plugin that registers a Wikipedia-based research provider."""

    name = "research-wiki"

    def register(self, ctx: PluginContext) -> None:
        """Register the wiki research provider."""
        ctx.research.register("wiki", _research_via_wiki)


def _research_via_wiki(ctx: Context, settings) -> ResearchInfo:
    """Fetch movie research data from Wikipedia's REST API.

    This function is called by the pipeline when ``research_provider``
    is set to ``"wiki"`` in the job configuration.

    Args:
        ctx: Pipeline context containing ``ctx.movie_name``.
        settings: Project settings (unused — Wikipedia API needs no key).

    Returns:
        ResearchInfo populated from Wikipedia data.

    Raises:
        RuntimeError: if Wikipedia lookup fails entirely.
    """
    movie = ctx.movie_name

    page_title = _search_wikipedia(movie)
    if not page_title:
        page_title = movie

    summary_data = _fetch_summary(page_title)
    extract = summary_data.get("extract", "")

    return ResearchInfo(
        title=summary_data.get("title", movie),
        year=_extract_year(extract),
        summary=extract[:500] if extract else "",
        genres=_extract_genres(extract),
        cast=[],
        keywords=[],
    )
