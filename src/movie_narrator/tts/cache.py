# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""TTSCacheKey dataclass and cache filesystem layout helper."""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

CACHE_SCHEMA_VERSION = 3  # v0.4.23: key gains style_prompt, drops pause_ms (not audio-affecting)

PROVIDER_CACHE_VERSIONS: dict[str, int] = {
    "edge": 1,
    "openai": 1,
    "mimo": 1,
}


@dataclass(frozen=True, slots=True)
class TTSCacheKey:
    """Cache key for TTS audio results."""

    schema_version: int  # currently 3; bumps when key shape changes
    provider: str  # "edge" | "openai" | "mimo"
    provider_version: int  # per-backend encoding version
    model: str  # "" for Edge, "tts-1" for OpenAI
    voice: str
    text: str
    style_prompt: str  # ST-08: MiMo style_prompt affects audio; must be in key


def cache_path_for(root: Path, key: TTSCacheKey) -> Path:
    """Produce ``root/<hash[:2]>/<hash[2:4]>/<hash>.mp3`` for a cache key.

    Two-level fan-out keeps filesystem scans cheap when the cache grows.
    """
    raw = json.dumps(asdict(key), sort_keys=True, ensure_ascii=False).encode()
    h = hashlib.sha256(raw).hexdigest()
    return root / h[:2] / h[2:4] / f"{h}.mp3"


# ── Cache accounting (v1.2) ─────────────────────────────────
#
# Process-level counters that make cache effectiveness observable/auditable
# without changing the on-disk layout. ``record_cache_hit`` /
# ``record_cache_miss`` track the *effective* outcome (a corrupt cache file
# that gets re-synthesized counts as a miss, matching the cost tracker), and
# ``get_cache_stats`` additionally reports the current on-disk entry count /
# total bytes by scanning the cache roots that have been recorded.
#
# Counters are intentionally module-level (cross-task); use
# ``reset_cache_stats`` to reset them between unrelated test runs.

_HITS: int = 0
_MISSES: int = 0
_TRACKED_ROOTS: set[Path] = set()


def record_cache_hit(root: Path | None = None) -> None:
    """Increment the cache-hit counter; optionally register *root* for scan."""
    global _HITS
    if root is not None:
        _TRACKED_ROOTS.add(root)
    _HITS += 1


def record_cache_miss(root: Path | None = None) -> None:
    """Increment the cache-miss counter; optionally register *root* for scan."""
    global _MISSES
    if root is not None:
        _TRACKED_ROOTS.add(root)
    _MISSES += 1


def get_cache_stats() -> dict:
    """Return cache-accounting statistics as a JSON-serializable dict.

    Keys: ``hits``, ``misses``, ``hit_rate`` (0–1), ``entry_count``,
    ``total_bytes``. ``entry_count`` / ``total_bytes`` are computed on
    demand by scanning the cache roots registered via
    :func:`record_cache_hit` / :func:`record_cache_miss`.
    """
    total = _HITS + _MISSES
    hit_rate = (_HITS / total) if total else 0.0
    entry_count = 0
    total_bytes = 0
    for root in _TRACKED_ROOTS:
        if not root.is_dir():
            continue
        for file in root.rglob("*.mp3"):
            entry_count += 1
            try:
                total_bytes += file.stat().st_size
            except OSError:
                # File vanished between glob and stat (e.g. eviction) — skip.
                continue
    return {
        "hits": _HITS,
        "misses": _MISSES,
        "hit_rate": round(hit_rate, 4),
        "entry_count": entry_count,
        "total_bytes": total_bytes,
    }


def reset_cache_stats() -> None:
    """Reset all process-level cache statistics and registered roots.

    Intended for tests and for very long-lived processes that want a fresh
    accounting window.
    """
    global _HITS, _MISSES
    _HITS = 0
    _MISSES = 0
    _TRACKED_ROOTS.clear()
