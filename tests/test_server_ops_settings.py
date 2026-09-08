# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Server ops settings view — Settings boundary-drift guards.

Verifies that the server/deployment operational surface exposed through
``ServerOpsSettings`` / ``get_server_ops()``:
  1. stays disjoint from the LLM/TTS/TMDB credential/model surface loaded via
     ``.env``, so ops knobs can never silently drift back into model config;
  2. assembles a correct, immutable snapshot from ``Settings``;
  3. only reflects fields ``Settings`` already carries (the view adds zero new
     ``MN_*`` environment variables).
"""

import dataclasses

import pytest

from movie_narrator.config import (
    ServerOpsSettings,
    Settings,
    get_server_ops,
    get_settings,
)

# Prefixes marking a Settings field as belonging to the LLM/TTS/TMDB
# credential/endpoint/model surface (the `.env`-earned surface). Any new field
# added to Settings must either carry one of these prefixes (model config) or
# be folded into ``ServerOpsSettings`` (ops) — never added to Settings alone.
_MODEL_PREFIXES = (
    "llm_",
    "script_",
    "research_",
    "translate_",
    "tmdb_",
    "voice_",
    "tts_",
    "openai_",
    "mimo_",
)


def _settings_field_names() -> set[str]:
    return set(Settings.model_fields.keys())


def _ops_field_names() -> set[str]:
    return {f.name for f in dataclasses.fields(ServerOpsSettings)}


def _model_field_names() -> set[str]:
    return {
        name
        for name in _settings_field_names()
        if name.startswith(_MODEL_PREFIXES)
    }


def _parse_nodes(raw: str) -> set[str]:
    return {u.strip() for u in raw.split(",") if u.strip()} if raw else set()


@pytest.fixture(autouse=True)
def _fresh_settings():
    """Re-read Settings on every test so env overrides take effect.

    ``get_settings`` / ``get_server_ops`` are lru_cached at module level; a
    new .env/process-env is only visible after the cache is cleared.
    """
    get_settings.cache_clear()
    get_server_ops.cache_clear()
    yield
    get_settings.cache_clear()
    get_server_ops.cache_clear()


def test_server_ops_view_is_frozen():
    ops = get_server_ops()
    assert dataclasses.is_dataclass(ops)
    with pytest.raises(dataclasses.FrozenInstanceError):
        ops.scheduler_enabled = not ops.scheduler_enabled


def test_server_ops_disjoint_from_model_surface():
    """Ops knobs must never overlap the LLM/TTS/TMDB .env surface."""
    assert _ops_field_names().isdisjoint(_model_field_names())


def test_get_server_ops_assembles_from_settings():
    """Value-level consistency: every ops field mirrors its Settings source."""
    ops = get_server_ops()
    s = get_settings()
    for name in _ops_field_names():
        if name == "distributed_nodes":
            continue
        assert getattr(ops, name) == getattr(s, name), name
    # distributed_nodes is exposed parsed (set of URLs), not the raw CSV.
    assert ops.distributed_nodes == _parse_nodes(s.distributed_nodes)


def test_get_server_ops_env_override(monkeypatch):
    """A few MN_* overrides flow through to the assembled view."""
    monkeypatch.setenv("MN_API_KEY", "s3cr3t")
    monkeypatch.setenv("MN_SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("MN_RATE_LIMIT_CAPACITY", "120")
    monkeypatch.setenv(
        "MN_DISTRIBUTED_NODES",
        "http://a:8765, http://b:8765 , ,http://c:8765",
    )
    get_settings.cache_clear()
    get_server_ops.cache_clear()
    ops = get_server_ops()
    assert ops.api_key == "s3cr3t"
    assert ops.scheduler_enabled is False
    assert ops.rate_limit_capacity == 120.0
    assert ops.distributed_nodes == {
        "http://a:8765",
        "http://b:8765",
        "http://c:8765",
    }


def test_server_ops_fields_are_subset_of_settings():
    """The view must not invent fields Settings doesn't already carry."""
    assert _ops_field_names() <= _settings_field_names()