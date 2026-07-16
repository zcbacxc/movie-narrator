"""Tests for web_api/form.py — validation and build_context kwargs conversion."""

from __future__ import annotations

import pytest

from movie_narrator.web_api.form import FormData, form_to_context_args, validate_form


def _base_form(**overrides) -> FormData:
    """Return a valid FormData with optional overrides."""
    defaults = dict(
        movie="Test Movie",
        style="热血搞笑",
        duration=60,
        voice="",
        format="16:9",
        video_path=None,
        library_dir="",
        research=False,
        bgm_path=None,
        no_bgm=False,
        no_clips=False,
        strict=False,
        subtitle_lang="",
        subtitle_mode="original",
        scene_threshold=None,
        match_min_score=None,
        translate_provider="",
        translate_retries=None,
    )
    defaults.update(overrides)
    return FormData(**defaults)


class TestValidateForm:
    def test_valid_form_no_errors(self):
        assert validate_form(_base_form()) == []

    def test_empty_movie_name(self):
        errors = validate_form(_base_form(movie=""))
        assert any("Movie name is required" in e for e in errors)

    def test_whitespace_movie_name(self):
        errors = validate_form(_base_form(movie="   "))
        assert any("Movie name is required" in e for e in errors)

    def test_duration_too_short(self):
        errors = validate_form(_base_form(duration=3))
        assert any("Duration" in e for e in errors)

    def test_duration_too_long(self):
        errors = validate_form(_base_form(duration=700))
        assert any("Duration" in e for e in errors)

    def test_invalid_format(self):
        errors = validate_form(_base_form(format="4:3"))
        assert any("Format" in e for e in errors)

    def test_translated_mode_without_lang(self):
        errors = validate_form(_base_form(subtitle_mode="translated", subtitle_lang=""))
        assert any("subtitle_lang is required" in e for e in errors)

    def test_translated_mode_with_lang_ok(self):
        errors = validate_form(_base_form(subtitle_mode="translated", subtitle_lang="en"))
        assert errors == []

    def test_bilingual_mode_without_lang(self):
        errors = validate_form(_base_form(subtitle_mode="bilingual", subtitle_lang=""))
        assert any("subtitle_lang is required" in e for e in errors)

    def test_scene_threshold_out_of_range(self):
        errors = validate_form(_base_form(scene_threshold=150))
        assert any("Scene threshold" in e for e in errors)

    def test_match_min_score_out_of_range(self):
        errors = validate_form(_base_form(match_min_score=2.0))
        assert any("Match min score" in e for e in errors)

    def test_translate_retries_out_of_range(self):
        errors = validate_form(_base_form(translate_retries=20))
        assert any("Translate retries" in e for e in errors)


class TestFormToContextArgs:
    def test_empty_advanced_params_yield_none_params(self):
        """Critical: empty = no override. params should be None."""
        args = form_to_context_args(_base_form())
        assert args["params"] is None

    def test_scene_threshold_set(self):
        args = form_to_context_args(_base_form(scene_threshold=0.7))
        assert args["params"] is not None
        assert args["params"]["scene_threshold"] == 0.7

    def test_match_min_score_set(self):
        args = form_to_context_args(_base_form(match_min_score=0.3))
        assert args["params"]["match_min_score"] == 0.3

    def test_translate_provider_set(self):
        args = form_to_context_args(_base_form(translate_provider="llm"))
        assert args["params"]["translate_provider"] == "llm"

    def test_translate_retries_set(self):
        args = form_to_context_args(_base_form(translate_retries=5))
        assert args["params"]["translate_retries"] == 5

    def test_empty_voice_becomes_none(self):
        args = form_to_context_args(_base_form(voice=""))
        assert args["voice"] is None

    def test_non_empty_voice_preserved(self):
        args = form_to_context_args(_base_form(voice="zh-CN-XiaoxiaoNeural"))
        assert args["voice"] == "zh-CN-XiaoxiaoNeural"

    def test_empty_subtitle_lang_becomes_none(self):
        args = form_to_context_args(_base_form(subtitle_lang=""))
        assert args["subtitle_lang"] is None

    def test_movie_name_stripped(self):
        args = form_to_context_args(_base_form(movie="  Inception  "))
        assert args["movie"] == "Inception"

    def test_no_bgm_propagated(self):
        args = form_to_context_args(_base_form(no_bgm=True))
        assert args["no_bgm"] is True

    def test_bgm_path_propagated(self):
        args = form_to_context_args(_base_form(bgm_path="/tmp/bgm.mp3"))
        assert args["bgm"] == "/tmp/bgm.mp3"

    def test_workflow_steps_always_none(self):
        """Web UI doesn't expose workflow_steps toggles (use CLI/YAML for that)."""
        args = form_to_context_args(_base_form())
        assert args["workflow_steps"] is None

    def test_config_path_always_none(self):
        """Web UI doesn't use YAML config files."""
        args = form_to_context_args(_base_form())
        assert args["config_path"] is None
