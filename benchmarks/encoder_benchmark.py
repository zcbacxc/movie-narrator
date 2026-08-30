#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Encoder benchmark — libx264 vs detected GPU H.264 encoders (v1.3.2).

Generates a short synthetic clip (ffmpeg ``testsrc2`` + ``sine`` lavfi
sources — no sample media needed), then encodes it once per encoder and
measures wall time, output size and encode fps. Uses ONLY the stdlib and
the resolved ffmpeg binary — no moviepy import, no ``movie_narrator``
pipeline machinery beyond ``utils.ffmpeg_bin`` / ``utils.gpu_detect``.

Encoders benchmarked:

- ``libx264`` (software baseline, CRF 20 / preset medium), plus
- every GPU encoder detected via :func:`utils.gpu_detect.detect_gpu_encoder`
  (its public API; CI-skip flag semantics respected — in CI the
  detection returns ``None`` and only libx264 is benchmarked). GPU
  encoders run with the exact recommended params the render pipeline
  would use (:func:`utils.gpu_detect.resolve_encoder`).

Usage::

    python benchmarks/encoder_benchmark.py                # table only
    python benchmarks/encoder_benchmark.py --out r.json   # also write JSON
    python benchmarks/encoder_benchmark.py --duration 8 --encoders libx264,nvenc

The JSON report (``schema_version`` 1) embeds environment info (ffmpeg
binary path, platform, GPU detection result + fallback_reason) so runs
can be compared across machines. ``mn benchmark`` (v1.4.2) is a thin
Typer wrapper over this module and accepts the same options.

The module is import-safe: nothing touches ffmpeg at import time; all
subprocess calls go through :func:`run_command` (monkeypatchable) and
argparse only runs under ``if __name__ == "__main__":``.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional

# Allow running from a source checkout without installing the package.
_SRC = Path(__file__).resolve().parents[1] / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from movie_narrator.utils.ffmpeg_bin import ffmpeg_bin  # noqa: E402
from movie_narrator.utils.gpu_detect import (  # noqa: E402
    detect_gpu_encoder,
    get_encoder_info,
    resolve_encoder,
)

#: Report schema version (bump on incompatible report changes).
SCHEMA_VERSION = 1

#: Synthetic clip parameters — big enough to measure, fast enough for CI.
CLIP_WIDTH = 1920
CLIP_HEIGHT = 1080
CLIP_DURATION_S = 5
CLIP_FPS = 30

#: libx264 quality settings — the software baseline the GPU encoders are
#: compared against (visually comparable quality tier: CRF 20 ≈ CQ 20).
LIBX264_PARAMS = ["-crf", "20", "-preset", "medium"]

#: Canonical GPU codec name -> resolve_encoder() hint (mirrors the
#: private mapping in utils.gpu_detect; stable public codec names).
_CODEC_TO_HINT = {
    "h264_nvenc": "nvenc",
    "h264_vaapi": "vaapi",
    "h264_videotoolbox": "videotoolbox",
}

_SUBPROCESS_TIMEOUT_S = 300

_FPS_RE = re.compile(r"fps=\s*([\d.]+)")


# ── Command builders (pure, easily testable) ─────────────


def build_generate_command(
    ffmpeg: str, clip_path: Path, duration_s: int = CLIP_DURATION_S
) -> list[str]:
    """Build the ffmpeg command that renders the synthetic test clip."""
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={CLIP_WIDTH}x{CLIP_HEIGHT}:rate={CLIP_FPS}:duration={duration_s}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=1000:duration={duration_s}",
        "-c:v",
        "libx264",
        "-crf",
        "23",
        "-preset",
        "veryfast",
        "-c:a",
        "aac",
        "-shortest",
        str(clip_path),
    ]


def build_encode_command(
    ffmpeg: str,
    clip_path: Path,
    out_path: Path,
    codec: str,
    extra_params: list[str],
) -> list[str]:
    """Build the ffmpeg command that encodes the clip with *codec*."""
    return [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-i",
        str(clip_path),
        "-c:v",
        codec,
        *extra_params,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(out_path),
    ]


# ── Helpers ───────────────────────────────────────────────


def run_command(cmd: list[str], timeout: float = _SUBPROCESS_TIMEOUT_S) -> subprocess.CompletedProcess:
    """Run a subprocess and capture output (isolated for tests to patch)."""
    return subprocess.run(  # nosec B603 B607  # benchmark drives explicit binaries
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def parse_encode_fps(stderr: str) -> Optional[float]:
    """Extract the last ``fps=`` figure from ffmpeg's progress stderr."""
    matches = _FPS_RE.findall(stderr)
    if not matches:
        return None
    try:
        return float(matches[-1])
    except ValueError:
        return None


def _tail(text: str, lines: int = 5) -> str:
    """Return the last non-empty lines of captured output."""
    kept = [ln for ln in (text or "").splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


def _resolve_ffmpeg_or_none() -> Optional[str]:
    """Return a concrete ffmpeg path, or ``None`` when none is usable.

    Mirrors the gpu_detect convention: the bare ``"ffmpeg"`` fallback from
    :func:`ffmpeg_bin` means no concrete binary could be resolved — it
    only counts when it is actually importable from PATH.
    """
    resolved = ffmpeg_bin()
    if resolved and resolved != "ffmpeg":
        return resolved
    if resolved == "ffmpeg" and shutil_which("ffmpeg"):
        return resolved
    return None


def shutil_which(name: str) -> Optional[str]:
    """Thin os-level wrapper (isolated so tests can patch it)."""
    import shutil

    return shutil.which(name)


# ── Benchmark core ────────────────────────────────────────


def benchmark_encoder(
    *,
    ffmpeg: str,
    clip_path: Path,
    work_dir: Path,
    label: str,
    codec: str,
    extra_params: list[str],
    duration_s: int = CLIP_DURATION_S,
) -> dict[str, Any]:
    """Encode the clip once with *codec* and return a result record."""
    out_path = work_dir / f"out_{label}.mp4"
    cmd = build_encode_command(ffmpeg, clip_path, out_path, codec, extra_params)
    record: dict[str, Any] = {
        "label": label,
        "codec": codec,
        "params": list(extra_params),
        "command": cmd,
        "status": "ok",
        "wall_time_s": None,
        "output_bytes": None,
        "encode_fps": None,
        "stderr_tail": "",
    }
    start = time.perf_counter()
    try:
        proc = run_command(cmd)
    except (OSError, subprocess.SubprocessError) as exc:
        record["status"] = "failed"
        record["stderr_tail"] = str(exc)
        return record
    record["wall_time_s"] = round(time.perf_counter() - start, 3)
    record["stderr_tail"] = _tail(proc.stderr)
    if proc.returncode != 0 or not out_path.is_file():
        record["status"] = "failed"
        return record
    record["output_bytes"] = out_path.stat().st_size
    # Prefer ffmpeg's own fps accounting; fall back to frames/wall-time.
    fps = parse_encode_fps(proc.stderr)
    if fps is None and record["wall_time_s"]:
        frames = duration_s * CLIP_FPS
        fps = round(frames / record["wall_time_s"], 2)
    record["encode_fps"] = fps
    return record


def _candidate_encoders() -> list[tuple[str, str, list[str]]]:
    """libx264 baseline + every GPU encoder the shared detector reports.

    Empty GPU list in CI (its skip semantics). Pure detection + command
    assembly — no subprocess here.
    """
    encoders: list[tuple[str, str, list[str]]] = [("libx264", "libx264", list(LIBX264_PARAMS))]
    detected = detect_gpu_encoder()
    if detected and detected in _CODEC_TO_HINT:
        codec, params = resolve_encoder(_CODEC_TO_HINT[detected])
        encoders.append((_CODEC_TO_HINT[detected], codec, list(params)))
    return encoders


def run_benchmark(
    work_dir: Optional[Path] = None,
    *,
    duration_s: Optional[int] = None,
    encoders_filter: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Run the full benchmark and return the JSON-serializable report.

    Never raises for environmental problems: when ffmpeg is absent the
    report carries ``status: "ffmpeg_unavailable"`` with no results; a
    GPU encoder that cannot encode (e.g. VAAPI without a usable device)
    records a per-encoder ``failed`` status instead of aborting.

    ``duration_s`` overrides the synthetic clip length (default:
    :data:`CLIP_DURATION_S`). ``encoders_filter`` restricts the encoders
    under test by label or codec name (e.g. ``["libx264", "nvenc"]``);
    ``None`` keeps auto-detection.
    """
    duration = int(duration_s) if duration_s else CLIP_DURATION_S
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "environment": {
            "ffmpeg_bin": None,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "gpu": get_encoder_info(None),
            "ci": bool(os.environ.get("CI")),
        },
        "clip": {
            "width": CLIP_WIDTH,
            "height": CLIP_HEIGHT,
            "duration_s": duration,
            "fps": CLIP_FPS,
        },
        "status": "ok",
        "results": [],
    }

    ffmpeg = _resolve_ffmpeg_or_none()
    if not ffmpeg:
        report["status"] = "ffmpeg_unavailable"
        return report
    report["environment"]["ffmpeg_bin"] = ffmpeg

    encoders = _candidate_encoders()
    detected = detect_gpu_encoder()
    report["environment"]["gpu_benchmarked"] = detected
    if encoders_filter:
        wanted = {str(e).strip().lower() for e in encoders_filter if str(e).strip()}
        encoders = [e for e in encoders if e[0].lower() in wanted or e[1].lower() in wanted]
        if not encoders:
            report["status"] = "no_encoders_matched"
            return report

    tmp_ctx = tempfile.TemporaryDirectory()  # noqa: SIM115  # cleaned in finally
    try:
        work = Path(tmp_ctx.name) if work_dir is None else Path(work_dir)
        work.mkdir(parents=True, exist_ok=True)
        clip_path = work / "synthetic_clip.mp4"
        gen = run_command(build_generate_command(ffmpeg, clip_path, duration_s=duration))
        if gen.returncode != 0 or not clip_path.is_file():
            report["status"] = "clip_generation_failed"
            report["results"] = []
            report["stderr_tail"] = _tail(gen.stderr)
            return report

        for label, codec, params in encoders:
            report["results"].append(
                benchmark_encoder(
                    ffmpeg=ffmpeg,
                    clip_path=clip_path,
                    work_dir=work,
                    label=label,
                    codec=codec,
                    extra_params=params,
                    duration_s=duration,
                )
            )
    finally:
        tmp_ctx.cleanup()
    return report


# ── Output ────────────────────────────────────────────────


def format_table(report: dict[str, Any]) -> str:
    """Render the human-readable results table."""
    lines: list[str] = []
    env = report.get("environment", {})
    lines.append(f"Encoder benchmark (ffmpeg: {env.get('ffmpeg_bin') or 'unavailable'})")
    gpu = env.get("gpu") or {}
    lines.append(
        f"GPU detection: detected={gpu.get('detected')} "
        f"active={gpu.get('active')} fallback_reason={gpu.get('fallback_reason')}"
    )
    if report.get("status") != "ok":
        lines.append(f"Status: {report['status']} — no measurements.")
        return "\n".join(lines)
    header = f"{'encoder':<14} {'status':<8} {'wall s':>8} {'size MB':>9} {'fps':>8}"
    lines.append(header)
    lines.append("-" * len(header))
    for r in report.get("results", []):
        size_mb = (r.get("output_bytes") or 0) / (1024 * 1024)
        lines.append(
            f"{r['label']:<14} {r['status']:<8} "
            f"{(r.get('wall_time_s') or 0):>8.2f} "
            f"{size_mb:>9.2f} "
            f"{(r.get('encode_fps') if r.get('encode_fps') is not None else 0):>8.1f}"
        )
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point (only invoked under ``if __name__ == "__main__"``)."""
    parser = argparse.ArgumentParser(description="Benchmark ffmpeg encoders")
    parser.add_argument("--out", default=None, help="optional path for the JSON report")
    parser.add_argument(
        "--duration",
        type=int,
        default=None,
        help="synthetic clip duration in seconds (default: %(default)s → module default 5)",
    )
    parser.add_argument(
        "--encoders",
        default=None,
        help=(
            "comma-separated encoder filter by label or codec name "
            "(e.g. 'libx264,nvenc'); default: auto-detect"
        ),
    )
    args = parser.parse_args(argv)

    encoders_filter = (
        [e.strip() for e in args.encoders.split(",") if e.strip()]
        if args.encoders
        else None
    )
    report = run_benchmark(duration_s=args.duration, encoders_filter=encoders_filter)
    print(format_table(report))
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\nJSON report written to {out_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
