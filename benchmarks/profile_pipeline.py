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
    - ffmpeg with mp3 encoder (libmp3lame) — optional; the benchmark
      stubs pydub audio methods when ffmpeg lacks audio codecs
    - CI=1 environment variable (auto-set by this script)

The benchmark does NOT make real LLM or TTS API calls — it uses the
CI mock fallback that produces 4 canned script segments and silent
TTS audio. This isolates pipeline orchestration overhead from network
latency.

v0.5.6+ — ``feature_timings`` field:
    In addition to per-step profiling, the benchmark now records
    timings for v0.5.6's new feature paths:

    - **judge_llm** — time spent in the script self-check judge
      (``judge_script``). In CI mode the judge short-circuits to a
      default pass score (``is_ci()``), so this may report
      ``triggered=false`` when the LLM is unreachable before the judge
      runs.
    - **tmdb_api** — time spent in TMDB API calls (``_tmdb_get``).
      Requires ``MN_TMDB_API_KEY`` and the research step enabled; in
      CI mode this reports ``triggered=false, status=skipped``.
    - **bgm_emotion_selection** — time spent in emotion-weighted BGM
      auto-selection (``select_bgm_by_emotion``). Only triggers when
      ``bgm_request == "default"`` and emotion metadata is present.

    All three gracefully degrade in CI mode (no network, no API keys):
    untriggered features report ``triggered=false, status=skipped`` with
    zero duration. Existing JSON fields are unchanged — only
    ``feature_timings`` is added.

Audio codec stubbing:
    When ffmpeg lacks *all* audio codecs (common with imageio-ffmpeg
    bundles that ship only libx264), the benchmark stubs
    ``AudioSegment.export``, ``.from_mp3``, and ``.from_file`` so the
    TTS and BGM steps can still execute and be timed. The stubs write
    placeholder files and return 1-second silent segments. This is a
    no-op when ffmpeg has full audio support. The render step (which
    calls ffmpeg directly via MoviePy) may still fail in this mode —
    its timing is recorded and marked ``status="failed"``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

# Force CI mode before importing movie_narrator
os.environ["CI"] = "1"

# Short LLM timeout so the retry loop exhausts quickly and falls back
# to CI mock content. Without this, each LLM call waits up to 60s
# (settings.llm_timeout default) before timing out, and with
# script_retries=3 the benchmark would stall for ~3 minutes on a
# dead LLM endpoint. A 1-second timeout reduces this to ~6s.
os.environ.setdefault("MN_LLM_TIMEOUT", "1")

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
class FeatureTiming:
    """Timing record for a v0.5.6 feature path.

    Attributes:
        name: Feature identifier (``judge_llm``, ``tmdb_api``,
            ``bgm_emotion_selection``).
        duration_sec: Cumulative wall-clock time spent in the feature
            across all invocations during the benchmark run.
        triggered: Whether the feature was invoked at least once.
            In CI mode most features stay untriggered (no network /
            no API key), so this is ``False`` and ``status`` is
            ``"skipped"``.
        status: ``"ok"`` if the feature ran without error, ``"failed"``
            if it raised, or ``"skipped"`` if it was never called.
        call_count: Number of times the feature function was invoked.
    """

    name: str
    duration_sec: float
    triggered: bool
    status: str = "skipped"
    call_count: int = 0


@dataclass
class BenchmarkResult:
    timestamp: str
    python_version: str
    total_duration_sec: float
    steps: list[StepTiming] = field(default_factory=list)
    slowest_step: Optional[str] = None
    fastest_step: Optional[str] = None
    # v0.5.6+: per-feature profiling. Backward compatible — existing
    # consumers that ignore this field are unaffected.
    feature_timings: list[FeatureTiming] = field(default_factory=list)


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


def _ffmpeg_has_audio() -> bool:
    """Check if ffmpeg has *any* audio encoder (not just libmp3lame).

    Some minimal builds (e.g. imageio-ffmpeg bundles) ship only libx264
    and have zero audio codec support. The benchmark needs to know this
    to decide whether to stub out pydub's FFmpeg-dependent methods.
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        # Actual encoder entries contain "(codec ...)" e.g.:
        #   "A..... libmp3lame    (codec mp3)"
        # Header lines like "A..... = Audio" do NOT contain "(codec".
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("A") and "(codec" in stripped:
                return True
        return False
    except Exception:
        return False


@contextmanager
def _patch_audio_codecs():
    """Stub pydub AudioSegment when ffmpeg lacks audio codecs.

    In minimal ffmpeg builds that only ship libx264 (no audio
    encoders/decoders at all), ``AudioSegment.export()`` and
    ``.from_mp3()`` / ``.from_file()`` raise because there is no audio
    codec. The benchmark measures pipeline *orchestration* overhead,
    not audio encoding speed, so we stub these methods to write/read
    dummy files:

    - ``export()`` writes a placeholder bytes file so the pipeline's
      file-based API (paths, existence checks) works.
    - ``from_mp3()`` / ``from_file()`` return a 1-second silent
      ``AudioSegment`` so ``len(audio)`` and concatenation work.

    This is a **no-op** when ffmpeg has full audio support — the real
    methods are used unchanged.
    """
    from pydub import AudioSegment

    if _ffmpeg_has_audio():
        yield
        return

    _stub_marker = b"PYDUB_STUB_AUDIO"
    _stub_duration_ms = 1000  # 1 second of silence per segment

    original_export = AudioSegment.export
    original_from_mp3 = AudioSegment.from_mp3
    original_from_file = AudioSegment.from_file

    def _stub_export(self, out_f, format="mp3", **kwargs):
        """Write a placeholder file; the content is irrelevant for timing."""
        if isinstance(out_f, (str, os.PathLike)):
            p = Path(out_f)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(_stub_marker)
        elif hasattr(out_f, "write"):
            out_f.write(_stub_marker)
        return out_f

    def _stub_from_mp3(file, *args, **kwargs):
        """Return a silent segment; no ffmpeg decoder needed."""
        return AudioSegment.silent(duration=_stub_duration_ms)

    def _stub_from_file(file, *args, **kwargs):
        """Return a silent segment; no ffmpeg decoder needed."""
        return AudioSegment.silent(duration=_stub_duration_ms)

    AudioSegment.export = _stub_export
    AudioSegment.from_mp3 = _stub_from_mp3
    AudioSegment.from_file = _stub_from_file

    try:
        yield
    finally:
        AudioSegment.export = original_export
        AudioSegment.from_mp3 = original_from_mp3
        AudioSegment.from_file = original_from_file


# ── Feature-level profiling helpers (v0.5.6+) ──────────────


def _make_feature_tracker() -> dict[str, dict]:
    """Create the initial feature timing tracking dict.

    Each feature maps to a mutable dict with cumulative duration,
    triggered flag, status, and call count.
    """
    return {
        "judge_llm": {
            "duration_sec": 0.0,
            "triggered": False,
            "status": "skipped",
            "call_count": 0,
        },
        "tmdb_api": {
            "duration_sec": 0.0,
            "triggered": False,
            "status": "skipped",
            "call_count": 0,
        },
        "bgm_emotion_selection": {
            "duration_sec": 0.0,
            "triggered": False,
            "status": "skipped",
            "call_count": 0,
        },
    }


def _install_feature_profilers(tracker: dict[str, dict]) -> dict:
    """Monkey-patch v0.5.6 feature functions with timing wrappers.

    Returns a dict of ``(module, attr, original_func)`` tuples so the
    caller can restore the originals after the benchmark run.

    The wrappers are designed to be zero-overhead when the feature is
    never called: the tracker dict keeps ``triggered=False`` and
    ``status="skipped"`` until the first invocation.
    """
    import movie_narrator.pipeline.script as script_mod
    import movie_narrator.providers.tmdb as tmdb_mod
    import movie_narrator.pipeline.bgm as bgm_mod

    originals: dict = {}

    # ── judge_script ─────────────────────────────────────
    # Called inside generate_script's retry loop. In CI mode the LLM
    # is usually unreachable, so the judge may never execute.
    original_judge = script_mod.judge_script

    def judge_wrapper(segments, movie_name, llm):
        tracker["judge_llm"]["triggered"] = True
        tracker["judge_llm"]["call_count"] += 1
        start = time.perf_counter()
        try:
            result = original_judge(segments, movie_name, llm)
            elapsed = time.perf_counter() - start
            tracker["judge_llm"]["duration_sec"] += elapsed
            tracker["judge_llm"]["status"] = "ok"
            return result
        except Exception:
            elapsed = time.perf_counter() - start
            tracker["judge_llm"]["duration_sec"] += elapsed
            tracker["judge_llm"]["status"] = "failed"
            raise

    script_mod.judge_script = judge_wrapper
    originals["judge_llm"] = (script_mod, "judge_script", original_judge)

    # ── TMDB _tmdb_get ───────────────────────────────────
    # Called by _search_movie and _get_movie_details. Only triggers
    # when the research step runs and TMDB enrichment is attempted.
    original_tmdb_get = tmdb_mod._tmdb_get

    def tmdb_wrapper(base_url, path, api_key, params, timeout=10):
        tracker["tmdb_api"]["triggered"] = True
        tracker["tmdb_api"]["call_count"] += 1
        start = time.perf_counter()
        try:
            result = original_tmdb_get(base_url, path, api_key, params, timeout)
            elapsed = time.perf_counter() - start
            tracker["tmdb_api"]["duration_sec"] += elapsed
            tracker["tmdb_api"]["status"] = "ok"
            return result
        except Exception:
            elapsed = time.perf_counter() - start
            tracker["tmdb_api"]["duration_sec"] += elapsed
            tracker["tmdb_api"]["status"] = "failed"
            raise

    tmdb_mod._tmdb_get = tmdb_wrapper
    originals["tmdb_api"] = (tmdb_mod, "_tmdb_get", original_tmdb_get)

    # ── BGM select_bgm_by_emotion ────────────────────────
    # Called inside mix_bgm when bgm_request == "default". In the
    # benchmark, bgm is None and not requested, so this stays
    # untriggered.
    original_select_bgm = bgm_mod.select_bgm_by_emotion

    def bgm_wrapper(ctx):
        tracker["bgm_emotion_selection"]["triggered"] = True
        tracker["bgm_emotion_selection"]["call_count"] += 1
        start = time.perf_counter()
        try:
            result = original_select_bgm(ctx)
            elapsed = time.perf_counter() - start
            tracker["bgm_emotion_selection"]["duration_sec"] += elapsed
            tracker["bgm_emotion_selection"]["status"] = "ok"
            return result
        except Exception:
            elapsed = time.perf_counter() - start
            tracker["bgm_emotion_selection"]["duration_sec"] += elapsed
            tracker["bgm_emotion_selection"]["status"] = "failed"
            raise

    bgm_mod.select_bgm_by_emotion = bgm_wrapper
    originals["bgm_emotion_selection"] = (bgm_mod, "select_bgm_by_emotion", original_select_bgm)

    return originals


def _uninstall_feature_profilers(originals: dict) -> None:
    """Restore the original feature functions after benchmarking."""
    for _feature, (module, attr, original) in originals.items():
        setattr(module, attr, original)


def _build_feature_timings(tracker: dict[str, dict]) -> list[FeatureTiming]:
    """Convert the raw tracker dict into a list of FeatureTiming dataclasses."""
    result: list[FeatureTiming] = []
    for name in ("judge_llm", "tmdb_api", "bgm_emotion_selection"):
        data = tracker[name]
        result.append(FeatureTiming(
            name=name,
            duration_sec=round(data["duration_sec"], 4),
            triggered=data["triggered"],
            status=data["status"],
            call_count=data["call_count"],
        ))
    return result


def _run_benchmark(output_dir: Path) -> BenchmarkResult:
    """Run one pipeline execution and collect per-step and per-feature timings."""
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

    # ── Feature-level profiling (v0.5.6+) ──────────────────
    feature_tracker = _make_feature_tracker()
    feature_originals = _install_feature_profilers(feature_tracker)

    # Wrap each step function with timing
    timings: list[StepTiming] = []

    # Retrieve StepEntry objects (not bare functions) so we can access
    # .func, .name, .soft, .status_field, .consequence for re-registration.
    ordered_names = step_registry.ordered_names()
    original_steps = [step_registry.get(name) for name in ordered_names]
    # Filter out any None (shouldn't happen, but be safe).
    original_steps = [e for e in original_steps if e is not None]

    # Monkey-patch the runner's STEPS list to wrap each step with timing
    import movie_narrator.pipeline.runner as runner_mod

    step_timings: dict[str, float] = {}
    failed_steps: set[str] = set()

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
                    failed_steps.add(name)
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

    # CRITICAL: run_pipeline iterates over the module-level STEPS constant,
    # which was captured at import time. Re-registering in the registry
    # alone is not enough — we must also refresh STEPS so the wrapped
    # functions are actually called.
    original_steps_list = runner_mod.STEPS
    runner_mod.STEPS = step_registry.ordered_steps()

    # Run the pipeline (with audio codec stubbing if ffmpeg lacks audio support)
    start_total = time.perf_counter()
    with _patch_audio_codecs():
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
    # Restore the module-level STEPS constant to the original functions
    runner_mod.STEPS = original_steps_list

    # Restore original feature functions
    _uninstall_feature_profilers(feature_originals)

    # Build timing list in step order
    for step_entry in original_steps:
        name = step_entry.name
        duration = step_timings.get(name, 0.0)
        if duration == 0.0:
            status = "skipped"
        elif name in failed_steps:
            status = "failed"
        else:
            status = "ok"
        timings.append(StepTiming(
            name=name,
            duration_sec=round(duration, 4),
            soft=step_entry.soft,
            status=status,
        ))

    slowest = max(timings, key=lambda t: t.duration_sec) if timings else None
    fastest = min((t for t in timings if t.duration_sec > 0), key=lambda t: t.duration_sec, default=None)

    # Build feature timing list
    feature_timings = _build_feature_timings(feature_tracker)

    from datetime import datetime, timezone
    return BenchmarkResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        python_version=sys.version.split()[0],
        total_duration_sec=round(total_duration, 4),
        steps=timings,
        slowest_step=slowest.name if slowest else None,
        fastest_step=fastest.name if fastest else None,
        feature_timings=feature_timings,
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

    # Feature timings section (v0.5.6+)
    if result.feature_timings:
        lines.append("")
        lines.append("  " + "-" * 56)
        lines.append("  v0.5.6 Feature Timings")
        lines.append("  " + "-" * 56)
        lines.append(
            f"  {'Feature':<25} {'Time':>8}  {'Trig':<5} {'Calls':>5} {'Status'}"
        )
        lines.append(
            f"  {'-'*25} {'-'*8}  {'-'*5} {'-'*5} {'-'*8}"
        )
        for ft in result.feature_timings:
            trig = "yes" if ft.triggered else "no"
            lines.append(
                f"  {ft.name:<25} {ft.duration_sec:>7.4f}s  "
                f"{trig:<5} {ft.call_count:>5} {ft.status}"
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
        # The pipeline's _export_robust falls back to WAV when MP3 encoding
        # is unavailable, so the benchmark can still run — only audio
        # export format differs. Warn rather than abort so the benchmark
        # works in minimal ffmpeg builds (e.g. imageio-ffmpeg bundles).
        print("WARNING: ffmpeg lacks libmp3lame — audio will use WAV fallback.", file=sys.stderr)
        print("Install a full ffmpeg build for production-accurate MP3 timings.", file=sys.stderr)

    if not _ffmpeg_has_audio():
        # ffmpeg has NO audio codecs at all (not even WAV/PCM). The
        # benchmark will stub out pydub's export/from_file methods so
        # the TTS and BGM steps can still run and be timed. The render
        # step (which calls ffmpeg directly via MoviePy) may still fail
        # — its timing is recorded but marked as "failed".
        print("WARNING: ffmpeg has no audio codecs — pydub methods will be stubbed.", file=sys.stderr)
        print("Step timings for TTS/BGM are accurate; render_video may fail.", file=sys.stderr)

    results: list[BenchmarkResult] = []
    for i in range(args.runs):
        if args.runs > 1:
            print(f"\n--- Run {i+1}/{args.runs} ---", file=sys.stderr)

        # ignore_cleanup_errors: on Windows, the pipeline may leave
        # locked files (e.g. partially-written audio) that prevent
        # TemporaryDirectory.__exit__ from deleting the temp dir.
        # Ignoring cleanup errors lets the benchmark still emit its
        # JSON report instead of crashing on dir teardown.
        with tempfile.TemporaryDirectory(prefix="mn_bench_", ignore_cleanup_errors=True) as tmpdir:
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
