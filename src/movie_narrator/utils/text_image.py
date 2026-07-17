"""Text-to-image rendering utility for subtitle overlays.

Extracted from ``render.py`` so the pure PIL text rendering logic is
independently testable and reusable without importing MoviePy.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from .font import get_font


def _wrap_line(text: str, draw: ImageDraw.ImageDraw, font, max_width: int) -> list[str]:
    """Wrap a single line to ``max_width`` pixels using font metrics.

    Greedy word-wrap (splits on spaces). For CJK text with no spaces,
    each character becomes its own break candidate so long paragraphs
    still wrap correctly.
    """
    if not text:
        return [""]
    # Try space-based wrapping first; fall back to char-based for CJK.
    has_spaces = " " in text
    tokens = text.split(" ") if has_spaces else list(text)

    lines: list[str] = []
    cur = ""
    for tok in tokens:
        sep = " " if has_spaces else ""
        candidate = f"{cur}{sep}{tok}" if cur else tok
        bbox = draw.textbbox((0, 0), candidate, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width or not cur:
            cur = candidate
        else:
            lines.append(cur)
            cur = tok
    if cur:
        lines.append(cur)
    return lines or [""]


def create_text_image(
    text: str,
    size: tuple,
    fontsize: int = 100,
    *,
    position: str = "center",
    max_width_ratio: float = 0.9,
    bottom_margin_ratio: float = 0.08,
    max_lines: int = 2,
):
    """Render text to a transparent RGBA image, supporting multi-line.

    Multi-line behavior (spec §7.3):
    - Lines are split on ``\\n``.
    - Fontscale per line: ``1.0 - 0.1 * (line_count - 1)``, clamped to ``[0.6, 1.0]``.
    - Lines are stacked vertically with a small spacing proportional to fontsize.

    ``position="center"`` (default, backward compatible): vertically center
    the text block — original behaviour for text-only segments.

    ``position="bottom"``: wrap text to ``max_width_ratio * width``, cap at
    ``max_lines`` (overflowing lines are truncated with an ellipsis), and
    draw the block near the bottom with ``bottom_margin_ratio * height``
    padding. Used for publishable recaps where subtitles must sit under
    the footage without obscuring the action.

    Returns a ``numpy.ndarray`` (RGBA) suitable for ``ImageClip``.
    """
    import numpy as np  # local import — only needed for return type

    if position == "bottom":
        return _render_bottom(
            text, size, fontsize,
            max_width_ratio=max_width_ratio,
            bottom_margin_ratio=bottom_margin_ratio,
            max_lines=max_lines,
        )

    # ── Center layout (original behaviour) ──────────────────────
    lines = text.split("\n")
    line_count = len(lines)
    scale = max(0.6, min(1.0, 1.0 - 0.1 * (line_count - 1)))
    eff_fontsize = max(1, int(round(fontsize * scale)))

    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = get_font(eff_fontsize)

    line_spacing = max(2, int(round(eff_fontsize * 0.15)))
    line_metrics = []
    total_h = 0
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        line_metrics.append((w, h))
        total_h += h
    total_h += line_spacing * (line_count - 1)

    y = (size[1] - total_h) // 2
    for line, (w, h) in zip(lines, line_metrics):
        x = (size[0] - w) // 2
        draw.text(
            (x, y), line, fill=(255, 255, 255, 255), font=font,
            stroke_width=2, stroke_fill=(0, 0, 0, 255),
        )
        y += h + line_spacing
    return np.array(img)


def _render_bottom(
    text: str,
    size: tuple,
    fontsize: int,
    *,
    max_width_ratio: float,
    bottom_margin_ratio: float,
    max_lines: int,
):
    """Render a publishable-quality bottom subtitle.

    Layout (spec §7.3, refined):
    - Wrap text to ``max_width_ratio * width`` using font metrics.
    - Cap at ``max_lines`` (overflow truncates with an ellipsis).
    - Anchor text block to the bottom with ``bottom_margin_ratio * height``.
    - Render each line with **white fill + 4 px black stroke** so it stays
      legible on bright footage.
    - Draw a **semi-transparent black backdrop bar** behind the entire text
      block (matches mainstream recap style: 65% alpha, 12 px vertical
      padding) so the text reads cleanly on any frame.

    Returns a full-canvas ``numpy.ndarray`` (RGBA) suitable for ``ImageClip``.
    The non-text region is fully transparent (alpha=0), so MoviePy's
    position math (``with_position((center, bottom))``) places only the
    text band at the bottom — the source footage above remains visible.
    """
    import numpy as np

    width, height = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = get_font(fontsize)

    max_width = int(width * max_width_ratio)

    # Pre-split on explicit newlines (bilingual), then wrap each piece.
    raw_pieces = text.split("\n")
    wrapped: list[str] = []
    for piece in raw_pieces:
        wrapped.extend(_wrap_line(piece, draw, font, max_width))

    # Cap total lines: keep the first ``max_lines``, ellipsis on the cut line.
    if len(wrapped) > max_lines:
        kept = wrapped[:max_lines]
        last = kept[-1]
        if last:
            kept[-1] = last[: max(0, len(last) - 1)] + "…"
        wrapped = kept

    if not wrapped:
        return np.array(img)

    line_spacing = max(2, int(round(fontsize * 0.15)))
    line_metrics: list[tuple[int, int]] = []
    total_h = 0
    for line in wrapped:
        bbox = draw.textbbox((0, 0), line, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        line_metrics.append((w, h))
        total_h += h
    total_h += line_spacing * (len(wrapped) - 1)

    bottom_margin = int(height * bottom_margin_ratio)
    # Stack the block so the last line's bottom edge sits at
    # ``height - bottom_margin``.
    block_top = height - bottom_margin - total_h

    # Compute bounding box enclosing all line metrics for the backdrop bar.
    widest = max(m[0] for m in line_metrics)
    bar_left = (width - widest) // 2 - 16   # 16 px horizontal padding
    bar_right = bar_left + widest + 32
    bar_top = max(0, block_top - 12)        # 12 px vertical padding above first line
    bar_bottom = height - bottom_margin + 12
    # Clamp bar inside frame, then draw semi-transparent black backdrop.
    bar_left = max(0, bar_left)
    bar_right = min(width, bar_right)
    draw.rectangle(
        [(bar_left, bar_top), (bar_right, bar_bottom)],
        fill=(0, 0, 0, 165),  # ~65% black
    )

    # Draw each line white with a 4 px black stroke for readability on bright
    # footage. Stroke sits behind fill in PIL's ordering.
    y = block_top
    for line, (w, _h) in zip(wrapped, line_metrics):
        x = (width - w) // 2
        draw.text(
            (x, y), line, fill=(255, 255, 255, 255), font=font,
            stroke_width=4, stroke_fill=(0, 0, 0, 255),
        )
        # advance by font height + spacing (use bbox ascent reliably)
        bbox = draw.textbbox((0, 0), line, font=font)
        y += (bbox[3] - bbox[1]) + line_spacing

    return np.array(img)
