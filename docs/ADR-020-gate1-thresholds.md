# ADR-020 — Gate-1 parallel race thresholds (freeze)

**Status:** Accepted (freeze before formal R=5)  
**Date:** 2026-09-16  
**Supersedes:** —  
**Related:** IMPLEMENTATION_PLAN M2 Gate-1; `scripts/race_parallel_benchmark.py`; `GATE1_DECISION.md`

## Context

Race candidates can run via bounded `ThreadPoolExecutor` (`mn race --parallel N`). Gate-1 decides whether parallelism may become a recommended non-default, or whether default stays **P=1** (sequential-equivalent). Formal Gate-1 requires cold-cache **R=5** independent processes per tier. Pilot runs never enter the Gate.

Pilot (mock, R=2, N=3, sleep=80ms) validated harness wiring only:

| P | median wall | speedup vs P=1 | usage_ratio |
|---|---|---|---|
| 1 | 0.246s | 1.00 | 1.00 |
| 2 | 0.164s | 1.50 | 1.00 |
| 3 | 0.082s | 2.99 | 1.00 |

These numbers are **not** Gate evidence (mock sleep ≈ ideal parallel).

## Decision (frozen thresholds)

| Criterion | Threshold | Role |
|---|---|---|
| `speedup(P=N)` where N = candidate count (default 3) | **≥ 1.10** | pass/fail primary |
| `speedup(P=2)` | report only | — |
| `usage_ratio(P)` | **≤ 1.05** | pass/fail (all tested P>1) |
| failure_rate(P) | report only | — |
| R | **5** independent processes | protocol |
| cold-cache | empty app media/prompt cache + `reset_usage_ledger()`; do **not** clear OS/HF model cache | protocol |
| Default if FAIL/ABORT | **remain P=1** | stop-loss |

Process:

```
pilot (≤2/tier, mock or real; NOT in Gate)
  → this ADR (thresholds frozen)
  → formal R=5 cold-cache
  → GATE1_DECISION.md
```

## Consequences

- PASS at N=3 → document `--parallel 3` as recommended for I/O-heavy races; default CLI still unset→P=1 unless product later changes default.
- FAIL/ABORT → keep P=1; retain `--parallel` as experimental.
- Mock/synthetic pilot data is never copied into `GATE1_DECISION.md` result tables.

## Alternatives considered

1. **Speedup ≥ 1.01 (any gain)** — rejected: noise dominates; 1.10 matches Gate-2 F-phase spirit.
2. **Require speedup ≥ 2.0 at P=3** — rejected: FFmpeg/GPU critical path limits ideal speedup below N.
3. **Change default to P=N on PASS** — deferred; product default change is a separate decision from Gate-1 measurement.
