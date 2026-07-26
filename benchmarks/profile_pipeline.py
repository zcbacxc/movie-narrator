#!/usr/bin/env python3
"""Pipeline performance benchmark.

Profiles each of the 15 built-in pipeline steps in CI mode (mock LLM +
silent TTS) to establish baseline timings. Output is a JSON report plus
a human-readable summary printed to stdout.

Usage::

    python benchmarks/profile_pipeline.py
    python benchmarks/profile_pipeline.py --output benchmark.json
    python benchmarks/profile_pipeline.py --runs 3

Requirements:
    - movie-narrator installed (pip install -e ".[dev]")
    - ffmpeg with mp3 encoder (libmp3lame)
    - CI=1 environment variable (auto-set by this script)

The benchmark does NOT make real LLM or TTS API calls — it uses the
CI mock fallback that produces 4 canned script segments and silent
TTS audio. This isolates pipeline orchestration overhead from network
latency.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# Force CI mode before importing movie_narrator
os.environ["CI"] = "1"

# Add src to path when running from source checkout
_src = Path(__file__).resolve().parent.parent / "src"
if _src.exists():
    sys.path.insert(0, str(_src))


@dataclass
class StepTiming:
    name: str
    duration_sec: float
    soft: bool
    status: str = "ok"


@dataclass
class BenchmarkResult:
    timestamp: str
    python_version: str
    total_duration_sec: float
    steps: list[StepTiming] = field(default_factory=list)
    slowest_step: Optional[str] = None
    fastest_step: Optional[str] = None


def _ffmpeg_has_mp3() -> bool:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        return "libmp3lame" in result.stdout
    except Exception:
        return False


def _run_benchmark(output_dir: Path) -> BenchmarkResult:
    """Run one pipeline execution and collect per-step timings."""
    from movie_narrator.models import Context, Services
    from movie_narrator.pipeline.runner import build_context, run_pipeline
    from movie_narrator.pipeline.registry import step_registry
    from movie_narrator.utils.console import SilentConsole

    from unittest.mock import patch

    # Build context in CI mode
    ctx = build_context(
        movie="Benchmark Movie",
        style="test",
        duration=30,
        voice=None,
        format="16:9",
        output_dir=output_dir,
        keep_cache=False,
        research=False,
        services=Services(console=SilentConsole()),
    )

    # Wrap each step function with timing
    timings: list[StepTiming] = []
    original_steps = step_registry.ordered_steps()

    # Monkey-patch the runner's STEPS list to wrap each step with timing
    import movie_narrator.pipeline.runner as runner_mod

    original_run = runner_mod.run_pipeline

    step_timings: dict[str, float] = {}

    # We'll intercept step execution by wrapping each step function
    wrapped_steps = {}
    for step_entry in original_steps:
        original_func = step_entry.func

        def make_wrapper(name, func):
            def wrapper(ctx):
                start = time.perf_counter()
                try:
                    result = func(ctx)
                    elapsed = time.perf_counter() - start
                    step_timings[name] = elapsed
                    return result
                except Exception:
                    elapsed = time.perf_counter() - start
                    step_timings[name] = elapsed
                    raise
            return wrapper

        wrapper = make_wrapper(step_entry.name, original_func)
        wrapped_steps[step_entry.name] = wrapper

        # Re-register the wrapped version
        if step_registry.contains(step_entry.name):
            step_registry.unregister(step_entry.name)
        step_registry.register(
            step_entry.name,
            wrapper,
            soft=step_entry.soft,
            status_field=step_entry.status_field,
            consequence=step_entry.consequence,
        )

    # Run the pipeline
    start_total = time.perf_counter()
    try:
        run_pipeline(ctx)
    except Exception as e:
        print(f"  WARNING: pipeline raised: {e}", file=sys.stderr)
    total_duration = time.perf_counter() - start_total

    # Restore original steps
    for step_entry in original_steps:
        if step_registry.contains(step_entry.name):
            step_registry.unregister(step_entry.name)
        step_registry.register(
            step_entry.name,
            step_entry.func,
            soft=step_entry.soft,
            status_field=step_entry.status_field,
            consequence=step_entry.consequence,
        )

    # Build timing list in step order
    for step_entry in original_steps:
        name = step_entry.name
        duration = step_timings.get(name, 0.0)
        timings.append(StepTiming(
            name=name,
            duration_sec=round(duration, 4),
            soft=step_entry.soft,
            status="skipped" if duration == 0.0 else "ok",
        ))

    slowest = max(timings, key=lambda t: t.duration_sec) if timings else None
    fastest = min((t for t in timings if t.duration_sec > 0), key=lambda t: t.duration_sec, default=None)

    from datetime import datetime, timezone
    return BenchmarkResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        python_version=sys.version.split()[0],
        total_duration_sec=round(total_duration, 4),
        steps=timings,
        slowest_step=slowest.name if slowest else None,
        fastest_step=fastest.name if fastest else None,
    )


def _format_summary(result: BenchmarkResult) -> str:
    """Format a human-readable summary."""
    lines = [
        "",
        "=" * 60,
        "  Pipeline Performance Benchmark",
        "=" * 60,
        f"  Python:    {result.python_version}",
        f"  Total:     {result.total_duration_sec:.3f}s",
        f"  Steps:     {len(result.steps)}",
        f"  Slowest:   {result.slowest_step}",
        f"  Fastest:   {result.fastest_step}",
        "=" * 60,
        "",
        f"  {'Step':<25} {'Time':>8}  {'Type':<6} {'Status'}",
        f"  {'-'*25} {'-'*8}  {'-'*6} {'-'*8}",
    ]
    for step in result.steps:
        lines.append(
            f"  {step.name:<25} {step.duration_sec:>7.3f}s  "
            f"{'soft' if step.soft else 'hard':<6} {step.status}"
        )
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Benchmark movie-narrator pipeline")
    parser.add_argument("--output", "-o", type=Path, default=None, help="Output JSON file path")
    parser.add_argument("--runs", "-n", type=int, default=1, help="Number of runs (averaged)")
    args = parser.parse_args()

    if not _ffmpeg_has_mp3():
        print("ERROR: ffmpeg with libmp3lame is required for benchmarking.", file=sys.stderr)
        print("Install ffmpeg or run in CI (ubuntu-latest).", file=sys.stderr)
        return 1

    results: list[BenchmarkResult] = []
    for i in range(args.runs):
        if args.runs > 1:
            print(f"\n--- Run {i+1}/{args.runs} ---", file=sys.stderr)

        with tempfile.TemporaryDirectory(prefix="mn_bench_") as tmpdir:
            result = _run_benchmark(Path(tmpdir))
            results.append(result)
            print(_format_summary(result), file=sys.stderr)

    # If multiple runs, compute averages
    if args.runs > 1 and results:
        avg_total = sum(r.total_duration_sec for r in results) / len(results)
        print(f"\n--- Average over {args.runs} runs: {avg_total:.3f}s ---", file=sys.stderr)

    # Output JSON
    output_data = [asdict(r) for r in results]
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(output_data, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"\nJSON report written to {args.output}", file=sys.stderr)
    else:
        print(json.dumps(output_data, indent=2, ensure_ascii=False))

    return 0


if __name__ == "__main__":
    sys.exit(main())
