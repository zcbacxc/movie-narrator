# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

#!/usr/bin/env python3
"""Ensure built distributions do not bundle an FFmpeg/FFprobe binary (ADR-011).

The project resolves FFmpeg at runtime through `utils/ffmpeg_bin.ffmpeg_bin()`
and never bundles the binary into a distribution. This guard fails the build
if a wheel accidentally ships `ffmpeg`/`ffprobe` (e.g. a stray `.exe`).

Usage: python check_no_ffmpeg_bundle.py [dist_dir]   # default: ./dist
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

BANNED = {"ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe"}


def main() -> int:
    dist = Path(sys.argv[1] if len(sys.argv) > 1 else "dist")
    if not dist.is_dir():
        print(f"No distribution directory at {dist} — nothing to check.")
        return 0

    failed = False
    for wheel in sorted(dist.glob("*.whl")):
        with zipfile.ZipFile(wheel) as zf:
            for name in zf.namelist():
                base = name.rsplit("/", 1)[-1]
                if base in BANNED:
                    print(f"ERROR: FFmpeg binary bundled in {wheel}: {name}")
                    failed = True

    if failed:
        return 1
    print("No FFmpeg binary bundled — OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
