"""Concurrent race tests (M2 / P2-8).

8 scenarios × P ∈ {1, 2, 3}. Mocks ``build_context`` / ``run_pipeline``
the same way as ``tests/test_race.py``.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from movie_narrator.models import Context, Services
from movie_narrator.race import (
    CandidateConfig,
    CandidateResult,
    format_race_report,
    generate_candidates,
    run_race,
    save_race_report,
    _select_winner,
)
from movie_narrator.race_executor import (
    CandidateExecutionMetrics,
    CandidateExecutor,
    CandidateOutcome,
    empty_usage_summary,
    resolve_parallelism,
    synthesize_estimated_cost_total_usd,
)
from movie_narrator.utils.console import SilentConsole
from movie_narrator.utils.cost_tracker import CostTracker

PARALLELISMS = [1, 2, 3]


def _cand(label: str, index: int = 0) -> CandidateConfig:
    return CandidateConfig(
        label=label,
        narration_preset="douyin-fast",
        match_topk=5,
        match_topk_reuse_penalty=0.15,
        match_diversity_window=3,
    )


def _ctx_for(output_dir, *, emb=0.8, avg=0.6, video="/fake/out.mp4") -> Context:
    ctx = Context(
        movie_name="test",
        output_dir=str(output_dir),
        source_video_path="/fake/video.mp4",
    )
    ctx.services = Services(console=SilentConsole())
    ctx.cost_tracker = CostTracker()
    ctx.metadata["match_summary"] = {
        "segments": 10,
        "embedding_ratio": emb,
        "score": {"avg": avg},
        "diversity": {"swaps": 2},
    }
    ctx.metadata["duration_metrics"] = {"ratio": 1.0}
    ctx.video_path = video
    return ctx


def _patch_pipeline(build_side_effect, run_side_effect=None):
    if run_side_effect is None:
        run_side_effect = lambda ctx, **kw: ctx
    return (
        patch("movie_narrator.pipeline.runner.build_context", side_effect=build_side_effect),
        patch("movie_narrator.pipeline.runner.run_pipeline", side_effect=run_side_effect),
    )


# ── 1. P=1 sequential-equivalence contract ─────────────────


@pytest.mark.parametrize("P", PARALLELISMS)
def test_output_score_outcome_contract(tmp_path, P):
    """Every successful candidate has score/outcome/metrics/index."""
    cands = generate_candidates(n=3)

    def build(**kwargs):
        return _ctx_for(kwargs["output_dir"])

    b, r = _patch_pipeline(build)
    with b, r:
        results = run_race(
            cands,
            movie="test",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            parallelism=P,
        )

    assert len(results) == 3
    assert all(x.outcome is CandidateOutcome.SUCCESS for x in results)
    assert all(x.error is None for x in results)
    assert all(x.metrics is not None for x in results)
    assert sorted(x.candidate_index for x in results) == [0, 1, 2]
    scores = [x.score for x in results]
    assert scores == sorted(scores, reverse=True)


def test_p1_equivalence_with_explicit_sequential_contract(tmp_path):
    """P=1 preserves sequential semantics including score sort + index tiebreak."""
    cands = generate_candidates(n=3)
    # Force identical scores → tiebreak must be candidate_index ascending.
    def build(**kwargs):
        return _ctx_for(kwargs["output_dir"], emb=0.5, avg=0.5)

    b, r = _patch_pipeline(build)
    with b, r:
        results = run_race(
            cands,
            movie="test",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            parallelism=1,
        )

    assert [x.candidate_index for x in results] == [0, 1, 2]
    assert [x.config.label for x in results] == ["aggressive", "balanced", "conservative"]


# ── 2. Winner ignores failed ───────────────────────────────


@pytest.mark.parametrize("P", PARALLELISMS)
def test_winner_selection_ignores_failed(tmp_path, P):
    cands = generate_candidates(n=3)
    lock = threading.Lock()
    calls = {"n": 0}

    def build(**kwargs):
        with lock:
            calls["n"] += 1
            n = calls["n"]
        if n == 1:
            raise RuntimeError("boom")
        # Remaining two: second call gets higher score
        emb = 0.9 if n == 3 else 0.4
        avg = 0.9 if n == 3 else 0.4
        return _ctx_for(kwargs["output_dir"], emb=emb, avg=avg)

    b, r = _patch_pipeline(build)
    with b, r:
        results = run_race(
            cands,
            movie="test",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            parallelism=P,
        )

    successes = [x for x in results if x.outcome is CandidateOutcome.SUCCESS]
    failures = [x for x in results if x.outcome is CandidateOutcome.FAILED]
    assert len(failures) == 1
    assert len(successes) == 2
    # Winner is first ranked and is a success
    assert results[0].outcome is CandidateOutcome.SUCCESS
    assert results[0].error is None
    winner = _select_winner(results)
    assert winner is not None
    assert winner.outcome is CandidateOutcome.SUCCESS
    # Failed rows sort after successes
    assert results[-1].outcome is CandidateOutcome.FAILED


# ── 3. Cancel queued vs running ────────────────────────────


@pytest.mark.parametrize("P", PARALLELISMS)
def test_cancel_queued_candidates(tmp_path, P):
    """With P=1, cancel indices 1,2 while they sit in the pool queue."""
    cands = generate_candidates(n=3)
    holder: dict = {"exec": None}
    started = threading.Event()

    def build(**kwargs):
        started.set()
        # Cancel the other two while this one is the only running worker.
        if holder["exec"] is not None:
            holder["exec"].cancel(1)
            holder["exec"].cancel(2)
        return _ctx_for(kwargs["output_dir"])

    # Integration path: cancel via a worker-visible shared executor handle.
    real_init = CandidateExecutor.__init__

    def tracking_init(self, max_workers=1):
        real_init(self, max_workers=max_workers)
        holder["exec"] = self

    b, r = _patch_pipeline(build)
    with patch.object(CandidateExecutor, "__init__", tracking_init), b, r:
        results = run_race(
            cands,
            movie="test",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            parallelism=P,
        )

    assert len(results) == 3
    # At least one success; cancelled ones must not be winners
    assert any(x.outcome is CandidateOutcome.SUCCESS for x in results)
    if P == 1:
        # Indices 1 and 2 should typically be cancelled (queued behind 0).
        cancelled = [x for x in results if x.outcome is CandidateOutcome.CANCELLED]
        assert len(cancelled) >= 1
        assert all(x.candidate_index in (1, 2) for x in cancelled)
        assert _select_winner(results) is not None
        assert _select_winner(results).candidate_index == 0


def test_cancel_running_via_controller(tmp_path):
    """RUNNING cancel surfaces as PipelineCancelled → outcome cancelled."""
    cands = generate_candidates(n=2)
    started = {0: threading.Event(), 1: threading.Event()}
    exec_holder: dict = {}

    real_init = CandidateExecutor.__init__

    def tracking_init(self, max_workers=1):
        real_init(self, max_workers=max_workers)
        exec_holder["exec"] = self

    def build(**kwargs):
        return _ctx_for(kwargs["output_dir"])

    def run(ctx, controller=None, **kw):
        # Identify candidate by output dir name.
        idx = 0 if "candidate-1" in str(ctx.output_dir) else 1
        started[idx].set()
        # Peer cancels this running candidate once both have started (P>=2).
        if idx == 0 and started[1].wait(timeout=2):
            exec_holder["exec"].cancel(1)
        if idx == 1 and started[0].wait(timeout=2):
            # Wait until cancel flag is set, then raise as pipeline would.
            deadline = time.time() + 2.0
            while time.time() < deadline:
                if controller is not None and controller.is_cancelled():
                    from movie_narrator.pipeline.errors import PipelineCancelled

                    raise PipelineCancelled()
                time.sleep(0.01)
        return ctx

    b, r = _patch_pipeline(build, run)
    with patch.object(CandidateExecutor, "__init__", tracking_init), b, r:
        results = run_race(
            cands,
            movie="test",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            parallelism=2,
        )

    outcomes = {x.candidate_index: x.outcome for x in results}
    assert outcomes[0] is CandidateOutcome.SUCCESS
    assert outcomes[1] is CandidateOutcome.CANCELLED
    assert results[0].error_type == "PipelineCancelled" or outcomes[0] is CandidateOutcome.SUCCESS
    cancelled = next(x for x in results if x.candidate_index == 1)
    assert cancelled.error == "cancelled"
    assert cancelled.error_type == "PipelineCancelled"


def test_cancel_all_queued_direct_executor():
    """Direct executor API: cancel_all before workers run (P=1, slow first)."""
    started = threading.Event()
    exec_holder: dict = {}
    real_init = CandidateExecutor.__init__

    def tracking_init(self, max_workers=1):
        real_init(self, max_workers=max_workers)
        exec_holder["exec"] = self

    def worker(i, controller):
        if i == 0:
            started.set()
            exec_holder["exec"].cancel_all()
            return "ok-0"
        return f"ok-{i}"

    with patch.object(CandidateExecutor, "__init__", tracking_init):
        ex = CandidateExecutor(max_workers=1)
        slots = ex.run_all(3, worker)

    assert slots[0].value == "ok-0"
    assert slots[0].outcome is CandidateOutcome.SUCCESS
    assert slots[1].outcome is CandidateOutcome.CANCELLED
    assert slots[2].outcome is CandidateOutcome.CANCELLED
    assert slots[1].value is None


# ── 4. Metrics fields present ──────────────────────────────


@pytest.mark.parametrize("P", PARALLELISMS)
def test_metrics_fields_present(tmp_path, P):
    cands = generate_candidates(n=2)

    def build(**kwargs):
        ctx = _ctx_for(kwargs["output_dir"])
        ctx.cost_tracker.record_llm_call(
            step="script",
            model="mock",
            usage={"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150},
        )
        return ctx

    b, r = _patch_pipeline(build)
    with b, r:
        results = run_race(
            cands,
            movie="test",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            parallelism=P,
        )

    for res in results:
        m = res.metrics
        assert m is not None
        assert m.candidate_index == res.candidate_index
        assert m.wall_time_s >= 0.0
        assert isinstance(m.usage_summary, dict)
        assert "llm" in m.usage_summary and "tts" in m.usage_summary
        assert m.outcome is res.outcome
        assert m.estimated_cost_total_usd == synthesize_estimated_cost_total_usd(m.usage_summary)
        # Non-zero LLM usage recorded
        assert m.usage_summary["llm"]["total_tokens"] == 150


def test_build_fail_usage_zero(tmp_path):
    cands = generate_candidates(n=1)

    def build(**kwargs):
        raise RuntimeError("nope")

    b, r = _patch_pipeline(build)
    with b, r:
        results = run_race(
            cands,
            movie="test",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            parallelism=1,
        )

    assert results[0].outcome is CandidateOutcome.FAILED
    assert results[0].metrics is not None
    assert results[0].metrics.estimated_cost_total_usd == 0.0
    assert results[0].metrics.usage_summary["llm"]["total_calls"] == 0


# ── 5. Fail-fast --parallel invalid values ─────────────────


@pytest.mark.parametrize(
    "raw",
    ["0", "-1", "abc", "1.5", "-2", True],
)
def test_resolve_parallelism_fail_fast(raw):
    with pytest.raises(ValueError):
        resolve_parallelism(raw)


@pytest.mark.parametrize(
    "raw,expected",
    [(None, 1), ("", 1), ("  ", 1), ("1", 1), ("3", 3), (2, 2), (" 2 ", 2)],
)
def test_resolve_parallelism_valid(raw, expected):
    assert resolve_parallelism(raw) == expected


def test_cli_parallel_fail_fast():
    import re

    from movie_narrator.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["race", "--movie", "M", "--parallel", "0"])
    assert result.exit_code != 0
    # Typer/rich may insert ANSI SGR sequences between flag characters.
    plain = re.sub(r"\x1b\[[0-9;]*m", "", result.output or "")
    assert "--parallel" in plain or "--parallel" in str(result.exception)

    result2 = runner.invoke(app, ["race", "--movie", "M", "--parallel", "nope"])
    assert result2.exit_code != 0


def test_cli_parallel_registered():
    import typer

    from movie_narrator.cli import app

    registered = {
        flag
        for param in typer.main.get_command(app).commands["race"].params
        if getattr(param, "opts", None)
        for flag in param.opts
    }
    assert "--parallel" in registered


# ── 6. candidate_index is input order, not completion order ─


@pytest.mark.parametrize("P", PARALLELISMS)
def test_candidate_index_is_input_order(tmp_path, P):
    """Slow early candidate must still keep index 0; results list is ranked
    but candidate_index never renumbers to completion order."""
    cands = generate_candidates(n=3)

    def build(**kwargs):
        preset = kwargs.get("narration_preset") or ""
        # First default candidate (aggressive / douyin-fast) is slowest.
        if preset == "douyin-fast":
            time.sleep(0.15)
            emb, avg = 0.3, 0.3  # lowest score
        elif preset == "mainstream-dry":
            time.sleep(0.01)
            emb, avg = 0.9, 0.9
        else:
            time.sleep(0.05)
            emb, avg = 0.6, 0.6
        return _ctx_for(kwargs["output_dir"], emb=emb, avg=avg)

    b, r = _patch_pipeline(build)
    with b, r:
        results = run_race(
            cands,
            movie="test",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            parallelism=P,
        )

    by_index = {x.candidate_index: x for x in results}
    assert set(by_index) == {0, 1, 2}
    # Input order preserved on the objects
    assert by_index[0].config.label == "aggressive"
    assert by_index[1].config.label == "balanced"
    assert by_index[2].config.label == "conservative"
    # Ranked order is by score, but indexes are not renumbered
    assert results[0].config.label == "balanced"
    assert results[0].candidate_index == 1


# ── 7. All-fail → no winner ────────────────────────────────


@pytest.mark.parametrize("P", PARALLELISMS)
def test_all_fail_no_winner(tmp_path, P):
    cands = generate_candidates(n=3)

    def build(**kwargs):
        raise RuntimeError("always fail")

    b, r = _patch_pipeline(build)
    with b, r:
        results = run_race(
            cands,
            movie="test",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            parallelism=P,
            auto_pick=True,
        )

    assert len(results) == 3
    assert all(x.outcome is CandidateOutcome.FAILED for x in results)
    assert _select_winner(results) is None
    report = format_race_report(results)
    assert "Winner:" not in report
    # auto_pick must not promote anything
    assert not list(tmp_path.glob("*.mp4"))


# ── 8. Partial success ─────────────────────────────────────


@pytest.mark.parametrize("P", PARALLELISMS)
def test_partial_success(tmp_path, P):
    cands = generate_candidates(n=3)
    lock = threading.Lock()
    state = {"n": 0}

    def build(**kwargs):
        with lock:
            state["n"] += 1
            n = state["n"]
        if n == 2:
            raise RuntimeError("mid fail")
        return _ctx_for(kwargs["output_dir"])

    b, r = _patch_pipeline(build)
    with b, r:
        results = run_race(
            cands,
            movie="test",
            style="s",
            duration=60,
            voice=None,
            video_format="16:9",
            output_base=tmp_path,
            parallelism=P,
            auto_pick=True,
        )

    successes = [x for x in results if x.outcome is CandidateOutcome.SUCCESS]
    failures = [x for x in results if x.outcome is CandidateOutcome.FAILED]
    assert len(successes) == 2
    assert len(failures) == 1
    assert _select_winner(results) is not None
    # Report JSON includes outcome + metrics
    report_path = tmp_path / "race_report.json"
    save_race_report(results, report_path)
    assert report_path.exists()
    text = report_path.read_text(encoding="utf-8")
    assert "candidate_index" in text
    assert "outcome" in text
    assert "estimated_cost_total_usd" in text


# ── Unit helpers ───────────────────────────────────────────


def test_empty_usage_summary_matches_cost_tracker_schema():
    s = empty_usage_summary()
    assert set(s.keys()) >= {"llm", "tts"}
    assert "estimated_cost_usd" in s["llm"]
    assert "estimated_cost_usd" in s["tts"]


def test_metrics_to_json_dict():
    m = CandidateExecutionMetrics(
        candidate_index=2,
        wall_time_s=1.25,
        usage_summary=empty_usage_summary(),
        outcome=CandidateOutcome.SUCCESS,
    )
    d = m.to_json_dict()
    assert d["candidate_index"] == 2
    assert d["outcome"] == "success"
    assert d["estimated_cost_total_usd"] == 0.0


def test_candidate_result_is_success_flag():
    ok = CandidateResult(
        config=_cand("a"),
        output_dir=Path("/tmp/a"),
        outcome=CandidateOutcome.SUCCESS,
    )
    bad = CandidateResult(
        config=_cand("b"),
        output_dir=Path("/tmp/b"),
        outcome=CandidateOutcome.FAILED,
        error="x",
    )
    assert ok.is_success
    assert not bad.is_success
