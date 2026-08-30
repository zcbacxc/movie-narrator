# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for v1.4.1 subtitle delivery modes (``burned | sidecar | muxed``).

ROADMAP long-term item made actionable: subtitles used to be ALWAYS
hard-burned (SRT -> PIL images composited during render). v1.4.1 adds
``JobParams.subtitle_delivery``:

- ``burned`` (default): byte-identical to v1.3.2 — burn-in overlay plus
  the two-input ffmpeg audio mux;
- ``sidecar``: burn-in skipped entirely, SRT sidecar files are the
  delivery, mux step unchanged;
- ``muxed``: burn-in skipped, the mode-selected SRT is muxed as a soft
  ``mov_text`` track (``-map 2:s:0 -c:s mov_text -metadata:s:s:0
  language=<lang>``) during the final ffmpeg pass.

Unavailable muxed requests (missing SRT / non-mp4 container / unknown
mode) degrade to ``burned`` with a structured log + metadata note —
never fail the render.

The merge mechanism mirrors v1.3.2 exactly (``timeline_export_backend``):
the ``"burned"`` default is dropped so jobs that never set the key keep
byte-identical params/metadata.
"""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from movie_narrator.config import Settings
from movie_narrator.models import Context, MatchedClip, Services, TimedSegment
from movie_narrator.workflow.load import load_job_config
from movie_narrator.workflow.merge import merge_job
from movie_narrator.workflow.schema import (
    VALID_SUBTITLE_DELIVERY_MODES,
    JobParams,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


# ── 1. Schema / whitelist / merge (unit level) ────────────


class TestSubtitleDeliverySchema:
    def test_default_is_burned(self):
        assert JobParams().subtitle_delivery == "burned"

    def test_valid_modes_accepted(self):
        for mode in ("burned", "sidecar", "muxed"):
            assert JobParams(subtitle_delivery=mode).subtitle_delivery == mode

    def test_invalid_mode_rejected(self):
        with pytest.raises(ValueError):
            JobParams(subtitle_delivery="telepathy")

    def test_frozenset_matches_literal_choices(self):
        assert VALID_SUBTITLE_DELIVERY_MODES == frozenset({"burned", "sidecar", "muxed"})

    def test_param_whitelist_includes_new_field(self):
        """PARAM_WHITELIST is derived from JobParams.model_fields — the new
        param must reach ctx.metadata via build_context without touching
        pipeline/runner.py."""
        from movie_narrator.pipeline.runner import PARAM_WHITELIST

        assert "subtitle_delivery" in PARAM_WHITELIST

    def test_job_yaml_key_survives_load_and_merge(self, tmp_path):
        job = tmp_path / "job.yaml"
        job.write_text(
            "movie: M\nparams:\n  subtitle_delivery: muxed\n",
            encoding="utf-8",
        )
        cfg = load_job_config(job)
        assert cfg.params.subtitle_delivery == "muxed"
        resolved = merge_job({"movie": "M"}, cfg, Settings())
        assert resolved.params["subtitle_delivery"] == "muxed"

    def test_default_burned_not_propagated(self, tmp_path):
        """The "burned" default is dropped (mirrors the v1.3.2 mechanism)
        so jobs that never set the key keep byte-identical params."""
        job = tmp_path / "job.yaml"
        job.write_text("movie: M\nparams:\n  lang: en\n", encoding="utf-8")
        cfg = load_job_config(job)
        assert cfg.params.subtitle_delivery == "burned"
        resolved = merge_job({"movie": "M"}, cfg, Settings())
        assert "subtitle_delivery" not in resolved.params

    def test_invalid_yaml_mode_rejected(self, tmp_path):
        job = tmp_path / "job.yaml"
        job.write_text(
            "movie: M\nparams:\n  subtitle_delivery: hardcode\n",
            encoding="utf-8",
        )
        with pytest.raises(Exception, match="subtitle_delivery"):
            load_job_config(job)


# ── 2. Render behaviour (fake runners, no real ffmpeg) ─────


def _make_ctx(tmp_path, *, segments=2):
    console = MagicMock()
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        timed_segments=[
            TimedSegment(text=f"line {i}", start=float(i), end=float(i) + 1.0)
            for i in range(segments)
        ],
        services=Services(console=console),
    )
    ctx.audio_path = str(tmp_path / "narration.mp3")
    (tmp_path / "narration.mp3").write_bytes(b"ID3")
    ctx.metadata["lang"] = "zh"
    return ctx


def _install_fake_render(monkeypatch):
    """Install the fake-runner render environment from test_render.py.

    Returns a ``calls`` dict capturing every STAGE-2 ffmpeg mux argv and
    the number of burn-in text images produced.
    """
    import movie_narrator.pipeline.render as render_mod

    calls = {"mux_cmds": [], "text_images": 0}

    audio_clip = MagicMock()
    audio_clip.duration = 2.0
    monkeypatch.setattr(render_mod, "AudioFileClip", lambda _p: audio_clip)
    monkeypatch.setattr(render_mod, "ensure_final_audio", lambda ctx_: None)
    monkeypatch.setattr(render_mod, "VideoFileClip", MagicMock())
    monkeypatch.setattr(render_mod, "CompositeVideoClip", MagicMock())
    monkeypatch.setattr(render_mod, "ColorClip", MagicMock())

    def counting_create(*_a, **_kw):
        calls["text_images"] += 1
        return MagicMock()

    monkeypatch.setattr(render_mod, "_create_text_image", counting_create)
    monkeypatch.setattr(render_mod, "ImageClip", MagicMock())
    monkeypatch.setattr(render_mod, "_get_video_sizes", lambda ctx_: {"16:9": (1920, 1080)})
    monkeypatch.setattr(render_mod, "resolve_encoder", lambda hint: ("libx264", []))
    monkeypatch.setattr(render_mod, "_write_videofile_with_deadline", lambda *a, **kw: None)
    monkeypatch.setattr(render_mod, "ffmpeg_bin", lambda: "/fake/ffmpeg")

    def fake_run(cmd, *a, **kw):
        calls["mux_cmds"].append(list(cmd))
        Path(str(cmd[-1])).write_bytes(b"0")
        return MagicMock(returncode=0, stderr="")

    monkeypatch.setattr(render_mod.subprocess, "run", fake_run)
    monkeypatch.setattr(
        render_mod,
        "get_encoder_info",
        lambda hint: {
            "requested": hint or "auto",
            "detected": "libx264",
            "active": "libx264",
            "gpu_available": False,
            "fallback_reason": None,
        },
    )
    monkeypatch.setattr(render_mod, "build_metadata_json", lambda ctx_: {})
    return calls


_LEGACY_BURNED_MUX_CMD = [
    "/fake/ffmpeg",
    "-y",
    "-loglevel",
    "error",
    "-i",
    "VIDEO_ONLY",
    "-i",
    "AUDIO",
    "-map",
    "0:v:0",
    "-map",
    "1:a:0",
    "-c:v",
    "copy",
    "-c:a",
    "aac",
    "-movflags",
    "+faststart",
    "-f",
    "FORMAT",
    "PARTIAL",
]


def _expected_burned_cmd(ctx, output_name="final.mp4"):
    """The exact STAGE-2 argv the v1.3.2 code path builds for *ctx*."""
    tmp = Path(ctx.output_dir) / ".tmp"
    target = Path(ctx.output_dir) / output_name
    return [
        part.replace("VIDEO_ONLY", str(tmp / "video_only.mp4"))
        .replace("AUDIO", str(Path(ctx.output_dir) / "narration.mp3"))
        .replace("PARTIAL", str(tmp / f"{target.name}.part"))
        .replace("FORMAT", target.suffix.lstrip("."))
        for part in _LEGACY_BURNED_MUX_CMD
    ]


class TestBurnedUnchanged:
    def test_default_render_is_legacy_burned(self, tmp_path, monkeypatch):
        """No subtitle_delivery key -> v1.3.2 behaviour: burn overlay per
        segment + legacy two-input mux argv (byte-identical)."""
        ctx = _make_ctx(tmp_path)
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        assert calls["text_images"] == 2  # one overlay per timed segment
        assert calls["mux_cmds"] == [_expected_burned_cmd(ctx)]
        assert ctx.metadata["subtitle_delivery_used"] == "burned"
        assert "subtitle_mux_language" not in ctx.metadata
        assert "subtitle_delivery_fallback_reason" not in ctx.metadata

    def test_explicit_burned_is_byte_identical_to_default(self, tmp_path, monkeypatch):
        ctx_a = _make_ctx(tmp_path)
        calls_a = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx_a)
        calls_a_cmds = calls_a["mux_cmds"]

        ctx_b = _make_ctx(tmp_path)
        ctx_b.metadata["subtitle_delivery"] = "burned"
        calls_b = _install_fake_render(monkeypatch)
        render_mod.render_video(ctx_b)

        assert calls_b["mux_cmds"] == calls_a_cmds
        assert calls_b["text_images"] == calls_a["text_images"]


class TestSidecarSkipsBurnIn:
    def test_sidecar_skips_overlay_and_keeps_mux(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        ctx.metadata["subtitle_delivery"] = "sidecar"
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        assert calls["text_images"] == 0  # overlay hook never invoked
        # Mux step otherwise unchanged: no SRT input, no -c:s options.
        assert calls["mux_cmds"] == [_expected_burned_cmd(ctx)]
        assert ctx.metadata["subtitle_delivery_used"] == "sidecar"
        assert "subtitle_mux_language" not in ctx.metadata

    def test_sidecar_skips_footage_fallback_text_card(self, tmp_path, monkeypatch):
        """When footage fails to decode, burned burns a fallback text card;
        sidecar must not burn any text."""
        ctx = _make_ctx(tmp_path, segments=1)
        ctx.source_video_path = str(tmp_path / "video.mp4")
        ctx.metadata["subtitle_delivery"] = "sidecar"

        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        # VideoFileClip opens fine, but the subclip decode fails so the
        # per-clip fallback branch fires.
        src = MagicMock()
        src.subclipped.side_effect = RuntimeError("decode failed")
        monkeypatch.setattr(render_mod, "VideoFileClip", lambda _p: src)
        ctx.matched_clips = [
            MatchedClip(
                segment_index=0,
                text="A",
                narr_start=0.0,
                narr_end=1.0,
                src_start=0.0,
                src_end=1.0,
                score=0.9,
                scene_index=0,
                source="heuristic",
            ),
        ]
        render_mod.render_video(ctx)
        assert calls["text_images"] == 0

    def test_burned_still_burns_footage_fallback_text_card(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path, segments=1)
        ctx.source_video_path = str(tmp_path / "video.mp4")

        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        src = MagicMock()
        src.subclipped.side_effect = RuntimeError("decode failed")
        monkeypatch.setattr(render_mod, "VideoFileClip", lambda _p: src)
        ctx.matched_clips = [
            MatchedClip(
                segment_index=0,
                text="A",
                narr_start=0.0,
                narr_end=1.0,
                src_start=0.0,
                src_end=1.0,
                score=0.9,
                scene_index=0,
                source="heuristic",
            ),
        ]
        render_mod.render_video(ctx)
        # 1 fallback card + 1 subtitle overlay for the single segment.
        assert calls["text_images"] == 2


class TestMuxedSoftTrack:
    def test_muxed_builds_soft_track_argv(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        srt = tmp_path / "subtitle.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nline 0\n\n", encoding="utf-8")
        ctx.render_subtitle_path = str(srt)
        ctx.metadata["subtitle_delivery"] = "muxed"
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)

        assert calls["text_images"] == 0  # no burn-in
        cmd = calls["mux_cmds"][0]
        # Input order: 0 = video-only, 1 = audio, 2 = the SRT.
        video_only = str(Path(tmp_path) / ".tmp" / "video_only.mp4")
        audio = str(Path(tmp_path) / "narration.mp3")
        assert cmd[:6] == ["/fake/ffmpeg", "-y", "-loglevel", "error", "-i", video_only]
        assert cmd[6:10] == ["-i", audio, "-i", str(srt)]
        assert cmd[cmd.index("2:s:0")] == "2:s:0"
        assert cmd.index("2:s:0") > cmd.index("1:a:0")  # mapped after a/v
        assert cmd[cmd.index("-c:s") + 1] == "mov_text"
        # 2-letter lang tags are normalized to ISO 639-2 (ffmpeg's mp4
        # muxer drops 2-letter tags — verified on the imageio build).
        assert cmd[cmd.index("-metadata:s:s:0") + 1] == "language=zho"
        # Video copy + audio codec semantics preserved.
        assert cmd[cmd.index("-c:v") + 1] == "copy"
        assert cmd[cmd.index("-c:a") + 1] == "aac"
        assert "-movflags" in cmd and cmd[-1].endswith("final.mp4.part")
        # Metadata records the effective mode + mux language (normalized).
        assert ctx.metadata["subtitle_delivery_used"] == "muxed"
        assert ctx.metadata["subtitle_mux_language"] == "zho"
        assert "subtitle_delivery_fallback_reason" not in ctx.metadata

    def test_muxed_translated_uses_subtitle_lang(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        srt = tmp_path / "subtitle.en.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nhello\n\n", encoding="utf-8")
        ctx.render_subtitle_path = str(srt)
        ctx.metadata["subtitle_delivery"] = "muxed"
        ctx.metadata["subtitle_mode"] = "translated"
        ctx.metadata["subtitle_lang"] = "en"
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        cmd = calls["mux_cmds"][0]
        assert cmd[cmd.index("-metadata:s:s:0") + 1] == "language=eng"
        assert ctx.metadata["subtitle_mux_language"] == "eng"


class TestMuxedFallbacks:
    """muxed degrades to burned (structured log + metadata note), never
    fails the render."""

    def test_missing_srt_falls_back_to_burned(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        ctx.render_subtitle_path = None  # subtitle step never ran
        ctx.metadata["subtitle_delivery"] = "muxed"
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        assert ctx.metadata["subtitle_delivery_used"] == "burned"
        assert ctx.metadata["subtitle_delivery_fallback_reason"] == "missing_srt"
        assert calls["mux_cmds"] == [_expected_burned_cmd(ctx)]
        assert calls["text_images"] == 2  # burned overlay happened

    def test_srt_file_absent_falls_back_to_burned(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        ctx.render_subtitle_path = str(tmp_path / "gone.srt")  # never written
        ctx.metadata["subtitle_delivery"] = "muxed"
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        assert ctx.metadata["subtitle_delivery_fallback_reason"] == "missing_srt"
        assert calls["mux_cmds"] == [_expected_burned_cmd(ctx)]

    def test_non_mp4_container_falls_back_to_burned(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        srt = tmp_path / "subtitle.srt"
        srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nline 0\n\n", encoding="utf-8")
        ctx.render_subtitle_path = str(srt)
        ctx.metadata["subtitle_delivery"] = "muxed"
        ctx.metadata["render_output_name"] = "final.mkv"
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        assert ctx.metadata["subtitle_delivery_used"] == "burned"
        assert ctx.metadata["subtitle_delivery_fallback_reason"] == "non_mp4_container"
        assert calls["text_images"] == 2
        # The fallback mux targets the mkv name, not the srt input.
        assert calls["mux_cmds"] == [_expected_burned_cmd(ctx, "final.mkv")]
        assert str(srt) not in calls["mux_cmds"][0]

    def test_invalid_mode_falls_back_to_burned(self, tmp_path, monkeypatch):
        """Direct metadata injection (plugins / cloud worker) is normalized
        defensively — unknown mode degrades to burned with a warn."""
        ctx = _make_ctx(tmp_path)
        ctx.metadata["subtitle_delivery"] = "telepathy"
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        assert ctx.metadata["subtitle_delivery_used"] == "burned"
        assert ctx.metadata["subtitle_delivery_fallback_reason"] == "invalid_mode"
        assert calls["text_images"] == 2


class TestMuxHelpers:
    def test_mux_subtitle_language_defaults_to_narration_lang(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        import movie_narrator.pipeline.render as render_mod

        # 2-letter narration lang is normalized to ISO 639-2 for the mp4
        # language tag (ffmpeg drops 2-letter tags).
        assert render_mod._mux_subtitle_language(ctx) == "zho"
        ctx.metadata["lang"] = "ja"
        assert render_mod._mux_subtitle_language(ctx) == "jpn"

    def test_mux_subtitle_language_normalizes_bcp47_region(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        import movie_narrator.pipeline.render as render_mod

        ctx.metadata["lang"] = "zh-TW"
        assert render_mod._mux_subtitle_language(ctx) == "zho"

    def test_mux_subtitle_language_passes_unknown_through(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        import movie_narrator.pipeline.render as render_mod

        ctx.metadata["lang"] = "zz"
        assert render_mod._mux_subtitle_language(ctx) == "zz"
        ctx.metadata["lang"] = "zho"
        assert render_mod._mux_subtitle_language(ctx) == "zho"

    def test_mux_subtitle_language_prefers_subtitle_lang_for_translated(self, tmp_path):
        ctx = _make_ctx(tmp_path)
        import movie_narrator.pipeline.render as render_mod

        ctx.metadata["subtitle_mode"] = "bilingual"
        ctx.metadata["subtitle_lang"] = "en"
        assert render_mod._mux_subtitle_language(ctx) == "eng"

    def test_build_mux_cmd_burned_matches_legacy_shape(self, tmp_path):
        import movie_narrator.pipeline.render as render_mod

        cmd = render_mod._build_mux_cmd(
            "FF",
            Path("v.mp4"),
            Path("a.mp3"),
            Path("out.mp4.part"),
            audio_codec="aac",
            faststart=True,
            target_format="mp4",
        )
        assert cmd == [
            "FF",
            "-y",
            "-loglevel",
            "error",
            "-i",
            "v.mp4",
            "-i",
            "a.mp3",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            "-f",
            "mp4",
            "out.mp4.part",
        ]

    def test_build_mux_cmd_lib_prefixed_audio_codec_stripped(self, tmp_path):
        import movie_narrator.pipeline.render as render_mod

        cmd = render_mod._build_mux_cmd(
            "FF",
            Path("v.mp4"),
            Path("a.mp3"),
            Path("out.mp4.part"),
            audio_codec="libmp3lame",
            faststart=False,
            target_format="",
        )
        assert cmd[cmd.index("-c:a") + 1] == "mp3lame"
        assert "-movflags" not in cmd
        assert "-f" not in cmd

    def test_build_mux_cmd_muxed_appends_soft_track(self, tmp_path):
        import movie_narrator.pipeline.render as render_mod

        cmd = render_mod._build_mux_cmd(
            "FF",
            Path("v.mp4"),
            Path("a.mp3"),
            Path("out.mp4.part"),
            audio_codec="aac",
            faststart=True,
            target_format="mp4",
            subtitle_srt="s.srt",
            subtitle_language="zho",
        )
        assert cmd[:10] == [
            "FF",
            "-y",
            "-loglevel",
            "error",
            "-i",
            "v.mp4",
            "-i",
            "a.mp3",
            "-i",
            "s.srt",
        ]
        assert cmd[10:16] == ["-map", "0:v:0", "-map", "1:a:0", "-map", "2:s:0"]
        assert cmd[cmd.index("-c:s") + 1] == "mov_text"
        assert cmd[cmd.index("-metadata:s:s:0") + 1] == "language=zho"
        assert cmd[-1] == "out.mp4.part"


# ── 3. QA must not false-positive on soft subtitle streams ──


class TestVideoQaIgnoresSubtitleStreams:
    def test_probe_ignores_mov_text_stream(self, monkeypatch):
        """The muxed output carries a third (subtitle) stream. video_qa
        inspects only codec_type video/audio — a mov_text stream must not
        leak into the metrics nor raise issues."""
        import movie_narrator.utils.video_qa as vqa

        fake_probe = {
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "profile": "High",
                    "width": 1920,
                    "height": 1080,
                    "r_frame_rate": "24/1",
                    "pix_fmt": "yuv420p",
                    "bit_rate": "4000000",
                },
                {
                    "codec_type": "audio",
                    "codec_name": "aac",
                    "bit_rate": "128000",
                    "channels": 2,
                    "sample_rate": "44100",
                },
                {
                    "codec_type": "subtitle",
                    "codec_name": "mov_text",
                    "tags": {"language": "zho"},
                },
            ],
            "format": {"bit_rate": "4128000"},
        }
        monkeypatch.setattr(vqa, "_run_ffprobe", lambda p, timeout=30: fake_probe)

        metrics = vqa.probe_video_encoding("fake.mp4")
        assert metrics.codec == "h264"
        assert metrics.audio_codec == "aac"
        assert metrics.width == 1920 and metrics.height == 1080

        report = vqa.check_encoding_quality(metrics)
        assert report.ok, report.issues
        assert not any("mov_text" in i for i in report.issues)


# ── 4. Integration: real ffmpeg mux (CI integration job) ───


@pytest.mark.integration
class TestMuxedSubtitleRealFfmpeg:
    def test_real_mux_produces_mov_text_track(self, tmp_path):
        """Mux a tiny synthetic video + SRT through the real ffmpeg mux
        helper and verify the output carries a mov_text subtitle stream."""
        from movie_narrator.utils.ffmpeg_bin import ffmpeg_bin

        ffmpeg = ffmpeg_bin()
        ffprobe = shutil.which("ffprobe")
        has_ffmpeg = Path(ffmpeg).is_file() or shutil.which("ffmpeg") is not None
        if not has_ffmpeg or ffprobe is None:
            pytest.skip("real ffmpeg/ffprobe unavailable")

        video_only = tmp_path / "video_only.mp4"
        audio = tmp_path / "audio.m4a"
        srt = tmp_path / "subtitle.srt"
        out = tmp_path / "final.mp4"

        def run(cmd):
            proc = subprocess.run(  # nosec B603 B607 — real ffmpeg integration check
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            assert proc.returncode == 0, proc.stderr
            return proc

        run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "color=c=green:s=320x240:d=1:r=12",
                "-frames:v",
                "12",
                "-c:v",
                "mpeg4",
                "-pix_fmt",
                "yuv420p",
                str(video_only),
            ]
        )
        run(
            [
                ffmpeg,
                "-y",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:duration=1",
                "-c:a",
                "aac",
                str(audio),
            ]
        )
        srt.write_text("1\n00:00:00,000 --> 00:00:00,900\n你好世界\n\n", encoding="utf-8")

        import movie_narrator.pipeline.render as render_mod

        cmd = render_mod._build_mux_cmd(
            ffmpeg,
            video_only,
            audio,
            out,
            audio_codec="aac",
            faststart=True,
            target_format="mp4",
            subtitle_srt=str(srt),
            subtitle_language="zho",
        )
        run(cmd)

        probe = subprocess.run(  # nosec B603 B607 — ffprobe verification
            [
                ffprobe,
                "-v",
                "quiet",
                "-print_format",
                "json",
                "-show_streams",
                str(out),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
        assert probe.returncode == 0
        streams = json.loads(probe.stdout).get("streams", [])
        subs = [s for s in streams if s.get("codec_type") == "subtitle"]
        assert len(subs) == 1, [s.get("codec_type") for s in streams]
        assert subs[0].get("codec_name") == "mov_text"
        # The 3-letter language tag survives the mp4 mux verbatim.
        assert (subs[0].get("tags") or {}).get("language") == "zho"
