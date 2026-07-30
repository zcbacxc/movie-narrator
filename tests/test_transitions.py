# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Tests for the v0.7.1 scene transition utility (utils/transitions.py).

Uses MagicMock clips that mimic the MoviePy clip surface (``duration``,
``with_effects``, ``with_position``, ``with_start``, ``w``, ``h``) so the
suite runs without touching ffmpeg or real video data.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from movie_narrator.utils.transitions import (
    apply_transition,
    get_transition_duration,
)


def _make_clip(duration: float = 2.0, w: int = 1920, h: int = 1080) -> MagicMock:
    """Build a MagicMock that quacks like a MoviePy clip.

    Numeric ``duration`` / ``w`` / ``h`` are set explicitly so the slide
    math does not trip over non-numeric MagicMock attributes. ``start``
    is left as ``None`` so the slide helper skips re-applying it.
    """
    clip = MagicMock()
    clip.duration = duration
    clip.w = w
    clip.h = h
    clip.start = None
    # with_position returns a fresh mock that also exposes with_effects /
    # with_start for the slide code path.
    slid = MagicMock()
    slid.with_effects = MagicMock(return_value=slid)
    slid.with_start = MagicMock(return_value=slid)
    clip.with_position = MagicMock(return_value=slid)
    return clip


# ── get_transition_duration (clamping) ────────────────────


class TestGetTransitionDuration:
    def test_returns_requested_when_within_cap(self):
        # 0.5s requested, clip is 3.0s → cap is 1.0s, so 0.5 passes through.
        assert get_transition_duration(3.0, 0.5) == pytest.approx(0.5)

    def test_clamps_to_one_third_of_clip(self):
        # 0.5s requested on a 1.0s clip → cap is 1/3 ≈ 0.333s.
        assert get_transition_duration(1.0, 0.5) == pytest.approx(1.0 / 3.0)

    def test_zero_clip_duration_returns_zero(self):
        assert get_transition_duration(0.0, 0.5) == 0.0

    def test_negative_clip_duration_returns_zero(self):
        assert get_transition_duration(-1.0, 0.5) == 0.0

    def test_non_positive_requested_returns_zero(self):
        assert get_transition_duration(3.0, 0.0) == 0.0
        assert get_transition_duration(3.0, -0.5) == 0.0

    def test_default_requested_is_half_second(self):
        assert get_transition_duration(3.0) == pytest.approx(0.5)


# ── apply_transition: "none" and unknown types ────────────


class TestApplyTransitionNoOp:
    def test_none_type_returns_clip_unchanged(self):
        clip = _make_clip()
        result = apply_transition(clip, "none", 0.5)
        assert result is clip

    def test_empty_type_returns_clip_unchanged(self):
        clip = _make_clip()
        result = apply_transition(clip, "", 0.5)
        assert result is clip

    def test_unknown_type_does_not_crash(self):
        """An unrecognised transition type degrades gracefully to no-op."""
        clip = _make_clip()
        result = apply_transition(clip, "bogus", 0.5)
        assert result is clip

    def test_zero_duration_returns_clip_unchanged(self):
        clip = _make_clip(duration=0.0)
        result = apply_transition(clip, "fade", 0.5)
        assert result is clip


# ── apply_transition: fade / dissolve ─────────────────────


class TestApplyTransitionFade:
    def test_fade_applies_effects(self):
        clip = _make_clip(duration=2.0)
        result = apply_transition(clip, "fade", 0.5, position="both")
        # with_effects must have been called with a non-empty effect list.
        assert clip.with_effects.called
        effects = clip.with_effects.call_args.args[0]
        assert isinstance(effects, list)
        assert len(effects) == 2  # FadeIn + FadeOut for position="both"
        # The returned clip is the with_effects result, not the original.
        assert result is clip.with_effects.return_value

    def test_fade_position_in_only(self):
        clip = _make_clip(duration=2.0)
        apply_transition(clip, "fade", 0.5, position="in")
        effects = clip.with_effects.call_args.args[0]
        assert len(effects) == 1  # FadeIn only

    def test_fade_position_out_only(self):
        clip = _make_clip(duration=2.0)
        apply_transition(clip, "fade", 0.5, position="out")
        effects = clip.with_effects.call_args.args[0]
        assert len(effects) == 1  # FadeOut only

    def test_dissolve_applies_effects(self):
        clip = _make_clip(duration=2.0)
        apply_transition(clip, "dissolve", 0.5)
        assert clip.with_effects.called
        effects = clip.with_effects.call_args.args[0]
        assert len(effects) == 2

    def test_fade_duration_is_clamped(self):
        """A long requested duration on a short clip is clamped to 1/3."""
        clip = _make_clip(duration=1.0)
        apply_transition(clip, "fade", 2.0)
        assert clip.with_effects.called
        effects = clip.with_effects.call_args.args[0]
        # Each effect's duration arg should be <= 1/3 of the clip.
        for fx in effects:
            assert fx.duration <= 1.0 / 3.0 + 1e-9


# ── apply_transition: slide ───────────────────────────────


class TestApplyTransitionSlide:
    def test_slide_uses_with_position(self):
        clip = _make_clip(duration=2.0, w=1920, h=1080)
        result = apply_transition(clip, "slide", 0.5)
        # The slide path animates position via with_position.
        assert clip.with_position.called
        # The returned clip is the slid mock (with_effects return value).
        assert result is clip.with_position.return_value.with_effects.return_value

    def test_slide_falls_back_when_no_with_position(self):
        """When a clip lacks with_position, slide degrades to a fade."""
        clip = _make_clip(duration=2.0, w=1920, h=1080)
        # Replace with_position with a non-callable so the slide helper
        # cannot animate position and must fall back to a fade.
        clip.with_position = None
        result = apply_transition(clip, "slide", 0.5)
        # Fade fallback still applies effects via with_effects.
        assert clip.with_effects.called
        effects = clip.with_effects.call_args.args[0]
        assert len(effects) >= 1
        assert result is clip.with_effects.return_value
