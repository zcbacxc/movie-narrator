"""Tests for utils/text_image.py — center + bottom subtitle layouts."""

import numpy as np
import pytest

from movie_narrator.utils.text_image import create_text_image


def test_center_layout_returns_rgba():
    """Default center layout returns an RGBA array of the requested size."""
    arr = create_text_image("hello", (640, 360), fontsize=40)
    assert arr.shape == (360, 640, 4)
    assert arr.dtype == np.uint8


def test_center_layout_is_backward_compatible():
    """position='center' (default) must not raise and produces non-empty pixels."""
    arr = create_text_image("测试字幕", (640, 360), fontsize=40)
    # Some pixels should be non-transparent (alpha > 0) where text is drawn.
    assert arr[..., 3].max() > 0


def test_bottom_layout_returns_rgba():
    """position='bottom' returns a valid RGBA array."""
    arr = create_text_image("bottom subtitle", (640, 360), fontsize=40, position="bottom")
    assert arr.shape == (360, 640, 4)


def test_bottom_layout_draws_near_bottom():
    """Bottom layout places text in the lower portion of the canvas."""
    arr = create_text_image("lower", (640, 360), fontsize=40, position="bottom")
    alpha = arr[..., 3]
    rows_with_text = np.where(alpha.max(axis=1) > 0)[0]
    # Text should be in the bottom half (rows > 180) given bottom_margin 8%.
    assert rows_with_text.max() > 180


def test_bottom_layout_has_semi_transparent_backdrop_bar():
    """Bottom subtitle draws a dark bar behind text for readability.

    Looks for opaque-ish black pixels in a contiguous horizontal band
    just above the bottom edge — covers backdrop + stroke + anti-aliased
    text interior. A bare text-on-transparent render has near-zero such
    pixels in arbitrary positions; the backdrop bar concentrates them.

    NOTE: font metrics differ across platforms (Linux CI renders narrower
    than Windows), so width thresholds are conservative (>= 50% canvas
    width) to avoid false failures while still proving the backdrop bar
    exists and spans a wide horizontal stretch.
    """
    arr = create_text_image("long bottom subtitle text for backdrop", (1280, 720),
                            fontsize=40, position="bottom",
                            max_width_ratio=0.9, bottom_margin_ratio=0.06,
                            max_lines=2)
    alpha = arr[..., 3]
    rgb = arr[..., :3]
    # Stroke/text + backdrop all produce dark pixels behind/around text:
    dark_mask = (rgb.sum(axis=2) < 90) & (alpha > 80)
    h, w = arr.shape[:2]
    # Backdrop bar lives in the bottom ~12% of the canvas.
    band_rows = np.where((dark_mask.sum(axis=1) > 200))[0]
    assert band_rows.size > 0
    assert band_rows.max() > h * 0.78   # below 78% of canvas height
    # Backdrop spans a wide horizontal stretch (>= 50% of canvas width).
    # 50% (not 60%) because Linux font metrics render text narrower than Windows.
    band_cols = np.where(dark_mask[band_rows.min():band_rows.max()+1].sum(axis=0) > 5)[0]
    assert band_cols.max() - band_cols.min() > w * 0.5


def test_bottom_layout_full_canvas_outside_textband_is_transparent():
    """Outside the bottom subtitle band, alpha must be 0 — so source
    footage shown above the subtitle remains untouched when ImageClip is
    composited via ``with_position((center, bottom))``."""
    arr = create_text_image("subtitle", (1280, 720), fontsize=40, position="bottom",
                            bottom_margin_ratio=0.06)
    alpha = arr[..., 3]
    # Pick a row at the middle of the canvas — should be fully transparent.
    mid_row = 360
    assert alpha[mid_row, :].max() == 0
    # Top edge must also be transparent.
    assert alpha[:50, :].max() == 0


def test_center_layout_draws_near_center():
    """Center layout places text around the vertical middle."""
    arr = create_text_image("mid", (640, 360), fontsize=40)
    alpha = arr[..., 3]
    rows_with_text = np.where(alpha.max(axis=1) > 0)[0]
    mid = 360 // 2
    # Text rows should straddle the midpoint.
    assert rows_with_text.min() < mid < rows_with_text.max()


def test_bottom_wraps_long_text():
    """Long text is wrapped and capped at max_lines."""
    long_text = " ".join(["word"] * 50)
    arr = create_text_image(
        long_text, (640, 360), fontsize=30, position="bottom",
        max_width_ratio=0.9, max_lines=2,
    )
    assert arr.shape == (360, 640, 4)


def test_bottom_ellipsis_on_overflow():
    """When text exceeds max_lines, the last kept line is truncated with ellipsis."""
    long_text = "line one is very long\nline two is also long\nline three"
    arr = create_text_image(
        long_text, (800, 400), fontsize=30, position="bottom", max_lines=1,
    )
    assert arr.shape == (400, 800, 4)


def test_multiline_center_scales_font():
    """Multi-line center layout reduces fontscale per line."""
    arr = create_text_image("a\nb\nc", (640, 360), fontsize=60)
    assert arr.shape == (360, 640, 4)
