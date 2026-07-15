"""Tests for the v0.3 multi-language subtitle feature.

Covers:
- translate_subtitles step (decision matrix, soft-step contract)
- subtitle.py multi-file output and render_subtitle_path resolution
- merge_job subtitle-lang / subtitle-mode validation
- JobConfig subtitle_mode whitelist
"""

from __future__ import annotations

import json
import os
from unittest.mock import patch

import pytest

from movie_narrator.models import (
    Context, StepResult, SubtitlePaths, TimedSegment,
)
from movie_narrator.pipeline.translate import (
    SUPPORTED_PROVIDERS, _call_llm_chunk, _chunk_texts, _is_blank, _translate_via_llm, translate_subtitles,
)
from movie_narrator.pipeline.subtitle import generate_subtitle
from movie_narrator.workflow.errors import JobConfigError
from movie_narrator.workflow.merge import merge_job
from movie_narrator.workflow.schema import JobConfig, JobSteps


# ── translate_subtitles step ────────────────────────────────


def _make_ctx(tmp_path, *, timed_segments=None, translated_texts=None, **meta) -> Context:
    ctx = Context(movie_name="t", output_dir=str(tmp_path))
    if timed_segments is not None:
        ctx.timed_segments = timed_segments
    if translated_texts is not None:
        ctx.translated_texts = translated_texts
    ctx.metadata.update(meta)
    return ctx


def test_translate_skipped_without_lang(tmp_path):
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[TimedSegment(text="你好", start=0.0, end=1.0)],
    )
    translate_subtitles(ctx)
    assert ctx.status.translate == "skipped"
    assert ctx.step_state.result == StepResult.SKIPPED
    assert ctx.step_state.message == "no subtitle_lang"


def test_translate_skipped_with_empty_timed_segments(tmp_path):
    ctx = _make_ctx(tmp_path, subtitle_lang="en", timed_segments=[])
    translate_subtitles(ctx)
    assert ctx.status.translate == "skipped"
    assert ctx.step_state.message == "no timed_segments"


def test_translate_disabled_unknown_provider(tmp_path):
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[TimedSegment(text="你好", start=0.0, end=1.0)],
        subtitle_lang="en",
        translate_provider="google",  # not in v0.3 registry
    )
    translate_subtitles(ctx)
    assert ctx.status.translate == "disabled"
    assert ctx.step_state.result == StepResult.SKIPPED
    assert "google" in ctx.step_state.message
    assert any("not supported" in w for w in ctx.metadata.get("warnings", []))


def test_translate_ci_passthrough(tmp_path, monkeypatch):
    monkeypatch.setenv("CI", "1")
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[
            TimedSegment(text="你好", start=0.0, end=1.0),
            TimedSegment(text="世界", start=1.0, end=2.0),
        ],
        subtitle_lang="en",
    )
    translate_subtitles(ctx)
    assert ctx.status.translate == "skipped"
    assert ctx.translated_texts == ["你好", "世界"]
    assert ctx.metadata["translate_provider"] == "ci-passthrough"


def test_translate_provider_records_metadata(tmp_path, monkeypatch):
    monkeypatch.delenv("CI", raising=False)
    # No LLM available → soft-degrade path.
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[TimedSegment(text="你好", start=0.0, end=1.0)],
        subtitle_lang="en",
    )
    # Patch the LLM call to raise so we exercise the failure path.
    from movie_narrator.pipeline import translate as t_mod
    monkeypatch.setattr(
        t_mod, "_call_llm_chunk",
        lambda **kw: (_ for _ in ()).throw(RuntimeError("net down")),
    )
    translate_subtitles(ctx)
    assert ctx.status.translate == "failed"
    assert ctx.step_state.result == StepResult.WARNING
    assert ctx.translated_texts == ["你好"]  # soft-degrade: passthrough
    assert any("net down" in w for w in ctx.metadata.get("warnings", []))


def test_translate_supported_providers_includes_llm():
    assert "llm" in SUPPORTED_PROVIDERS


# ── _chunk_texts helper ─────────────────────────────────────


def test_chunk_texts_under_threshold_single_chunk():
    texts = ["a" * 100, "b" * 100, "c" * 100]
    chunks = _chunk_texts(texts, max_chars=4000, max_items=20)
    assert chunks == [[0, 1, 2]]


def test_chunk_texts_splits_by_item_count():
    texts = [f"line {i}" for i in range(45)]
    chunks = _chunk_texts(texts, max_chars=10_000, max_items=20)
    # 45 / 20 → 20 + 20 + 5
    assert [len(c) for c in chunks] == [20, 20, 5]
    # indices preserved in order
    assert sum(chunks, []) == list(range(45))


def test_chunk_texts_splits_by_char_budget():
    texts = ["x" * 500] * 10  # 5000 chars total
    chunks = _chunk_texts(texts, max_chars=1500, max_items=100)
    # 500 + 500 = 1000 < 1500, 500 + 500 + 500 = 1500 boundary, push to next
    # so each chunk holds 3 items (1500 chars) — 10 / 3 → 3+3+3+1
    assert [len(c) for c in chunks] == [3, 3, 3, 1]


# ── _is_blank helper ────────────────────────────────────────


def test_is_blank_variants():
    assert _is_blank("")
    assert _is_blank("   ")
    assert _is_blank("\t\n")
    assert _is_blank("\u3000")  # full-width space
    assert _is_blank(" \u3000 ")
    assert not _is_blank("hello")
    assert not _is_blank(" 你好 ")


# ── subtitle.py multi-file + render_subtitle_path ───────────


def test_subtitle_writes_only_original_without_translations(tmp_path):
    srt = tmp_path / "subtitle.srt"
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[
            TimedSegment(text="你好", start=0.0, end=1.5),
            TimedSegment(text="世界", start=1.5, end=3.0),
        ],
    )
    generate_subtitle(ctx)
    assert srt.exists()
    content = srt.read_text(encoding="utf-8")
    assert "你好" in content
    assert "世界" in content
    assert ctx.subtitle_paths is not None
    assert ctx.subtitle_paths.original == str(srt)
    assert ctx.subtitle_paths.translated is None
    assert ctx.subtitle_paths.bilingual is None
    # default mode = original → render_subtitle_path == original
    assert ctx.render_subtitle_path == str(srt)


def test_subtitle_writes_three_files_when_translations_aligned(tmp_path):
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[
            TimedSegment(text="你好", start=0.0, end=1.5),
            TimedSegment(text="世界", start=1.5, end=3.0),
        ],
        translated_texts=["hello", "world"],
        subtitle_lang="en",
    )
    generate_subtitle(ctx)
    srt = tmp_path / "subtitle.srt"
    en = tmp_path / "subtitle.en.srt"
    bi = tmp_path / "subtitle.bilingual.srt"
    assert srt.exists()
    assert en.exists()
    assert bi.exists()

    en_content = en.read_text(encoding="utf-8")
    assert "hello" in en_content
    assert "world" in en_content
    assert "你好" not in en_content  # pure translated

    bi_content = bi.read_text(encoding="utf-8")
    # Cue 1 body should be "你好\nhello" with explicit LF
    assert "你好\nhello" in bi_content
    assert "世界\nworld" in bi_content


def test_subtitle_bilingual_uses_lf_not_crlf(tmp_path):
    """Cue body must be LF-separated, even on Windows."""
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[TimedSegment(text="你好", start=0.0, end=1.0)],
        translated_texts=["hello"],
        subtitle_lang="en",
    )
    generate_subtitle(ctx)
    bi = tmp_path / "subtitle.bilingual.srt"
    raw = bi.read_bytes()
    # The cue body line should be "你好\nhello" (LF only)
    assert "你好\nhello".encode("utf-8") in raw
    # No CRLF immediately between the two translations
    assert "你好\r\nhello".encode("utf-8") not in raw


def test_subtitle_filename_lowercases_lang_tag(tmp_path):
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[TimedSegment(text="你好", start=0.0, end=1.0)],
        translated_texts=["hello"],
        subtitle_lang="zh-TW",
    )
    generate_subtitle(ctx)
    assert (tmp_path / "subtitle.zh-tw.srt").exists()


def test_subtitle_render_subtitle_path_resolves_per_mode(tmp_path):
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[TimedSegment(text="你好", start=0.0, end=1.0)],
        translated_texts=["hello"],
        subtitle_lang="en",
    )
    # mode = original → pick original even though translated exists
    ctx.metadata["subtitle_mode"] = "original"
    generate_subtitle(ctx)
    assert ctx.render_subtitle_path == ctx.subtitle_paths.original
    assert ctx.render_subtitle_path != ctx.subtitle_paths.translated

    # mode = translated → pick translated
    ctx.metadata["subtitle_mode"] = "translated"
    # re-run to re-resolve (subtitle_paths already populated; resolve helper
    # is pure, so calling again mutates only render_subtitle_path)
    from movie_narrator.pipeline.subtitle import _resolve_render_subtitle_path
    ctx.render_subtitle_path = _resolve_render_subtitle_path(ctx, ctx.subtitle_paths)
    assert ctx.render_subtitle_path == ctx.subtitle_paths.translated

    # mode = bilingual → pick bilingual
    ctx.metadata["subtitle_mode"] = "bilingual"
    ctx.render_subtitle_path = _resolve_render_subtitle_path(ctx, ctx.subtitle_paths)
    assert ctx.render_subtitle_path == ctx.subtitle_paths.bilingual


def test_subtitle_render_subtitle_path_falls_back_when_track_missing(tmp_path):
    """When mode=translated but no translated_texts, fall back to original."""
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[TimedSegment(text="你好", start=0.0, end=1.0)],
        subtitle_mode="translated",
        subtitle_lang="en",
    )
    generate_subtitle(ctx)
    assert ctx.subtitle_paths.translated is None
    assert ctx.render_subtitle_path == ctx.subtitle_paths.original


def test_subtitle_skips_misaligned_translations(tmp_path):
    """Length mismatch → fall back to original-only output, no partial files."""
    ctx = _make_ctx(
        tmp_path,
        timed_segments=[
            TimedSegment(text="a", start=0.0, end=1.0),
            TimedSegment(text="b", start=1.0, end=2.0),
        ],
        translated_texts=["only-one"],  # length 1, segments length 2
        subtitle_lang="en",
    )
    generate_subtitle(ctx)
    assert (tmp_path / "subtitle.srt").exists()
    assert not (tmp_path / "subtitle.en.srt").exists()
    assert not (tmp_path / "subtitle.bilingual.srt").exists()
    assert ctx.subtitle_paths.translated is None


# ── merge_job validation ────────────────────────────────────


def _empty_settings():
    """Minimal Settings stand-in (we only test merge_job's surface area)."""
    from movie_narrator.config import Settings
    return Settings()


def test_merge_subtitle_mode_translated_requires_lang():
    with pytest.raises(JobConfigError, match="requires"):
        merge_job(
            cli={"subtitle_mode": "translated"},
            job=None,
            settings=_empty_settings(),
        )


def test_merge_subtitle_mode_bilingual_requires_lang():
    with pytest.raises(JobConfigError, match="requires"):
        merge_job(
            cli={"subtitle_mode": "bilingual"},
            job=None,
            settings=_empty_settings(),
        )


def test_merge_subtitle_mode_invalid_value_rejected():
    with pytest.raises(JobConfigError, match="subtitle_mode must be"):
        merge_job(
            cli={"subtitle_mode": "sideways"},
            job=None,
            settings=_empty_settings(),
        )


def test_merge_subtitle_defaults_to_original():
    resolved = merge_job(cli={}, job=None, settings=_empty_settings())
    assert resolved.subtitle_mode == "original"
    assert resolved.subtitle_lang is None


def test_merge_subtitle_lang_from_yaml():
    job = JobConfig(subtitle_lang="ja", subtitle_mode="bilingual")
    resolved = merge_job(cli={}, job=job, settings=_empty_settings())
    assert resolved.subtitle_lang == "ja"
    assert resolved.subtitle_mode == "bilingual"


def test_merge_workflow_steps_translate_key():
    job = JobConfig(steps=JobSteps(translate=False))
    resolved = merge_job(cli={}, job=job, settings=_empty_settings())
    assert resolved.workflow_steps.get("translate") is False


def test_merge_params_translate_keys():
    job = JobConfig(
        params=__import__("movie_narrator.workflow.schema", fromlist=["JobParams"]).JobParams(
            translate_provider="llm",
            translate_retries=5,
        )
    )
    resolved = merge_job(cli={}, job=job, settings=_empty_settings())
    assert resolved.params["translate_provider"] == "llm"
    assert resolved.params["translate_retries"] == 5


def test_jobconfig_subtitle_mode_validator_rejects_invalid():
    with pytest.raises(ValueError, match="subtitle_mode"):
        JobConfig(subtitle_mode="garbage")


# ── _call_llm_chunk dependency injection (REC-10) ──────────


def _mock_llm_cm(translations=None, error=None):
    """Build a mock llm_factory returning a context-managed mock LLM."""
    from unittest.mock import MagicMock

    mock_llm = MagicMock()
    mock_llm.model = "test-model"
    if error:
        mock_llm.client.chat.completions.create.side_effect = error
    else:
        content = json.dumps({"translations": translations or []}, ensure_ascii=False)
        mock_llm.client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=content))]
        )

    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_llm)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return MagicMock(return_value=mock_cm)


def test_call_llm_chunk_with_injected_factory():
    """_call_llm_chunk accepts llm_factory and returns translations."""
    factory = _mock_llm_cm(translations=["hello", "world"])
    result = _call_llm_chunk(
        cues=["你好", "世界"],
        target_lang="en",
        source_lang="zh-CN",
        llm_factory=factory,
    )
    assert result == ["hello", "world"]
    factory.assert_called_once()


def test_call_llm_chunk_injected_factory_raises_on_mismatch():
    """_call_llm_chunk raises when translation count doesn't match cues."""
    factory = _mock_llm_cm(translations=["only-one"])
    with pytest.raises(ValueError, match="expected 2"):
        _call_llm_chunk(
            cues=["你好", "世界"],
            target_lang="en",
            source_lang="zh-CN",
            llm_factory=factory,
        )


def test_translate_via_llm_chunk_failure_degrades():
    """_translate_via_llm with failing factory raises _ChunkFailure."""
    from movie_narrator.pipeline.translate import _ChunkFailure

    factory = _mock_llm_cm(error=RuntimeError("LLM timeout"))
    texts = ["你好", "世界"]
    with pytest.raises(_ChunkFailure, match="LLM timeout"):
        _translate_via_llm(
            texts,
            target_lang="en",
            source_lang="zh-CN",
            retries=2,
            llm_factory=factory,
        )


def test_translate_via_llm_retries_before_failure():
    """_translate_via_llm retries the specified number of times before raising."""
    from movie_narrator.pipeline.translate import _ChunkFailure

    factory = _mock_llm_cm(error=RuntimeError("LLM timeout"))
    with pytest.raises(_ChunkFailure):
        _translate_via_llm(
            ["你好"],
            target_lang="en",
            source_lang="zh-CN",
            retries=2,
            llm_factory=factory,
        )
    # 1 initial attempt + 2 retries = 3 calls
    assert factory.call_count == 3
