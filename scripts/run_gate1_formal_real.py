# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Formal Gate-1 runner with real LLM/TTS and CPU-only render.

Disables GPU encoder auto-detect (broken NVENC probe on this host)
without setting ``CI=1`` (which would mock the LLM).

Usage (env must already point at a working OpenAI-compatible LLM):

    python scripts/run_gate1_formal_real.py --reps 5
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_SRC = _REPO / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def _disable_gpu_detect() -> None:
    import movie_narrator.utils.gpu_detect as gd
    import movie_narrator.pipeline.render as render_mod

    def _cpu_encoder(_requested=None):
        return "libx264", []

    def _no_gpu(*_a, **_k):
        return None

    gd.resolve_encoder = _cpu_encoder  # type: ignore[assignment]
    if hasattr(gd, "get_encoder_info"):
        gd.get_encoder_info = lambda requested=None: {  # type: ignore[assignment]
            "codec": "libx264",
            "params": [],
            "gpu": False,
        }
    # Render imported names at module load — patch the bound references too.
    render_mod.resolve_encoder = _cpu_encoder  # type: ignore[assignment]
    if hasattr(render_mod, "get_encoder_info"):
        render_mod.get_encoder_info = gd.get_encoder_info  # type: ignore[assignment]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reps", type=int, default=5)
    parser.add_argument("--tiers", default="1,2,3")
    parser.add_argument("--candidates", type=int, default=3)
    parser.add_argument("--duration", type=int, default=8)
    parser.add_argument("--video", default="benchmarks/gate_source.mp4")
    parser.add_argument("--output", default="benchmarks/gate1_formal_real.json")
    parser.add_argument("--tag", default="formal-real")
    args = parser.parse_args(argv)

    if not os.environ.get("MN_LLM_API_KEY"):
        print("ERROR: set MN_LLM_BASE_URL / MN_LLM_MODEL / MN_LLM_API_KEY", file=sys.stderr)
        return 2
    if os.environ.get("CI"):
        print("ERROR: unset CI so the real LLM path is used (CI mocks the script)", file=sys.stderr)
        return 2
    # Mimo TTS needs a Chinese voice id (zh-CN-* is Edge-style).
    os.environ.setdefault("MN_DEFAULT_VOICE", "冰糖")

    _disable_gpu_detect()

    # Import benchmark main after GPU patch.
    sys.path.insert(0, str(_REPO / "scripts"))
    from race_parallel_benchmark import main as bench_main  # type: ignore

    return bench_main(
        [
            "--movie",
            "BenchGate",
            "--style",
            "热血搞笑",
            "--duration",
            str(args.duration),
            "--video",
            args.video,
            "--candidates",
            str(args.candidates),
            "--tiers",
            args.tiers,
            "--reps",
            str(args.reps),
            "--cold-cache",
            "--tag",
            args.tag,
            "--output",
            args.output,
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
