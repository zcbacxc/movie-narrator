"""Unit tests for render_template (NA-M6-S1).

Covers ``_substitute_movie`` placeholder replacement and the
``render_template`` parsing logic embedded in ``render_video``
(``title_card_text``, ``end_card_text``, ``watermark_text``,
``disclaimer_text``).  Heavy rendering dependencies (MoviePy clips,
ffmpeg, audio) are mocked so only text substitution and template
field resolution are exercised.
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from movie_narrator.models import Context, TimedSegment
from movie_narrator.pipeline import render as render_mod
from movie_narrator.pipeline.render import _substitute_movie, render_video


# ── Helpers ────────────────────────────────────────────────


def _chainable_clip(end: float = 6.0) -> MagicMock:
    """Return a MoviePy-style chainable clip mock.

    Every ``with_*`` method returns the same mock so call chains like
    ``clip.with_duration(x).with_start(y)`` work without error.
    """
    clip = MagicMock(name="clip")
    clip.duration = end
    clip.with_start.return_value = clip
    clip.with_duration.return_value = clip
    clip.with_position.return_value = clip
    clip.with_audio.return_value = clip
    clip.with_effects.return_value = clip
    clip.write_videofile = MagicMock()
    clip.close = MagicMock()
    return clip


def _make_ctx(tmp_path, movie_name="飞驰人生", render_template=None):
    """Build a minimal Context for render_video template tests.

    ``render_title_card_sec`` is set to 2 so the title-card branch is
    fully exercised (template substitution *and* image creation).
    """
    ctx = Context(
        movie_name=movie_name,
        output_dir=str(tmp_path),
        timed_segments=[TimedSegment(text="片段一", start=0.0, end=2.0)],
    )
    ctx.audio_path = str(tmp_path / "narration.wav")
    ctx.metadata["render_title_card_sec"] = 2
    if render_template is not None:
        ctx.metadata["render_template"] = render_template
    return ctx


def _run_render_with_mocks(ctx, monkeypatch):
    """Run ``render_video`` with all heavy dependencies mocked.

    Returns a dict with:

    - ``spy``: MagicMock wrapping ``_substitute_movie`` (delegates to
      the real implementation so substitution still occurs).
    - ``text_images``: list of text strings passed to
      ``_create_text_image`` (subtitles + cards + disclaimer).
    - ``watermark_images``: list of text strings passed to
      ``_create_watermark_image``.
    """
    # Spy on _substitute_movie — delegates to the real function so the
    # substitution result flows downstream to image helpers.
    spy = MagicMock(side_effect=render_mod._substitute_movie)
    monkeypatch.setattr(render_mod, "_substitute_movie", spy)

    # Capture text args passed to image-creation helpers.
    text_images: list[str] = []

    def _capture_text_image(text, *args, **kwargs):
        text_images.append(text)
        return MagicMock()

    watermark_images: list[str] = []

    def _capture_watermark_image(text, *args, **kwargs):
        watermark_images.append(text)
        return MagicMock()

    # Mock heavy rendering dependencies.
    final = _chainable_clip()
    final.write_videofile = MagicMock(
        side_effect=lambda path, **kw: Path(path).write_bytes(b"fake")
    )
    audio = MagicMock(name="audio")
    audio.duration = 6.0
    audio.close = MagicMock()

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stderr = ""

    monkeypatch.setattr(render_mod, "ensure_final_audio", MagicMock())
    monkeypatch.setattr(render_mod, "CompositeVideoClip", MagicMock(return_value=final))
    monkeypatch.setattr(render_mod, "ColorClip", MagicMock(return_value=_chainable_clip()))
    monkeypatch.setattr(render_mod, "ImageClip", MagicMock(return_value=_chainable_clip()))
    monkeypatch.setattr(render_mod, "AudioFileClip", MagicMock(return_value=audio))
    monkeypatch.setattr(render_mod, "_create_text_image", _capture_text_image)
    monkeypatch.setattr(render_mod, "_create_watermark_image", _capture_watermark_image)
    monkeypatch.setattr(render_mod, "build_metadata_json", MagicMock(return_value={}))
    monkeypatch.setattr("subprocess.run", MagicMock(return_value=fake_proc))
    monkeypatch.setattr("shutil.which", MagicMock(return_value="/fake/ffmpeg"))

    render_video(ctx)
    return {"spy": spy, "text_images": text_images, "watermark_images": watermark_images}


# ── 1. _substitute_movie unit tests ───────────────────────


class TestSubstituteMovie:
    """Unit tests for the ``_substitute_movie`` helper."""

    def test_replaces_movie_placeholder(self):
        """``{movie}`` is replaced by the movie name."""
        assert _substitute_movie("{movie}解说", "飞驰人生") == "飞驰人生解说"

    def test_no_placeholder_unchanged(self):
        """Text without the placeholder is returned unchanged."""
        assert _substitute_movie("精彩解说视频", "飞驰人生") == "精彩解说视频"

    def test_multiple_placeholders(self):
        """All occurrences of ``{movie}`` are replaced."""
        assert _substitute_movie("{movie} - {movie}精华", "飞驰人生") == "飞驰人生 - 飞驰人生精华"

    def test_empty_movie_name(self):
        """An empty (or None) movie name yields an empty replacement."""
        assert _substitute_movie("{movie}解说", "") == "解说"
        assert _substitute_movie("{movie}解说", None) == "解说"

    def test_special_chars_in_movie_name(self):
        """Special characters (quotes, parentheses) are inserted literally."""
        movie = '"战狼2"（特别版）'
        assert _substitute_movie("{movie}解说", movie) == f'{movie}解说'


# ── 2. render_template field parsing ──────────────────────


class TestRenderTemplateParsing:
    """Tests for ``render_template`` field parsing inside ``render_video``."""

    def test_empty_template_dict(self, tmp_path, monkeypatch):
        """An empty ``render_template`` dict does not break rendering."""
        ctx = _make_ctx(tmp_path, render_template={})
        result = _run_render_with_mocks(ctx, monkeypatch)
        result["spy"].assert_not_called()

    def test_none_render_template(self, tmp_path, monkeypatch):
        """Missing ``render_template`` key in metadata does not break rendering."""
        ctx = _make_ctx(tmp_path)
        result = _run_render_with_mocks(ctx, monkeypatch)
        result["spy"].assert_not_called()

    def test_title_card_text_substitution(self, tmp_path, monkeypatch):
        """``title_card_text`` with ``{movie}`` is substituted."""
        ctx = _make_ctx(tmp_path, render_template={"title_card_text": "{movie}解说"})
        result = _run_render_with_mocks(ctx, monkeypatch)
        result["spy"].assert_any_call("{movie}解说", "飞驰人生")
        assert "飞驰人生解说" in result["text_images"]

    def test_end_card_text_substitution(self, tmp_path, monkeypatch):
        """``end_card_text`` with ``{movie}`` is substituted."""
        ctx = _make_ctx(tmp_path, render_template={"end_card_text": "{movie}完结"})
        result = _run_render_with_mocks(ctx, monkeypatch)
        result["spy"].assert_any_call("{movie}完结", "飞驰人生")
        assert "飞驰人生完结" in result["text_images"]

    def test_watermark_text_substitution(self, tmp_path, monkeypatch):
        """``watermark_text`` with ``{movie}`` is substituted."""
        ctx = _make_ctx(tmp_path, render_template={"watermark_text": "{movie}出品"})
        result = _run_render_with_mocks(ctx, monkeypatch)
        result["spy"].assert_any_call("{movie}出品", "飞驰人生")
        assert "飞驰人生出品" in result["watermark_images"]

    def test_disclaimer_text_substitution(self, tmp_path, monkeypatch):
        """``disclaimer_text`` with ``{movie}`` is substituted."""
        ctx = _make_ctx(
            tmp_path,
            render_template={"disclaimer_text": "{movie}版权归原作者所有"},
        )
        result = _run_render_with_mocks(ctx, monkeypatch)
        result["spy"].assert_any_call("{movie}版权归原作者所有", "飞驰人生")
        assert "飞驰人生版权归原作者所有" in result["text_images"]


# ── 3. render_template integration ────────────────────────


class TestRenderTemplateIntegration:
    """Integration tests for ``render_template`` with multiple fields."""

    def test_template_with_all_fields(self, tmp_path, monkeypatch):
        """All four template fields are substituted in a single render pass."""
        template = {
            "title_card_text": "{movie} · 片头",
            "end_card_text": "{movie} · 片尾",
            "watermark_text": "{movie}出品",
            "disclaimer_text": "{movie}版权归原作者所有",
        }
        ctx = _make_ctx(tmp_path, render_template=template)
        result = _run_render_with_mocks(ctx, monkeypatch)
        spy = result["spy"]

        # Every field triggers exactly one _substitute_movie call.
        assert spy.call_count == 4
        spy.assert_any_call("{movie} · 片头", "飞驰人生")
        spy.assert_any_call("{movie} · 片尾", "飞驰人生")
        spy.assert_any_call("{movie}出品", "飞驰人生")
        spy.assert_any_call("{movie}版权归原作者所有", "飞驰人生")

        # Substituted texts reach the image-creation helpers.
        assert "飞驰人生 · 片头" in result["text_images"]
        assert "飞驰人生 · 片尾" in result["text_images"]
        assert "飞驰人生出品" in result["watermark_images"]
        assert "飞驰人生版权归原作者所有" in result["text_images"]

    def test_template_partial_fields(self, tmp_path, monkeypatch):
        """Only ``watermark_text`` set — other cards fall back to defaults."""
        ctx = _make_ctx(tmp_path, render_template={"watermark_text": "{movie}独家"})
        result = _run_render_with_mocks(ctx, monkeypatch)
        spy = result["spy"]

        # Only the watermark field triggers substitution.
        assert spy.call_count == 1
        spy.assert_any_call("{movie}独家", "飞驰人生")
        assert "飞驰人生独家" in result["watermark_images"]

        # No card text should contain the watermark substitution result.
        assert not any("独家" in t for t in result["text_images"])
