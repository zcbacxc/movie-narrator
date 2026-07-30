# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the v0.7.1 text animation utility (utils/text_anim.py).

Uses MagicMock clips that mimic the MoviePy ImageClip surface
(``duration``, ``with_effects``, ``with_position``, ``with_start``,
``w``, ``h``) so the suite runs without ffmpeg or real image data.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from movie_narrator.utils.text_anim import (
    apply_text_animation,
    get_animation_duration,
)


def _make_clip(duration: float = 2.0, w: int = 1280, h: int = 720) -> MagicMock:
    """Build a MagicMock that quacks like a MoviePy ImageClip.

    Numeric ``duration`` / ``w`` / ``h`` are set explicitly so the slide
    math does not trip over non-numeric MagicMock attributes. ``start``
    mimics a subtitle clip that already had ``with_start`` applied.
    """
    clip = MagicMock()
    clip.duration = duration
    clip.w = w
    clip.h = h
    clip.start = 0.0
    # with_position returns a fresh mock that also exposes with_effects /
    # with_start for the slide code path (each returns itself so the
    # builder chain resolves to a single object).
    slid = MagicMock()
    slid.with_effects = MagicMock(return_value=slid)
    slid.with_start = MagicMock(return_value=slid)
    clip.with_position = MagicMock(return_value=slid)
    return clip


# ── get_animation_duration (clamping) ─────────────────────


class TestGetAnimationDuration:
    def test_returns_requested_when_within_cap(self):
        # 0.3s requested, clip is 2.0s → cap is 0.5s, so 0.3 passes through.
        assert get_animation_duration(2.0, 0.3) == pytest.approx(0.3)

    def test_clamps_to_one_quarter_of_clip(self):
        # 0.3s requested on a 1.0s clip → cap is 1/4 = 0.25s.
        assert get_animation_duration(1.0, 0.3) == pytest.approx(0.25)

    def test_zero_clip_duration_returns_zero(self):
        assert get_animation_duration(0.0, 0.3) == 0.0

    def test_negative_clip_duration_returns_zero(self):
        assert get_animation_duration(-1.0, 0.3) == 0.0

    def test_non_positive_requested_returns_zero(self):
        assert get_animation_duration(2.0, 0.0) == 0.0
        assert get_animation_duration(2.0, -0.3) == 0.0

    def test_default_requested_is_three_tenths(self):
        assert get_animation_duration(2.0) == pytest.approx(0.3)


# ── apply_text_animation: "none" and unknown types ────────


class TestApplyTextAnimationNoOp:
    def test_none_type_returns_clip_unchanged(self):
        clip = _make_clip()
        result = apply_text_animation(clip, "none", 0.3)
        assert result is clip

    def test_empty_type_returns_clip_unchanged(self):
        clip = _make_clip()
        result = apply_text_animation(clip, "", 0.3)
        assert result is clip

    def test_unknown_type_does_not_crash(self):
        """An unrecognised animation type degrades gracefully to no-op."""
        clip = _make_clip()
        result = apply_text_animation(clip, "bogus", 0.3)
        assert result is clip

    def test_zero_duration_returns_clip_unchanged(self):
        clip = _make_clip(duration=0.0)
        result = apply_text_animation(clip, "fade", 0.3)
        assert result is clip


# ── apply_text_animation: fade ────────────────────────────


class TestApplyTextAnimationFade:
    def test_fade_applies_effects(self):
        clip = _make_clip(duration=2.0)
        result = apply_text_animation(clip, "fade", 0.3)
        # with_effects must have been called with a non-empty effect list.
        assert clip.with_effects.called
        effects = clip.with_effects.call_args.args[0]
        assert isinstance(effects, list)
        assert len(effects) == 1  # FadeIn entrance only
        # The returned clip is the with_effects result, not the original.
        assert result is clip.with_effects.return_value

    def test_fade_duration_is_clamped(self):
        """A long requested duration on a short clip is clamped to 1/4."""
        clip = _make_clip(duration=1.0)
        apply_text_animation(clip, "fade", 1.0)
        assert clip.with_effects.called
        effects = clip.with_effects.call_args.args[0]
        fx = effects[0]
        assert fx.duration <= 0.25 + 1e-9


# ── apply_text_animation: slide ───────────────────────────


class TestApplyTextAnimationSlide:
    def test_slide_up_uses_with_position(self):
        clip = _make_clip(duration=2.0, w=1280, h=720)
        result = apply_text_animation(clip, "slide_up", 0.3)
        # The slide path animates position via with_position.
        assert clip.with_position.called
        # Result is the slid mock (the with_effects return value chain).
        assert result is not clip

    def test_slide_left_uses_with_position(self):
        clip = _make_clip(duration=2.0, w=1280, h=720)
        result = apply_text_animation(clip, "slide_left", 0.3)
        assert clip.with_position.called
        assert result is not clip

    def test_slide_falls_back_when_no_with_position(self):
        """When a clip lacks with_position, slide degrades to a fade."""
        clip = _make_clip(duration=2.0, w=1280, h=720)
        # Replace with_position with a non-callable so the slide helper
        # cannot animate position and must fall back to a fade.
        clip.with_position = None
        result = apply_text_animation(clip, "slide_up", 0.3)
        # Fade fallback still applies effects via with_effects.
        assert clip.with_effects.called
        effects = clip.with_effects.call_args.args[0]
        assert len(effects) >= 1
        assert result is clip.with_effects.return_value
