"""Tests for TMDB research provider and card enrichment (NA-M2-S1+).

Verifies that:
1. TMDB provider is registered as "tmdb"
2. tmdb_research raises RuntimeError without API key
3. _extract_director parses crew correctly
4. _extract_cast respects limit
5. _extract_genres parses genres
6. _build_movie_card builds correct card
7. enrich_movie_card_with_tmdb returns original card when no API key
8. enrich_movie_card_with_tmdb returns original card when movie not found
9. enrich_movie_card_with_tmdb overrides factual fields when TMDB data available
"""

from unittest.mock import MagicMock, patch, PropertyMock

from movie_narrator.models import MovieCard, ResearchInfo
from movie_narrator.providers.registry import research_registry
from movie_narrator.providers.tmdb import (
    _extract_director,
    _extract_cast,
    _extract_genres,
    _build_movie_card,
    _build_research_info,
    tmdb_research,
    enrich_movie_card_with_tmdb,
)


class TestTmdbProviderRegistration:
    def test_tmdb_provider_is_registered(self):
        assert research_registry.contains("tmdb")

    def test_tmdb_provider_factory_is_callable(self):
        factory = research_registry.get("tmdb")
        assert callable(factory)


class TestTmdbResearchNoApiKey:
    def test_raises_runtime_error_without_api_key(self):
        ctx = MagicMock()
        ctx.movie_name = "Test Movie"
        ctx.metadata = {}
        settings = MagicMock()
        settings.tmdb_api_key = None
        settings.tmdb_base_url = "https://api.themoviedb.org/3"
        settings.tmdb_language = "zh-CN"

        try:
            tmdb_research(ctx, settings)
            assert False, "Should have raised RuntimeError"
        except RuntimeError as e:
            assert "MN_TMDB_API_KEY" in str(e)


class TestExtractDirector:
    def test_finds_director(self):
        credits = {
            "crew": [
                {"job": "Producer", "name": "Producer A"},
                {"job": "Director", "name": "Director B"},
            ]
        }
        assert _extract_director(credits) == "Director B"

    def test_no_director_returns_none(self):
        credits = {"crew": [{"job": "Producer", "name": "A"}]}
        assert _extract_director(credits) is None

    def test_empty_crew(self):
        assert _extract_director({"crew": []}) is None

    def test_missing_crew_key(self):
        assert _extract_director({}) is None


class TestExtractCast:
    def test_respects_limit(self):
        credits = {"cast": [{"name": f"Actor {i}"} for i in range(10)]}
        result = _extract_cast(credits, limit=3)
        assert len(result) == 3
        assert result == ["Actor 0", "Actor 1", "Actor 2"]

    def test_empty_cast(self):
        assert _extract_cast({"cast": []}) == []

    def test_skips_non_dict_entries(self):
        credits = {"cast": ["not a dict", None, {"name": "Real Actor"}]}
        assert _extract_cast(credits) == ["Real Actor"]


class TestExtractGenres:
    def test_parses_genres(self):
        details = {"genres": [{"name": "Action"}, {"name": "Drama"}]}
        assert _extract_genres(details) == ["Action", "Drama"]

    def test_empty_genres(self):
        assert _extract_genres({"genres": []}) == []

    def test_missing_genres(self):
        assert _extract_genres({}) == []


class TestBuildMovieCard:
    def test_builds_card_from_details(self):
        details = {
            "title": "Test Movie",
            "release_date": "2023-05-15",
            "overview": "A great movie",
            "genres": [{"name": "Action"}],
            "credits": {
                "crew": [{"job": "Director", "name": "Director A"}],
                "cast": [{"name": "Actor A"}],
            },
        }
        card = _build_movie_card(details)
        assert card.title == "Test Movie"
        assert card.year == "2023"
        assert card.director == "Director A"
        assert card.genres == ["Action"]
        assert "Actor A" in card.cast
        assert card.summary == "A great movie"
        assert card.set_pieces == []

    def test_falls_back_to_original_title(self):
        details = {"original_title": "Original", "release_date": ""}
        card = _build_movie_card(details)
        assert card.title == "Original"
        assert card.year is None


class TestBuildResearchInfo:
    def test_builds_research_info(self):
        details = {
            "title": "Test",
            "release_date": "2020-01-01",
            "overview": "Overview",
            "genres": [{"name": "Drama"}],
            "credits": {"cast": [{"name": "Actor A"}, {"name": "Actor B"}]},
        }
        info = _build_research_info(details)
        assert info.title == "Test"
        assert info.year == 2020
        assert info.genres == ["Drama"]
        assert "Actor A" in info.cast
        assert "Drama" in info.keywords


class TestEnrichMovieCard:
    def _make_settings(self, api_key="test_key"):
        settings = MagicMock()
        settings.tmdb_api_key = api_key
        settings.tmdb_base_url = "https://api.themoviedb.org/3"
        settings.tmdb_language = "zh-CN"
        return settings

    def test_no_api_key_returns_original_card(self):
        card = MovieCard(title="Test", director="LLM Director")
        ctx = MagicMock()
        ctx.movie_name = "Test"
        ctx.metadata = {}
        settings = self._make_settings(api_key=None)
        result = enrich_movie_card_with_tmdb(card, ctx, settings)
        assert result is card

    @patch("movie_narrator.providers.tmdb._search_movie")
    def test_movie_not_found_returns_original_card(self, mock_search):
        mock_search.return_value = None
        card = MovieCard(title="Unknown", director="LLM")
        ctx = MagicMock()
        ctx.movie_name = "Unknown"
        ctx.metadata = {}
        settings = self._make_settings()
        result = enrich_movie_card_with_tmdb(card, ctx, settings)
        assert result is card

    @patch("movie_narrator.providers.tmdb._get_movie_details")
    @patch("movie_narrator.providers.tmdb._search_movie")
    def test_overrides_factual_fields(self, mock_search, mock_details):
        mock_search.return_value = {"id": 123, "title": "Test"}
        mock_details.return_value = {
            "title": "Test Movie TMDB",
            "release_date": "2023-06-01",
            "overview": "TMDB overview",
            "genres": [{"name": "Action"}, {"name": "Sci-Fi"}],
            "credits": {
                "crew": [{"job": "Director", "name": "Real Director"}],
                "cast": [{"name": "Real Actor 1"}, {"name": "Real Actor 2"}],
            },
        }
        card = MovieCard(
            title="Test",
            director="Wrong Director",
            cast=["Wrong Actor"],
            genres=["Wrong Genre"],
            summary="LLM summary",
            set_pieces=["Iconic Scene"],
        )
        ctx = MagicMock()
        ctx.movie_name = "Test"
        ctx.metadata = {}
        settings = self._make_settings()

        result = enrich_movie_card_with_tmdb(card, ctx, settings)

        assert result.director == "Real Director"
        assert result.cast == ["Real Actor 1", "Real Actor 2"]
        assert result.genres == ["Action", "Sci-Fi"]
        assert result.year == "2023"
        # LLM fields preserved
        assert result.summary == "LLM summary"
        assert result.set_pieces == ["Iconic Scene"]
        # Metadata updated
        assert ctx.metadata.get("movie_card_source") == "tmdb_enriched"

    @patch("movie_narrator.providers.tmdb._get_movie_details")
    @patch("movie_narrator.providers.tmdb._search_movie")
    def test_tracks_corrections(self, mock_search, mock_details):
        mock_search.return_value = {"id": 1, "title": "Test"}
        mock_details.return_value = {
            "title": "Test",
            "release_date": "2023-01-01",
            "genres": [],
            "credits": {
                "crew": [{"job": "Director", "name": "Correct Director"}],
                "cast": [],
            },
        }
        card = MovieCard(title="Test", director="Wrong Director", year="2020")
        ctx = MagicMock()
        ctx.movie_name = "Test"
        ctx.metadata = {}
        settings = self._make_settings()

        enrich_movie_card_with_tmdb(card, ctx, settings)

        corrections = ctx.metadata.get("tmdb_corrections", [])
        assert any("director" in c for c in corrections)
        assert any("year" in c for c in corrections)

    @patch("movie_narrator.providers.tmdb._get_movie_details")
    @patch("movie_narrator.providers.tmdb._search_movie")
    def test_no_corrections_when_matching(self, mock_search, mock_details):
        mock_search.return_value = {"id": 1, "title": "Test"}
        mock_details.return_value = {
            "title": "Test",
            "release_date": "2023-01-01",
            "genres": [],
            "credits": {
                "crew": [{"job": "Director", "name": "Same Director"}],
                "cast": [],
            },
        }
        card = MovieCard(title="Test", director="Same Director", year="2023")
        ctx = MagicMock()
        ctx.movie_name = "Test"
        ctx.metadata = {}
        settings = self._make_settings()

        enrich_movie_card_with_tmdb(card, ctx, settings)
        assert "tmdb_corrections" not in ctx.metadata
