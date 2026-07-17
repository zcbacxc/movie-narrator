"""Tests for the narration preset system (Stage 0.5)."""

import pytest

from movie_narrator.presets import get_preset, list_presets, BUILTIN_PRESETS
from movie_narrator.presets.base import (
    ALLOWED_PARAM_KEYS,
    ALLOWED_PROMPT_TAGS,
)
from movie_narrator.presets.registry import _validate
from movie_narrator.utils.prompts import build_cadence_hint, SCRIPT_PROMPT


# ── Registry / lookup ───────────────────────────────────────


def test_list_presets_returns_three_builtins():
    presets = list_presets()
    assert "douyin-fast" in presets
    assert "mainstream-dry" in presets
    assert "bilibili-long" in presets
    assert len(presets) >= 3


def test_get_preset_returns_validated_params():
    preset = get_preset("mainstream-dry")
    assert preset.name == "mainstream-dry"
    assert isinstance(preset.param_dict, dict)
    assert isinstance(preset.tag_dict, dict)
    assert preset.desc  # non-empty description


def test_get_preset_unknown_raises():
    with pytest.raises(KeyError, match="Unknown narration preset"):
        get_preset("nonexistent-preset")


# ── Preset param keys within whitelist ──────────────────────


@pytest.mark.parametrize("preset_name", ["douyin-fast", "mainstream-dry", "bilibili-long"])
def test_preset_params_within_allowed_keys(preset_name):
    preset = get_preset(preset_name)
    bad_keys = set(preset.param_dict) - ALLOWED_PARAM_KEYS
    assert not bad_keys, f"Preset '{preset_name}' has keys outside ALLOWED_PARAM_KEYS: {bad_keys}"


@pytest.mark.parametrize("preset_name", ["douyin-fast", "mainstream-dry", "bilibili-long"])
def test_preset_tags_within_allowed_vocab(preset_name):
    preset = get_preset(preset_name)
    for tag_key, tag_val in preset.tag_dict.items():
        assert tag_key in ALLOWED_PROMPT_TAGS, f"Unknown tag key: {tag_key}"
        assert tag_val in ALLOWED_PROMPT_TAGS[tag_key], f"Tag '{tag_key}' value '{tag_val}' not allowed"


# ── Preset differentiation ──────────────────────────────────


def test_presets_have_different_sentence_counts():
    """Each preset should target a different sentence count."""
    counts = {
        name: get_preset(name).param_dict.get("prompt_target_sentences")
        for name in ("douyin-fast", "mainstream-dry", "bilibili-long")
    }
    assert counts["douyin-fast"] > counts["mainstream-dry"] > counts["bilibili-long"]


def test_presets_have_different_cadence():
    tags = {
        name: get_preset(name).tag_dict.get("prompt_cadence")
        for name in ("douyin-fast", "mainstream-dry", "bilibili-long")
    }
    assert len(set(tags.values())) == 3  # all different


def test_douyin_fast_is_fastest_preset():
    """Douyin-fast should have the highest speed_clamp_max (fastest pacing)."""
    dy = get_preset("douyin-fast").param_dict
    md = get_preset("mainstream-dry").param_dict
    bl = get_preset("bilibili-long").param_dict
    assert dy["match_speed_clamp_max"] > md["match_speed_clamp_max"] > bl["match_speed_clamp_max"]


# ── Prompt shaping ──────────────────────────────────────────


def test_build_cadence_hint_brisk():
    hint = build_cadence_hint(cadence="brisk", connectors="interjection")
    assert "brisk" in hint
    assert "interjection" in hint or "哦" in hint


def test_build_cadence_hint_languid():
    hint = build_cadence_hint(cadence="languid", connectors="narrative")
    assert "slow" in hint or "contemplative" in hint
    assert "话说" in hint or "narrative" in hint


def test_build_cadence_hint_with_register():
    """Register tag (spoken/written/mixed) is consumed and appears in hint."""
    hint = build_cadence_hint(cadence="measured", connectors="narrative", register="written")
    assert "written" in hint.lower() or "literary" in hint.lower()


def test_build_cadence_hint_empty_tags():
    """Empty/unknown tags produce empty string (backward compat)."""
    assert build_cadence_hint() == ""
    assert build_cadence_hint(cadence="unknown", connectors="unknown") == ""


def test_script_prompt_has_all_placeholders():
    """SCRIPT_PROMPT must have all required format placeholders."""
    # Should format without errors when all keys are provided
    formatted = SCRIPT_PROMPT.format(
        movie="Test",
        style="drama",
        duration=60,
        research="",
        max_chars=15,
        target_sentences=15,
        hook_seconds=3,
        cadence_hint="",
    )
    assert "Test" in formatted
    assert "15" in formatted


# ── Validation ──────────────────────────────────────────────


def test_validate_rejects_bad_param_key():
    """A preset with a param key outside ALLOWED_PARAM_KEYS is rejected."""

    class BadPreset:
        name = "bad"
        def params(self):
            return {"nonexistent_key": 42}
        def prompt_tags(self):
            return {}
        def description(self):
            return "bad"

    with pytest.raises(ValueError, match="not in ALLOWED_PARAM_KEYS"):
        _validate(BadPreset())


def test_validate_rejects_bad_tag_value():
    """A preset with a tag value outside the allowed set is rejected."""

    class BadTagPreset:
        name = "bad-tag"
        def params(self):
            return {}
        def prompt_tags(self):
            return {"prompt_cadence": "supersonic"}  # not in allowed set
        def description(self):
            return "bad tag"

    with pytest.raises(ValueError, match="not allowed"):
        _validate(BadTagPreset())


# ── BUILTIN_PRESETS proxy ───────────────────────────────────


def test_builtin_presets_proxy_is_iterable():
    """BUILTIN_PRESETS should be iterable and contain the three builtins."""
    names = list(BUILTIN_PRESETS)
    assert "douyin-fast" in names
    assert "mainstream-dry" in names
    assert "bilibili-long" in names


# ── PARAM_WHITELIST single-source-of-truth ─────────────────


def test_allowed_param_keys_subset_of_param_whitelist():
    """ALLOWED_PARAM_KEYS must be a subset of PARAM_WHITELIST."""
    from movie_narrator.pipeline.runner import PARAM_WHITELIST
    assert ALLOWED_PARAM_KEYS <= PARAM_WHITELIST, (
        f"Keys in ALLOWED_PARAM_KEYS but not PARAM_WHITELIST: "
        f"{ALLOWED_PARAM_KEYS - PARAM_WHITELIST}"
    )


# ── YAML config narration_preset ────────────────────────────


def test_yaml_narration_preset_field_accepted(tmp_path):
    """JobConfig accepts narration_preset as a top-level field."""
    from movie_narrator.workflow.schema import JobConfig
    import yaml

    job_yaml = {
        "movie": "test",
        "narration_preset": "mainstream-dry",
    }
    config = JobConfig(**job_yaml)
    assert config.narration_preset == "mainstream-dry"


def test_yaml_narration_preset_defaults_none():
    """JobConfig.narration_preset defaults to None when not specified."""
    from movie_narrator.workflow.schema import JobConfig
    config = JobConfig(movie="test")
    assert config.narration_preset is None
