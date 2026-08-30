# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for v1.4.2 Feature 1 — Premiere XML timeline adapter.

The out-of-tree ``timeline_export`` plugin (``examples/plugins/
timeline_export/``) grows a ``premiere`` backend that serialises the
unified ``Timeline`` to a Final Cut Pro 7 XML (``xmeml``) file — the
interchange format Adobe Premiere Pro imports natively via
``File > Import``. Stdlib-only (``xml.etree.ElementTree``), no optional
dependency, ``.xml`` output.

Covered here:

- whitelist: the core ``JobParams.timeline_export_backend`` validator
  accepts ``"premiere"`` and the value survives load + merge (only the
  frozenset changed — load/merge pass the key through since v1.3.2);
- XML structure (unit): parsed with ElementTree — sequence/rate,
  clipitem count, start/end/in/out frames, file refs (first defines,
  later ones id-only), text generatoritems, narration audio track;
- dispatch (integration): ``timeline_export_backend = "premiere"``
  flows through metadata into the plugin step and writes the ``.xml``.
"""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from movie_narrator.config import Settings
from movie_narrator.workflow.load import load_job_config
from movie_narrator.workflow.merge import merge_job
from movie_narrator.workflow.schema import JobParams

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "examples" / "plugins" / "timeline_export"


# ── 1. Whitelist / load / merge (unit level) ──────────────


class TestPremiereBackendWhitelist:
    def test_premiere_accepted_by_job_params(self):
        assert JobParams(timeline_export_backend="premiere").timeline_export_backend == "premiere"

    def test_invalid_backend_still_rejected(self):
        with pytest.raises(ValueError, match="timeline_export_backend"):
            JobParams(timeline_export_backend="aaf")

    def test_premiere_survives_load_and_merge(self, tmp_path):
        """job.yaml `premiere` → load → merge → resolved params intact."""
        job = tmp_path / "job.yaml"
        job.write_text(
            "movie: M\nparams:\n  timeline_export_backend: premiere\n",
            encoding="utf-8",
        )
        cfg = load_job_config(job)
        assert cfg.params.timeline_export_backend == "premiere"
        resolved = merge_job({"movie": "M"}, cfg, Settings())
        assert resolved.params["timeline_export_backend"] == "premiere"


# ── 2. FCP7 XML structure (unit, no Context needed) ───────


def _import_plugin():
    """Import the example plugin package from its source tree."""
    if str(PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR))
    import movie_narrator_timeline_export.plugin as plugin_mod

    return plugin_mod


def _make_timeline(plugin_mod):
    """Build a two-clip timeline with overlays and a narration stem."""
    tl = plugin_mod.Timeline(
        movie_name="demo",
        source_video_path=r"C:\mock\source.mp4",
        fps=30,
        narration_audio_path=r"C:\mock\narration.mp3",
        clips=[
            plugin_mod.TimelineClip(
                label="clip a",
                src_start=10.0,
                src_end=12.5,
                timeline_start=0.0,
                duration=2.5,
            ),
            plugin_mod.TimelineClip(
                label="clip b",
                src_start=120.0,
                src_end=123.0,
                timeline_start=2.5,
                duration=3.0,
            ),
        ],
        text_overlays=[
            plugin_mod.TextOverlay(text="hello", start=0.0, end=2.5, kind="subtitle"),
            plugin_mod.TextOverlay(text="the end", start=0.0, end=0.1, kind="end"),
        ],
    )
    return tl


class TestPremiereXmlStructure:
    @pytest.fixture()
    def xml_path(self, tmp_path):
        plugin_mod = _import_plugin()
        return plugin_mod._write_premiere(_make_timeline(plugin_mod), tmp_path / "out")

    def test_writes_xml_file(self, xml_path):
        assert xml_path.suffix == ".xml"
        assert xml_path.is_file()
        assert xml_path.name == "demo.xml"

    def test_parses_and_has_sequence_rate(self, xml_path):
        root = ET.parse(xml_path).getroot()
        assert root.tag == "xmeml"
        assert root.get("version") == "4"
        seq = root.find("project/children/sequence")
        assert seq is not None
        assert seq.findtext("rate/timebase") == "30"  # from tl.fps
        assert seq.findtext("rate/ntsc") == "FALSE"
        fmt = seq.find("media/video/format/samplecharacteristics")
        assert fmt is not None
        assert fmt.findtext("width") == "1920"
        assert fmt.findtext("height") == "1080"

    def test_clipitems_start_end_in_out_frames(self, xml_path):
        root = ET.parse(xml_path).getroot()
        seq = root.find("project/children/sequence")
        items = seq.findall("media/video/track/clipitem")
        assert len(items) == 2
        first, second = items
        # Timeline start/end in frames (30 fps).
        assert first.findtext("start") == "0" and first.findtext("end") == "75"
        # Source in/out in frames (10s..12.5s @ 30fps).
        assert first.findtext("in") == "300" and first.findtext("out") == "375"
        assert second.findtext("start") == "75" and second.findtext("end") == "165"

    def test_file_refs_first_defines_rest_reference(self, xml_path):
        root = ET.parse(xml_path).getroot()
        items = root.findall("project/children/sequence/media/video/track/clipitem")
        first_file = items[0].find("file")
        assert first_file is not None
        assert first_file.get("id") == "file-1"
        assert first_file.findtext("pathurl") == Path(r"C:\mock\source.mp4").resolve().as_uri()
        # Subsequent references are id-only (no repeated definition).
        ref = items[1].find("file")
        assert ref is not None
        assert ref.get("id") == "file-1"
        assert len(ref) == 0

    def test_text_overlays_become_generatoritems(self, xml_path):
        root = ET.parse(xml_path).getroot()
        gens = root.findall(
            "project/children/sequence/media/video/track/generatoritem"
        )
        assert len(gens) == 2
        assert gens[0].findtext("name") == "hello"
        effect = gens[0].find("effect")
        assert effect is not None
        assert effect.findtext("effectid") == "Text"
        assert effect.findtext("effecttype") == "generator"

    def test_narration_audio_track(self, xml_path):
        root = ET.parse(xml_path).getroot()
        audio_item = root.find(
            "project/children/sequence/media/audio/track/clipitem"
        )
        assert audio_item is not None
        assert audio_item.findtext("start") == "0"
        # Audio file defined with a pathurl; video media absent.
        file_el = audio_item.find("file")
        assert file_el is not None
        assert file_el.findtext("pathurl") == Path(r"C:\mock\narration.mp3").resolve().as_uri()
        assert file_el.find("media/video") is None
        assert file_el.find("media/audio") is not None

    def test_no_narration_omits_audio_track(self, tmp_path):
        plugin_mod = _import_plugin()
        tl = _make_timeline(plugin_mod)
        tl.narration_audio_path = None
        out = plugin_mod._write_premiere(tl, tmp_path / "out2")
        root = ET.parse(out).getroot()
        assert root.find("project/children/sequence/media/audio") is None

    def test_default_fps_falls_back_to_24(self, tmp_path):
        plugin_mod = _import_plugin()
        tl = _make_timeline(plugin_mod)
        tl.fps = 24  # dataclass default
        out = plugin_mod._write_premiere(tl, tmp_path / "out3")
        root = ET.parse(out).getroot()
        assert root.findtext("project/children/sequence/rate/timebase") == "24"


# ── 3. Plugin end-to-end (integration) ────────────────────


@pytest.mark.integration
class TestPremierePluginE2E:
    @pytest.fixture()
    def ctx(self, tmp_path):
        from movie_narrator.models import Context, MatchedClip, TimedSegment

        ctx = Context(
            movie_name="飞驰人生",
            style="热血搞笑",
            duration=60,
            output_dir=str(tmp_path),
            source_video_path=r"C:\mock\source.mp4",
            audio_path=r"C:\mock\narration.mp3",
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
        ]
        ctx.metadata["render_template"] = {
            "title_card_text": "{movie} · 飞驰人生解说",
        }
        return ctx

    def test_premiere_backend_end_to_end(self, tmp_path, ctx):
        """metadata backend `premiere` → step writes a parseable .xml."""
        plugin = _import_plugin()
        ctx.metadata["timeline_export_backend"] = "premiere"
        out = plugin._timeline_export_step(ctx)
        assert out.step_state.result.value == "success"
        assert "premiere" in (out.step_state.message or "")
        export_path = Path(out.metadata["timeline_export_path"])
        assert export_path.suffix == ".xml"
        assert export_path.is_file()
        root = ET.parse(export_path).getroot()
        seq = root.find("project/children/sequence")
        assert seq is not None
        assert len(seq.findall("media/video/track/clipitem")) == 2
        # Narration stem carried from ctx.audio_path.
        audio_file = seq.find("media/audio/track/clipitem/file")
        assert audio_file is not None
        assert audio_file.findtext("pathurl") == Path(r"C:\mock\narration.mp3").resolve().as_uri()

    def test_render_fps_metadata_drives_timebase(self, tmp_path, ctx):
        plugin = _import_plugin()
        ctx.metadata["timeline_export_backend"] = "premiere"
        ctx.metadata["render_fps"] = 25
        out = plugin._timeline_export_step(ctx)
        assert out.step_state.result.value == "success"
        export_path = Path(out.metadata["timeline_export_path"])
        seq = ET.parse(export_path).getroot().find("project/children/sequence")
        assert seq is not None
        assert seq.findtext("rate/timebase") == "25"

    def test_unknown_backend_still_soft_skips(self, tmp_path, ctx):
        """A non-whitelisted value never activates the step."""
        plugin = _import_plugin()
        ctx.metadata["timeline_export_backend"] = "aaf"
        out = plugin._timeline_export_step(ctx)
        assert out.step_state.result.value == "skipped"
        assert "aaf" in (out.step_state.message or "")
