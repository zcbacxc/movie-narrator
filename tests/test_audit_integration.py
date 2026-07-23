"""P1+P2: Integration tests for Stage D audit fields.

Replaces the 50-min L2+ handtest driver with CI-friendly tests that
verify audit fields (diversity.swaps_log + script_truncated) are
correctly written to metadata under controlled conditions.

P2 (force-trigger diversity swaps): uses a small scene pool (3 scenes)
with many narration segments (10) and tight max_reuse=1 to naturally
trigger swaps — no production code changes needed.

P1 (audit field schema validation): asserts the exact schema from
the L2+ handtest (2026-07-23) is present and populated.
"""

import json
import sys
from types import ModuleType
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from movie_narrator.models import Context, Scene, Services, TimedSegment
from movie_narrator.pipeline import match as match_module
from movie_narrator.pipeline.match import match_clips
from movie_narrator.pipeline.script import (
    _expand_beats_to_script,
    _truncate_to_max_chars,
)


@pytest.fixture(autouse=True)
def _clear_embedding_cache():
    """Clear lru_cache between tests so mock SentenceTransformer doesn't leak."""
    match_module._load_embedding_model.cache_clear()
    yield
    match_module._load_embedding_model.cache_clear()


# ── P2: diversity.swaps_log triggered with small scene pool ──


def test_diversity_swaps_log_triggered_with_small_scene_pool(tmp_path, monkeypatch):
    """P2: With 3 scenes + 10 narration segments + max_reuse=1,
    diversity swaps must trigger and swaps_log must be populated.

    This replaces the handtest finding that swaps=0 because the real
    movie had 2424 scenes (too many to trigger reuse). By using a
    minimal scene pool, we force the swap path and verify the audit
    log schema end-to-end through match_clips().
    """
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
    )
    ctx.services = Services(console=MagicMock())
    ctx.status.scene = "success"
    (tmp_path / "video.mp4").write_bytes(b"00")

    # 3 scenes — small pool, segments will have to reuse
    ctx.scenes = [
        Scene(index=0, start=0.0, end=10.0),
        Scene(index=1, start=10.0, end=20.0),
        Scene(index=2, start=20.0, end=30.0),
    ]

    # 10 narration segments — more than 3x the scene pool
    ctx.timed_segments = [
        TimedSegment(text=f"segment {i}", start=float(i * 3), end=float(i * 3 + 2.5))
        for i in range(10)
    ]

    # Tight diversity: window=3, max_reuse=1
    ctx.metadata["match_diversity_window"] = 3
    ctx.metadata["match_max_scene_reuse"] = 1
    # Disable embedding path — we want pure heuristic to isolate diversity
    monkeypatch.setattr(match_module, "probe", lambda name: (False, ""))

    match_clips(ctx)

    summary = ctx.metadata["match_summary"]
    diversity = summary["diversity"]

    # Schema validation (P1)
    assert "swaps" in diversity
    assert "swaps_log" in diversity
    assert "window" in diversity
    assert "max_reuse" in diversity

    # Values from metadata
    assert diversity["window"] == 3
    assert diversity["max_reuse"] == 1

    # P2: swaps must be > 0 — 10 segments over 3 scenes with max_reuse=1
    # forces at least some swaps in windows of 3.
    assert diversity["swaps"] > 0, (
        "Expected diversity swaps > 0 with 10 segments over 3 scenes "
        "and max_reuse=1, but got 0"
    )

    # swaps_log must be a non-empty list with correct schema
    assert isinstance(diversity["swaps_log"], list)
    assert len(diversity["swaps_log"]) == diversity["swaps"]

    for entry in diversity["swaps_log"]:
        assert "segment_index" in entry
        assert "old_scene" in entry
        assert "new_scene" in entry
        assert entry["old_scene"] != entry["new_scene"], (
            "swap entry must have different old/new scene"
        )


def test_diversity_swaps_zero_with_large_scene_pool(tmp_path, monkeypatch):
    """P1: With ample scenes, swaps=0 and swaps_log=[] (baseline).

    Mirrors the handtest finding: 2424 scenes → natural diversity →
    no swaps needed. Verifies the schema is still present.
    """
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
    )
    ctx.services = Services(console=MagicMock())
    ctx.status.scene = "success"
    (tmp_path / "video.mp4").write_bytes(b"00")

    # 20 scenes — ample for 5 segments
    ctx.scenes = [
        Scene(index=i, start=float(i * 5), end=float(i * 5 + 5))
        for i in range(20)
    ]
    ctx.timed_segments = [
        TimedSegment(text=f"segment {i}", start=float(i * 3), end=float(i * 3 + 2.5))
        for i in range(5)
    ]

    ctx.metadata["match_diversity_window"] = 3
    ctx.metadata["match_max_scene_reuse"] = 2

    monkeypatch.setattr(match_module, "probe", lambda name: (False, ""))

    match_clips(ctx)

    diversity = ctx.metadata["match_summary"]["diversity"]
    assert diversity["swaps"] == 0
    assert diversity["swaps_log"] == []


# ── P1: script_truncated audit field validation ──


def _mock_settings():
    s = MagicMock()
    s.script_retries = 3
    s.script_retry_delay = 0
    s.script_temperature = 0.7
    s.script_expand_temperature = 0.5
    s.script_max_tokens = 2048
    s.research_temperature = 0.3
    s.research_max_tokens = 1024
    return s


def _mock_llm_response(json_str: str):
    resp = MagicMock()
    resp.choices = [MagicMock()]
    resp.choices[0].message.content = json_str
    return resp


def _mock_llm_cm(response):
    mock_llm = MagicMock()
    mock_llm.model = "test-model"
    mock_llm.client.chat.completions.create.return_value = response
    mock_cm = MagicMock()
    mock_cm.__enter__ = MagicMock(return_value=mock_llm)
    mock_cm.__exit__ = MagicMock(return_value=False)
    return mock_cm


def test_script_truncated_audit_field_populated(tmp_path):
    """P1: When LLM returns text exceeding max_chars, script_truncated
    metadata must be populated with count, max_chars, and details.

    Mirrors the handtest scenario (max_chars=3, LLM returns 7-10 char
    sentences) but in a controlled unit test.
    """
    ctx = Context(
        movie_name="test_movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )
    ctx.metadata["prompt_max_chars_per_sentence"] = 5

    # LLM returns 4 segments, all exceeding max_chars=5
    long_texts = [
        "这是一句很长的中文句子",   # 11 chars
        "另一段超长的旁白内容",     # 10 chars
        "第三段也超过了限制",       # 9 chars
        "最后一段同样很长",         # 8 chars
    ]
    seg_json = json.dumps(
        {"segments": [{"text": t} for t in long_texts]},
        ensure_ascii=False,
    )

    mock_cm = _mock_llm_cm(_mock_llm_response(seg_json))

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            segments = _expand_beats_to_script(
                ctx, _mock_settings(), mock_cm.__enter__(),
                beats=["beat1", "beat2", "beat3", "beat4"],
                target_count=4,
            )

    # All segments should be truncated to <= 5 chars
    for seg in segments:
        assert len(seg.text) <= 5, f"segment '{seg.text}' exceeds max_chars=5"

    # Audit metadata must be present
    assert "script_truncated" in ctx.metadata
    truncated = ctx.metadata["script_truncated"]

    assert truncated["count"] == 4
    assert truncated["max_chars"] == 5
    assert len(truncated["details"]) == 4

    for detail in truncated["details"]:
        assert "original_len" in detail
        assert "truncated_len" in detail
        assert detail["original_len"] > detail["truncated_len"]
        assert detail["truncated_len"] <= 5


def test_script_truncated_absent_when_no_truncation(tmp_path):
    """P1: When LLM respects max_chars, script_truncated must NOT be set.

    Verifies the field is only written when truncation actually occurs
    (zero overhead when LLM obeys).
    """
    ctx = Context(
        movie_name="test_movie",
        style="热血搞笑",
        duration=60,
        output_dir=str(tmp_path),
        services=Services(console=MagicMock()),
    )
    ctx.metadata["prompt_max_chars_per_sentence"] = 20

    # LLM returns short segments within max_chars=20
    short_texts = ["短句", "另一句", "第三句", "最后"]
    seg_json = json.dumps(
        {"segments": [{"text": t} for t in short_texts]},
        ensure_ascii=False,
    )

    mock_cm = _mock_llm_cm(_mock_llm_response(seg_json))

    with patch("movie_narrator.pipeline.script.get_settings", return_value=_mock_settings()):
        with patch("movie_narrator.pipeline.script.get_llm_client", return_value=mock_cm):
            segments = _expand_beats_to_script(
                ctx, _mock_settings(), mock_cm.__enter__(),
                beats=["beat1", "beat2", "beat3", "beat4"],
                target_count=4,
            )

    # No truncation → no audit field
    assert "script_truncated" not in ctx.metadata
    assert len(segments) == 4
