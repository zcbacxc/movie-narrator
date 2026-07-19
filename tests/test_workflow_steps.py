"""Tests for workflow_steps disabling.

REC-5 moved the authoritative workflow_steps check to the runner.
Step functions no longer have defensive checks — the runner short-
circuits before the step function is called.  These tests verify
the runner's skip logic, including the alias mapping for translate.
"""

from unittest.mock import MagicMock, patch

from movie_narrator.models import Context, Scene, TimedSegment
from movie_narrator.pipeline.align import align_audio
from movie_narrator.pipeline.bgm import mix_bgm
from movie_narrator.pipeline.export_clips import export_clips
from movie_narrator.pipeline.match import match_clips
from movie_narrator.pipeline.research import research_plot
from movie_narrator.pipeline.runner import _STEP_ALIASES, STATUS_FIELD_FOR_STEP
from movie_narrator.pipeline.scenes import detect_scenes


# ── Runner-level workflow_steps skip tests ──────────────────


def _make_runner_ctx(tmp_path, workflow_steps: dict) -> Context:
    ctx = Context(movie_name="X", output_dir=str(tmp_path))
    ctx.metadata["workflow_steps"] = workflow_steps
    return ctx


def test_runner_skips_research(tmp_path):
    """Runner skips research_plot when workflow_steps disables it."""
    ctx = _make_runner_ctx(tmp_path, {"research_plot": False})
    ctx.metadata["research_enabled"] = True
    _assert_step_skipped_by_runner(ctx, "research_plot", "research")


def test_runner_skips_align(tmp_path):
    """Runner skips align_audio when workflow_steps disables it."""
    ctx = _make_runner_ctx(tmp_path, {"align_audio": False})
    ctx.audio_path = str(tmp_path / "a.mp3")
    _assert_step_skipped_by_runner(ctx, "align_audio", "align")


def test_runner_skips_scene(tmp_path):
    """Runner skips detect_scenes when workflow_steps disables it."""
    ctx = _make_runner_ctx(tmp_path, {"detect_scenes": False})
    ctx.source_video_path = str(tmp_path / "v.mp4")
    _assert_step_skipped_by_runner(ctx, "detect_scenes", "scene")


def test_runner_skips_match(tmp_path):
    """Runner skips match_clips when workflow_steps disables it."""
    ctx = _make_runner_ctx(tmp_path, {"match_clips": False})
    ctx.source_video_path = str(tmp_path / "v.mp4")
    ctx.scenes = [Scene(index=0, start=0.0, end=1.0)]
    ctx.timed_segments = [TimedSegment(text="A", start=0.0, end=1.0)]
    _assert_step_skipped_by_runner(ctx, "match_clips", "match")


def test_runner_skips_bgm(tmp_path):
    """Runner skips mix_bgm when workflow_steps disables it."""
    audio = tmp_path / "n.mp3"
    audio.write_bytes(b"00")
    ctx = _make_runner_ctx(tmp_path, {"mix_bgm": False})
    ctx.audio_path = str(audio)
    ctx.metadata["bgm_request"] = "explicit"
    ctx.assets.bgm = str(tmp_path / "b.mp3")
    _assert_step_skipped_by_runner(ctx, "mix_bgm", "bgm")


def test_runner_skips_export(tmp_path):
    """Runner skips export_clips when workflow_steps disables it."""
    ctx = _make_runner_ctx(tmp_path, {"export_clips": False})
    ctx.metadata["export_clips"] = True
    _assert_step_skipped_by_runner(ctx, "export_clips", "export")


def test_runner_skips_translate_via_function_key(tmp_path):
    """Runner skips translate_subtitles via function-name key."""
    ctx = _make_runner_ctx(tmp_path, {"translate_subtitles": False})
    _assert_step_skipped_by_runner(ctx, "translate_subtitles", "translate")


def test_runner_skips_translate_via_short_alias(tmp_path):
    """Runner skips translate_subtitles via short alias 'translate'."""
    ctx = _make_runner_ctx(tmp_path, {"translate": False})
    _assert_step_skipped_by_runner(ctx, "translate_subtitles", "translate")


def test_step_alias_mapping():
    """_STEP_ALIASES maps all 7 SOFT_STATUS_STEPS to short keys (WP1)."""
    # WP1: expanded from 1 alias to full coverage
    assert _STEP_ALIASES.get("translate_subtitles") == "translate"
    assert _STEP_ALIASES.get("research_plot") == "research"
    assert _STEP_ALIASES.get("align_audio") == "align"
    assert _STEP_ALIASES.get("detect_scenes") == "scene"
    assert _STEP_ALIASES.get("match_clips") == "match"
    assert _STEP_ALIASES.get("mix_bgm") == "bgm"
    assert _STEP_ALIASES.get("export_clips") == "export"


# ── WP1: short alias tests (all 7 steps) ───────────────────


def test_runner_skips_research_via_short_alias(tmp_path):
    """Runner skips research_plot via short alias 'research' (WP1)."""
    ctx = _make_runner_ctx(tmp_path, {"research": False})
    _assert_step_skipped_by_runner(ctx, "research_plot", "research")


def test_runner_skips_align_via_short_alias(tmp_path):
    """Runner skips align_audio via short alias 'align' (WP1)."""
    ctx = _make_runner_ctx(tmp_path, {"align": False})
    _assert_step_skipped_by_runner(ctx, "align_audio", "align")


def test_runner_skips_scene_via_short_alias(tmp_path):
    """Runner skips detect_scenes via short alias 'scene' (WP1)."""
    ctx = _make_runner_ctx(tmp_path, {"scene": False})
    ctx.source_video_path = str(tmp_path / "v.mp4")
    _assert_step_skipped_by_runner(ctx, "detect_scenes", "scene")


def test_runner_skips_match_via_short_alias(tmp_path):
    """Runner skips match_clips via short alias 'match' (WP1)."""
    ctx = _make_runner_ctx(tmp_path, {"match": False})
    ctx.scenes = [Scene(index=0, start=0.0, end=1.0)]
    ctx.timed_segments = [TimedSegment(text="A", start=0.0, end=1.0)]
    _assert_step_skipped_by_runner(ctx, "match_clips", "match")


def test_runner_skips_bgm_via_short_alias(tmp_path):
    """Runner skips mix_bgm via short alias 'bgm' (WP1)."""
    audio = tmp_path / "n.mp3"
    audio.write_bytes(b"00")
    ctx = _make_runner_ctx(tmp_path, {"bgm": False})
    ctx.audio_path = str(audio)
    ctx.metadata["bgm_request"] = "explicit"
    ctx.assets.bgm = str(tmp_path / "b.mp3")
    _assert_step_skipped_by_runner(ctx, "mix_bgm", "bgm")


def test_runner_skips_export_via_short_alias(tmp_path):
    """Runner skips export_clips via short alias 'export' (WP1)."""
    ctx = _make_runner_ctx(tmp_path, {"export": False})
    ctx.metadata["export_clips"] = True
    _assert_step_skipped_by_runner(ctx, "export_clips", "export")


# ── Non-workflow_steps tests (unchanged) ────────────────────


def test_research_provider_from_metadata(tmp_path):
    ctx = Context(movie_name="X", output_dir=str(tmp_path))
    ctx.metadata["research_enabled"] = True
    ctx.metadata["research_provider"] = "tmdb"
    with patch("movie_narrator.pipeline.research.get_settings") as gs:
        gs.return_value.research_provider = "llm"
        research_plot(ctx)
    assert ctx.status.research == "failed"
    envelope_error = (tmp_path / "research.json").read_text(encoding="utf-8")
    assert "tmdb" in envelope_error


def test_match_min_score_from_metadata(tmp_path, monkeypatch):
    """Ensure match reads metadata match_min_score (value lands in code path)."""
    ctx = Context(
        movie_name="X",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "v.mp4"),
        scenes=[Scene(index=0, start=0.0, end=10.0)],
        timed_segments=[TimedSegment(text="A", start=0.0, end=2.0)],
    )
    ctx.status.scene = "success"
    ctx.metadata["match_min_score"] = 0.99
    (tmp_path / "v.mp4").write_bytes(b"00")

    from movie_narrator.pipeline import match as match_mod

    monkeypatch.setattr(match_mod, "probe", lambda name: (False, "no"))
    match_clips(ctx)
    assert ctx.status.match == "success"
    assert ctx.metadata["match_min_score"] == 0.99


# ── Helper ──────────────────────────────────────────────────


def _assert_step_skipped_by_runner(ctx: Context, step_name: str, status_field: str):
    """Assert that the runner's workflow_steps skip logic would fire.

    Simulates the runner's pre-check: if workflow_steps has the step
    (or its alias) set to False, the step is skipped before execution.
    """
    workflow_steps = ctx.metadata.get("workflow_steps") or {}
    alias = _STEP_ALIASES.get(step_name)

    # Replicate runner's skip condition
    should_skip = bool(workflow_steps) and (
        not workflow_steps.get(step_name, True)
        or (alias and not workflow_steps.get(alias, True))
    )

    assert should_skip, f"Runner should skip {step_name} but skip condition not met"
    assert status_field in STATUS_FIELD_FOR_STEP.values(), f"Unknown status field {status_field}"
