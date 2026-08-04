# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Per-run cost tracking for LLM token usage and TTS calls.

v0.7.0: Accumulates LLM and TTS call records throughout a single
pipeline run, then exports a summary dict for ``metadata.json``.

The tracker is thread-safe (guarded by an internal :class:`threading.Lock`)
so concurrent TTS / LLM calls can safely record usage. Cost figures are
coarse approximations (``estimated``), not precise billing values.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from typing import Any, Dict

# ── Estimated unit costs (coarse approximation) ───────────
# These are simple heuristics for a GPT-4o-mini class model and the
# built-in TTS providers. They are NOT precise billing values and are
# always flagged as ``estimated`` in the summary so downstream readers
# do not mistake them for authoritative numbers.
_LLM_PROMPT_COST_PER_1K = 0.002  # USD per 1K prompt tokens
_LLM_COMPLETION_COST_PER_1K = 0.006  # USD per 1K completion tokens

# TTS cost per 1K characters. ``edge`` and ``mimo`` are free.
_TTS_COST_PER_1K_CHARS: Dict[str, float] = {
    "edge": 0.0,
    "openai": 0.015,
    "mimo": 0.0,
}


@dataclass
class LLMCallRecord:
    """Single LLM API call record."""

    step: str  # "script" | "research" | "translate"
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class TTSCallRecord:
    """Single TTS call record."""

    provider: str  # "edge" | "openai" | "mimo"
    model: str = ""
    characters: int = 0
    segments: int = 1
    cached: bool = False


@dataclass
class CostTracker:
    """Thread-safe cost tracker for a single pipeline run.

    Accumulates LLM token usage and TTS call counts throughout the
    pipeline, then exports a summary for ``metadata.json``.
    """

    llm_calls: list[LLMCallRecord] = field(default_factory=list)
    tts_calls: list[TTSCallRecord] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock, repr=False)

    def record_llm_call(self, step: str, model: str, usage: dict) -> None:
        """Record an LLM API call from an OpenAI ``response.usage`` dict.

        The *usage* dict typically has ``prompt_tokens``,
        ``completion_tokens``, and ``total_tokens`` keys. Missing keys
        default to 0. ``step`` should be one of ``"script"``,
        ``"research"``, or ``"translate"``.

        Non-dict *usage* values (e.g. mock objects without ``.get``) are
        silently ignored so cost tracking never breaks the pipeline.
        """
        if not isinstance(usage, dict):
            return
        record = LLMCallRecord(
            step=step,
            model=model,
            prompt_tokens=int(usage.get("prompt_tokens", 0) or 0),
            completion_tokens=int(usage.get("completion_tokens", 0) or 0),
            total_tokens=int(usage.get("total_tokens", 0) or 0),
        )
        with self._lock:
            self.llm_calls.append(record)

    def record_tts_call(
        self,
        provider: str,
        model: str = "",
        characters: int = 0,
        segments: int = 1,
        cached: bool = False,
    ) -> None:
        """Record a TTS call.

        ``provider`` should be one of ``"edge"``, ``"openai"``, or
        ``"mimo"``. ``cached=True`` marks the segment as served from
        cache (no network/API cost incurred).
        """
        record = TTSCallRecord(
            provider=provider,
            model=model,
            characters=int(characters),
            segments=int(segments),
            cached=bool(cached),
        )
        with self._lock:
            self.tts_calls.append(record)

    # ── Summary export ─────────────────────────────────────

    def _llm_by_step(self) -> Dict[str, Dict[str, int]]:
        """Aggregate LLM usage per step name."""
        by_step: Dict[str, Dict[str, int]] = {}
        with self._lock:
            calls = list(self.llm_calls)
        for rec in calls:
            bucket = by_step.setdefault(
                rec.step,
                {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            )
            bucket["calls"] += 1
            bucket["prompt_tokens"] += rec.prompt_tokens
            bucket["completion_tokens"] += rec.completion_tokens
            bucket["total_tokens"] += rec.total_tokens
        return by_step

    def _tts_by_provider(self) -> Dict[str, Dict[str, int]]:
        """Aggregate TTS usage per provider name."""
        by_provider: Dict[str, Dict[str, int]] = {}
        with self._lock:
            calls = list(self.tts_calls)
        for rec in calls:
            bucket = by_provider.setdefault(
                rec.provider,
                {"calls": 0, "segments": 0, "characters": 0, "cached_segments": 0},
            )
            bucket["calls"] += 1
            bucket["segments"] += rec.segments
            bucket["characters"] += rec.characters
            if rec.cached:
                bucket["cached_segments"] += rec.segments
        return by_provider

    def summary(self) -> Dict[str, Any]:
        """Export cost summary for ``metadata.json``.

        Returns:
            A dict with ``llm`` and ``tts`` sub-dicts. All cost
            figures are flagged ``estimated`` (coarse approximation, not
            precise billing values).
        """
        by_step = self._llm_by_step()
        by_provider = self._tts_by_provider()

        # LLM totals
        total_prompt = sum(b["prompt_tokens"] for b in by_step.values())
        total_completion = sum(b["completion_tokens"] for b in by_step.values())
        total_tokens = sum(b["total_tokens"] for b in by_step.values())
        total_llm_calls = sum(b["calls"] for b in by_step.values())

        # Estimated LLM cost (USD)
        llm_cost = (
            total_prompt / 1000.0 * _LLM_PROMPT_COST_PER_1K
            + total_completion / 1000.0 * _LLM_COMPLETION_COST_PER_1K
        )

        # TTS totals
        total_tts_calls = sum(b["calls"] for b in by_provider.values())
        total_segments = sum(b["segments"] for b in by_provider.values())
        total_characters = sum(b["characters"] for b in by_provider.values())
        cached_segments = sum(b["cached_segments"] for b in by_provider.values())

        # Estimated TTS cost (USD) — only non-cached characters are billed.
        tts_cost = 0.0
        for rec in self.tts_calls:
            if rec.cached:
                continue
            rate = _TTS_COST_PER_1K_CHARS.get(rec.provider, 0.0)
            tts_cost += rec.characters / 1000.0 * rate

        return {
            "llm": {
                "total_calls": total_llm_calls,
                "total_prompt_tokens": total_prompt,
                "total_completion_tokens": total_completion,
                "total_tokens": total_tokens,
                "by_step": by_step,
                "estimated_cost_usd": round(llm_cost, 6),
            },
            "tts": {
                "total_calls": total_tts_calls,
                "total_segments": total_segments,
                "total_characters": total_characters,
                "cached_segments": cached_segments,
                "by_provider": by_provider,
                "estimated_cost_usd": round(tts_cost, 6),
            },
        }
