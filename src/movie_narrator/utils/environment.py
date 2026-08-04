# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Environment information collection."""

import platform
import shutil
import sys


def collect_environment() -> dict:
    """Collect environment and dependency information."""
    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.platform(),
        "ffmpeg": ffmpeg or "",
    }
