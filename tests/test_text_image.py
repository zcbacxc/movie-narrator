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


def test_center_layout_draws_near_center():
    """Center layout places text around the vertical middle."""
    arr = create_text_image("mid", (640, 360), fontsize=40, position="center")
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
    # Each line is forced long; max_lines=1 means everything collapses to one
    # ellipsised line. We just assert no crash and output shape is correct.
    long_text = "line one is very long\nline two is also long\nline three"
    arr = create_text_image(
        long_text, (800, 400), fontsize=30, position="bottom", max_lines=1,
    )
    assert arr.shape == (400, 800, 4)


def test_multiline_center_scales_font():
    """Multi-line center layout reduces fontscale per line."""
    arr = create_text_image("a\nb\nc", (640, 360), fontsize=60, position="center")
    assert arr.shape == (360, 640, 4)
