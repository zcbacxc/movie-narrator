# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Provider usage ledger — always-on, cheap call counters (v1.5.1).

The ledger records *how much* the engine talks to its providers at the
shared call boundaries:

- **LLM** — one record per attempt (retry outcomes included) from the
  shared chat-completion path in ``utils/llm.py``: logical kind
  (``research`` / ``script_beats`` / ``script_expand`` / ``judge`` /
  ``translate`` / ...), attempt count, errors, prompt/response chars.
- **TTS** — per-segment synthesis counters from ``pipeline/tts.py``:
  provider, characters, cache hits, retries.
- **VLM** — captioner call counters (API is provided now; the src hook
  lands with the next vision-touching release).

Design constraints (deliberate):

- **Always-on, cheap, no I/O** — pure in-memory counters behind one
  lock; recording can never fail a pipeline step.
- **Measurable, not gating** — this makes the deferred idempotency
  decision (ROADMAP: "revisit only if duplicate-billing becomes
  measurable") observable: duplicate LLM/TTS spend becomes a number,
  not an anecdote.
- **Snapshot surface** — ``ctx.metadata["usage"]`` (set at the TTS
  step) flows into ``metadata.json`` via the existing export path.
  execution-manifest integration waits for a runner-touching release.

Typical usage::

    from movie_narrator.utils.cost_ledger import get_usage_ledger

    get_usage_ledger().record_llm(kind="research", attempts=1)
    print(get_usage_ledger().summary())
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Optional

__all__ = ["UsageLedger", "get_usage_ledger", "reset_usage_ledger"]


def _bucket(attempts: int = 0, cache_hits: int = 0, prompt_chars: int = 0, resp_chars: int = 0) -> Dict[str, int]:
    return {
        "attempts": attempts,
        "cache_hits": cache_hits,
        "prompt_chars": prompt_chars,
        "resp_chars": resp_chars,
    }


class UsageLedger:
    """Thread-safe, in-memory provider usage counters.

    All ``record_*`` methods are best-effort by contract: they accept
    plain ints and never raise on sane input. ``summary()`` returns a
    JSON-serializable deep copy, safe to embed in metadata.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # LLM counters (shared chat-completion boundary)
        self._llm: Dict[str, int] = {
            "attempts": 0,
            "errors": 0,
            "cache_hits": 0,
            "prompt_chars": 0,
            "resp_chars": 0,
        }
        self._llm_by_kind: Dict[str, Dict[str, int]] = {}
        # TTS counters (segment synthesis boundary)
        self._tts: Dict[str, int] = {
            "synth_calls": 0,
            "chars": 0,
            "cache_hits": 0,
            "retries": 0,
        }
        self._tts_by_provider: Dict[str, Dict[str, int]] = {}
        # VLM counters (captioner boundary)
        self._vlm: Dict[str, int] = {"calls": 0, "cache_hits": 0}

    # ── Recording ──────────────────────────────────────────

    def record_llm(
        self,
        *,
        kind: str = "other",
        attempts: int = 1,
        cache_hit: bool = False,
        prompt_chars: int = 0,
        resp_chars: int = 0,
        error: bool = False,
    ) -> None:
        """Record LLM usage from the shared completion path.

        Args:
            kind: Logical call identity (``research``,
                ``script_beats``, ``script_expand``, ``judge``,
                ``translate``, ...). The shared path in
                ``utils/llm.py`` records ``"other"`` because call-kind
                attribution is not available at that boundary.
            attempts: Attempts consumed (1 per record from the hook;
                aggregated call sites may pass more).
            cache_hit: The call was served from the prompt cache
                (i.e. it never reached the network).
            prompt_chars / resp_chars: Character volumes observed.
            error: The attempt failed (retry outcome).
        """
        with self._lock:
            self._llm["attempts"] += max(0, attempts)
            self._llm["errors"] += 1 if error else 0
            self._llm["cache_hits"] += 1 if cache_hit else 0
            self._llm["prompt_chars"] += max(0, prompt_chars)
            self._llm["resp_chars"] += max(0, resp_chars)
            bucket = self._llm_by_kind.setdefault(str(kind or "other"), _bucket())
            bucket["attempts"] += max(0, attempts)
            bucket["cache_hits"] += 1 if cache_hit else 0
            bucket["prompt_chars"] += max(0, prompt_chars)
            bucket["resp_chars"] += max(0, resp_chars)

    def record_tts(
        self,
        *,
        provider: str = "",
        chars: int = 0,
        cache_hit: bool = False,
        retries: int = 0,
    ) -> None:
        """Record TTS usage for one synthesis (one segment).

        Args:
            provider: TTS provider name (``edge`` / ``openai`` / ...).
            chars: Synthesized character count.
            cache_hit: The segment was served from the TTS cache.
            retries: Retry attempts consumed by this synthesis.
        """
        with self._lock:
            self._tts["synth_calls"] += 1
            self._tts["chars"] += max(0, chars)
            self._tts["cache_hits"] += 1 if cache_hit else 0
            self._tts["retries"] += max(0, retries)
            key = str(provider or "unknown")
            bucket = self._tts_by_provider.setdefault(
                key, {"calls": 0, "chars": 0, "cache_hits": 0}
            )
            bucket["calls"] += 1
            bucket["chars"] += max(0, chars)
            bucket["cache_hits"] += 1 if cache_hit else 0

    def record_vlm(self, *, calls: int = 1, cache_hit: bool = False) -> None:
        """Record vision-captioner (VLM) usage.

        Args:
            calls: Captioner invocations to add.
            cache_hit: The caption was served from a cache.
        """
        with self._lock:
            self._vlm["calls"] += max(0, calls)
            self._vlm["cache_hits"] += 1 if cache_hit else 0

    # ── Snapshot ───────────────────────────────────────────

    def summary(self) -> Dict[str, Any]:
        """Return a JSON-serializable deep copy of all counters."""
        with self._lock:
            return {
                "llm": {
                    **self._llm,
                    "by_kind": {k: dict(v) for k, v in sorted(self._llm_by_kind.items())},
                },
                "tts": {
                    **self._tts,
                    "by_provider": {
                        k: dict(v) for k, v in sorted(self._tts_by_provider.items())
                    },
                },
                "vlm": dict(self._vlm),
            }


# ── Process-level shared instance ─────────────────────────

_shared_ledger: Optional[UsageLedger] = None


def get_usage_ledger() -> UsageLedger:
    """Return the process-level shared :class:`UsageLedger`.

    The CLI pipeline is single-run per process, so a shared instance
    keeps the provider-boundary hooks (``utils/llm.py``,
    ``pipeline/tts.py``) to one line each — they have no ``ctx`` at
    that depth. Concurrent in-process runs share counters; use
    :func:`reset_usage_ledger` at run boundaries if embedding.
    """
    global _shared_ledger
    if _shared_ledger is None:
        _shared_ledger = UsageLedger()
    return _shared_ledger


def reset_usage_ledger() -> None:
    """Drop the shared instance (used by tests / run boundaries)."""
    global _shared_ledger
    _shared_ledger = None
