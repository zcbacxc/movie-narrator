# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""M5 E2 dual-baseline harness — unit tests with mocks/fakes.

No real video, LLM, TTS, or ffmpeg. Integration-style E2 that runs the
real pipeline is marked ``@pytest.mark.integration`` and skipped when
heavy.

Covers:

- ``reconstruct_context_from_snapshot`` is not deepcopy (fresh
  Context / CostTracker / step_state / status; data value-copied).
- ``apply_disjoint_diffs`` resource-level merge + dual-write conflict.
- ``normalize_deliverable_identity`` ignores generated_at / mtime.
- Directory identity via sorted (rel, size, sha256) manifest hash.
- Dual-baseline comparison: disjoint writes pass; undeclared writes
  fail; dual-write conflicts fail.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from movie_narrator.models import Context, Services, StepState
from movie_narrator.pipeline.e2_harness import (
    E2EquivalenceResult,
    apply_disjoint_diffs,
    apply_resource_diff,
    compare_parallel_vs_sequential,
    diff_resources,
    directory_identity,
    file_digest,
    normalize_deliverable_identity,
    normalize_diff_for_equivalence,
    project_resources,
    reconstruct_context_from_snapshot,
    snapshot_context_data,
    snapshot_resource_values,
    strip_r1_control,
    undeclared_resources,
)
from movie_narrator.utils.console import SilentConsole
from movie_narrator.utils.cost_tracker import CostTracker


def _s0(tmp_path: Path, **meta) -> Context:
    ctx = Context(
        movie_name="E2-Mock",
        style="热血搞笑",
        duration=30,
        output_dir=str(tmp_path / "s0"),
        metadata=dict(meta),
    )
    return ctx


# ── reconstruct_context_from_snapshot ───────────────────────


class TestReconstruct:
    def test_fresh_control_objects_not_deepcopy(self, tmp_path):
        s0 = _s0(tmp_path)
        s0.cost_tracker = CostTracker()
        s0.status.research = "success"
        s0.step_state.message = "hello"
        s0.cost_tracker.record_llm_call("script", "m", {"total_tokens": 3})

        snap = snapshot_context_data(s0)
        assert "status" not in snap
        assert "services" not in snap
        assert "cost_tracker" not in snap
        assert "step_state" not in snap

        branch = reconstruct_context_from_snapshot(snap)
        assert branch is not s0
        assert branch.status is not s0.status
        assert branch.step_state is not s0.step_state
        assert branch.cost_tracker is not s0.cost_tracker
        assert isinstance(branch.cost_tracker, CostTracker)
        assert branch.cost_tracker.summary()["llm"]["total_calls"] == 0
        assert branch.status.research == "disabled"

    def test_data_fields_are_value_copied(self, tmp_path):
        s0 = _s0(tmp_path)
        s0.metadata["movie_card"] = {"title": "A"}
        snap = snapshot_context_data(s0)
        branch = reconstruct_context_from_snapshot(snap)
        branch.metadata["movie_card"]["title"] = "B"
        assert s0.metadata["movie_card"]["title"] == "A"

    def test_independent_output_root(self, tmp_path):
        s0 = _s0(tmp_path)
        snap = snapshot_context_data(s0)
        branch = reconstruct_context_from_snapshot(snap, output_dir=str(tmp_path / "branch_a"))
        assert branch.output_dir.endswith("branch_a")
        assert branch.output_dir != s0.output_dir

    def test_independent_services_fixture(self, tmp_path):
        s0 = _s0(tmp_path)
        snap = snapshot_context_data(s0)
        svc = Services(console=SilentConsole())
        a = reconstruct_context_from_snapshot(snap, services=svc)
        b = reconstruct_context_from_snapshot(snap)
        assert a.services is svc
        assert b.services is not svc
        assert a.services is not b.services

    def test_movie_name_preserved(self, tmp_path):
        s0 = _s0(tmp_path, lang="zh")
        branch = reconstruct_context_from_snapshot(snapshot_context_data(s0))
        assert branch.movie_name == "E2-Mock"
        assert branch.metadata.get("lang") == "zh"


# ── projection / diff / strip ───────────────────────────────


class TestDiffPrimitives:
    def test_diff_detects_changed_metadata(self, tmp_path):
        s0 = _s0(tmp_path)
        before = snapshot_resource_values(s0)
        s0.metadata["script_source"] = "llm"
        after = snapshot_resource_values(s0)
        diff = diff_resources(before, after)
        assert "meta.script_source" in diff
        assert diff["meta.script_source"] == "llm"

    def test_project_resources_reads_and_writes(self, tmp_path):
        s0 = _s0(tmp_path)
        s0.metadata["movie_card"] = {"title": "X"}
        projected = project_resources(s0, ["ctx.movie_name", "meta.movie_card", "external.llm"])
        assert projected["ctx.movie_name"] == "E2-Mock"
        assert projected["meta.movie_card"] == {"title": "X"}
        assert "external.llm" not in projected

    def test_strip_r1_control_and_observational(self, tmp_path):
        diff = {
            "ctx.status": "x",
            "ctx.step_state": "y",
            "meta.usage": {"llm": {}},
            "meta.generated_at": "2026-01-01T00:00:00Z",
            "meta.script_source": "llm",
        }
        stripped = strip_r1_control(diff)
        assert set(stripped) == {"meta.script_source"}

    def test_undeclared_resources(self):
        declared = {"meta.script_source", "ctx.segments"}
        branch = {"meta.script_source": "llm", "meta.secret": 1}
        assert undeclared_resources(branch, declared) == {"meta.secret"}

    def test_normalize_diff_basename_paths(self, tmp_path):
        diff = {
            "artifact.narration_audio": str(tmp_path / "a" / "narration.mp3"),
            "ctx.script_md_path": str(tmp_path / "b" / "script.md"),
            "meta.script_source": "llm",
        }
        norm = normalize_diff_for_equivalence(diff)
        assert norm["artifact.narration_audio"] == "narration.mp3"
        assert norm["ctx.script_md_path"] == "script.md"
        assert norm["meta.script_source"] == "llm"


# ── apply_disjoint_diffs ────────────────────────────────────


class TestApplyDisjointDiffs:
    def test_disjoint_metadata_writes_merge(self, tmp_path):
        s0 = _s0(tmp_path)
        base = reconstruct_context_from_snapshot(snapshot_context_data(s0))
        result = apply_disjoint_diffs(
            base,
            {"meta.script_source": "llm"},
            {"meta.align_backend_used": "whisperx"},
        )
        assert result.conflicts == ()
        assert result.context.metadata.get("script_source") == "llm"
        assert result.context.metadata.get("align_backend_used") == "whisperx"

    def test_same_resource_dual_write_is_conflict(self, tmp_path):
        s0 = _s0(tmp_path)
        base = reconstruct_context_from_snapshot(snapshot_context_data(s0))
        result = apply_disjoint_diffs(
            base,
            {"meta.script_source": "llm"},
            {"meta.script_source": "fallback"},
        )
        assert result.conflicts == ("meta.script_source",)
        # Conflicting key is NOT applied.
        assert result.context.metadata.get("script_source") is None

    def test_ctx_and_meta_are_distinct_resources(self, tmp_path):
        s0 = _s0(tmp_path)
        base = reconstruct_context_from_snapshot(snapshot_context_data(s0))
        result = apply_disjoint_diffs(
            base,
            {"ctx.duration": 45},
            {"meta.duration_metrics": {"total": 45.0}},
        )
        assert result.conflicts == ()
        assert result.context.duration == 45
        assert result.context.metadata["duration_metrics"] == {"total": 45.0}

    def test_apply_resource_diff_artifact_maps_to_path_field(self, tmp_path):
        s0 = _s0(tmp_path)
        ctx = reconstruct_context_from_snapshot(snapshot_context_data(s0))
        apply_resource_diff(ctx, {"artifact.narration_audio": "/x/narration.mp3"})
        assert ctx.audio_path == "/x/narration.mp3"


# ── identity ────────────────────────────────────────────────


class TestIdentity:
    def test_file_digest_stable(self, tmp_path):
        p = tmp_path / "a.bin"
        p.write_bytes(b"hello")
        q = tmp_path / "b.bin"
        q.write_bytes(b"hello")
        assert file_digest(p) == file_digest(q)
        p.write_bytes(b"hello2")
        assert file_digest(p) != file_digest(q)

    def test_directory_identity_sorted_manifest(self, tmp_path):
        d = tmp_path / "clips"
        d.mkdir()
        (d / "b.mp4").write_bytes(b"bbb")
        (d / "a.mp4").write_bytes(b"aaa")
        ident1 = directory_identity(d)
        # Re-create in reverse write order — identity must not depend on mtime/order.
        d2 = tmp_path / "clips2"
        d2.mkdir()
        (d2 / "a.mp4").write_bytes(b"aaa")
        (d2 / "b.mp4").write_bytes(b"bbb")
        ident2 = directory_identity(d2)
        assert ident1["hash"] == ident2["hash"]
        assert ident1["entries"] == ident2["entries"]

    def test_directory_identity_changes_with_content(self, tmp_path):
        d = tmp_path / "clips"
        d.mkdir()
        (d / "a.mp4").write_bytes(b"aaa")
        before = directory_identity(d)["hash"]
        (d / "a.mp4").write_bytes(b"zzz")
        after = directory_identity(d)["hash"]
        assert before != after

    def test_normalize_deliverable_identity_ignores_generated_at(self):
        a = normalize_deliverable_identity(
            media_facts={
                "duration": 12.5,
                "codec": "h264",
                "width": 1920,
                "height": 1080,
                "frame_count": 300,
                "generated_at": "2026-01-01T00:00:00Z",
            }
        )
        b = normalize_deliverable_identity(
            media_facts={
                "duration": 12.5,
                "codec": "h264",
                "width": 1920,
                "height": 1080,
                "frame_count": 300,
                "generated_at": "2026-12-31T23:59:59Z",
            }
        )
        assert a == b
        assert "generated_at" not in a
        assert a["resolution"] == "1920x1080"
        assert a["kind"] == "media_identity"

    def test_normalize_merges_manifest_qa_and_probe(self):
        ident = normalize_deliverable_identity(
            manifest={
                "generated_at": "2026-01-01T00:00:00Z",
                "qa": {"video_qa": {"codec": "h264", "duration": 10.0}},
            },
            qa_facts={"metrics": {"width": 1280, "height": 720}},
            media_facts={"frame_count": 240, "subtitle": "embedded"},
        )
        assert ident["codec"] == "h264"
        assert ident["duration"] == 10.0
        assert ident["width"] == 1280
        assert ident["frame_count"] == 240
        assert ident["subtitle"] == "embedded"
        assert ident["resolution"] == "1280x720"

    def test_mtime_not_in_identity(self):
        ident = normalize_deliverable_identity(media_facts={"duration": 1.0, "mtime": 123})
        assert "mtime" not in ident


# ── dual-baseline comparison (mocks) ────────────────────────


def _write_meta(key: str, value):
    def _step(ctx: Context) -> Context:
        ctx.metadata[key] = value
        return ctx

    return _step


class TestDualBaseline:
    def test_disjoint_writes_are_equivalent(self, tmp_path):
        s0 = _s0(tmp_path)
        result = compare_parallel_vs_sequential(
            s0,
            execute_a=_write_meta("script_source", "llm"),
            execute_b=_write_meta("align_backend_used", "whisperx"),
            writes_a=["meta.script_source"],
            writes_b=["meta.align_backend_used"],
        )
        assert isinstance(result, E2EquivalenceResult)
        assert result.undeclared_a == ()
        assert result.undeclared_b == ()
        assert result.merge_conflicts == ()
        assert result.equivalent is True
        assert result.sequential_diff == result.merged_diff
        assert result.sequential_diff["meta.script_source"] == "llm"
        assert result.sequential_diff["meta.align_backend_used"] == "whisperx"

    def test_dual_write_conflict_fails(self, tmp_path):
        s0 = _s0(tmp_path)
        result = compare_parallel_vs_sequential(
            s0,
            execute_a=_write_meta("script_source", "llm"),
            execute_b=_write_meta("script_source", "fallback"),
            writes_a=["meta.script_source"],
            writes_b=["meta.script_source"],
        )
        assert result.merge_conflicts == ("meta.script_source",)
        assert result.equivalent is False
        assert any("dual-write" in n for n in result.notes)

    def test_undeclared_write_fails(self, tmp_path):
        s0 = _s0(tmp_path)

        def sneaky(ctx: Context) -> Context:
            ctx.metadata["script_source"] = "llm"
            ctx.metadata["secret_side_effect"] = 1
            return ctx

        result = compare_parallel_vs_sequential(
            s0,
            execute_a=sneaky,
            execute_b=_write_meta("align_backend_used", "whisperx"),
            writes_a=["meta.script_source"],  # secret undeclared
            writes_b=["meta.align_backend_used"],
        )
        assert result.undeclared_a == ("meta.secret_side_effect",)
        assert result.equivalent is False
        assert any("undeclared" in n for n in result.notes)

    def test_observational_usage_not_undeclared(self, tmp_path):
        s0 = _s0(tmp_path)

        def with_usage(ctx: Context) -> Context:
            ctx.metadata["script_source"] = "llm"
            ctx.metadata["usage"] = {"llm": {"total_calls": 1}}
            return ctx

        result = compare_parallel_vs_sequential(
            s0,
            execute_a=with_usage,
            execute_b=_write_meta("align_backend_used", "whisperx"),
            writes_a=["meta.script_source"],
            writes_b=["meta.align_backend_used"],
        )
        assert result.undeclared_a == ()
        assert "meta.usage" not in result.branch_diff_a
        assert result.equivalent is True

    def test_status_and_step_state_not_in_diff(self, tmp_path):
        s0 = _s0(tmp_path)

        def touch_status(ctx: Context) -> Context:
            ctx.status.research = "success"
            ctx.step_state = StepState(message="ok")
            ctx.metadata["script_source"] = "llm"
            return ctx

        result = compare_parallel_vs_sequential(
            s0,
            execute_a=touch_status,
            execute_b=_write_meta("align_backend_used", "whisperx"),
            writes_a=["meta.script_source"],
            writes_b=["meta.align_backend_used"],
        )
        assert "ctx.status" not in result.branch_diff_a
        assert "ctx.step_state" not in result.branch_diff_a
        assert result.equivalent is True

    def test_branches_do_not_share_mutation(self, tmp_path):
        s0 = _s0(tmp_path)
        s0.metadata["shared"] = {"n": 0}

        def bump(ctx: Context) -> Context:
            ctx.metadata["shared"]["n"] += 1
            ctx.metadata["script_source"] = "llm"
            return ctx

        result = compare_parallel_vs_sequential(
            s0,
            execute_a=bump,
            execute_b=_write_meta("align_backend_used", "x"),
            writes_a=["meta.script_source", "meta.shared"],
            writes_b=["meta.align_backend_used"],
        )
        # S0 itself must be untouched.
        assert s0.metadata["shared"]["n"] == 0
        assert result.equivalent is True

    def test_different_run_roots_normalized_to_basename(self, tmp_path):
        s0 = _s0(tmp_path)

        def write_audio(ctx: Context) -> Context:
            ctx.audio_path = str(Path(ctx.output_dir) / "narration.mp3")
            return ctx

        result = compare_parallel_vs_sequential(
            s0,
            execute_a=write_audio,
            execute_b=_write_meta("align_backend_used", "x"),
            writes_a=["artifact.narration_audio"],
            writes_b=["meta.align_backend_used"],
        )
        # Full paths differ across roots; identity compares basename.
        assert result.equivalent is True
        assert result.sequential_diff.get("artifact.narration_audio") == "narration.mp3"
        assert result.merged_diff.get("artifact.narration_audio") == "narration.mp3"


# ── integration-style (skipped when heavy) ──────────────────


@pytest.mark.integration
def test_e2_integration_placeholder_skipped():
    """Real-pipeline E2 is deferred; unit mocks above cover the harness."""
    pytest.skip("real dual-baseline E2 requires deterministic provider fixtures")
