# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Opt-in prompt/script response cache (v1.3.2).

Caches *raw* LLM completion responses for deterministic input tuples so
repeated runs of the same job (same movie / style / language / model /
provider) skip the research and script LLM round-trips. Opt-in only:
``MN_PROMPT_CACHE=1`` (default off, zero behaviour change).

Design notes:

- **Storage**: one JSON file per entry under
  ``~/.movie-narrator/prompts/`` — the same user-level cache directory
  convention as the GPU capability cache (``utils/gpu_detect.py``),
  which mirrors ``config._USER_DIR`` without importing config (keeps
  this low-level util free of the pydantic-settings dependency).
- **Key**: ``sha256`` of a canonical JSON tuple
  ``{kind, topic, style, language, prompt_template_version, model,
  provider, extra?}``. ``topic`` is normalized with NFKC + strip +
  casefold so equivalent titles share an entry. ``extra`` carries
  call-specific deterministic inputs (e.g. the beat target count or the
  phase-1 beats themselves) — without them a cached response could be
  returned for *different* actual inputs.
- **TTL / LRU**: entries older than ``ttl_seconds`` (default 7 days)
  are treated as misses and deleted; the cache keeps at most
  ``max_entries`` (default 200) files, evicting the oldest on write.
- **Corruption**: a corrupt / foreign entry file is treated as a miss
  and deleted — never fatal.
- **Stats**: per-instance ``hits`` / ``misses`` counters (see
  :meth:`PromptCache.stats`), exposed for tests and run metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import unicodedata
from contextlib import suppress
from pathlib import Path
from typing import Any, Optional

#: Version of the deterministic key tuple / prompt-template identity.
#: Bump when prompt templates change in ways that alter raw responses,
#: so stale entries stop matching.
PROMPT_TEMPLATE_VERSION = "1"

#: Default entry lifetime (seconds): 7 days.
DEFAULT_TTL_SECONDS = 7 * 24 * 3600

#: Default maximum number of cache files kept on disk.
DEFAULT_MAX_ENTRIES = 200

#: User-level cache subdirectory (same base convention as gpu_detect).
_DEFAULT_CACHE_DIR = Path.home() / ".movie-narrator" / "prompts"

#: Environment variable that enables the cache. Default: off.
_ENABLED_ENV = "MN_PROMPT_CACHE"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _now() -> float:
    """Monotonic-ish wall clock, isolated for tests to monkeypatch."""
    return time.time()


def _default_cache_dir() -> Path:
    """Return the user-level prompt cache directory (overridable in tests)."""
    return _DEFAULT_CACHE_DIR


def _env_enabled() -> bool:
    """True iff ``MN_PROMPT_CACHE`` opts the cache in (default off)."""
    return os.environ.get(_ENABLED_ENV, "").strip().lower() in _TRUTHY


def _normalize_topic(topic: str) -> str:
    """Normalize a topic string: NFKC fold + strip + casefold."""
    return unicodedata.normalize("NFKC", topic).strip().casefold()


class PromptCache:
    """File-backed LLM response cache for deterministic input tuples."""

    def __init__(
        self,
        *,
        cache_dir: Optional[Path] = None,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        enabled: Optional[bool] = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir is not None else _default_cache_dir()
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self.enabled = _env_enabled() if enabled is None else enabled
        self.hits = 0
        self.misses = 0

    # ── Key construction ──────────────────────────────────

    def make_key(
        self,
        *,
        kind: str,
        topic: str,
        style: str = "",
        language: str = "",
        model: str = "",
        provider: str = "",
        extra: Optional[dict] = None,
    ) -> str:
        """Build the sha256 cache key for a deterministic input tuple.

        Args:
            kind: Logical call identity, e.g. ``"research"``,
                ``"script_beats"``, ``"script_expand"``.
            topic: The movie/topic — NFKC + strip + casefold normalized.
            style: Narration style (free string, stripped).
            language: Narration language tag (stripped).
            model: LLM model name.
            provider: LLM provider name.
            extra: Optional flat dict of call-specific deterministic
                inputs (e.g. ``{"target_count": 18}`` or the phase-1
                beats) folded into the key so cached responses are only
                reused for identical actual inputs.

        Returns:
            A 64-char sha256 hex digest.
        """
        payload = {
            "kind": str(kind),
            "topic": _normalize_topic(str(topic)),
            "style": str(style).strip(),
            "language": str(language).strip(),
            "prompt_template_version": PROMPT_TEMPLATE_VERSION,
            "model": str(model).strip(),
            "provider": str(provider).strip(),
            "extra": extra or {},
        }
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    # ── Entry path / lookup / store ───────────────────────

    def _entry_path(self, key: str) -> Path:
        """Return the JSON file path for a cache key."""
        return self.cache_dir / f"{key}.json"

    def lookup(self, key: str) -> Optional[dict]:
        """Return the cached entry dict, or ``None`` on miss/expiry/corruption.

        A hit increments ``hits``; every non-hit increments ``misses``.
        Expired and corrupt entries are deleted on sight. When the cache
        is disabled this always returns ``None`` without touching disk.
        """
        if not self.enabled:
            self.misses += 1
            return None
        path = self._entry_path(key)
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # Corrupt / unreadable file — treat as a miss and clean up.
            self.misses += 1
            with suppress(OSError):
                path.unlink()
            return None
        if not isinstance(entry, dict) or entry.get("key") != key or "response" not in entry:
            # Corrupt or foreign file — treat as a miss and clean up.
            self.misses += 1
            with suppress(OSError):
                path.unlink()
            return None
        raw_created = entry.get("created_at")
        age = _now() - float(raw_created) if isinstance(raw_created, (int, float)) else None
        if age is None or age < 0 or age > self.ttl_seconds:
            self.misses += 1
            with suppress(OSError):
                path.unlink()
            return None
        self.hits += 1
        return entry

    def store(
        self,
        key: str,
        *,
        kind: str,
        response: str,
        model: str = "",
        provider: str = "",
    ) -> None:
        """Persist a raw response atomically and enforce the LRU cap.

        Best-effort: any filesystem failure is suppressed so a cache
        write can never break a pipeline step. No-op when disabled.
        """
        if not self.enabled:
            return
        entry = {
            "key": key,
            "kind": str(kind),
            "response": str(response),
            "model": str(model),
            "provider": str(provider),
            "created_at": _now(),
        }
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            fd, tmp_path = tempfile.mkstemp(dir=str(self.cache_dir), suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump(entry, f, ensure_ascii=False)
                os.replace(tmp_path, self._entry_path(key))
            except BaseException:
                with suppress(OSError):
                    os.unlink(tmp_path)
                raise
        except OSError:
            return
        self._evict_over_cap()

    def _evict_over_cap(self) -> None:
        """Delete the oldest entries beyond ``max_entries`` (LRU by mtime)."""
        try:
            files = [p for p in self.cache_dir.glob("*.json") if p.is_file()]
        except OSError:
            return
        overflow = len(files) - self.max_entries
        if overflow <= 0:
            return
        try:
            files.sort(key=lambda p: p.stat().st_mtime)
        except OSError:
            return
        for path in files[:overflow]:
            with suppress(OSError):
                path.unlink()

    # ── Stats ─────────────────────────────────────────────

    def stats(self) -> dict:
        """Return hit/miss counters as a JSON-serializable dict."""
        total = self.hits + self.misses
        return {
            "enabled": self.enabled,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": (self.hits / total) if total else 0.0,
        }


# ── Process-level shared instance ─────────────────────────

_shared_cache: Optional[PromptCache] = None


def get_prompt_cache() -> PromptCache:
    """Return the process-level shared :class:`PromptCache`.

    Constructed lazily so ``MN_PROMPT_CACHE`` is read after env setup
    (tests construct explicit instances or call
    :func:`reset_prompt_cache` instead).
    """
    global _shared_cache
    if _shared_cache is None:
        _shared_cache = PromptCache()
    return _shared_cache


def reset_prompt_cache() -> None:
    """Drop the shared instance (used by tests / config reloads)."""
    global _shared_cache
    _shared_cache = None


def record_prompt_cache(ctx: Any, stage: str, hit: bool, key: str) -> None:
    """Record per-stage cache outcome in ``ctx.metadata["prompt_cache"]``.

    Stored as a list of ``{"stage", "hit", "key_prefix"}`` entries (one
    per cached stage; the key is truncated to 12 chars for readable
    metadata). Never raises — metadata bookkeeping must not break a step.
    """
    # Best-effort bookkeeping: suppress anything so metadata never breaks a step.
    with suppress(Exception):
        entry = {"stage": stage, "hit": hit, "key_prefix": str(key)[:12]}
        history = ctx.metadata.get("prompt_cache")
        if isinstance(history, list):
            history.append(entry)
        else:
            ctx.metadata["prompt_cache"] = [entry]


def note_prompt_cache(cache: "PromptCache", ctx: Any, stage: str, hit: bool, key: str) -> None:
    """Record cache outcome only when the cache is enabled.

    Keeps the default-off behaviour byte-identical: with the cache off,
    ``ctx.metadata`` gains no ``prompt_cache`` key at all.
    """
    if cache.enabled:
        record_prompt_cache(ctx, stage, hit, key)
