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

import http.client
import urllib.error
from io import BytesIO
from unittest.mock import MagicMock, patch, PropertyMock, call

from movie_narrator.models import MovieCard, ResearchInfo
from movie_narrator.providers.registry import research_registry
from movie_narrator.providers.tmdb import (
    _TMDB_CACHE,
    _extract_director,
    _extract_cast,
    _extract_genres,
    _build_movie_card,
    _build_research_info,
    _tmdb_get,
    _search_movie,
    tmdb_research,
    enrich_movie_card_with_tmdb,
)


def _make_response(status: int, body: str, headers=None):
    """Build a mock HTTP response usable as a context manager."""
    resp = MagicMock()
    resp.status = status
    resp.read.return_value = body.encode("utf-8")
    resp.headers = headers if headers is not None else http.client.HTTPMessage()
    resp.__enter__.return_value = resp
    resp.__exit__.return_value = False
    return resp


def _make_http_error(code: int, headers=None):
    """Build an HTTPError instance for testing error paths."""
    hdrs = headers if headers is not None else http.client.HTTPMessage()
    return urllib.error.HTTPError(
        "http://test", code, "Error", hdrs, BytesIO(b"")
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


# ── Robustness tests: retry, cache, rate-limit, error handling ──


class TestTmdbRetry:
    """Verify HTTP 429 retry logic with exponential backoff."""

    def setup_method(self):
        _TMDB_CACHE.clear()

    def test_retries_on_429_then_succeeds(self):
        ok_resp = _make_response(200, '{"results": []}')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=[_make_http_error(429), ok_resp],
        ) as mock_open, patch(
            "movie_narrator.providers.tmdb.time.sleep"
        ) as mock_sleep:
            result = _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
        assert result == {"results": []}
        # First attempt raised 429; second succeeded.
        assert mock_open.call_count == 2
        # First retry uses backoff[0] = 1s.
        mock_sleep.assert_called_once_with(1)

    def test_gives_up_after_max_retries(self):
        err = _make_http_error(429)
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=[err, err, err, err],
        ) as mock_open, patch(
            "movie_narrator.providers.tmdb.time.sleep"
        ) as mock_sleep:
            result = _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
        assert result is None
        # 1 initial attempt + 3 retries = 4 calls; 3 sleeps (1s, 2s, 4s).
        assert mock_open.call_count == 4
        assert mock_sleep.call_count == 3
        assert mock_sleep.call_args_list == [call(1), call(2), call(4)]

    def test_non_429_http_error_is_not_retried(self):
        err = _make_http_error(404)
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=err,
        ) as mock_open, patch(
            "movie_narrator.providers.tmdb.time.sleep"
        ) as mock_sleep:
            result = _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
        assert result is None
        assert mock_open.call_count == 1
        mock_sleep.assert_not_called()


class TestTmdbCache:
    """Verify the in-memory cache avoids duplicate network requests."""

    def setup_method(self):
        _TMDB_CACHE.clear()

    def test_second_call_hits_cache(self):
        resp = _make_response(200, '{"ok": true}')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            return_value=resp,
        ) as mock_open:
            result1 = _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
            result2 = _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
        assert result1 == {"ok": True}
        assert result2 == {"ok": True}
        # Only the first call reached the network.
        assert mock_open.call_count == 1

    def test_different_urls_are_not_cached_together(self):
        resp = _make_response(200, '{"ok": true}')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            return_value=resp,
        ) as mock_open:
            _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "a"},
            )
            _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "b"},
            )
        # Different query params produce different URLs — both hit network.
        assert mock_open.call_count == 2

    def test_cached_value_is_returned_without_network(self):
        resp = _make_response(200, '{"ok": true}')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            return_value=resp,
        ):
            # Prime the cache with the first call.
            _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
        # Second call: urlopen is not mocked, so any network access would
        # hit the real internet and fail. A cache hit avoids that entirely.
        result = _tmdb_get(
            "https://api.themoviedb.org/3", "/search/movie", "key",
            {"query": "test", "api_key": "key"},
        )
        assert result == {"ok": True}


class TestTmdbRateLimitWithRetryAfter:
    """Verify the Retry-After header is honored on HTTP 429."""

    def setup_method(self):
        _TMDB_CACHE.clear()

    def test_uses_retry_after_header_value(self):
        headers = http.client.HTTPMessage()
        headers.add_header("Retry-After", "5")
        err = _make_http_error(429, headers=headers)
        ok_resp = _make_response(200, '{"ok": true}')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=[err, ok_resp],
        ), patch(
            "movie_narrator.providers.tmdb.time.sleep"
        ) as mock_sleep:
            result = _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
        assert result == {"ok": True}
        # Retry-After (5s) overrides the default backoff (1s).
        mock_sleep.assert_called_once_with(5)

    def test_retry_after_used_on_every_retry(self):
        headers = http.client.HTTPMessage()
        headers.add_header("Retry-After", "3")
        err = _make_http_error(429, headers=headers)
        ok_resp = _make_response(200, '{"ok": true}')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=[err, err, ok_resp],
        ), patch(
            "movie_narrator.providers.tmdb.time.sleep"
        ) as mock_sleep:
            result = _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
        assert result == {"ok": True}
        # Both retries use the Retry-After value (3s), not the backoff.
        assert mock_sleep.call_count == 2
        assert mock_sleep.call_args_list == [call(3), call(3)]

    def test_falls_back_to_backoff_without_retry_after(self):
        ok_resp = _make_response(200, '{"ok": true}')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=[_make_http_error(429), ok_resp],
        ), patch(
            "movie_narrator.providers.tmdb.time.sleep"
        ) as mock_sleep:
            result = _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
        assert result == {"ok": True}
        # No Retry-After header → default backoff[0] = 1s.
        mock_sleep.assert_called_once_with(1)

    def test_fractional_retry_after_is_parsed(self):
        headers = http.client.HTTPMessage()
        headers.add_header("Retry-After", "0.5")
        err = _make_http_error(429, headers=headers)
        ok_resp = _make_response(200, '{"ok": true}')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=[err, ok_resp],
        ), patch(
            "movie_narrator.providers.tmdb.time.sleep"
        ) as mock_sleep:
            result = _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
        assert result == {"ok": True}
        mock_sleep.assert_called_once_with(0.5)


class TestTmdbNetworkErrorHandling:
    """Verify graceful degradation on network errors."""

    def setup_method(self):
        _TMDB_CACHE.clear()

    def test_tmdb_get_propagates_urlerror(self):
        err = urllib.error.URLError("connection refused")
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=err,
        ):
            try:
                _tmdb_get(
                    "https://api.themoviedb.org/3", "/search/movie", "key",
                    {"query": "test"},
                )
                assert False, "Should have raised URLError"
            except urllib.error.URLError:
                pass

    def test_enrichment_returns_original_card_on_network_error(self):
        card = MovieCard(title="Test", director="LLM Director")
        ctx = MagicMock()
        ctx.movie_name = "Test"
        ctx.metadata = {}
        settings = MagicMock()
        settings.tmdb_api_key = "key"
        settings.tmdb_base_url = "https://api.themoviedb.org/3"
        settings.tmdb_language = "zh-CN"
        err = urllib.error.URLError("connection refused")
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=err,
        ):
            result = enrich_movie_card_with_tmdb(card, ctx, settings)
        assert result is card

    def test_enrichment_logs_warning_on_network_error(self):
        card = MovieCard(title="Test", director="LLM Director")
        ctx = MagicMock()
        ctx.movie_name = "Test"
        ctx.metadata = {}
        settings = MagicMock()
        settings.tmdb_api_key = "key"
        settings.tmdb_base_url = "https://api.themoviedb.org/3"
        settings.tmdb_language = "zh-CN"
        err = urllib.error.URLError("connection refused")
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=err,
        ), patch(
            "movie_narrator.providers.tmdb.logger"
        ) as mock_logger:
            enrich_movie_card_with_tmdb(card, ctx, settings)
        # A network error during enrichment should be logged at warning.
        assert mock_logger.warning.called

    def test_research_raises_runtime_error_on_network_error(self):
        ctx = MagicMock()
        ctx.movie_name = "Test"
        ctx.metadata = {}
        settings = MagicMock()
        settings.tmdb_api_key = "key"
        settings.tmdb_base_url = "https://api.themoviedb.org/3"
        settings.tmdb_language = "zh-CN"
        err = urllib.error.URLError("connection refused")
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            side_effect=err,
        ):
            try:
                tmdb_research(ctx, settings)
                assert False, "Should have raised RuntimeError"
            except RuntimeError as e:
                assert "network error" in str(e).lower()


class TestTmdbSearchEmptyResults:
    """Verify handling of empty search results."""

    def setup_method(self):
        _TMDB_CACHE.clear()

    def test_search_returns_none_for_empty_results(self):
        resp = _make_response(200, '{"results": []}')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            return_value=resp,
        ):
            result = _search_movie(
                "https://api.themoviedb.org/3", "key", "Test", "zh-CN"
            )
        assert result is None

    def test_search_returns_none_for_missing_results_key(self):
        resp = _make_response(200, '{"page": 1}')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            return_value=resp,
        ):
            result = _search_movie(
                "https://api.themoviedb.org/3", "key", "Test", "zh-CN"
            )
        assert result is None

    def test_enrichment_returns_original_card_for_empty_results(self):
        resp = _make_response(200, '{"results": []}')
        card = MovieCard(title="Test", director="LLM")
        ctx = MagicMock()
        ctx.movie_name = "Test"
        ctx.metadata = {}
        settings = MagicMock()
        settings.tmdb_api_key = "key"
        settings.tmdb_base_url = "https://api.themoviedb.org/3"
        settings.tmdb_language = "zh-CN"
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            return_value=resp,
        ):
            result = enrich_movie_card_with_tmdb(card, ctx, settings)
        assert result is card


class TestTmdbMalformedResponse:
    """Verify handling of malformed JSON responses."""

    def setup_method(self):
        _TMDB_CACHE.clear()

    def test_tmdb_get_returns_none_for_malformed_json(self):
        resp = _make_response(200, '{"broken":')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            return_value=resp,
        ):
            result = _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
        assert result is None

    def test_malformed_response_is_not_cached(self):
        resp = _make_response(200, '{"broken":')
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            return_value=resp,
        ) as mock_open:
            _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test"},
            )
            _tmdb_get(
                "https://api.themoviedb.org/3", "/search/movie", "key",
                {"query": "test", "api_key": "key"},
            )
        # Malformed JSON should not be cached, so both calls hit network.
        assert mock_open.call_count == 2

    def test_enrichment_returns_original_card_for_malformed_json(self):
        resp = _make_response(200, '{"broken":')
        card = MovieCard(title="Test", director="LLM")
        ctx = MagicMock()
        ctx.movie_name = "Test"
        ctx.metadata = {}
        settings = MagicMock()
        settings.tmdb_api_key = "key"
        settings.tmdb_base_url = "https://api.themoviedb.org/3"
        settings.tmdb_language = "zh-CN"
        with patch(
            "movie_narrator.providers.tmdb.urllib.request.urlopen",
            return_value=resp,
        ):
            result = enrich_movie_card_with_tmdb(card, ctx, settings)
        assert result is card
