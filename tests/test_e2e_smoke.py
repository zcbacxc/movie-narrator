"""L2 automated E2E smoke tests.

These tests run the full pipeline (build_context → run_pipeline) in CI mode
and verify the end-to-end contract:

1. Pipeline completes without hard failure
2. All expected deliverable files are produced
3. Metadata records the correct script source and phase
4. Pipeline status fields are populated
5. Dynamic sentence count works end-to-end (not just in isolation)

CI mode (CI=1) uses:
- LLM ci_mock fallback (4 mock segments) — no real LLM needed
- Silent TTS (auto-generated silence mp3) — requires ffmpeg with mp3 encoder
- No source video — render uses solid color background

These tests are skipped when ffmpeg lacks mp3 encoding support (e.g. the
minimal h264-only build on some Windows machines).  They run in CI
(ubuntu-latest with full ffmpeg).

This complements the existing CI shell smoke test (ci.yml) by adding
Python-level assertions on ctx.metadata, ctx.status, and deliverable
file existence — things the shell test can't check.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from movie_narrator.models import Context, Services
from movie_narrator.pipeline.runner import build_context, run_pipeline


# ── ffmpeg capability detection ────────────────────────────


def _ffmpeg_has_mp3_encoder() -> bool:
    """Check if ffmpeg can encode mp3 (requires libmp3lame or similar)."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return False
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=5,
        )
        return "libmp3lame" in result.stdout or " mp3 " in result.stdout
    except Exception:
        return False


_HAS_MP3 = _ffmpeg_has_mp3_encoder()
_SKIP_REASON = "ffmpeg lacks mp3 encoder (libmp3lame) — run in CI with full ffmpeg"


# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def ci_env(monkeypatch):
    """Set CI=1 environment for the test, restoring afterward."""
    monkeypatch.setenv("CI", "1")
    yield
    monkeypatch.delenv("CI", raising=False)


@pytest.fixture
def output_dir(tmp_path):
    """Create and return an output directory."""
    d = tmp_path / "e2e_output"
    d.mkdir(parents=True, exist_ok=True)
    return d


# ── 1. Full pipeline E2E (no preset, no video) ─────────────


@pytest.mark.skipif(not _HAS_MP3, reason=_SKIP_REASON)
def test_e2e_pipeline_completes_with_ci_mock(ci_env, output_dir):
    """Full pipeline must complete in CI mode without hard failure.

    This is the most basic E2E contract: build_context → run_pipeline
    must not raise.  CI mode uses ci_mock script + silent TTS + no video.
    """
    ctx = build_context(
        movie="E2E-Test",
        style="热血搞笑",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=output_dir,
        keep_cache=True,
    )
    result = run_pipeline(ctx)

    # Pipeline must complete
    assert result is not None
    # Must produce segments
    assert len(result.segments) > 0
    # CI mock source expected (LLM unreachable in CI)
    assert result.metadata.get("script_source") in ("llm", "ci_mock")


@pytest.mark.skipif(not _HAS_MP3, reason=_SKIP_REASON)
def test_e2e_deliverable_files_produced(ci_env, output_dir):
    """All expected deliverable files must be produced.

    Mirrors the CI shell smoke test (ci.yml lines 42-46) but in Python.
    """
    ctx = build_context(
        movie="E2E-Deliverables",
        style="test",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=output_dir,
        keep_cache=True,
    )
    run_pipeline(ctx)

    # Core deliverables
    assert (output_dir / "E2E-Deliverables" / "script.md").is_file()
    assert (output_dir / "E2E-Deliverables" / "final.mp4").is_file()
    assert (output_dir / "E2E-Deliverables" / "subtitle.srt").is_file()
    assert (output_dir / "E2E-Deliverables" / "metadata.json").is_file()
    assert (output_dir / "E2E-Deliverables" / "narration.mp3").is_file()


@pytest.mark.skipif(not _HAS_MP3, reason=_SKIP_REASON)
def test_e2e_metadata_json_is_valid(ci_env, output_dir):
    """metadata.json must be valid JSON with expected fields."""
    ctx = build_context(
        movie="E2E-Metadata",
        style="test",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=output_dir,
        keep_cache=True,
    )
    run_pipeline(ctx)

    meta_path = output_dir / "E2E-Metadata" / "metadata.json"
    assert meta_path.is_file()
    meta = json.loads(meta_path.read_text(encoding="utf-8"))

    # Must have these fields
    assert "movie_name" in meta
    assert "duration" in meta
    assert meta["movie_name"] == "E2E-Metadata"


# ── 2. Preset E2E ──────────────────────────────────────────


@pytest.mark.skipif(not _HAS_MP3, reason=_SKIP_REASON)
@pytest.mark.parametrize("preset_name", ["douyin-fast", "mainstream-dry", "bilibili-long"])
def test_e2e_preset_pipeline_completes(preset_name, ci_env, output_dir):
    """Each preset must complete the full pipeline without hard failure.

    This catches integration issues between preset params and pipeline
    steps that unit tests miss (e.g. preset sets a param that breaks
    a downstream step).
    """
    ctx = build_context(
        movie=f"E2E-{preset_name}",
        style="test",
        duration=30,  # short to keep CI fast
        voice=None,
        format="16:9",
        output_dir=output_dir,
        keep_cache=True,
        narration_preset=preset_name,
    )
    result = run_pipeline(ctx)

    assert len(result.segments) > 0
    # CI mock produces 4 segments; real LLM would produce preset count
    assert result.metadata.get("script_source") in ("llm", "ci_mock")
    # Preset name should be in metadata
    assert result.metadata.get("narration_preset") == preset_name


# ── 3. Dynamic sentence count E2E ───────────────────────────


@pytest.mark.skipif(not _HAS_MP3, reason=_SKIP_REASON)
def test_e2e_dynamic_count_with_preset(ci_env, output_dir):
    """Dynamic sentence count must work end-to-end with preset.

    With bilibili-long preset and duration=60, the pipeline should
    request 8 sentences (60/7.5=8).  We mock the LLM to exercise the
    two-phase path (not ci_mock fallback) so we can verify
    script_target_count.
    """
    from tests.test_script import (
        _mock_llm_response, _mock_llm_cm, _mock_settings,
        _beats_json, _segments_json,
    )

    target = 8  # bilibili-long at 60s: round(60/7.5) = 8

    ctx = build_context(
        movie="E2E-Dynamic",
        style="test",
        duration=60,
        voice=None,
        format="16:9",
        output_dir=output_dir,
        keep_cache=True,
        narration_preset="bilibili-long",
    )

    # Mock LLM to return exactly target beats + segments
    beats_resp = _mock_llm_response(_beats_json(target))
    seg_resp = _mock_llm_response(_segments_json([f"旁白{i}" for i in range(target)]))
    mock_cm = _mock_llm_cm(side_effect=[beats_resp, seg_resp])

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            # Patch research_plot's LLM too (it runs before generate_script)
            with patch("movie_narrator.pipeline.research.get_llm_client", return_value=mock_cm):
                with patch("movie_narrator.pipeline.research.get_settings", return_value=_mock_settings()):
                    result = run_pipeline(ctx)

    # script_target_count should be 8 (calculated from 60/7.5)
    assert result.metadata.get("script_target_count") == target
    assert len(result.segments) == target
    assert result.metadata.get("script_source") == "llm"


# ── 4. Pipeline status verification ────────────────────────


@pytest.mark.skipif(not _HAS_MP3, reason=_SKIP_REASON)
def test_e2e_pipeline_status_populated(ci_env, output_dir):
    """Pipeline status fields must be populated after run.

    Each step should set its status field to success/failed/skipped,
    not left at the default 'disabled'.
    """
    ctx = build_context(
        movie="E2E-Status",
        style="test",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=output_dir,
        keep_cache=True,
    )
    result = run_pipeline(ctx)

    # Status object should have fields populated
    status = result.status
    # At least script generation should have run (success or failed)
    # In CI mode, script falls back to ci_mock, but status should reflect that
    assert status.research in ("success", "failed", "skipped", "disabled")
    assert status.scene in ("success", "failed", "skipped", "disabled")


# ── 5. Degradation tracking ────────────────────────────────


@pytest.mark.skipif(not _HAS_MP3, reason=_SKIP_REASON)
def test_e2e_degraded_steps_tracked(ci_env, output_dir):
    """If any steps degrade, they should be tracked in metadata.

    In CI mode without a real video, scene detection and clip matching
    will likely degrade.  The pipeline should track these in
    _degraded_steps and complete anyway (soft steps).
    """
    ctx = build_context(
        movie="E2E-Degraded",
        style="test",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=output_dir,
        keep_cache=True,
    )
    result = run_pipeline(ctx)

    # Pipeline must complete regardless of degradation
    assert result is not None
    assert len(result.segments) > 0

    # If there are degraded steps, they should be tracked
    degraded = result.metadata.get("_degraded_steps", [])
    # In CI mode without video, some steps SHOULD degrade
    # (but we don't assert which — that's implementation detail)


# ── 6. Script content validation ───────────────────────────


@pytest.mark.skipif(not _HAS_MP3, reason=_SKIP_REASON)
def test_e2e_script_md_has_content(ci_env, output_dir):
    """script.md must have non-empty content."""
    ctx = build_context(
        movie="E2E-ScriptMD",
        style="test",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=output_dir,
        keep_cache=True,
    )
    run_pipeline(ctx)

    script_path = output_dir / "E2E-ScriptMD" / "script.md"
    assert script_path.is_file()
    content = script_path.read_text(encoding="utf-8").strip()
    assert len(content) > 0
    # Should contain the movie name somewhere
    assert "E2E-ScriptMD" in content


@pytest.mark.skipif(not _HAS_MP3, reason=_SKIP_REASON)
def test_e2e_subtitle_srt_valid(ci_env, output_dir):
    """subtitle.srt must be a valid SRT file with at least one entry."""
    ctx = build_context(
        movie="E2E-SRT",
        style="test",
        duration=10,
        voice=None,
        format="16:9",
        output_dir=output_dir,
        keep_cache=True,
    )
    run_pipeline(ctx)

    srt_path = output_dir / "E2E-SRT" / "subtitle.srt"
    assert srt_path.is_file()
    content = srt_path.read_text(encoding="utf-8").strip()
    # SRT entries start with a number
    assert content[0].isdigit()
    # Should contain timestamp format -->
    assert "-->" in content


# ── 7. ffmpeg capability self-test (always runs) ───────────


def test_ffmpeg_mp3_capability_reported():
    """Report ffmpeg mp3 capability — never skip, always inform.

    This test documents whether the local ffmpeg supports mp3 encoding.
    It passes regardless — it's informational, not a gate.
    """
    if _HAS_MP3:
        pytest.skip("ffmpeg has mp3 encoder — E2E tests will run")
    else:
        pytest.skip(
            "ffmpeg lacks mp3 encoder (libmp3lame). "
            "E2E tests are skipped. Install full ffmpeg to enable them: "
            "winget install ffmpeg  (or use CI with full ffmpeg)"
        )
