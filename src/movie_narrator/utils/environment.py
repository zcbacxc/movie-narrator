import platform
import shutil
import sys


def collect_environment() -> dict:
    ffmpeg = shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")
    return {
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "platform": platform.platform(),
        "ffmpeg": ffmpeg or "",
    }
