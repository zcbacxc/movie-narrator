#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Trend analysis across golden-sample regression runs.

Scans ``output/l2-runs/<run>/metadata.json`` and prints a trend table of
match-quality metrics, alerting when ``heuristic_ratio`` regresses beyond a
threshold between consecutive runs.

Usage:
    python scripts/match_trend.py [--root output/l2-runs] [--warn-delta 0.1]

This tool is part of the Golden Sample Regression SOP (see
``docs/BEST_PRACTICES.md``). It is standalone and does not import
``movie_narrator``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

DEFAULT_ROOT = Path("output/l2-runs")
WARN_DELTA = 0.1  # heuristic_ratio 环比回升超过此值即告警


def safe_get(d: dict, *keys: str, default: Any = None) -> Any:
    """Safely traverse nested dict keys (mirrors compare_runs.py)."""
    cur: Any = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
    return cur


def fmt(v: float | None) -> str:
    return "-" if v is None else f"{v:.4f}"


def load_runs(root: Path) -> list[tuple[str, dict[str, Any]]]:
    """Return (run_name, metadata) for each run exposing metadata.json."""
    runs: list[tuple[str, dict[str, Any]]] = []
    if not root.exists():
        print(f"WARN: regression root not found: {root}", file=sys.stderr)
        return runs
    for child in sorted(root.iterdir()):
        meta = child / "metadata.json"
        if child.is_dir() and meta.exists():
            try:
                with open(meta, encoding="utf-8") as f:
                    runs.append((child.name, json.load(f)))
            except (json.JSONDecodeError, OSError) as e:  # noqa: BLE001
                print(f"WARN: skip {meta}: {e}", file=sys.stderr)
    return runs


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Trend analysis across golden-sample regression runs."
    )
    parser.add_argument(
        "--root", default=str(DEFAULT_ROOT),
        help=f"Regression runs root (default: {DEFAULT_ROOT})",
    )
    parser.add_argument(
        "--warn-delta", type=float, default=WARN_DELTA,
        help=f"heuristic_ratio ring-ratio alert threshold (default: {WARN_DELTA})",
    )
    args = parser.parse_args()

    runs = load_runs(Path(args.root))
    if not runs:
        print("No regression runs found.")
        return 0

    print(f"# Match Trend - {len(runs)} runs from {args.root}\n")
    print("| Run | heuristic_ratio | embedding_ratio | score.avg | "
          "speed_factor.avg |")
    print("|-----|-----------------|-----------------|-----------|"
          "------------------|")

    prev_hr: float | None = None
    alerts: list[str] = []
    for name, meta in runs:
        ms = safe_get(meta, "match_summary", default={}) or {}
        hr = ms.get("heuristic_ratio")
        er = ms.get("embedding_ratio")
        sa = safe_get(ms, "score", "avg")
        sfa = safe_get(ms, "speed_factor", "avg")
        print(f"| {name} | {fmt(hr)} | {fmt(er)} | {fmt(sa)} | {fmt(sfa)} |")

        if prev_hr is not None and hr is not None:
            delta = hr - prev_hr
            if delta > args.warn_delta:
                alerts.append(
                    f"  [!] {name}: heuristic_ratio 环比回升 +{delta:.4f} "
                    f"(> {args.warn_delta}) - match quality may have regressed"
                )
        prev_hr = hr

    print()
    if alerts:
        print("## Alerts")
        for a in alerts:
            print(a)
        print("\nAction: before releasing, re-check the flagged sample and "
              "identify the PR that raised heuristic_ratio.")
        return 1
    print(f"OK: no heuristic_ratio ring-ratio regression beyond "
          f"{args.warn_delta}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
