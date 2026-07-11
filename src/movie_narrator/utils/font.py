import platform
from pathlib import Path

from PIL import ImageFont


def get_font(fontsize: int = 100) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    project_font = (
        Path(__file__).parent.parent.parent.parent
        / "assets"
        / "fonts"
        / "NotoSansSC-Regular.otf"
    )
    if project_font.exists():
        try:
            return ImageFont.truetype(str(project_font), fontsize)
        except OSError:
            pass

    system_fonts = {
        "Linux": [
            "/usr/share/fonts/truetype/noto/NotoSansSC-Regular.otf",
            "/usr/share/fonts/noto-cjk/NotoSansSC-Regular.otf",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
        ],
        "Darwin": [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/STHeiti Light.ttc",
            "/Library/Fonts/Arial Unicode.ttf",
        ],
        "Windows": [
            "C:/Windows/Fonts/msyh.ttc",
            "C:/Windows/Fonts/msyhbd.ttc",
            "C:/Windows/Fonts/simhei.ttf",
            "C:/Windows/Fonts/simsun.ttc",
        ],
    }

    for path in system_fonts.get(platform.system(), []):
        p = Path(path)
        if p.exists():
            try:
                return ImageFont.truetype(str(p), fontsize)
            except OSError:
                continue

    raise RuntimeError(
        "Cannot find Chinese font!\n"
        "Download NotoSansSC into assets/fonts/NotoSansSC-Regular.otf\n"
        "or install system font:\n"
        "  Ubuntu: sudo apt install fonts-noto-cjk\n"
        "  macOS:  brew install font-noto-sans-cjk-sc\n"
        "  Windows: download NotoSansSC\n"
        "Project: https://github.com/googlefonts/noto-cjk"
    )
