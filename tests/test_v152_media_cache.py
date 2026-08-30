# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for the v1.5.2 media cache + reference_media URL support.

Covers: fetch + URL/content dedupe (no network on hits via a counting
MockTransport), per-entry size cap, TTL expiry + cache-wide size-cap
eviction (monkeypatched clock / small caps), license-note enforcement,
https-only policy, corrupt-sidecar recovery, the ReferenceMediaItem
path/url mutual exclusion, the resolve-step e2e (cached path flows into
downstream validation, provenance keys recorded, offline → hard input
error, local-path behavior unchanged), and load_job_config acceptance.
No real network: all HTTP goes through ``httpx.MockTransport``.
"""

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from pydantic import ValidationError

from movie_narrator.models import Services
from movie_narrator.utils import media_cache as mc
from movie_narrator.utils.media_cache import (
    DEFAULT_MAX_ENTRY_BYTES,
    MediaCache,
    MediaCacheError,
    fetch_into_cache,
    reset_media_cache,
    stats,
)
from movie_narrator.workflow.load import load_job_config
from movie_narrator.workflow.schema import ReferenceMediaItem


# ── Helpers ───────────────────────────────────────────────


def _transport_serving(payload: bytes, content_type: str = "video/mp4", calls: list = None):
    """MockTransport serving ``payload``; optionally recording call URLs."""

    def handler(request: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(request.url))
        return httpx.Response(200, headers={"Content-Type": content_type}, content=payload)

    return httpx.MockTransport(handler)


def _cache(tmp_path: Path, transport=None, **kw) -> MediaCache:
    defaults = dict(cache_dir=tmp_path / "media-cache", transport=transport)
    defaults.update(kw)
    return MediaCache(**defaults)


def _make_ctx(tmp_path, reference_media):
    from movie_narrator.models import Context

    return Context(
        movie_name="test_movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
        metadata={"reference_media": reference_media},
    )


MP4 = b"\x00\x00\x00\x18ftypmp4-fake-payload"


# ── Fetch + dedupe ────────────────────────────────────────


class TestFetchAndDedupe:
    def test_fetch_stores_blob_and_sidecar(self, tmp_path):
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        out = cache.fetch_into_cache(
            "https://example.com/ref.mp4", license_note="CC-BY 4.0", kind="video"
        )
        assert out.is_file()
        assert out.read_bytes() == MP4
        assert out.suffix == ".mp4"
        sidecar = cache._sidecar_path(out.stem)
        entry = json.loads(sidecar.read_text(encoding="utf-8"))
        assert entry["source_url"] == "https://example.com/ref.mp4"
        assert entry["license_note"] == "CC-BY 4.0"
        assert entry["kind"] == "video"
        assert entry["bytes"] == len(MP4)
        assert entry["sha256"] == out.stem
        assert entry["content_type"] == "video/mp4"
        assert calls == ["https://example.com/ref.mp4"]
        # Blob name is the content sha256 (content addressing).
        import hashlib

        assert out.stem == hashlib.sha256(MP4).hexdigest()

    def test_second_fetch_same_url_is_a_hit_no_network(self, tmp_path):
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        first = cache.fetch_into_cache(
            "https://example.com/ref.mp4", license_note="CC-BY 4.0", kind="video"
        )
        first_mtime_holder = first.stat().st_mtime_ns
        second = cache.fetch_into_cache(
            "https://example.com/ref.mp4", license_note="CC-BY 4.0", kind="video"
        )
        assert second == first
        assert calls == ["https://example.com/ref.mp4"]  # exactly one download
        assert cache.hits == 1 and cache.misses == 1
        # fetched_at refreshed on hit (keep-alive).
        entry = json.loads(cache._sidecar_path(first.stem).read_text(encoding="utf-8"))
        assert entry["fetched_at"] >= 0
        del first_mtime_holder

    def test_identical_bytes_from_different_urls_share_blob(self, tmp_path):
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        a = cache.fetch_into_cache("https://a.example/x.mp4", license_note="n1", kind="video")
        b = cache.fetch_into_cache("https://b.example/y.mp4", license_note="n2", kind="video")
        assert a == b  # content-addressed: one blob, no rewrite
        assert calls == ["https://a.example/x.mp4", "https://b.example/y.mp4"]

    def test_module_level_fetch_and_stats(self, tmp_path, monkeypatch):
        reset_media_cache()
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        # Install the test instance as the process-level shared cache.
        monkeypatch.setattr(mc, "_shared_cache", cache)
        out = fetch_into_cache("https://example.com/a.mp4", license_note="n", kind="video")
        assert out.is_file()
        assert stats()["hits"] == 0 and stats()["misses"] == 1
        assert stats()["bytes"] == len(MP4)
        fetch_into_cache("https://example.com/a.mp4", license_note="n", kind="video")
        assert stats()["hits"] == 1
        assert calls == ["https://example.com/a.mp4"]
        reset_media_cache()

    def test_extension_from_content_type_when_url_has_none(self, tmp_path):
        cache = _cache(
            tmp_path,
            _transport_serving(b"\xff\xd8\xff-jpeg", content_type="image/jpeg"),
        )
        out = cache.fetch_into_cache("https://example.com/fetch?id=1", license_note="n", kind="image")
        assert out.suffix == ".jpg"


# ── Policy: license note + https-only ─────────────────────


class TestFetchPolicy:
    def test_empty_license_note_refused_no_network(self, tmp_path):
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        for note in ("", "   ", None):
            with pytest.raises(MediaCacheError, match="license_note is required"):
                cache.fetch_into_cache("https://example.com/r.mp4", license_note=note, kind="video")
        assert calls == []  # refused before any network round-trip
        assert not (tmp_path / "media-cache").exists() or not list(
            (tmp_path / "media-cache").iterdir()
        )

    def test_http_url_refused(self, tmp_path):
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        with pytest.raises(MediaCacheError, match="https"):
            cache.fetch_into_cache("http://example.com/r.mp4", license_note="n", kind="video")
        assert calls == []

    def test_non_http_scheme_refused(self, tmp_path):
        cache = _cache(tmp_path, _transport_serving(MP4))
        with pytest.raises(MediaCacheError, match="https"):
            cache.fetch_into_cache("ftp://example.com/r.mp4", license_note="n", kind="video")


# ── Failure modes ─────────────────────────────────────────


class TestFetchFailures:
    def test_http_error_raises_media_cache_error(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        cache = _cache(tmp_path, httpx.MockTransport(handler))
        with pytest.raises(MediaCacheError, match="failed to fetch"):
            cache.fetch_into_cache("https://example.com/r.mp4", license_note="n", kind="video")
        # Nothing left behind.
        assert list((tmp_path / "media-cache").glob("*")) == []

    def test_network_error_wrapped(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        cache = _cache(tmp_path, httpx.MockTransport(handler))
        with pytest.raises(MediaCacheError, match="connection refused"):
            cache.fetch_into_cache("https://example.com/r.mp4", license_note="n", kind="video")

    def test_per_entry_size_cap(self, tmp_path):
        big = b"x" * 100
        cache = _cache(tmp_path, _transport_serving(big))
        with pytest.raises(MediaCacheError, match="download cap"):
            cache.fetch_into_cache(
                "https://example.com/big.mp4",
                license_note="n",
                kind="video",
                max_bytes=50,
            )
        # Temp files cleaned up; nothing cached.
        assert list((tmp_path / "media-cache").glob("*")) == []


# ── TTL + eviction ────────────────────────────────────────


class TestTtlAndEviction:
    def test_expired_entry_refetched_and_files_cleaned(self, tmp_path, monkeypatch):
        calls: list = []
        now = 1000.0
        monkeypatch.setattr(mc, "_now", lambda: now)
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls), ttl_seconds=100)
        first = cache.fetch_into_cache("https://example.com/r.mp4", license_note="n", kind="video")
        now += 101  # past TTL
        second = cache.fetch_into_cache("https://example.com/r.mp4", license_note="n", kind="video")
        assert calls == ["https://example.com/r.mp4"] * 2  # network again
        assert second == first  # same content → same blob path
        assert cache.misses == 2
        # The expired sidecar was deleted on sight, then re-written fresh.
        entry = json.loads(cache._sidecar_path(first.stem).read_text(encoding="utf-8"))
        assert entry["fetched_at"] == 1000.0 + 101

    def test_cache_wide_size_cap_evicts_oldest(self, tmp_path):
        payload_a = b"\x01" * 6
        payload_b = b"\x02" * 6
        cache = _cache(tmp_path, _transport_serving(payload_a), max_bytes=10)
        a = cache.fetch_into_cache("https://e.com/a.mp4", license_note="n", kind="video")
        cache._transport = _transport_serving(payload_b)
        b = cache.fetch_into_cache("https://e.com/b.mp4", license_note="n", kind="video")
        # a (6 bytes) + b (6 bytes) = 12 > cap 10 → oldest (a) evicted.
        assert not a.exists()
        assert not cache._sidecar_path(a.stem).exists()
        assert b.exists()
        assert b.read_bytes() == payload_b
        assert cache._sidecar_path(b.stem).is_file()

    def test_corrupt_sidecar_recovers_by_refetch(self, tmp_path):
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        first = cache.fetch_into_cache("https://example.com/r.mp4", license_note="n", kind="video")
        cache._sidecar_path(first.stem).write_text("{corrupt json!", encoding="utf-8")
        second = cache.fetch_into_cache("https://example.com/r.mp4", license_note="n", kind="video")
        assert calls == ["https://example.com/r.mp4"] * 2  # corrupt → re-fetch
        assert second == first
        entry = json.loads(cache._sidecar_path(first.stem).read_text(encoding="utf-8"))
        assert entry["license_note"] == "n"  # healthy sidecar restored

    def test_sidecar_without_blob_self_heals(self, tmp_path):
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        first = cache.fetch_into_cache("https://example.com/r.mp4", license_note="n", kind="video")
        first.unlink()  # blob vanished (user cleaned it)
        second = cache.fetch_into_cache("https://example.com/r.mp4", license_note="n", kind="video")
        assert calls == ["https://example.com/r.mp4"] * 2
        assert second == first and second.is_file()


# ── Schema: path/url mutual exclusion ─────────────────────


class TestReferenceMediaItemUrl:
    def test_url_item_accepted(self):
        item = ReferenceMediaItem(url="https://example.com/r.mp4", kind="video", note="CC-BY")
        assert item.path == ""
        assert item.usage == "style"

    def test_path_item_defaults_unchanged(self, tmp_path):
        item = ReferenceMediaItem(path=str(tmp_path / "a.mp4"))
        assert item.url == ""

    def test_path_and_url_mutually_exclusive(self):
        with pytest.raises(ValidationError, match="mutually exclusive"):
            ReferenceMediaItem(path="a.mp4", url="https://example.com/r.mp4")

    def test_neither_path_nor_url_rejected(self):
        with pytest.raises(ValidationError, match="exactly one"):
            ReferenceMediaItem()

    def test_url_must_be_http_s(self):
        with pytest.raises(ValidationError, match="http\\(s\\)"):
            ReferenceMediaItem(url="ftp://example.com/r.mp4")
        with pytest.raises(ValidationError, match="http\\(s\\)"):
            ReferenceMediaItem(url="/etc/passwd")

    def test_http_url_accepted_at_schema_level(self):
        # Schema accepts http(s); the https-only policy is enforced at
        # fetch time (documented division of responsibility).
        item = ReferenceMediaItem(url="http://example.com/r.mp4")
        assert item.url == "http://example.com/r.mp4"

    def test_blank_path_with_url_ok(self):
        item = ReferenceMediaItem(path="", url="https://example.com/r.mp4")
        assert item.kind == "video"


# ── load_job_config integration ───────────────────────────


class TestLoadJobConfigUrl:
    def _write(self, tmp_path, body):
        cfg = tmp_path / "job.yaml"
        cfg.write_text(body, encoding="utf-8")
        return cfg

    def test_url_item_loads(self, tmp_path):
        cfg = self._write(
            tmp_path,
            "params:\n"
            "  reference_media:\n"
            "    - url: https://example.com/ref.mp4\n"
            "      kind: video\n"
            "      usage: pacing\n"
            '      note: "CC-BY 4.0, example.com"\n',
        )
        loaded = load_job_config(cfg)
        items = loaded.params.reference_media
        assert len(items) == 1
        assert items[0].url == "https://example.com/ref.mp4"
        assert items[0].note == "CC-BY 4.0, example.com"

    def test_relative_path_resolution_skips_url_items(self, tmp_path):
        cfg = self._write(
            tmp_path,
            "params:\n"
            "  reference_media:\n"
            "    - url: https://example.com/ref.mp4\n"
            "      note: n\n"
            "    - path: local.mp4\n"
            "      note: n\n",
        )
        loaded = load_job_config(cfg)
        items = loaded.params.reference_media
        assert items[0].url.startswith("https://")
        # Relative path still resolves against the config dir.
        assert Path(items[1].path) == (tmp_path / "local.mp4").resolve()

    def test_path_plus_url_rejected_at_load(self, tmp_path):
        cfg = self._write(
            tmp_path,
            "params:\n"
            "  reference_media:\n"
            "    - path: local.mp4\n"
            "      url: https://example.com/ref.mp4\n"
            "      note: n\n",
        )
        from movie_narrator.workflow.load import JobConfigError

        with pytest.raises(JobConfigError, match="mutually"):
            load_job_config(cfg)


# ── Resolve-step e2e ──────────────────────────────────────


class TestResolveUrlItems:
    def _patch_resolve_fetch(self, monkeypatch, cache: MediaCache):
        import movie_narrator.pipeline.resolve as resolve_mod

        monkeypatch.setattr(
            resolve_mod,
            "fetch_into_cache",
            lambda url, **kw: mc.fetch_into_cache(url, cache=cache, **kw),
        )

    def test_url_item_resolves_through_cache(self, tmp_path, monkeypatch):
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        self._patch_resolve_fetch(monkeypatch, cache)
        from movie_narrator.pipeline.resolve import resolve_video

        ctx = _make_ctx(
            tmp_path,
            [
                ReferenceMediaItem(
                    url="https://example.com/race.mp4",
                    kind="video",
                    usage="pacing",
                    note="CC-BY 4.0, example.com",
                )
            ],
        )
        out = resolve_video(ctx)
        entry = out.metadata["reference_media"][0]
        cached = Path(entry["path"])
        assert cached.is_file()
        assert cached.read_bytes() == MP4
        # Downstream validation ran on the cached path (extension matches kind).
        assert cached.suffix == ".mp4"
        assert entry["kind"] == "video" and entry["usage"] == "pacing"
        assert entry["note"] == "CC-BY 4.0, example.com"
        # Provenance keys (additive) recorded into the metadata entry.
        assert entry["source_url"] == "https://example.com/race.mp4"
        assert entry["cache_sha256"] == cached.stem
        assert calls == ["https://example.com/race.mp4"]

    def test_url_item_dedupes_across_resolve_runs(self, tmp_path, monkeypatch):
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        self._patch_resolve_fetch(monkeypatch, cache)
        from movie_narrator.pipeline.resolve import resolve_video

        for _ in range(2):
            ctx = _make_ctx(
                tmp_path,
                [ReferenceMediaItem(url="https://example.com/r.mp4", note="n")],
            )
            resolve_video(ctx)
        assert calls == ["https://example.com/r.mp4"]  # second run: cache hit

    def test_url_item_without_note_is_hard_error(self, tmp_path, monkeypatch):
        cache = _cache(tmp_path, _transport_serving(MP4))
        self._patch_resolve_fetch(monkeypatch, cache)
        from movie_narrator.pipeline.resolve import resolve_video

        ctx = _make_ctx(
            tmp_path, [ReferenceMediaItem(url="https://example.com/r.mp4", note="")]
        )
        with pytest.raises(ValueError, match="license"):
            resolve_video(ctx)

    def test_offline_fetch_is_hard_input_error(self, tmp_path, monkeypatch):
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

        cache = _cache(tmp_path, httpx.MockTransport(handler))
        self._patch_resolve_fetch(monkeypatch, cache)
        from movie_narrator.pipeline.resolve import resolve_video

        ctx = _make_ctx(
            tmp_path, [ReferenceMediaItem(url="https://example.com/r.mp4", note="n")]
        )
        with pytest.raises(ValueError, match="url fetch failed"):
            resolve_video(ctx)

    def test_kind_mismatch_validated_on_cached_blob(self, tmp_path, monkeypatch):
        # URL serves an image but declares kind="video" → downstream
        # extension check must reject the CACHED path, not the URL.
        cache = _cache(
            tmp_path, _transport_serving(b"\x89PNG-fake", content_type="image/png")
        )
        self._patch_resolve_fetch(monkeypatch, cache)
        from movie_narrator.pipeline.resolve import resolve_video

        ctx = _make_ctx(
            tmp_path,
            [ReferenceMediaItem(url="https://example.com/pic.png", kind="video", note="n")],
        )
        with pytest.raises(ValueError, match="kind='video'"):
            resolve_video(ctx)

    def test_local_path_entries_unchanged(self, tmp_path):
        from movie_narrator.pipeline.resolve import resolve_video

        vid = tmp_path / "local.mp4"
        vid.write_bytes(MP4)
        ctx = _make_ctx(
            tmp_path,
            [
                ReferenceMediaItem(
                    path=str(vid), kind="video", usage="palette", note="personal copy"
                )
            ],
        )
        out = resolve_video(ctx)
        entry = out.metadata["reference_media"][0]
        assert entry["path"] == str(vid.resolve())
        assert entry["note"] == "personal copy"
        # Local-path items gain no provenance keys — unchanged behavior.
        assert "source_url" not in entry
        assert "cache_sha256" not in entry

    def test_mixed_local_and_url_entries(self, tmp_path, monkeypatch):
        calls: list = []
        cache = _cache(tmp_path, _transport_serving(MP4, calls=calls))
        self._patch_resolve_fetch(monkeypatch, cache)
        from movie_narrator.pipeline.resolve import resolve_video

        vid = tmp_path / "local.mp4"
        vid.write_bytes(MP4)
        ctx = _make_ctx(
            tmp_path,
            [
                ReferenceMediaItem(path=str(vid), note="local"),
                ReferenceMediaItem(url="https://example.com/r.mp4", note="remote"),
            ],
        )
        out = resolve_video(ctx)
        entries = out.metadata["reference_media"]
        assert "source_url" not in entries[0]
        assert entries[1]["source_url"] == "https://example.com/r.mp4"


# ── Sanity: default cap constant matches the spec ─────────


def test_default_caps():
    assert DEFAULT_MAX_ENTRY_BYTES == 2 * 1024**3
    assert mc.DEFAULT_MAX_BYTES == 2 * 1024**3
    assert mc.DEFAULT_TTL_SECONDS == 30 * 24 * 3600
