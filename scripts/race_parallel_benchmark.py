#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Three-tier cold-cache race benchmark harness (P2-9).

**Not** part of the default unit suite. Run manually for Gate-1 formal
or pilot measurements. Does **not** decide Gate-1 — fill
``docs-nocommit/in-progress/GATE1_DECISION.md`` from the JSON output.

Usage (pilot, mocked pipeline — no network):

    python scripts/race_parallel_benchmark.py --mock --tiers 1,2,3 --reps 2

Usage (formal R=5 cold-cache, real pipeline):

    python scripts/race_parallel_benchmark.py \\
        --movie "YourMovie" --video path/to/movie.mp4 \\
        --tiers 1,2,3 --reps 5 --cold-cache \\
        --output benchmarks/race_gate1.json

Gate-1 formulas (frozen):

    speedup(P) = median(t_P1) / median(t_P)
    usage_ratio(P) = median(cost_P/cand) / median(cost_1/cand) <= 1.05

Cold-cache: empty app media/prompt cache + reset shared ledger.
Do **not** clear OS/HF model cache.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Allow running from a source checkout without install.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _parse_tiers(raw: str) -> List[int]:
    tiers: List[int] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        n = int(part)
        if n < 1:
            raise SystemExit(f"invalid tier {part!r}: must be >= 1")
        tiers.append(n)
    if not tiers:
        raise SystemExit("--tiers must list at least one positive integer")
    return tiers


def _median(xs: List[float]) -> float:
    return float(statistics.median(xs)) if xs else 0.0


def _p95(xs: List[float]) -> float:
    if not xs:
        return 0.0
    if len(xs) == 1:
        return float(xs[0])
    ordered = sorted(xs)
    # Nearest-rank p95
    idx = min(len(ordered) - 1, max(0, int(round(0.95 * len(ordered))) - 1))
    return float(ordered[idx])


def _cold_cache_reset() -> None:
    """Reset shared usage ledger. App cache clearing is operator-side."""
    try:
        from movie_narrator.utils.cost_ledger import reset_usage_ledger

        reset_usage_ledger()
    except Exception as e:  # noqa: BLE001
        print(f"WARN: reset_usage_ledger failed: {e}", file=sys.stderr)


def _install_mocks(sleep_s: float = 0.05) -> None:
    """Install deterministic build_context/run_pipeline mocks (pilot only)."""
    from unittest.mock import patch

    from movie_narrator.models import Context, Services
    from movie_narrator.utils.console import SilentConsole
    from movie_narrator.utils.cost_tracker import CostTracker

    def fake_build(**kwargs: Any) -> Context:
        ctx = Context(
            movie_name="bench",
            output_dir=str(kwargs["output_dir"]),
            source_video_path="/fake/video.mp4",
        )
        ctx.services = Services(console=SilentConsole())
        ctx.cost_tracker = CostTracker()
        ctx.cost_tracker.record_llm_call(
            step="script", model="mock", usage={"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}
        )
        return ctx

    def fake_run(ctx: Context, **kwargs: Any) -> Context:
        time.sleep(sleep_s)
        ctx.metadata["match_summary"] = {
            "segments": 10,
            "embedding_ratio": 0.8,
            "score": {"avg": 0.6},
            "diversity": {"swaps": 2},
        }
        ctx.metadata["duration_metrics"] = {"ratio": 1.0}
        ctx.video_path = str(Path(ctx.output_dir) / "output.mp4")
        return ctx

    patch("movie_narrator.pipeline.runner.build_context", side_effect=fake_build).start()
    patch("movie_narrator.pipeline.runner.run_pipeline", side_effect=fake_run).start()


def _run_once(
    *,
    parallelism: int,
    candidates: int,
    movie: str,
    style: str,
    duration: int,
    video: Optional[str],
    output_root: Path,
    presets: Optional[List[str]],
) -> Dict[str, Any]:
    from movie_narrator.race import generate_candidates, run_race
    from movie_narrator.race_executor import synthesize_estimated_cost_total_usd

    out = output_root / f"P{parallelism}" / f"run-{time.time_ns()}"
    out.mkdir(parents=True, exist_ok=True)

    cands = generate_candidates(n=candidates, presets=presets)
    t0 = time.perf_counter()
    results = run_race(
        cands,
        movie=movie,
        style=style,
        duration=duration,
        voice=None,
        video_format="16:9",
        output_base=out,
        video=video,
        parallelism=parallelism,
    )
    wall = time.perf_counter() - t0

    costs = []
    outcomes: Dict[str, int] = {}
    for r in results:
        outcomes[r.outcome.value] = outcomes.get(r.outcome.value, 0) + 1
        if r.metrics is not None:
            costs.append(r.metrics.estimated_cost_total_usd)

    n = max(1, len(results))
    return {
        "parallelism": parallelism,
        "wall_time_s": wall,
        "submitted": len(results),
        "outcomes": outcomes,
        "est_cost_total_usd": sum(costs),
        "est_cost_per_candidate_usd": sum(costs) / n,
        "winner": next((r.config.label for r in results if r.is_success), None),
    }


def _summarize(runs_by_p: Dict[int, List[Dict[str, Any]]]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"tiers": {}}
    for p, runs in sorted(runs_by_p.items()):
        walls = [r["wall_time_s"] for r in runs]
        costs = [r["est_cost_per_candidate_usd"] for r in runs]
        summary["tiers"][str(p)] = {
            "reps": len(runs),
            "wall_median_s": _median(walls),
            "wall_p95_s": _p95(walls),
            "cost_per_cand_median_usd": _median(costs),
            "runs": runs,
        }

    base = summary["tiers"].get("1")
    if base:
        t1 = base["wall_median_s"]
        c1 = base["cost_per_cand_median_usd"]
        for p, tier in summary["tiers"].items():
            if p == "1":
                tier["speedup"] = 1.0
                tier["usage_ratio"] = 1.0
                continue
            tier["speedup"] = (t1 / tier["wall_median_s"]) if tier["wall_median_s"] else 0.0
            tier["usage_ratio"] = (
                (tier["cost_per_cand_median_usd"] / c1) if c1 else None
            )
    return summary


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--movie", default="BenchMovie")
    parser.add_argument("--style", default="热血搞笑")
    parser.add_argument("--duration", type=int, default=30)
    parser.add_argument("--video", default=None, help="Source video path (formal runs)")
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--presets", default=None, help="Comma-separated preset names")
    parser.add_argument("--tiers", default="1,2,3", help="Parallelism tiers, e.g. 1,2,3")
    parser.add_argument("--reps", type=int, default=5, help="Independent repetitions (formal Gate-1 = 5)")
    parser.add_argument("--sleep", type=float, default=0.05, help="Mock pipeline sleep (mock mode only)")
    parser.add_argument("--mock", action="store_true", help="Use mocked build_context/run_pipeline (pilot)")
    parser.add_argument("--cold-cache", action="store_true", help="Reset shared usage ledger before each run")
    parser.add_argument("--output", default="benchmarks/race_parallel.json")
    parser.add_argument(
        "--tag",
        default="pilot",
        help="Label stored in JSON (pilot | formal | ...). Formal data is required for Gate-1.",
    )
    args = parser.parse_args(argv)

    if args.reps < 1:
        raise SystemExit("--reps must be >= 1")
    tiers = _parse_tiers(args.tiers)
    presets = [p.strip() for p in args.presets.split(",") if p.strip()] if args.presets else None

    if args.mock:
        _install_mocks(sleep_s=args.sleep)
    elif not args.video:
        print("NOTE: formal runs usually need --video; continuing without it.", file=sys.stderr)

    output_root = Path("benchmarks") / "runs"
    runs_by_p: Dict[int, List[Dict[str, Any]]] = {p: [] for p in tiers}

    for p in tiers:
        for rep in range(args.reps):
            if args.cold_cache:
                _cold_cache_reset()
            print(f"[{args.tag}] P={p} rep={rep + 1}/{args.reps} ...", flush=True)
            rec = _run_once(
                parallelism=p,
                candidates=args.candidates,
                movie=args.movie,
                style=args.style,
                duration=args.duration,
                video=args.video,
                output_root=output_root,
                presets=presets,
            )
            runs_by_p[p].append(rec)
            print(
                f"  wall={rec['wall_time_s']:.3f}s "
                f"cost/cand=${rec['est_cost_per_candidate_usd']:.6f} "
                f"outcomes={rec['outcomes']}",
                flush=True,
            )

    summary = _summarize(runs_by_p)
    summary["meta"] = {
        "tag": args.tag,
        "mock": bool(args.mock),
        "cold_cache": bool(args.cold_cache),
        "candidates": args.candidates,
        "tiers": tiers,
        "reps": args.reps,
        "note": "pilot data must not enter Gate-1; fill GATE1_DECISION.md from formal R=5 runs only",
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {out_path}")
    print(json.dumps({k: v for k, v in summary.items() if k != "tiers"}, indent=2))
    for p, tier in summary["tiers"].items():
        print(
            f"P={p}: median_wall={tier['wall_median_s']:.3f}s "
            f"speedup={tier.get('speedup')} usage_ratio={tier.get('usage_ratio')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
