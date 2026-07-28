"""TMDB (The Movie Database) research provider.

NA-M2-S1+: external fact-verification data source.

Provides two capabilities:
1. **Standalone research provider** — fetches movie metadata directly from
   TMDB's API, bypassing LLM hallucination risk entirely for factual fields
   (director, cast, genres, year).
2. **Card enrichment** — cross-validates an LLM-sourced ``MovieCard`` against
   TMDB data, correcting wrong director/cast/year entries and filling in
   missing fields.

The TMDB API is accessed via ``urllib.request`` (stdlib) so no extra
dependency is needed. The provider gracefully degrades when:
  - no API key is configured (``MN_TMDB_API_KEY``)
  - the movie is not found on TMDB
  - the API request fails (network error, rate limit, etc.)

TMDB API reference: https://developer.themoviedb.org/reference/intro/getting-started
"""

from __future__ import annotations

import json
import logging
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from ..config import Settings
from ..models import Context, MovieCard, ResearchInfo
from .registry import register_research

logger = logging.getLogger(__name__)

# TMDB API endpoints used:
#   GET /search/movie — search by title
#   GET /movie/{id} — detailed info with credits appended
_TMDB_SEARCH_PATH = "/search/movie"
_TMDB_MOVIE_PATH = "/movie/{movie_id}"


def _tmdb_get(
    base_url: str,
    path: str,
    api_key: str,
    params: Dict[str, str],
    timeout: int = 10,
) -> Optional[Dict[str, Any]]:
    """Make a GET request to the TMDB API and return parsed JSON.

    Returns ``None`` on any error (network, HTTP, parse). Errors are
    logged at DEBUG level so they don't pollute production logs.
    """
    params["api_key"] = api_key
    url = base_url.rstrip("/") + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if resp.status != 200:
                logger.debug(f"TMDB API returned status {resp.status} for {path}")
                return None
            body = resp.read().decode("utf-8")
            return json.loads(body)
    except Exception as e:
        logger.debug(f"TMDB API request failed for {path}: {e}")
        return None


def _search_movie(
    base_url: str, api_key: str, query: str, language: str
) -> Optional[Dict[str, Any]]:
    """Search TMDB for a movie by title. Returns the first result or None."""
    data = _tmdb_get(base_url, _TMDB_SEARCH_PATH, api_key, {
        "query": query,
        "language": language,
        "page": "1",
        "include_adult": "false",
    })
    if not data or not isinstance(data, dict):
        return None
    results = data.get("results")
    if not isinstance(results, list) or not results:
        return None
    return results[0]


def _get_movie_details(
    base_url: str, api_key: str, movie_id: int, language: str
) -> Optional[Dict[str, Any]]:
    """Fetch detailed movie info from TMDB with credits appended."""
    path = _TMDB_MOVIE_PATH.format(movie_id=movie_id)
    return _tmdb_get(base_url, path, api_key, {
        "language": language,
        "append_to_response": "credits",
    })


def _extract_director(credits: Dict[str, Any]) -> Optional[str]:
    """Extract the director name from TMDB credits."""
    crew = credits.get("crew") or []
    if not isinstance(crew, list):
        return None
    for member in crew:
        if not isinstance(member, dict):
            continue
        if member.get("job") == "Director":
            name = member.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _extract_cast(credits: Dict[str, Any], limit: int = 5) -> List[str]:
    """Extract top-N cast names from TMDB credits."""
    cast_list = credits.get("cast") or []
    if not isinstance(cast_list, list):
        return []
    result = []
    for member in cast_list[:limit]:
        if not isinstance(member, dict):
            continue
        name = member.get("name")
        if isinstance(name, str) and name.strip():
            result.append(name.strip())
    return result


def _extract_genres(details: Dict[str, Any]) -> List[str]:
    """Extract genre names from TMDB movie details."""
    genres = details.get("genres") or []
    if not isinstance(genres, list):
        return []
    result = []
    for g in genres:
        if isinstance(g, dict):
            name = g.get("name")
            if isinstance(name, str) and name.strip():
                result.append(name.strip())
    return result


def _build_movie_card(details: Dict[str, Any]) -> MovieCard:
    """Build a MovieCard from TMDB movie details."""
    credits = details.get("credits") or {}
    return MovieCard(
        title=str(details.get("title") or details.get("original_title") or ""),
        year=str(details.get("release_date", ""))[:4] or None,
        genres=_extract_genres(details),
        summary=str(details.get("overview") or ""),
        director=_extract_director(credits),
        cast=_extract_cast(credits),
        set_pieces=[],  # TMDB doesn't provide named scenes
    )


def _build_research_info(details: Dict[str, Any]) -> ResearchInfo:
    """Build a ResearchInfo from TMDB movie details."""
    credits = details.get("credits") or {}
    cast = _extract_cast(credits, limit=10)
    genres = _extract_genres(details)
    # Derive keywords from genre names + cast names (TMDB has no keyword
    # field in the basic details response).
    keywords = genres[:3] + cast[:2]
    return ResearchInfo(
        title=str(details.get("title") or details.get("original_title") or ""),
        year=str(details.get("release_date", ""))[:4] or None,
        summary=str(details.get("overview") or ""),
        genres=genres,
        cast=cast,
        keywords=keywords,
    )


# ── Standalone TMDB research provider ─────────────────────


@register_research("tmdb")
def tmdb_research(ctx: Context, settings: Settings) -> ResearchInfo:
    """Fetch movie research data from TMDB API.

    Used when ``research_provider: "tmdb"`` is configured. Requires
    ``MN_TMDB_API_KEY`` to be set. Raises ``RuntimeError`` if the key
    is missing — the research step's retry loop will handle it.
    """
    api_key = settings.tmdb_api_key
    if not api_key:
        raise RuntimeError(
            "TMDB research provider requires MN_TMDB_API_KEY to be set. "
            "Either configure it in .env or switch to the 'llm' provider."
        )

    # Search for the movie
    search_result = _search_movie(
        settings.tmdb_base_url, api_key, ctx.movie_name, settings.tmdb_language
    )
    if not search_result or not isinstance(search_result, dict):
        raise RuntimeError(
            f"Movie '{ctx.movie_name}' not found on TMDB"
        )

    movie_id = search_result.get("id")
    if not isinstance(movie_id, int):
        raise RuntimeError(f"TMDB search returned no valid movie ID for '{ctx.movie_name}'")

    # Fetch detailed info with credits
    details = _get_movie_details(
        settings.tmdb_base_url, api_key, movie_id, settings.tmdb_language
    )
    if not details or not isinstance(details, dict):
        raise RuntimeError(f"TMDB API returned no details for movie ID {movie_id}")

    # Build and store MovieCard
    ctx.metadata["movie_card"] = _build_movie_card(details)
    ctx.metadata["movie_card_source"] = "tmdb"

    return _build_research_info(details)


# ── Card enrichment (cross-validation) ────────────────────


def enrich_movie_card_with_tmdb(
    card: MovieCard, ctx: Context, settings: Settings
) -> MovieCard:
    """Cross-validate and enrich an LLM-sourced MovieCard with TMDB data.

    When TMDB is available (API key configured and movie found), this
    function overrides LLM-sourced factual fields (director, cast, genres,
    year) with TMDB-verified data, reducing hallucination. Fields that
    TMDB doesn't provide (set_pieces, summary) are kept from the LLM card.

    When TMDB is unavailable (no key, movie not found, network error),
    the original card is returned unchanged — the feature is a soft
    enhancement.

    Args:
        card: The LLM-sourced MovieCard to enrich.
        ctx: Pipeline context (used for movie name).
        settings: Settings (used for TMDB config).

    Returns:
        An enriched MovieCard, or the original card if TMDB is unavailable.
    """
    api_key = settings.tmdb_api_key
    if not api_key:
        logger.debug("TMDB enrichment skipped: no API key configured")
        return card

    search_result = _search_movie(
        settings.tmdb_base_url, api_key, ctx.movie_name, settings.tmdb_language
    )
    if not search_result or not isinstance(search_result, dict):
        logger.debug(f"TMDB enrichment: movie '{ctx.movie_name}' not found")
        return card

    movie_id = search_result.get("id")
    if not isinstance(movie_id, int):
        return card

    details = _get_movie_details(
        settings.tmdb_base_url, api_key, movie_id, settings.tmdb_language
    )
    if not details or not isinstance(details, dict):
        return card

    # Override factual fields with TMDB-verified data
    tmdb_card = _build_movie_card(details)

    # Track which fields were corrected
    corrections: List[str] = []
    if tmdb_card.director and card.director and tmdb_card.director != card.director:
        corrections.append(f"director: '{card.director}' -> '{tmdb_card.director}'")
    if tmdb_card.year and card.year and tmdb_card.year != card.year:
        corrections.append(f"year: '{card.year}' -> '{tmdb_card.year}'")

    # Build enriched card: TMDB for factual fields, LLM for creative fields
    enriched = MovieCard(
        title=tmdb_card.title or card.title,
        year=tmdb_card.year or card.year,
        genres=tmdb_card.genres if tmdb_card.genres else card.genres,
        # Keep LLM summary (TMDB overview is often too generic for narration)
        summary=card.summary,
        director=tmdb_card.director or card.director,
        cast=tmdb_card.cast if tmdb_card.cast else card.cast,
        # Keep LLM set_pieces (TMDB doesn't provide named scenes)
        set_pieces=card.set_pieces,
    )

    if corrections:
        ctx.metadata["tmdb_corrections"] = corrections
        logger.info(
            f"TMDB enrichment corrected {len(corrections)} field(s): {corrections}"
        )

    ctx.metadata["movie_card_source"] = "tmdb_enriched"
    return enriched
