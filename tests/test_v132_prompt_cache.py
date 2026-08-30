# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the v1.3.2 opt-in prompt/script cache (Feature 10).

Covers: key normalization stability, hit-skips-LLM with a counting fake
LLM, miss-stores round-trip, TTL expiry (monkeypatched clock), LRU cap
eviction, corrupt-file tolerance, and the default-off zero-behavior
guarantee (research + script steps unchanged, no metadata key).
"""

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import movie_narrator.utils.prompt_cache as pc_module
from movie_narrator.models import Context
from movie_narrator.pipeline.research import _research_via_llm
from movie_narrator.pipeline.script import _generate_plot_beats
from movie_narrator.utils.prompt_cache import PromptCache, reset_prompt_cache


# ── Helpers ───────────────────────────────────────────────


def _make_ctx(tmp_path, **kw):
    defaults = dict(
        movie_name="test_movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=__import__("movie_narrator").models.Services(console=MagicMock()),
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
    s.script_max_tokens = overrides.get("script_max_tokens", 2048)
    s.script_expand_temperature = 0.5
    s.research_temperature = 0.3
    s.research_max_tokens = 1024
    s.research_retries = 3
    s.research_retry_delay = 0
    s.llm_provider = overrides.get("llm_provider", "openai")
    return s


def _beats_json(n: int) -> str:
    beats = [f"剧情关键点{i + 1}" for i in range(n)]
    return '{"beats": ' + str(beats).replace("'", '"') + "}"


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch):
    """Keep MN_PROMPT_CACHE and the shared instance out of test state."""
    monkeypatch.delenv("MN_PROMPT_CACHE", raising=False)
    reset_prompt_cache()
    yield
    reset_prompt_cache()


# ── 1. Key construction / normalization ───────────────────


class TestKeyNormalization:
    def test_topic_nfkc_strip_casefold(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=True)
        k1 = cache.make_key(kind="research", topic="Inception", model="m", provider="openai")
        # Full-width chars, surrounding whitespace, case differences.
        k2 = cache.make_key(
            kind="research", topic="ｉｎｃｅｐｔｉｏｎ ", model="m", provider="openai"
        )
        k3 = cache.make_key(kind="research", topic=" INCEPTION", model="m", provider="openai")
        assert k1 == k2 == k3

    def test_different_inputs_differ(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=True)
        base = dict(kind="research", topic="M", model="m", provider="p")
        assert cache.make_key(**base) != cache.make_key(**{**base, "kind": "script_beats"})
        assert cache.make_key(**base) != cache.make_key(**{**base, "style": "热血"})
        assert cache.make_key(**base) != cache.make_key(**{**base, "language": "en"})
        assert cache.make_key(**base) != cache.make_key(**{**base, "model": "m2"})
        assert cache.make_key(**base) != cache.make_key(**{**base, "provider": "p2"})

    def test_extra_participates_in_key(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=True)
        base = dict(kind="script_beats", topic="M", model="m", provider="p")
        assert cache.make_key(**base) != cache.make_key(**base, extra={"target_count": 18})
        assert cache.make_key(**base, extra={"target_count": 18}) == cache.make_key(
            **base, extra={"target_count": 18}
        )

    def test_key_is_sha256_hex(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=True)
        key = cache.make_key(kind="research", topic="M", model="m", provider="p")
        assert len(key) == 64
        int(key, 16)  # parses as hex
        # Deterministic: canonical JSON sha256 of the documented tuple.
        expected_payload = json.dumps(
            {
                "kind": "research",
                "topic": "m",
                "style": "",
                "language": "",
                "prompt_template_version": pc_module.PROMPT_TEMPLATE_VERSION,
                "model": "m",
                "provider": "p",
                "extra": {},
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        assert key == hashlib.sha256(expected_payload.encode("utf-8")).hexdigest()

    def test_env_opt_in(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MN_PROMPT_CACHE", "1")
        assert PromptCache().enabled is True
        monkeypatch.setenv("MN_PROMPT_CACHE", "true")
        assert PromptCache().enabled is True
        monkeypatch.setenv("MN_PROMPT_CACHE", "0")
        assert PromptCache().enabled is False
        monkeypatch.delenv("MN_PROMPT_CACHE")
        assert PromptCache().enabled is False


# ── 2. Store / lookup round-trip ──────────────────────────


class TestStoreLookup:
    def test_miss_then_hit_round_trip(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=True)
        key = cache.make_key(kind="research", topic="M", model="m", provider="p")
        assert cache.lookup(key) is None
        cache.store(key, kind="research", response="RAW", model="m", provider="p")
        entry = cache.lookup(key)
        assert entry is not None
        assert entry["response"] == "RAW"
        assert entry["kind"] == "research"
        assert entry["model"] == "m"
        assert entry["provider"] == "p"
        assert entry["key"] == key
        assert "created_at" in entry
        assert cache.hits == 1
        assert cache.misses == 1

    def test_stats_shape(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=True)
        key = cache.make_key(kind="research", topic="M", model="m", provider="p")
        cache.lookup(key)  # miss
        cache.store(key, kind="research", response="RAW", model="m", provider="p")
        cache.lookup(key)  # hit
        stats = cache.stats()
        assert stats == {"enabled": True, "hits": 1, "misses": 1, "hit_rate": 0.5}

    def test_entry_file_layout(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=True)
        key = cache.make_key(kind="research", topic="M", model="m", provider="p")
        cache.store(key, kind="research", response="RAW", model="m", provider="p")
        path = tmp_path / f"{key}.json"
        assert path.is_file()
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert set(payload) == {"key", "kind", "response", "model", "provider", "created_at"}

    def test_disabled_cache_is_noop(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=False)
        key = cache.make_key(kind="research", topic="M", model="m", provider="p")
        assert cache.lookup(key) is None
        cache.store(key, kind="research", response="RAW", model="m", provider="p")
        assert list(tmp_path.glob("*")) == []


# ── 3. TTL expiry ─────────────────────────────────────────


class TestTTL:
    def test_expired_entry_is_miss_and_deleted(self, tmp_path, monkeypatch):
        cache = PromptCache(cache_dir=tmp_path, enabled=True, ttl_seconds=100)
        key = cache.make_key(kind="research", topic="M", model="m", provider="p")
        cache.store(key, kind="research", response="RAW", model="m", provider="p")
        assert cache.lookup(key) is not None

        # Jump the clock past the TTL.
        real_now = pc_module._now()
        monkeypatch.setattr(pc_module, "_now", lambda: real_now + 101.0)
        assert cache.lookup(key) is None
        assert not (tmp_path / f"{key}.json").exists()
        assert cache.misses == 1

    def test_future_created_at_treated_as_miss(self, tmp_path, monkeypatch):
        """Clock skew backwards must not produce a permanent hit."""
        cache = PromptCache(cache_dir=tmp_path, enabled=True)
        key = cache.make_key(kind="research", topic="M", model="m", provider="p")
        cache.store(key, kind="research", response="RAW", model="m", provider="p")
        real_now = pc_module._now()
        monkeypatch.setattr(pc_module, "_now", lambda: real_now - 50.0)
        assert cache.lookup(key) is None


# ── 4. LRU cap eviction ───────────────────────────────────


class TestCapEviction:
    def test_oldest_entries_evicted_on_write(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=True, max_entries=3)
        keys = []
        for i in range(5):
            key = cache.make_key(kind="research", topic=f"M{i}", model="m", provider="p")
            keys.append(key)
            cache.store(key, kind="research", response=f"R{i}", model="m", provider="p")
            # Give every stored file a distinct deterministic mtime so the
            # eviction order is unambiguous even on coarse filesystems.
            for idx, k in enumerate(keys):
                p = tmp_path / f"{k}.json"
                if p.exists():
                    os.utime(p, (100 + idx, 100 + idx))
        files = sorted(tmp_path.glob("*.json"))
        assert len(files) == 3
        assert {p.stem for p in files} == set(keys[-3:])


# ── 5. Corrupt file tolerance ─────────────────────────────


class TestCorruptFile:
    def test_corrupt_entry_is_miss_and_deleted(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=True)
        key = cache.make_key(kind="research", topic="M", model="m", provider="p")
        (tmp_path / f"{key}.json").write_text("{not json", encoding="utf-8")
        assert cache.lookup(key) is None
        assert not (tmp_path / f"{key}.json").exists()

    def test_foreign_schema_entry_is_miss(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path, enabled=True)
        key = cache.make_key(kind="research", topic="M", model="m", provider="p")
        (tmp_path / f"{key}.json").write_text(json.dumps({"something": "else"}), encoding="utf-8")
        assert cache.lookup(key) is None


# ── 6. Integration: research step ─────────────────────────


class TestResearchIntegration:
    def _run_research(self, tmp_path, monkeypatch, cache):
        ctx = Context(movie_name="Inception", output_dir=str(tmp_path))
        ctx.metadata["research_enabled"] = True
        mock_response = _mock_llm_response(
            '{"title": "Inception", "year": 2010, "summary": "s", '
            '"genres": ["Sci-Fi"], "cast": [], "keywords": []}'
        )
        monkeypatch.setattr("movie_narrator.pipeline.research.get_prompt_cache", lambda: cache)
        with (
            patch("movie_narrator.pipeline.research.get_settings") as gs,
            patch("movie_narrator.pipeline.research.get_llm_client") as gl,
        ):
            settings = _mock_settings()
            gs.return_value = settings
            gl.return_value.__enter__.return_value = gl.return_value
            gl.return_value.model = "test-model"  # stable across runs → stable cache key
            gl.return_value.client.chat.completions.create.return_value = mock_response
            info = _research_via_llm(ctx, settings)
        return ctx, info, gl.return_value.client.chat.completions.create

    def test_miss_stores_then_hit_skips_llm(self, tmp_path, monkeypatch):
        cache = PromptCache(cache_dir=tmp_path / "pc", enabled=True)

        ctx, info, create = self._run_research(tmp_path, monkeypatch, cache)
        assert create.call_count == 1
        assert info.title == "Inception"
        records = ctx.metadata["prompt_cache"]
        assert records[-1]["stage"] == "research"
        assert records[-1]["hit"] is False
        assert len(records[-1]["key_prefix"]) == 12

        # Warm cache: identical run must skip the LLM entirely.
        ctx2, info2, create2 = self._run_research(tmp_path, monkeypatch, cache)
        assert create2.call_count == 0
        assert info2.title == "Inception"
        assert ctx2.metadata["prompt_cache"][-1] == {
            "stage": "research",
            "hit": True,
            "key_prefix": ctx.metadata["prompt_cache"][-1]["key_prefix"],
        }

    def test_disabled_cache_leaves_behavior_unchanged(self, tmp_path, monkeypatch):
        """Default-off: LLM still called, no prompt_cache metadata key."""
        cache = PromptCache(cache_dir=tmp_path / "pc", enabled=False)
        ctx, info, create = self._run_research(tmp_path, monkeypatch, cache)
        assert create.call_count == 1
        assert info.title == "Inception"
        assert "prompt_cache" not in ctx.metadata
        assert list((tmp_path / "pc").glob("*")) == []


# ── 7. Integration: script beats phase ────────────────────


class TestScriptBeatsIntegration:
    def _run_beats(self, tmp_path, cache, target_count=3):
        ctx = _make_ctx(tmp_path)
        mock_cm = _mock_llm_cm(response=_mock_llm_response(_beats_json(target_count)))
        with patch(
            "movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()
        ):
            llm = mock_cm.__enter__.return_value
            beats = _generate_plot_beats(ctx, _mock_settings(), llm, target_count)
        return ctx, mock_cm.__enter__.return_value, beats

    def test_beats_cache_hit_skips_llm(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path / "pc", enabled=True)
        with patch(
            "movie_narrator.pipeline.script.get_prompt_cache", lambda: cache
        ):
            ctx, llm, beats = self._run_beats(tmp_path, cache)
            assert llm.client.chat.completions.create.call_count == 1
            assert len(beats) == 3

            # Same tuple again → served from cache, zero LLM calls.
            ctx2, llm2, beats2 = self._run_beats(tmp_path, cache)
            assert llm2.client.chat.completions.create.call_count == 0
            assert beats2 == beats
            assert ctx2.metadata["prompt_cache"][-1]["hit"] is True

    def test_different_target_count_is_a_different_key(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path / "pc", enabled=True)
        with patch(
            "movie_narrator.pipeline.script.get_prompt_cache", lambda: cache
        ):
            ctx, llm, _ = self._run_beats(tmp_path, cache, target_count=3)
            ctx2, llm2, _ = self._run_beats(tmp_path, cache, target_count=5)
            assert llm.client.chat.completions.create.call_count == 1
            assert llm2.client.chat.completions.create.call_count == 1
            assert ctx2.metadata["prompt_cache"][-1]["hit"] is False

    def test_disabled_cache_unchanged(self, tmp_path):
        cache = PromptCache(cache_dir=tmp_path / "pc", enabled=False)
        with patch(
            "movie_narrator.pipeline.script.get_prompt_cache", lambda: cache
        ):
            ctx, llm, beats = self._run_beats(tmp_path, cache)
            assert llm.client.chat.completions.create.call_count == 1
            assert "prompt_cache" not in ctx.metadata
            assert list((tmp_path / "pc").glob("*")) == []
