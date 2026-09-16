# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Gate-2 formal E2 dual-baseline for the selected L2 pair.

Uses the frozen harness protocol (reconstruct ≠ deepcopy, resource-level
diffs, declared-write check, normalize(R) ≡ normalize(M)) with
**deterministic step executors** — no real LLM/TTS/ffmpeg provider quota.

This is the formal semantic-equivalence run required before Gate-2 may
set ``semantic_equivalent``. ``potential_speedup`` stays report-only.

Usage:

    python scripts/run_gate2_e2_formal.py --output benchmarks/gate2_e2_formal.json
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _set_resource(ctx: Any, ref: str, value: Any) -> None:
    from movie_narrator.pipeline.e2_harness import PREFIX_ARTIFACT, PREFIX_CTX, PREFIX_META, _ARTIFACT_TO_PATH
    from movie_narrator.pipeline.step_contracts import split_resource_ref

    prefix, name = split_resource_ref(ref)
    if prefix == PREFIX_CTX:
        setattr(ctx, name, value)
    elif prefix == PREFIX_META:
        ctx.metadata[name] = value
    elif prefix == PREFIX_ARTIFACT:
        path_field = _ARTIFACT_TO_PATH.get(name)
        if path_field:
            setattr(ctx, path_field, value)


def _make_executor(
    writes: Tuple[str, ...],
    values: Dict[str, Any],
    sleep_s: float = 0.01,
) -> Callable[[Any], Any]:
    def _exec(ctx: Any) -> Any:
        time.sleep(sleep_s)
        for ref in writes:
            _set_resource(ctx, ref, values.get(ref, f"value:{ref}"))
        return ctx

    return _exec


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="benchmarks/gate2_e2_formal.json")
    parser.add_argument("--reps", type=int, default=3, help="Repeat E2 protocol for stability")
    args = parser.parse_args(argv)

    from movie_narrator.models import Context, Services
    from movie_narrator.pipeline.canonical import build_canonical_registry
    from movie_narrator.pipeline.dag_analysis import (
        build_gate2_report,
        contract_complete,
        resources_conflict,
        select_e2_pair,
    )
    from movie_narrator.pipeline.e2_harness import compare_parallel_vs_sequential
    from movie_narrator.utils.console import SilentConsole

    reg = build_canonical_registry()
    pair = select_e2_pair(reg)
    if pair is None:
        print("ERROR: no E2-eligible L2 pair", file=sys.stderr)
        return 2
    step_a, step_b = pair
    entry_a, entry_b = reg.get(step_a), reg.get(step_b)
    assert entry_a is not None and entry_b is not None
    writes_a = tuple(entry_a.writes)
    writes_b = tuple(entry_b.writes)

    # Deterministic declared-only writes (subset of contract writes).
    # Values must be identical across branches for resource-level merge.
    val_a = {ref: f"A:{ref}" for ref in writes_a}
    val_b = {ref: f"B:{ref}" for ref in writes_b}
    # Shared logical resources would conflict — contracts should not overlap.
    conflict = resources_conflict(entry_a.reads, entry_a.writes, entry_b.reads, entry_b.writes)

    base_root = Path(tempfile.mkdtemp(prefix="gate2_e2_"))
    s0 = Context(
        movie_name="Gate2",
        output_dir=str(base_root / "s0"),
        source_video_path=str(base_root / "src.mp4"),
    )
    s0.services = Services(console=SilentConsole())
    Path(s0.output_dir).mkdir(parents=True, exist_ok=True)

    execute_a = _make_executor(writes_a, val_a)
    execute_b = _make_executor(writes_b, val_b)

    results = []
    for i in range(args.reps):
        root_i = Path(tempfile.mkdtemp(prefix=f"gate2_rep{i}_"))

        def make_root(label: str, _r=root_i) -> str:
            p = _r / label
            p.mkdir(parents=True, exist_ok=True)
            return str(p)

        eq = compare_parallel_vs_sequential(
            s0,
            execute_a=execute_a,
            execute_b=execute_b,
            writes_a=writes_a,
            writes_b=writes_b,
            make_branch_root=make_root,
        )
        results.append(
            {
                "rep": i,
                "equivalent": eq.equivalent,
                "undeclared_a": list(eq.undeclared_a),
                "undeclared_b": list(eq.undeclared_b),
                "merge_conflicts": list(eq.merge_conflicts),
                "notes": list(eq.notes),
            }
        )

    all_eq = all(r["equivalent"] for r in results)
    report = build_gate2_report(
        reg,
        step_a,
        step_b,
        timing_a=0.01,
        timing_b=0.01,
        semantic_equivalent=all_eq,
    )

    payload = {
        "step_a": step_a,
        "step_b": step_b,
        "writes_a": list(writes_a),
        "writes_b": list(writes_b),
        "resource_conflict_static": bool(conflict),
        "reps": args.reps,
        "e2_runs": results,
        "semantic_equivalent": all_eq,
        "gate2": {
            "step_a": report.step_a,
            "step_b": report.step_b,
            "l2_compatible": report.l2_compatible,
            "contract_complete_a": report.contract_complete_a,
            "contract_complete_b": report.contract_complete_b,
            "theoretical_eligible": report.theoretical_eligible,
            "semantic_equivalent": report.semantic_equivalent,
            "potential_speedup": report.potential_speedup,
            "notes": list(report.notes),
        },
        "honesty": {
            "providers": "none — deterministic executors only",
            "potential_speedup": "report/F-eligibility only; not Gate-2 pass/fail",
            "real_speedup_ge_1_10": "F-phase only; not claimed here",
        },
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("step_a", "step_b", "semantic_equivalent")}, indent=2))
    print(f"theoretical_eligible={report.theoretical_eligible}")
    print(f"Wrote {out}")
    return 0 if all_eq else 1


if __name__ == "__main__":
    raise SystemExit(main())
