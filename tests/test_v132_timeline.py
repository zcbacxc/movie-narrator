# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for v1.3.2 timeline-export hardening (Feature 9).

The out-of-tree ``timeline_export`` plugin (``examples/plugins/
timeline_export/``) reads ``ctx.metadata["timeline_export_backend"]``
with active values ``"jianying"`` and ``"otio"``; anything else is
soft-skipped. The core whitelist now accepts the param so a job.yaml
key survives load + merge and reaches the plugin through metadata.

Decision (recorded): the core ``JobParams.timeline_export_backend``
defaults to ``"none"`` and the default is NOT propagated through
merge — an absent metadata key lets the plugin keep its own default
behaviour, while explicit ``jianying`` / ``otio`` values pass through.

The e2e plugin classes mirror ``smoke_test.py`` (integration-marked):
jianying export through the registered step, and the otio backend's
skip-not-raise soft-disable when ``opentimelineio`` is absent.
"""

import json
import sys
from pathlib import Path

import pytest

from movie_narrator.config import Settings
from movie_narrator.workflow.load import load_job_config
from movie_narrator.workflow.merge import merge_job
from movie_narrator.workflow.schema import JobParams

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "examples" / "plugins" / "timeline_export"


# ── 1. Schema / whitelist / merge (unit level) ────────────


class TestTimelineExportBackendSchema:
    def test_default_is_none(self):
        assert JobParams().timeline_export_backend == "none"

    def test_invalid_backend_rejected(self):
        # v1.4.2 note: "premiere" became a valid backend (FCP7 XML), so an
        # out-of-whitelist example ("aaf") is used here instead.
        with pytest.raises(ValueError, match="timeline_export_backend"):
            JobParams(timeline_export_backend="aaf")

    def test_job_yaml_key_survives_load_and_merge(self, tmp_path):
        """The ROADMAP v1.3 item: the core whitelist accepts the key."""
        job = tmp_path / "job.yaml"
        job.write_text(
            "movie: M\nparams:\n  timeline_export_backend: jianying\n",
            encoding="utf-8",
        )
        cfg = load_job_config(job)
        assert cfg.params.timeline_export_backend == "jianying"
        resolved = merge_job({"movie": "M"}, cfg, Settings())
        assert resolved.params["timeline_export_backend"] == "jianying"

    def test_default_none_not_propagated(self, tmp_path):
        """The "none" default is dropped so the plugin keeps its own default."""
        job = tmp_path / "job.yaml"
        job.write_text("movie: M\n", encoding="utf-8")
        resolved = merge_job({"movie": "M"}, load_job_config(job), Settings())
        assert "timeline_export_backend" not in resolved.params


# ── 2. Plugin end-to-end (integration) ────────────────────


def _import_plugin():
    """Import the example plugin package from its source tree."""
    if str(PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR))
    import movie_narrator_timeline_export.plugin as plugin_mod

    return plugin_mod


@pytest.mark.integration
class TestTimelineExportPluginE2E:
    @pytest.fixture()
    def ctx(self, tmp_path):
        from movie_narrator.models import Context, MatchedClip, TimedSegment

        ctx = Context(
            movie_name="飞驰人生",
            style="热血搞笑",
            duration=60,
            output_dir=str(tmp_path),
            source_video_path=r"C:\mock\source.mp4",
        )
        ctx.matched_clips = [
            MatchedClip(
                segment_index=0,
                text="开场张驰落魄",
                narr_start=0.0,
                narr_end=2.5,
                src_start=10.0,
                src_end=12.5,
                score=0.91,
                source="embedding",
            ),
            MatchedClip(
                segment_index=1,
                text="赛道疾驰高潮",
                narr_start=2.5,
                narr_end=5.0,
                src_start=120.0,
                src_end=123.0,
                score=0.88,
                source="heuristic",
            ),
        ]
        ctx.timed_segments = [
            TimedSegment(text="今天讲一个赛车手的故事。", start=0.0, end=2.5),
            TimedSegment(text="他曾经跌落谷底。", start=2.5, end=5.0),
        ]
        ctx.metadata["render_template"] = {
            "title_card_text": "{movie} · 飞驰人生解说",
            "end_card_text": "感谢观看 {movie}",
        }
        return ctx

    def test_jianying_backend_end_to_end(self, tmp_path, ctx):
        """job.yaml key → metadata → plugin step writes a Jianying draft."""
        plugin = _import_plugin()
        ctx.metadata["timeline_export_backend"] = "jianying"
        out = plugin._timeline_export_step(ctx)
        assert out.step_state.result.value == "success"
        assert "jianying" in (out.step_state.message or "")
        export_path = Path(out.metadata["timeline_export_path"])
        assert export_path.is_dir()
        content = json.loads(
            (export_path / "draft_content.json").read_text(encoding="utf-8")
        )
        assert len(content["draft_timeline"]["videos"][0]["tracks"][0]["segments"]) == 2
        assert content["materials"]["videos"][0]["path"] == r"C:\mock\source.mp4"

    def test_otio_backend_soft_disable_when_missing(self, tmp_path, ctx):
        """opentimelineio absent → step skips with a clear message, never raises."""
        plugin = _import_plugin()
        if plugin._probe_otio():
            pytest.skip("opentimelineio is installed — soft-disable path not applicable")
        ctx.metadata["timeline_export_backend"] = "otio"
        out = plugin._timeline_export_step(ctx)
        assert out.step_state.result.value == "skipped"
        assert "opentimelineio" in (out.step_state.message or "")
        assert "timeline_export_path" not in out.metadata

    def test_none_backend_soft_skips(self, tmp_path, ctx):
        """The core "none" sentinel never activates the plugin step."""
        plugin = _import_plugin()
        ctx.metadata["timeline_export_backend"] = "none"
        out = plugin._timeline_export_step(ctx)
        assert out.step_state.result.value == "skipped"

    def test_no_backend_key_uses_plugin_default(self, tmp_path, ctx):
        """Key absent from metadata → plugin default (jianying) still works."""
        plugin = _import_plugin()
        out = plugin._timeline_export_step(ctx)
        assert out.step_state.result.value == "success"
        assert "jianying" in (out.step_state.message or "")
