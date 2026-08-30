# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tests for v1.5.0 pixel pipeline — 10-bit + color metadata (ADR-017).

ROADMAP HDR/4K item made actionable: the render step used to emit an
unlabeled 8-bit yuv420p stream. v1.5.0 adds ``JobParams.render_bit_depth``
(8 | 10) and ``JobParams.render_color_space`` (``sdr`` | ``hdr10``):

- 8-bit/sdr (default): byte-identical encode argv PLUS explicit bt709
  color tags written during the STAGE-2 copy mux (they only write down
  the color interpretation ffmpeg previously applied implicitly);
- 10-bit: ``yuv420p10le`` + libx264 ``high10`` profile, CPU-only in
  v1.5.0 — a resolved GPU encoder is overridden with libx264 and
  ``encoder_info.fallback_reason`` records ``"10bit_gpu_unsupported"``
  (NVENC/VAAPI/VideoToolbox H.264 are 8-bit only; HEVC main10 is future
  work);
- hdr10: BT.2020 primaries + smpte2084 (PQ) transfer + bt2020nc matrix
  tags with the bit depth auto-forced to 10 (recorded as a note, never
  rejected). Mastering-display / MaxCLL/MaxFALL SEI is out of scope for
  v1.5.0.

The color tags live at the mux (not the MoviePy encode): libx264 parses
encode-level ``-color_primaries``/``-color_trc`` but silently drops them
(only ``-colorspace`` reaches the H.264 VUI — verified on ffmpeg 7.1 /
8.1), while stream-copy output options land in the container for every
encoder, GPU backends included.

The merge mechanism mirrors v1.4.1 exactly (``subtitle_delivery``): the
8/"sdr" defaults are dropped so jobs that never set the keys keep
byte-identical params/metadata.
"""

import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from movie_narrator.config import Settings
from movie_narrator.models import Context, Services, TimedSegment
from movie_narrator.utils.resources import estimate_temp_space_bytes
from movie_narrator.utils.video_qa import check_encoding_quality, probe_video_encoding
from movie_narrator.workflow.load import load_job_config
from movie_narrator.workflow.merge import merge_job
from movie_narrator.workflow.schema import (
    VALID_RENDER_COLOR_SPACES,
    JobParams,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
GB = 1024 * 1024 * 1024

# The exact encode argv the v1.4.2 code built for the default job
# (MoviePy write kwargs: crf/preset for libx264, no pixel args).
_V142_DEFAULT_ENCODE_PARAMS = ["-crf", "18", "-preset", "slow"]

# v1.5.0 10-bit encode args (appended to the encoder base).
_10BIT_ARGS = ["-pix_fmt", "yuv420p10le", "-profile:v", "high10"]

# v1.5.0 color tags, applied at the STAGE-2 copy mux.
_SDR_COLOR_ARGS = [
    "-color_primaries",
    "bt709",
    "-color_trc",
    "bt709",
    "-colorspace",
    "bt709",
]
_HDR10_COLOR_ARGS = [
    "-color_primaries",
    "bt2020",
    "-color_trc",
    "smpte2084",
    "-colorspace",
    "bt2020nc",
]


# ── 1. Schema / whitelist / merge (unit level) ────────────


class TestPixelPipelineSchema:
    def test_defaults_are_8_bit_sdr(self):
        params = JobParams()
        assert params.render_bit_depth == 8
        assert params.render_color_space == "sdr"

    def test_valid_values_accepted(self):
        assert JobParams(render_bit_depth=10).render_bit_depth == 10
        assert JobParams(render_color_space="hdr10").render_color_space == "hdr10"
        assert JobParams(render_bit_depth=8).render_bit_depth == 8
        assert JobParams(render_color_space="sdr").render_color_space == "sdr"

    def test_invalid_values_rejected(self):
        with pytest.raises(ValueError):
            JobParams(render_bit_depth=12)
        with pytest.raises(ValueError):
            JobParams(render_bit_depth="10")  # str is not Literal[8, 10]
        with pytest.raises(ValueError):
            JobParams(render_color_space="pq")
        with pytest.raises(ValueError):
            JobParams(render_color_space="hlg")

    def test_frozenset_matches_literal_choices(self):
        assert VALID_RENDER_COLOR_SPACES == frozenset({"sdr", "hdr10"})

    def test_param_whitelist_includes_new_fields(self):
        """PARAM_WHITELIST is derived from JobParams.model_fields — the new
        params must reach ctx.metadata via build_context without touching
        pipeline/runner.py."""
        from movie_narrator.pipeline.runner import PARAM_WHITELIST

        assert "render_bit_depth" in PARAM_WHITELIST
        assert "render_color_space" in PARAM_WHITELIST

    def test_job_yaml_keys_survive_load_and_merge(self, tmp_path):
        job = tmp_path / "job.yaml"
        job.write_text(
            "movie: M\nparams:\n  render_bit_depth: 10\n  render_color_space: hdr10\n",
            encoding="utf-8",
        )
        cfg = load_job_config(job)
        assert cfg.params.render_bit_depth == 10
        assert cfg.params.render_color_space == "hdr10"
        resolved = merge_job({"movie": "M"}, cfg, Settings())
        assert resolved.params["render_bit_depth"] == 10
        assert resolved.params["render_color_space"] == "hdr10"

    def test_defaults_not_propagated(self, tmp_path):
        """The 8/"sdr" defaults are dropped (mirrors the v1.4.1 mechanism)
        so jobs that never set the keys keep byte-identical params."""
        job = tmp_path / "job.yaml"
        job.write_text("movie: M\nparams:\n  lang: en\n", encoding="utf-8")
        cfg = load_job_config(job)
        assert cfg.params.render_bit_depth == 8
        assert cfg.params.render_color_space == "sdr"
        resolved = merge_job({"movie": "M"}, cfg, Settings())
        assert "render_bit_depth" not in resolved.params
        assert "render_color_space" not in resolved.params

    def test_explicit_defaults_also_dropped(self, tmp_path):
        """An explicit 8/"sdr" is indistinguishable from the default —
        dropped too, so the metadata never carries phantom keys."""
        job = tmp_path / "job.yaml"
        job.write_text(
            "movie: M\nparams:\n  render_bit_depth: 8\n  render_color_space: sdr\n",
            encoding="utf-8",
        )
        cfg = load_job_config(job)
        resolved = merge_job({"movie": "M"}, cfg, Settings())
        assert "render_bit_depth" not in resolved.params
        assert "render_color_space" not in resolved.params

    def test_invalid_yaml_values_rejected(self, tmp_path):
        job = tmp_path / "job.yaml"
        job.write_text("movie: M\nparams:\n  render_bit_depth: 12\n", encoding="utf-8")
        with pytest.raises(Exception, match="render_bit_depth"):
            load_job_config(job)
        job.write_text("movie: M\nparams:\n  render_color_space: dv\n", encoding="utf-8")
        with pytest.raises(Exception, match="render_color_space"):
            load_job_config(job)


# ── 2. Pixel plan helper (pure) ───────────────────────────


class TestResolvePixelPlan:
    def test_default_8bit_sdr_plan(self):
        import movie_narrator.pipeline.render as render_mod

        plan = render_mod._resolve_pixel_plan(8, "sdr")
        assert plan["bit_depth"] == 8
        assert plan["pix_fmt"] == "yuv420p"
        assert plan["color_space"] == "sdr"
        assert plan["forced_note"] is None
        assert plan["pixel_args"] == []  # encode argv stays byte-identical
        assert plan["color_args"] == _SDR_COLOR_ARGS
        assert plan["color_tags"] == {
            "color_primaries": "bt709",
            "color_trc": "bt709",
            "colorspace": "bt709",
        }

    def test_10bit_sdr_adds_pix_fmt_and_profile(self):
        import movie_narrator.pipeline.render as render_mod

        plan = render_mod._resolve_pixel_plan(10, "sdr")
        assert plan["bit_depth"] == 10
        assert plan["pix_fmt"] == "yuv420p10le"
        assert plan["pixel_args"] == _10BIT_ARGS
        assert plan["color_args"] == _SDR_COLOR_ARGS

    def test_hdr10_uses_bt2020_smpte2084(self):
        import movie_narrator.pipeline.render as render_mod

        plan = render_mod._resolve_pixel_plan(10, "hdr10")
        assert plan["bit_depth"] == 10
        assert plan["pix_fmt"] == "yuv420p10le"
        assert plan["color_tags"] == {
            "color_primaries": "bt2020",
            "color_trc": "smpte2084",
            "colorspace": "bt2020nc",
        }
        assert plan["color_args"] == _HDR10_COLOR_ARGS

    def test_hdr10_forces_10bit_with_note(self):
        import movie_narrator.pipeline.render as render_mod

        plan = render_mod._resolve_pixel_plan(8, "hdr10")
        assert plan["bit_depth"] == 10
        assert plan["forced_note"] is not None
        assert "auto-forced" in plan["forced_note"]

    def test_invalid_values_normalize_defensively(self):
        """Direct metadata injection (plugins / cloud worker) is normalized
        defensively — unknown values fall back to the 8-bit sdr default."""
        import movie_narrator.pipeline.render as render_mod

        plan = render_mod._resolve_pixel_plan(12, "pq")
        assert plan["bit_depth"] == 8
        assert plan["color_space"] == "sdr"
        assert plan["forced_note"] is None
        plan = render_mod._resolve_pixel_plan("nope", None)
        assert plan["bit_depth"] == 8
        assert plan["color_space"] == "sdr"


# ── 3. Render behaviour (fake runners, no real ffmpeg) ─────


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


def _install_fake_render(monkeypatch, *, resolve=None):
    """Install the fake-runner render environment from test_render.py.

    Returns a ``calls`` dict capturing every MoviePy encode kwargs
    snapshot and every STAGE-2 ffmpeg mux argv.
    """
    import movie_narrator.pipeline.render as render_mod

    calls = {"write_kwargs": [], "mux_cmds": [], "gpu_fail_count": 0}

    audio_clip = MagicMock()
    audio_clip.duration = 2.0
    monkeypatch.setattr(render_mod, "AudioFileClip", lambda _p: audio_clip)
    monkeypatch.setattr(render_mod, "ensure_final_audio", lambda ctx_: None)
    monkeypatch.setattr(render_mod, "VideoFileClip", MagicMock())
    monkeypatch.setattr(render_mod, "CompositeVideoClip", MagicMock())
    monkeypatch.setattr(render_mod, "ColorClip", MagicMock())
    monkeypatch.setattr(render_mod, "_create_text_image", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(render_mod, "ImageClip", MagicMock())
    monkeypatch.setattr(render_mod, "_get_video_sizes", lambda ctx_: {"16:9": (1920, 1080)})

    if resolve is None:
        monkeypatch.setattr(render_mod, "resolve_encoder", lambda hint: ("libx264", []))
    else:
        monkeypatch.setattr(render_mod, "resolve_encoder", lambda hint: resolve(hint))

    def fake_write(_video, _path, video_write_kwargs, _timeout):
        # Snapshot: the runtime fallback REPLACES dict values (never
        # mutates the captured lists), so a shallow copy is truthful.
        calls["write_kwargs"].append(dict(video_write_kwargs))

    def fake_write_gpu_then_fails(_video, _path, kwargs, _timeout):
        calls["write_kwargs"].append(dict(kwargs))
        calls["gpu_fail_count"] += 1
        if calls["gpu_fail_count"] == 1:
            raise OSError("nvidia driver failed")  # first (GPU) attempt only

    calls["fake_write_gpu_then_fails"] = fake_write_gpu_then_fails
    monkeypatch.setattr(render_mod, "_write_videofile_with_deadline", fake_write)

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


def _expected_mux_cmd(ctx, color_args, output_name="final.mp4", *, srt=None):
    """The exact STAGE-2 argv the v1.4.1 code builds for *ctx*, with the
    v1.5.0 color args appended after ``-c:v copy``."""
    tmp = Path(ctx.output_dir) / ".tmp"
    target = Path(ctx.output_dir) / output_name
    cmd = [
        "/fake/ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-i",
        str(tmp / "video_only.mp4"),
        "-i",
        str(Path(ctx.output_dir) / "narration.mp3"),
    ]
    if srt is not None:
        cmd += ["-i", str(srt)]
    cmd += ["-map", "0:v:0", "-map", "1:a:0"]
    if srt is not None:
        cmd += ["-map", "2:s:0"]
    cmd += ["-c:v", "copy", "-c:a", "aac"] + list(color_args)
    if srt is not None:
        cmd += ["-c:s", "mov_text"]
    cmd += ["-movflags", "+faststart", "-f", target.suffix.lstrip("."), str(tmp / f"{target.name}.part")]
    return cmd


class TestDefaultEncodeArgv:
    def test_default_encode_argv_is_byte_identical_to_v142(self, tmp_path, monkeypatch):
        """8-bit/sdr (default): the MoviePy encode argv is EXACTLY today's
        (crf/preset, no pixel args) — the color tags live at the mux."""
        ctx = _make_ctx(tmp_path)
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)

        assert len(calls["write_kwargs"]) == 1
        kw = calls["write_kwargs"][0]
        assert kw["codec"] == "libx264"
        assert kw["audio"] is False
        assert kw["fps"] == 24  # historical default untouched
        assert kw["threads"] == 4
        assert kw["ffmpeg_params"] == _V142_DEFAULT_ENCODE_PARAMS

    def test_default_mux_carries_bt709_tags(self, tmp_path, monkeypatch):
        """The STAGE-2 mux argv is today's shape with ONLY the explicit
        bt709 tags appended after -c:v copy (same visual stream)."""
        ctx = _make_ctx(tmp_path)
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        assert calls["mux_cmds"] == [_expected_mux_cmd(ctx, _SDR_COLOR_ARGS)]
        cmd = calls["mux_cmds"][0]
        assert cmd[cmd.index("-color_trc") + 1] == "bt709"

    def test_default_render_pixel_metadata(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        assert ctx.metadata["render_pixel"] == {
            "bit_depth": 8,
            "pix_fmt": "yuv420p",
            "color_space": "sdr",
            "color_tags": {
                "color_primaries": "bt709",
                "color_trc": "bt709",
                "colorspace": "bt709",
            },
            "encoder_path": "cpu",
        }
        assert "note" not in ctx.metadata["render_pixel"]
        assert len(calls["write_kwargs"]) == 1


class TestTenBitEncode:
    def test_10bit_sdr_uses_yuv420p10le_high10(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        ctx.metadata["render_bit_depth"] = 10
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        kw = calls["write_kwargs"][0]
        assert kw["codec"] == "libx264"
        assert kw["ffmpeg_params"] == _V142_DEFAULT_ENCODE_PARAMS + _10BIT_ARGS
        assert ctx.metadata["render_pixel"]["bit_depth"] == 10
        assert ctx.metadata["render_pixel"]["pix_fmt"] == "yuv420p10le"
        assert ctx.metadata["render_pixel"]["encoder_path"] == "cpu"
        # Mux still carries the sdr tags.
        assert calls["mux_cmds"] == [_expected_mux_cmd(ctx, _SDR_COLOR_ARGS)]

    def test_hdr10_forces_10bit_with_note(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        ctx.metadata["render_color_space"] = "hdr10"  # bit depth left at 8
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        kw = calls["write_kwargs"][0]
        assert kw["ffmpeg_params"] == _V142_DEFAULT_ENCODE_PARAMS + _10BIT_ARGS
        assert calls["mux_cmds"] == [_expected_mux_cmd(ctx, _HDR10_COLOR_ARGS)]
        report = ctx.metadata["render_pixel"]
        assert report["bit_depth"] == 10
        assert report["color_space"] == "hdr10"
        assert report["color_tags"]["color_trc"] == "smpte2084"
        assert "auto-forced" in report["note"]
        # The forcing is announced (info, not warn — never a failure).
        ctx.services.console.info.assert_any_call(
            "  render_color_space=hdr10 — forcing 10-bit encode (yuv420p10le)"
        )

    def test_explicit_10bit_hdr10_has_no_forcing_note(self, tmp_path, monkeypatch):
        ctx = _make_ctx(tmp_path)
        ctx.metadata["render_bit_depth"] = 10
        ctx.metadata["render_color_space"] = "hdr10"
        calls = _install_fake_render(monkeypatch)
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        report = ctx.metadata["render_pixel"]
        assert report["bit_depth"] == 10
        assert "note" not in report
        assert report["color_tags"]["color_primaries"] == "bt2020"
        assert calls["mux_cmds"] == [_expected_mux_cmd(ctx, _HDR10_COLOR_ARGS)]

    def test_10bit_with_gpu_encoder_forces_cpu_with_reason(self, tmp_path, monkeypatch):
        """ADR-017: 10-bit renders are CPU-only in v1.5.0 — a resolved GPU
        encoder is overridden and ``encoder_info.fallback_reason`` records
        ``10bit_gpu_unsupported`` (no GPU 10-bit probing is attempted)."""
        ctx = _make_ctx(tmp_path)
        ctx.metadata["render_bit_depth"] = 10
        calls = _install_fake_render(
            monkeypatch,
            resolve=lambda hint: ("h264_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "20"]),
        )
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)

        kw = calls["write_kwargs"][0]
        assert kw["codec"] == "libx264"  # GPU encoder never attempted
        assert kw["ffmpeg_params"] == _V142_DEFAULT_ENCODE_PARAMS + _10BIT_ARGS
        assert ctx.metadata["render_pixel"]["encoder_path"] == "cpu"
        info = ctx.metadata["encoder_info"]
        assert info["active"] == "libx264"
        assert info["fallback_reason"] == "10bit_gpu_unsupported"
        warn_texts = [str(c.args[0]) for c in ctx.services.console.inline_warn.call_args_list]
        assert any("10-bit" in t and "libx264" in t for t in warn_texts)

    def test_8bit_gpu_encoder_keeps_gpu_params(self, tmp_path, monkeypatch):
        """8-bit GPU encodes are untouched by the pixel policy: the encode
        argv is exactly the v0.7.0 GPU params; only the mux tags."""
        ctx = _make_ctx(tmp_path)
        gpu_params = ["-preset", "p4", "-rc", "vbr", "-cq", "20"]
        calls = _install_fake_render(
            monkeypatch, resolve=lambda hint: ("h264_nvenc", list(gpu_params))
        )
        import movie_narrator.pipeline.render as render_mod

        render_mod.render_video(ctx)
        kw = calls["write_kwargs"][0]
        assert kw["codec"] == "h264_nvenc"
        assert kw["ffmpeg_params"] == gpu_params  # no color args at encode
        assert ctx.metadata["render_pixel"]["encoder_path"] == "gpu"
        assert ctx.metadata["encoder_info"]["fallback_reason"] is None


class TestRuntimeGpuFallback:
    def test_runtime_fallback_retries_with_legacy_argv(self, tmp_path, monkeypatch):
        """v0.7.0 GPU→CPU runtime retry keeps the v1.4.2 retry argv (the
        v1.5.0 color tags live at the mux, which runs afterwards)."""
        ctx = _make_ctx(tmp_path)
        calls = _install_fake_render(
            monkeypatch,
            resolve=lambda hint: ("h264_nvenc", ["-preset", "p4", "-rc", "vbr", "-cq", "20"]),
        )
        import movie_narrator.pipeline.render as render_mod

        monkeypatch.setattr(
            render_mod, "_write_videofile_with_deadline", calls["fake_write_gpu_then_fails"]
        )
        monkeypatch.setattr(
            render_mod,
            "get_encoder_info",
            lambda hint: {
                "requested": hint or "auto",
                "detected": "h264_nvenc",
                "active": "h264_nvenc",
                "gpu_available": True,
                "fallback_reason": None,
            },
        )

        render_mod.render_video(ctx)

        assert len(calls["write_kwargs"]) == 2  # GPU try + libx264 retry
        first, retry = calls["write_kwargs"]
        assert first["codec"] == "h264_nvenc"
        assert retry["codec"] == "libx264"
        assert retry["ffmpeg_params"] == _V142_DEFAULT_ENCODE_PARAMS
        assert ctx.metadata["render_pixel"]["encoder_path"] == "cpu"
        info = ctx.metadata["encoder_info"]
        assert info["active"] == "libx264"
        assert info["fallback_reason"] == "gpu_runtime_fallback"
        # The mux still carries the tags — the deliverable is tagged.
        assert calls["mux_cmds"] == [_expected_mux_cmd(ctx, _SDR_COLOR_ARGS)]


# ── 4. Mux helper: color_args plumbing ────────────────────


class TestBuildMuxCmdColorArgs:
    def test_no_color_args_keeps_v141_shape(self, tmp_path):
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

    def test_color_args_appended_after_audio_codec(self, tmp_path):
        import movie_narrator.pipeline.render as render_mod

        cmd = render_mod._build_mux_cmd(
            "FF",
            Path("v.mp4"),
            Path("a.mp3"),
            Path("out.mp4.part"),
            audio_codec="aac",
            faststart=True,
            target_format="mp4",
            color_args=_HDR10_COLOR_ARGS,
        )
        # Color args land in the options block, after the codecs, before
        # faststart/format/output.
        assert cmd[cmd.index("-c:v") + 1] == "copy"
        assert cmd[cmd.index("-c:a") + 1] == "aac"  # audio untouched
        assert cmd[cmd.index("-c:a") + 2 : cmd.index("-c:a") + 8] == _HDR10_COLOR_ARGS
        assert cmd.index("-movflags") > cmd.index("-colorspace")

    def test_color_args_and_muxed_subtitle_coexist(self, tmp_path):
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
            color_args=_SDR_COLOR_ARGS,
        )
        assert cmd.index("2:s:0") > cmd.index("1:a:0")
        assert cmd[cmd.index("-c:s") + 1] == "mov_text"
        assert cmd[cmd.index("-metadata:s:s:0") + 1] == "language=zho"
        assert cmd[cmd.index("-colorspace") + 1] == "bt709"
        assert cmd[-1] == "out.mp4.part"


# ── 5. Admission heuristic: 10-bit factor ─────────────────


class TestAdmissionBitDepthFactor:
    def test_8bit_baseline_unchanged(self):
        assert estimate_temp_space_bytes((1920, 1080), 60.0) == 2 * GB
        assert estimate_temp_space_bytes((1920, 1080), 60.0, bit_depth=8) == 2 * GB

    def test_10bit_applies_documented_factor(self):
        assert estimate_temp_space_bytes((1920, 1080), 60.0, bit_depth=10) == int(2 * GB * 1.25)
        # 4K / 60 s 10-bit: 2 GB × area ratio 4 × duration 1 × 1.25 = 10 GB.
        assert estimate_temp_space_bytes((3840, 2160), 60.0, bit_depth=10) == int(10 * GB)

    def test_10bit_clamps_still_apply(self):
        # 8K / 1 h 10-bit would be 2 GB × 16 × 60 × 1.25 = 2400 GB — capped.
        assert estimate_temp_space_bytes((7680, 4320), 3600.0, bit_depth=10) == int(50 * GB)
        # Tiny renders still hit the 0.5 GB floor.
        assert estimate_temp_space_bytes((320, 180), 1.0, bit_depth=10) == int(0.5 * GB)

    def test_admission_uses_bit_depth_and_reports_factor(self, tmp_path, monkeypatch):
        from movie_narrator.utils.resources import check_render_admission

        monkeypatch.setattr(
            "movie_narrator.utils.resources.shutil.disk_usage",
            lambda _p: MagicMock(free=int(9 * GB)),
        )
        check = check_render_admission(
            resolution=(3840, 2160),  # needs ~10 GB at 10-bit
            duration_estimate_s=60.0,
            temp_dir=tmp_path,
            bit_depth=10,
        )
        assert check.ok is False
        assert any("×1.25 10-bit factor" in r for r in check.reasons)

        monkeypatch.setattr(
            "movie_narrator.utils.resources.shutil.disk_usage",
            lambda _p: MagicMock(free=int(20 * GB)),
        )
        check = check_render_admission(
            resolution=(3840, 2160),
            duration_estimate_s=60.0,
            temp_dir=tmp_path,
            bit_depth=10,
        )
        assert check.ok is True

    def test_admission_8bit_message_has_no_factor_note(self, tmp_path, monkeypatch):
        from movie_narrator.utils.resources import check_render_admission

        monkeypatch.setattr(
            "movie_narrator.utils.resources.shutil.disk_usage",
            lambda _p: MagicMock(free=int(1 * GB)),
        )
        check = check_render_admission(
            resolution=(1920, 1080),
            duration_estimate_s=60.0,
            temp_dir=tmp_path,
        )
        assert check.ok is False
        assert not any("10-bit factor" in r for r in check.reasons)


# ── 6. metadata.json export path ──────────────────────────


class TestMetadataExportSurface:
    def test_render_pixel_exported_to_metadata_json(self, tmp_path):
        """``render_pixel`` is surfaced in metadata.json via the existing
        build_metadata_json export path."""
        from movie_narrator.utils.metadata_export import build_metadata_json

        ctx = Context(
            movie_name="m",
            output_dir=str(tmp_path),
            services=Services(console=MagicMock()),
        )
        meta = build_metadata_json(ctx)
        assert meta["render_pixel"] is None  # absent plan exports as null

        ctx.metadata["render_pixel"] = {
            "bit_depth": 10,
            "pix_fmt": "yuv420p10le",
            "color_space": "hdr10",
            "color_tags": {"color_primaries": "bt2020", "color_trc": "smpte2084"},
            "encoder_path": "cpu",
        }
        meta = build_metadata_json(ctx)
        assert meta["render_pixel"]["bit_depth"] == 10
        assert meta["render_pixel"]["color_space"] == "hdr10"


# ── 7. Integration: real ffmpeg encode + mux (CI integration job) ──


def _probe_video_stream(ffprobe, path):
    proc = subprocess.run(  # nosec B603 B607 — real ffmpeg integration check
        [ffprobe, "-v", "quiet", "-print_format", "json", "-show_streams", str(path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )
    assert proc.returncode == 0
    streams = [
        s for s in json.loads(proc.stdout).get("streams", []) if s.get("codec_type") == "video"
    ]
    assert len(streams) == 1
    return streams[0]


def _encode_and_mux_pixel_pipeline(ffmpeg, tmp_path, requested_bit_depth, color_space):
    """Encode a 1 s 640x360 clip with the exact argv the render step builds
    (via ``_resolve_pixel_plan``) and run the STAGE-2 copy mux — the shared
    real-ffmpeg fixture for the integration classes below.

    Returns:
        ``(final_path, plan)`` — the muxed deliverable and the pixel plan
        that produced it.
    """
    import movie_narrator.pipeline.render as render_mod

    plan = render_mod._resolve_pixel_plan(requested_bit_depth, color_space)

    video_only = tmp_path / "video_only.mp4"
    encode_cmd = [
        ffmpeg,
        "-y",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=blue:s=640x360:d=1:r=12",
        "-frames:v",
        "12",
        "-c:v",
        "libx264",
        "-preset",
        "ultrafast",  # speed — the argv shape matches the render step
    ] + plan["pixel_args"] + [str(video_only)]
    encode = subprocess.run(  # nosec B603 B607 — real ffmpeg integration check
        encode_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert encode.returncode == 0, encode.stderr

    audio = tmp_path / "audio.m4a"
    make_audio = subprocess.run(  # nosec B603 B607 — real ffmpeg integration check
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
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert make_audio.returncode == 0, make_audio.stderr

    final = tmp_path / "final.mp4"
    mux_cmd = render_mod._build_mux_cmd(
        ffmpeg,
        video_only,
        audio,
        final,
        audio_codec="aac",
        faststart=True,
        target_format="mp4",
        color_args=plan["color_args"],
    )
    mux = subprocess.run(  # nosec B603 B607 — real ffmpeg integration check
        mux_cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert mux.returncode == 0, mux.stderr
    return final, plan


@pytest.mark.integration
class TestPixelPipelineRealFfmpeg:
    """Encode a tiny 640x360 clip with the exact argv the render step
    builds (via the same ``_resolve_pixel_plan`` helper + the STAGE-2
    copy mux) and verify bit depth + color tags through ffprobe."""

    @pytest.fixture(autouse=True)
    def _require_ffmpeg(self):
        from movie_narrator.utils.ffmpeg_bin import ffmpeg_bin

        self.ffmpeg = ffmpeg_bin()
        self.ffprobe = shutil.which("ffprobe")
        has_ffmpeg = Path(self.ffmpeg).is_file() or shutil.which("ffmpeg") is not None
        if not has_ffmpeg or self.ffprobe is None:
            pytest.skip("real ffmpeg/ffprobe unavailable")

    def _render_pixel_pipeline(self, tmp_path, requested_bit_depth, color_space):
        """Encode the video-only intermediate (MoviePy-style argv) and run
        the STAGE-2 copy mux exactly like render_video does."""
        final, _plan = _encode_and_mux_pixel_pipeline(
            self.ffmpeg, tmp_path, requested_bit_depth, color_space
        )
        return final

    def test_default_sdr_deliverable_is_8bit_bt709(self, tmp_path):
        final = self._render_pixel_pipeline(tmp_path, 8, "sdr")
        stream = _probe_video_stream(self.ffprobe, final)
        assert stream["pix_fmt"] == "yuv420p"
        assert stream.get("color_primaries") == "bt709"
        assert stream.get("color_transfer") == "bt709"
        assert stream.get("color_space") == "bt709"

    def test_10bit_sdr_deliverable_yuv420p10le(self, tmp_path):
        final = self._render_pixel_pipeline(tmp_path, 10, "sdr")
        stream = _probe_video_stream(self.ffprobe, final)
        assert stream["pix_fmt"] == "yuv420p10le"
        assert stream.get("profile") == "High 10"
        assert stream.get("color_transfer") == "bt709"

    def test_hdr10_deliverable_smpte2084_bt2020(self, tmp_path):
        final = self._render_pixel_pipeline(tmp_path, 10, "hdr10")
        stream = _probe_video_stream(self.ffprobe, final)
        assert stream["pix_fmt"] == "yuv420p10le"
        assert stream.get("color_primaries") == "bt2020"
        assert stream.get("color_transfer") == "smpte2084"
        assert stream.get("color_space") == "bt2020nc"


@pytest.mark.integration
class TestPixelQARealFfmpeg:
    """v1.5.0: the expectations-aware video QA loop over real encodes —
    the real-ffmpeg deliverable is run through ``evaluate_video_quality``
    with the expectations derived from its own ``render_pixel`` plan
    (Feature 2's checks validating Feature 1's output).

    The 640x360 fixture clip sits below the 720p floor, so these QA calls
    lower the resolution minimums to isolate the pixel checks — the floor
    itself is unit-covered in tests/test_v120_vertical_qa.py and
    tests/test_v150_qa.py."""

    @pytest.fixture(autouse=True)
    def _require_ffmpeg(self):
        from movie_narrator.utils.ffmpeg_bin import ffmpeg_bin

        self.ffmpeg = ffmpeg_bin()
        self.ffprobe = shutil.which("ffprobe")
        has_ffmpeg = Path(self.ffmpeg).is_file() or shutil.which("ffmpeg") is not None
        if not has_ffmpeg or self.ffprobe is None:
            pytest.skip("real ffmpeg/ffprobe unavailable")

    def _qa_report(self, tmp_path, requested_bit_depth, color_space):
        final, plan = _encode_and_mux_pixel_pipeline(
            self.ffmpeg, tmp_path, requested_bit_depth, color_space
        )
        metrics = probe_video_encoding(str(final))
        # The synthetic 1 s / 12 fps clip trips the bitrate + fps floors and
        # sits below the 720p floor — all relaxed here to isolate the pixel
        # expectations, which are what's under test (the floors themselves
        # are unit-covered in tests/test_v120_vertical_qa.py +
        # tests/test_v150_qa.py).
        return check_encoding_quality(
            metrics,
            min_width=320,
            min_height=180,
            min_bitrate_kbps=0,
            min_fps=0.0,
            max_fps=1000.0,
            expected_pixel=plan,
        )

    def test_qa_accepts_default_sdr_deliverable(self, tmp_path):
        report = self._qa_report(tmp_path, 8, "sdr")
        assert report.ok is True, report.issues
        assert report.metrics.pixel_format == "yuv420p"
        assert report.metrics.color_transfer == "bt709"

    def test_qa_accepts_10bit_sdr_deliverable(self, tmp_path):
        report = self._qa_report(tmp_path, 10, "sdr")
        assert report.ok is True, report.issues
        assert report.metrics.pixel_format == "yuv420p10le"
        assert report.metrics.color_transfer == "bt709"

    def test_qa_accepts_hdr10_deliverable(self, tmp_path):
        report = self._qa_report(tmp_path, 10, "hdr10")
        assert report.ok is True, report.issues
        assert report.metrics.pixel_format == "yuv420p10le"
        assert report.metrics.color_transfer == "smpte2084"
        assert report.metrics.color_primaries == "bt2020"
        assert report.metrics.color_space == "bt2020nc"

    def test_qa_flags_8bit_output_against_10bit_plan(self, tmp_path):
        """A real 8-bit deliverable probed against a 10-bit plan produces
        the pix_fmt mismatch finding (yuv420p10le expected)."""
        import movie_narrator.pipeline.render as render_mod

        final, _plan = _encode_and_mux_pixel_pipeline(self.ffmpeg, tmp_path, 8, "sdr")
        ten_bit_plan = render_mod._resolve_pixel_plan(10, "sdr")
        metrics = probe_video_encoding(str(final))
        report = check_encoding_quality(
            metrics,
            min_width=320,
            min_height=180,
            min_bitrate_kbps=0,
            min_fps=0.0,
            max_fps=1000.0,
            expected_pixel=ten_bit_plan,
        )
        assert report.ok is False
        assert any("does not match the render plan" in i for i in report.issues)
        assert any("yuv420p10le" in i for i in report.issues)
