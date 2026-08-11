# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Local smoke test for G2 — slideshow-risk & black-frame QA on mock videos.

Generates three mock videos with ffmpeg (no real footage needed):
  1. static.mp4     — solid-color "slideshow" (zero motion)
  2. moving.mp4     — animated bars (real motion)
  3. black_gap.mp4  — normal motion with a long black segment at the end

Then runs ``check_slideshow_risk`` on each and prints the resulting
SlideshowRisk metrics, plus the G2 issues that ``evaluate_deliverable``
would surface with typical thresholds.

Requires ffmpeg + Pillow on PATH/environment. Output goes to
``output/_g2_mock/``.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import imageio_ffmpeg  # noqa: E402

# The system PATH ffmpeg may be a minimal build without lavfi inputs; the
# imageio-ffmpeg bundled binary is a full build that supports lavfi
# (color/testsrc) sources, which we need to synthesize mock videos.
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()

# The detection path (``_extract_luma_frames``) resolves ffmpeg via
# ``shutil.which`` and needs the ``fps`` filter that the minimal system build
# lacks. Put the full imageio-ffmpeg binary on PATH as ``ffmpeg`` so the same
# engine is used for both synthesis and detection.
import shutil  # noqa: E402
import tempfile  # noqa: E402

_FF_DIR = Path(tempfile.mkdtemp(prefix="g2_ffmpeg_"))
_FF_LINK = _FF_DIR / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
shutil.copy2(FFMPEG, _FF_LINK)
os.environ["PATH"] = str(_FF_DIR) + os.pathsep + os.environ.get("PATH", "")

from movie_narrator.utils.deliverable_qa import evaluate_deliverable  # noqa: E402
from movie_narrator.utils.video_qa import check_slideshow_risk  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "output" / "_g2_mock"
OUT.mkdir(parents=True, exist_ok=True)

DUR = 8  # seconds
FPS = 25


def make_static(path: Path) -> None:
    """A single solid color for the whole clip → carousel-like slideshow."""
    subprocess.run(
        [
            FFMPEG, "-y", "-f", "lavfi",
            "-i", "color=c=0x4477AA:s=640x360:d=%d:r=%d" % (DUR, FPS),
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )


def make_moving(path: Path) -> None:
    """Animated rolling bars → genuine motion across frames."""
    subprocess.run(
        [
            FFMPEG, "-y", "-f", "lavfi",
            "-i", "testsrc=duration=%d:size=640x360:rate=%d" % (DUR, FPS),
            "-pix_fmt", "yuv420p", str(path),
        ],
        check=True, capture_output=True,
    )


def make_black_gap(path: Path) -> None:
    """Normal motion (6s testsrc) followed by a 2s black tail."""
    mid = max(1, int(DUR * 0.75))
    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi", "-i", "testsrc=duration=%d:size=640x360:rate=%d" % (mid, FPS),
        "-f", "lavfi", "-i", "color=c=black:s=640x360:d=%d:r=%d" % (DUR - mid, FPS),
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0",
        "-pix_fmt", "yuv420p", str(path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def main() -> int:
    files = {"static": make_static, "moving": make_moving, "black_gap": make_black_gap}
    print(f"Generating mock videos in {OUT}\n")
    for name, maker in files.items():
        p = OUT / f"{name}.mp4"
        maker(p)
        print(f"  {name}.mp4  ({p.stat().st_size} bytes)")

    print("\n=== check_slideshow_risk (raw metrics) ===")
    for name in files:
        p = OUT / f"{name}.mp4"
        r = check_slideshow_risk(str(p))
        d = r.to_dict()
        print(f"\n[{name}] probed={d['probed']} samples={d['samples']} "
              f"risk={d['risk']:.3f} static={d['static_ratio']:.0%} "
              f"black={d['black_ratio']:.0%} avg_motion={d['avg_motion']:.2f}")

    print("\n=== evaluate_deliverable (G2 issues, thresholds risk<=0.5 black<=0.15) ===")
    for name in files:
        p = OUT / f"{name}.mp4"
        rep = evaluate_deliverable(
            str(p),
            expected_duration=DUR,
            max_slideshow_risk=0.5,
            max_black_ratio=0.15,
        )
        g2 = [i.code for i in rep.issues if i.code in ("slideshow_degraded", "excessive_black_frames")]
        print(f"[{name}] ok={rep.ok} G2 issues={g2} "
              f"(risk={rep.metrics.get('slideshow_risk')}, "
              f"black={rep.metrics.get('slideshow_black_ratio')})")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
