"""Text-to-image rendering utility for subtitle overlays.

Extracted from ``render.py`` so the pure PIL text rendering logic is
independently testable and reusable without importing MoviePy.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .font import get_font


def create_text_image(text: str, size: tuple, fontsize: int = 100):
    """Render text to a transparent RGBA image, supporting multi-line.

    Multi-line behavior (spec §7.3):
    - Lines are split on ``\\n``.
    - Fontscale per line: ``1.0 - 0.1 * (line_count - 1)``, clamped to ``[0.6, 1.0]``.
    - Lines are stacked vertically with a small spacing proportional to fontsize.

    Returns a ``numpy.ndarray`` (RGBA) suitable for ``ImageClip``.
    """
    import numpy as np  # local import — only needed for return type

    lines = text.split("\n")
    line_count = len(lines)
    scale = max(0.6, min(1.0, 1.0 - 0.1 * (line_count - 1)))
    eff_fontsize = max(1, int(round(fontsize * scale)))

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = get_font(eff_fontsize)

    # Measure each line, compute total stack height.
    line_spacing = max(2, int(round(eff_fontsize * 0.15)))
    line_metrics = []
    total_h = 0
    max_w = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        line_metrics.append((w, h))
        total_h += h
        max_w = max(max_w, w)
    total_h += line_spacing * (line_count - 1)

    # Stack lines centered.
    y = (size[1] - total_h) // 2
    for line, (w, h) in zip(lines, line_metrics):
        x = (size[0] - w) // 2
        draw.text(
            (x, y), line, fill=(255, 255, 255, 255), font=font,
            stroke_width=2, stroke_fill=(0, 0, 0, 255),
        )
        y += h + line_spacing
    return np.array(img)
