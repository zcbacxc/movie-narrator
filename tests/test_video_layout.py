"""Tests for utils/video_layout.py — pure geometry helper."""

import pytest

from movie_narrator.utils.video_layout import LayoutBox, compute_fit_box


def test_cover_same_aspect():
    """Source and canvas same aspect → no crop, output = canvas."""
    box = compute_fit_box((1920, 1080), (1920, 1080), mode="cover")
    assert box.crop_x == 0 and box.crop_y == 0
    assert box.crop_w == 1920 and box.crop_h == 1080
    assert box.out_w == 1920 and box.out_h == 1080


def test_cover_wide_source_crops_width():
    """Source wider than canvas → crop width, keep full height."""
    # 1920x1080 source onto 1080x1080 canvas (square).
    box = compute_fit_box((1920, 1080), (1080, 1080), mode="cover")
    assert box.crop_h == 1080          # full height retained
    assert box.crop_w == 1080          # width cropped to match canvas aspect
    assert box.crop_x == (1920 - 1080) // 2  # center-cropped
    assert box.out_w == 1080 and box.out_h == 1080


def test_cover_tall_source_crops_height():
    """Source taller than canvas → crop height, keep full width."""
    # 1080x1920 source onto 1080x1080 canvas.
    box = compute_fit_box((1080, 1920), (1080, 1080), mode="cover")
    assert box.crop_w == 1080          # full width retained
    assert box.crop_h == 1080          # height cropped
    assert box.crop_y == (1920 - 1080) // 2
    assert box.out_w == 1080 and box.out_h == 1080


def test_contain_fits_inside_letterbox():
    """Contain mode scales down, no crop, output <= canvas."""
    box = compute_fit_box((1920, 1080), (960, 540), mode="contain")
    assert box.crop_w == 1920 and box.crop_h == 1080  # no crop
    # Scale 0.5 → 960x540 (fits exactly, same aspect).
    assert box.out_w == 960 and box.out_h == 540


def test_contain_letterbox_when_aspect_mismatch():
    """Contain with mismatched aspect → output smaller on one axis."""
    # 1920x1080 (16:9) into 1080x1080 (1:1) → scale by min(1080/1920, 1080/1080)
    box = compute_fit_box((1920, 1080), (1080, 1080), mode="contain")
    assert box.out_w == 1080 and box.out_h == 608  # 1080 * (1080/1920) ≈ 607.5 → 608


def test_invalid_mode_raises():
    with pytest.raises(ValueError, match="mode"):
        compute_fit_box((1920, 1080), (1080, 1080), mode="stretch")


def test_zero_size_raises():
    with pytest.raises(ValueError):
        compute_fit_box((0, 1080), (1080, 1080))


def test_returns_layout_box():
    box = compute_fit_box((1920, 1080), (1280, 720))
    assert isinstance(box, LayoutBox)
