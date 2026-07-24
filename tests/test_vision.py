"""Tests for EP8 VisionCaptioner abstraction.

Verifies the protocol contract, stub implementation, factory dispatch,
and integration behavior (stub labels are flagged as fake for the
match pipeline's fake-caption guard).
"""

from __future__ import annotations

import pytest

from movie_narrator.models import Scene
from movie_narrator.vision import (
    StubVisionCaptioner,
    VisionCaptioner,
    get_vision_captioner,
)


# ── Fixtures ───────────────────────────────────────────────


def _scenes() -> list[Scene]:
    return [
        Scene(index=0, start=0.0, end=5.0),
        Scene(index=1, start=5.0, end=10.0),
        Scene(index=2, start=10.0, end=15.0),
    ]


# ── Protocol / ABC contract ────────────────────────────────


def test_vision_captioner_is_abstract():
    """VisionCaptioner is an ABC — cannot be instantiated directly."""
    with pytest.raises(TypeError):
        VisionCaptioner()


def test_subclass_must_implement_caption_scenes():
    """Subclass without caption_scenes raises TypeError on instantiation."""

    class Incomplete(VisionCaptioner):
        pass

    with pytest.raises(TypeError):
        Incomplete()


# ── StubVisionCaptioner ────────────────────────────────────


class TestStubVisionCaptioner:
    def test_returns_one_caption_per_scene(self):
        """Output list length matches input scenes length."""
        captioner = StubVisionCaptioner()
        scenes = _scenes()
        captions = captioner.caption_scenes(scenes)
        assert len(captions) == len(scenes)

    def test_caption_format_matches_scene_label(self):
        """Stub labels follow the 'scene {i} from {s}s to {e}s' format."""
        captioner = StubVisionCaptioner()
        scenes = _scenes()
        captions = captioner.caption_scenes(scenes)
        assert captions[0] == "scene 0 from 0.0s to 5.0s"
        assert captions[1] == "scene 1 from 5.0s to 10.0s"
        assert captions[2] == "scene 2 from 10.0s to 15.0s"

    def test_works_without_video_path(self):
        """Stub does not require a video_path — video_path=None is fine."""
        captioner = StubVisionCaptioner()
        captions = captioner.caption_scenes(_scenes(), video_path=None)
        assert len(captions) == 3

    def test_empty_scenes_returns_empty_list(self):
        """No scenes → no captions."""
        captioner = StubVisionCaptioner()
        assert captioner.caption_scenes([]) == []

    def test_is_subclass_of_vision_captioner(self):
        """StubVisionCaptioner is a proper subclass of VisionCaptioner."""
        assert issubclass(StubVisionCaptioner, VisionCaptioner)
        captioner = StubVisionCaptioner()
        assert isinstance(captioner, VisionCaptioner)


# ── Factory ────────────────────────────────────────────────


class TestGetVisionCaptioner:
    def test_stub_provider_returns_stub(self):
        """get_vision_captioner('stub') returns StubVisionCaptioner."""
        captioner = get_vision_captioner("stub")
        assert isinstance(captioner, StubVisionCaptioner)

    def test_default_provider_is_stub(self):
        """Default provider is 'stub'."""
        captioner = get_vision_captioner()
        assert isinstance(captioner, StubVisionCaptioner)

    def test_none_provider_raises_value_error(self):
        """'none' is not a valid provider — raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported vision captioner"):
            get_vision_captioner("none")

    def test_unknown_provider_raises_value_error(self):
        """Unknown provider raises ValueError with helpful message."""
        with pytest.raises(ValueError, match="Unsupported vision captioner"):
            get_vision_captioner("blip")

    def test_unknown_provider_message_lists_supported(self):
        """Error message mentions 'stub' as supported."""
        with pytest.raises(ValueError, match="stub"):
            get_vision_captioner("invalid_provider")

    def test_accepts_kwargs(self):
        """Factory passes kwargs through without error (future providers)."""
        captioner = get_vision_captioner("stub", model_name="test", device="cpu")
        assert isinstance(captioner, StubVisionCaptioner)


# ── Integration: stub labels are detectable as placeholders ─


def test_stub_labels_match_placeholder_pattern():
    """Stub labels follow the same format as match._build_scene_label.

    This ensures the fake-caption guard in match.py correctly detects
    stub labels as placeholders (>70% fake → force heuristic match),
    so EP8 stub integration does not change existing behavior.
    """
    captioner = StubVisionCaptioner()
    scenes = _scenes()
    captions = captioner.caption_scenes(scenes)

    # Every stub label starts with "scene " — same as _build_scene_label
    for cap in captions:
        assert cap.startswith("scene ")
        assert "from" in cap
        assert "to" in cap


def test_stub_captions_are_deterministic():
    """Same scenes always produce same captions (no randomness)."""
    captioner = StubVisionCaptioner()
    scenes = _scenes()
    first = captioner.caption_scenes(scenes)
    second = captioner.caption_scenes(scenes)
    assert first == second
