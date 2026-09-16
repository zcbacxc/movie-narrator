# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Hook dimension — opening-hook strength.

Frozen source: ``metadata["script_qa"]["hook_strength"]`` (0–10) →
``score = value / 10``.

Production note: the script judge currently writes ``hook_strength`` into
``metadata["script_judge"]`` (see ``pipeline/script.py``). This module
checks the frozen key first, then the production key, so existing runs
score correctly without changing the script step.

Only when both are missing does an optional independent judge callable
run; without one the dimension is ``unavailable`` (no LLM call by
default in post-pipeline analysis).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from .schema import DimensionResult


def _extract_hook_strength(metadata: Dict[str, Any]) -> tuple[Optional[float], str]:
    """Return (hook_strength 0–10, source_label) or (None, "missing")."""
    script_qa = metadata.get("script_qa") or {}
    if isinstance(script_qa, dict) and "hook_strength" in script_qa:
        try:
            val = float(script_qa["hook_strength"])
            return val, "script_qa"
        except (TypeError, ValueError):
            pass

    script_judge = metadata.get("script_judge") or {}
    if isinstance(script_judge, dict) and "hook_strength" in script_judge:
        try:
            val = float(script_judge["hook_strength"])
            return val, "script_judge"
        except (TypeError, ValueError):
            pass

    return None, "missing"


def evaluate_hook(
    metadata: Dict[str, Any],
    *,
    independent_judge: Optional[Callable[[Dict[str, Any]], Optional[float]]] = None,
) -> DimensionResult:
    """Compute the hook dimension from pipeline metadata.

    Args:
        metadata: ``ctx.metadata`` (or a facts dict with script_qa /
            script_judge).
        independent_judge: Optional fallback callable returning a 0–10
            hook strength when neither script_qa nor script_judge has
            the field. Invoked only on that path.

    Returns:
        DimensionResult with status measured | unavailable | error.
    """
    raw, source = _extract_hook_strength(metadata)

    if raw is None and independent_judge is not None:
        try:
            raw = independent_judge(metadata)
            if raw is not None:
                raw = float(raw)
                source = "independent_judge"
        except Exception as e:  # noqa: BLE001 — judge must not break analysis
            return DimensionResult(
                name="hook",
                score=None,
                status="error",
                details={"reason": f"independent_judge_failed: {e}"},
            )

    if raw is None:
        return DimensionResult(
            name="hook",
            score=None,
            status="unavailable",
            details={"reason": "hook_strength_missing", "source": source},
        )

    # Clamp raw to [0, 10] then normalize.
    raw_clamped = max(0.0, min(10.0, raw))
    score = raw_clamped / 10.0
    return DimensionResult(
        name="hook",
        score=round(score, 4),
        status="measured",
        details={
            "hook_strength": round(raw_clamped, 4),
            "source": source,
        },
    )
