import math
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pytest

from movie_narrator.models import Context, MatchedClip, Scene, TimedSegment
from movie_narrator.pipeline import match as match_module
from movie_narrator.pipeline.match import match_clips


@pytest.fixture(autouse=True)
def _clear_embedding_cache():
    """Clear lru_cache between tests so mock SentenceTransformer doesn't leak."""
    match_module._load_embedding_model.cache_clear()
    yield
    match_module._load_embedding_model.cache_clear()


def _make_ctx(tmp_path, source_video="video.mp4"):
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / source_video),
        timed_segments=[
            TimedSegment(text="A", start=0.0, end=2.0),
            TimedSegment(text="B", start=2.5, end=5.0),
        ],
        scenes=[
            Scene(index=0, start=0.0, end=10.0),
        ],
    )
    ctx.status.scene = "success"
    return ctx


def test_match_clips_skipped_no_source(tmp_path):
    ctx = Context(movie_name="m", output_dir=str(tmp_path))
    match_clips(ctx)
    assert ctx.status.match == "skipped"


def test_match_clips_disabled_when_scene_disabled(tmp_path):
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "v.mp4"),
    )
    ctx.status.scene = "disabled"
    match_clips(ctx)
    assert ctx.status.match == "disabled"


def test_match_clips_skipped_no_scenes(tmp_path):
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "v.mp4"),
        timed_segments=[TimedSegment(text="A", start=0.0, end=2.0)],
    )
    ctx.status.scene = "success"
    match_clips(ctx)
    assert ctx.status.match == "skipped"


def test_match_clips_success(tmp_path):
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    match_clips(ctx)
    assert ctx.status.match == "success"
    assert len(ctx.matched_clips) == 2
    assert (tmp_path / "matches.json").exists()
    assert all(m.source == "heuristic" for m in ctx.matched_clips)
    assert all(m.score == 1.0 for m in ctx.matched_clips)


def test_match_clips_embedding_disabled_keeps_heuristic(tmp_path, monkeypatch):
    """sentence_transformers probe unavailable → keep heuristic path."""
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    monkeypatch.setattr(
        match_module,
        "probe",
        lambda name: (False, "pip install movie-narrator[ml]") if name == "sentence_transformers" else (True, ""),
    )
    match_clips(ctx)
    assert ctx.status.match == "success"
    assert all(m.source == "heuristic" for m in ctx.matched_clips)


# ── F1: match_summary full schema validation ──


def test_match_summary_full_schema_when_embedding_path_runs(tmp_path, monkeypatch):
    """F1: match_summary contains all CORE_ENGINE_TREATMENT_PLAN §5.2.3 fields
    when embedding path runs successfully with real transcript."""
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    ctx.scenes = [
        Scene(index=0, start=0.0, end=4.0),
        Scene(index=1, start=4.0, end=10.0),
    ]
    ctx.timed_segments = [
        TimedSegment(text="alpha alpha", start=0.0, end=2.0),
        TimedSegment(text="beta beta", start=2.5, end=5.0),
    ]

    monkeypatch.setattr(match_module, "probe", lambda name: (True, ""))
    mock_transcript = [
        {"start": 0.0, "end": 3.5, "text": "alpha scene zero"},
        {"start": 4.0, "end": 9.0, "text": "beta scene one"},
    ]
    monkeypatch.setattr(
        match_module, "_transcribe_video_audio", lambda *a, **k: mock_transcript
    )

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            arr = np.zeros((len(texts), 2), dtype=float)
            for i, t in enumerate(texts):
                if "alpha" in t or "scene 0" in t.lower():
                    arr[i] = np.array([1.0, 0.0])
                elif "beta" in t or "scene 1" in t.lower():
                    arr[i] = np.array([0.0, 1.0])
            return arr

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    summary = ctx.metadata["match_summary"]

    # F1: all required fields present
    required_fields = [
        "version", "status", "segments", "scenes_in", "scenes_after_merge",
        "scenes_after_drop", "merge_min_duration", "drop_min_duration",
        "min_score", "speed_clamp", "source_counts", "heuristic_ratio",
        "embedding_ratio", "score", "raw_score", "speed_factor",
        "low_score_fallback_count", "captioning", "embedding_model",
        "degraded_reason", "diversity",
        # back-compat
        "total", "embedding", "heuristic", "captions_fake",
    ]
    for field in required_fields:
        assert field in summary, f"F1: missing field '{field}' in match_summary"

    # Specific value checks
    assert summary["version"] == 1
    assert summary["status"] == "success"
    assert summary["segments"] == 2
    assert summary["scenes_in"] == 2
    assert summary["source_counts"]["embedding"] == 2
    assert summary["source_counts"]["heuristic"] == 0
    assert summary["embedding_ratio"] == 1.0
    assert summary["heuristic_ratio"] == 0.0
    assert summary["low_score_fallback_count"] == 0
    assert summary["degraded_reason"] is None  # not degraded
    assert summary["captioning"]["used"] is True
    assert summary["captioning"]["usable_label_ratio"] == 1.0  # all real
    # raw_score should have stats (2 embedding scores collected)
    assert summary["raw_score"] is not None
    assert summary["raw_score"]["n"] == 2


def test_match_summary_degraded_reason_all_heuristic(tmp_path, monkeypatch):
    """F1: degraded_reason='all_heuristic' when all clips fall back to heuristic."""
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    ctx.scenes = [
        Scene(index=0, start=0.0, end=4.0),
        Scene(index=1, start=4.0, end=10.0),
    ]
    # sentence_transformers NOT available → all heuristic
    monkeypatch.setattr(
        match_module, "probe", lambda name: (False, "") if name == "sentence_transformers" else (False, ""),
    )

    match_clips(ctx)
    summary = ctx.metadata["match_summary"]

    assert summary["source_counts"]["heuristic"] == len(ctx.timed_segments)
    assert summary["heuristic_ratio"] == 1.0
    assert summary["degraded_reason"] == "all_heuristic"
    assert summary["low_score_fallback_count"] == 0  # no embedding attempted


def test_match_summary_low_score_fallback_count(tmp_path, monkeypatch):
    """F1: low_score_fallback_count tracks segments that fell back due to low score."""
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    ctx.scenes = [
        Scene(index=0, start=0.0, end=4.0),
        Scene(index=1, start=4.0, end=10.0),
    ]
    ctx.timed_segments = [
        TimedSegment(text="alpha alpha", start=0.0, end=2.0),
        TimedSegment(text="gamma gamma", start=2.5, end=5.0),  # won't match well
    ]

    monkeypatch.setattr(match_module, "probe", lambda name: (True, ""))
    mock_transcript = [
        {"start": 0.0, "end": 3.5, "text": "alpha scene zero"},
        {"start": 4.0, "end": 9.0, "text": "beta scene one"},
    ]
    monkeypatch.setattr(
        match_module, "_transcribe_video_audio", lambda *a, **k: mock_transcript
    )

    # Embeddings: alpha matches scene 0 strongly, gamma is orthogonal to both.
    # NOTE: _embed_texts L2-normalizes vectors, so we must use vectors that
    # remain orthogonal after normalization. gamma=[1,1] normalizes to
    # [0.707, 0.707], which has dot 0.707 with both scene axes — too high.
    # Use gamma=[1,-1] (normalizes to [0.707,-0.707]): dot with [1,0] is
    # 0.707, still too high. The only way to get a low cosine with both
    # [1,0] and [0,1] is to have a vector whose max component is small.
    # Use a 3D embedding space: scene0=[1,0,0], scene1=[0,1,0], gamma=[0,0,1].
    # After normalization all are unit vectors; gamma's dot with both = 0.
    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            arr = np.zeros((len(texts), 3), dtype=float)
            for i, t in enumerate(texts):
                if "alpha" in t or "scene 0" in t.lower():
                    arr[i] = np.array([1.0, 0.0, 0.0])
                elif "beta" in t or "scene 1" in t.lower():
                    arr[i] = np.array([0.0, 1.0, 0.0])
                elif "gamma" in t:
                    arr[i] = np.array([0.0, 0.0, 1.0])  # orthogonal → cosine=0
            return arr

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    summary = ctx.metadata["match_summary"]

    # gamma should have score=0.0 < min_score=0.25 → fallback to heuristic
    assert summary["low_score_fallback_count"] >= 1
    # raw_score should still contain the original (low) score
    assert summary["raw_score"] is not None
    assert summary["raw_score"]["n"] == 2  # both attempted embedding
    assert summary["raw_score"]["min"] == 0.0  # gamma's score


# ── F2: is_fake contract tests ──


def test_build_scene_captions_returns_tuples_with_is_fake_flag():
    """F2: _build_scene_captions returns List[Tuple[str, bool]] not List[str].

    The is_fake flag replaces fragile string-pattern matching for detecting
    placeholder labels. Callers should use the flag, not startswith().
    """
    from movie_narrator.pipeline.match import _build_scene_captions

    scenes = [
        Scene(index=0, start=0.0, end=5.0),
        Scene(index=1, start=5.0, end=10.0),
    ]

    # No transcript → all fake
    result = _build_scene_captions(scenes, None)
    assert isinstance(result, list)
    assert len(result) == 2
    for item in result:
        assert isinstance(item, tuple)
        assert len(item) == 2
        assert isinstance(item[0], str)
        assert isinstance(item[1], bool)
        assert item[1] is True  # all fake when no transcript


def test_build_scene_captions_real_transcript_marks_is_fake_false():
    """F2: scenes with overlapping speech get is_fake=False."""
    from movie_narrator.pipeline.match import _build_scene_captions

    scenes = [
        Scene(index=0, start=0.0, end=5.0),
        Scene(index=1, start=5.0, end=10.0),
    ]
    transcript = [
        {"start": 0.0, "end": 4.0, "text": "real speech in scene 0"},
        {"start": 6.0, "end": 9.0, "text": "real speech in scene 1"},
    ]

    result = _build_scene_captions(scenes, transcript)
    assert len(result) == 2
    assert result[0][1] is False  # scene 0 has speech → real
    assert result[1][1] is False  # scene 1 has speech → real
    assert "real speech" in result[0][0]
    assert "real speech" in result[1][0]


def test_build_scene_captions_mixed_real_and_fake():
    """F2: scenes without overlapping speech get is_fake=True even with transcript."""
    from movie_narrator.pipeline.match import _build_scene_captions

    scenes = [
        Scene(index=0, start=0.0, end=5.0),   # has speech
        Scene(index=1, start=5.0, end=10.0),  # no speech (gap)
        Scene(index=2, start=10.0, end=15.0), # has speech
    ]
    transcript = [
        {"start": 0.0, "end": 4.0, "text": "speech in scene 0"},
        {"start": 11.0, "end": 14.0, "text": "speech in scene 2"},
    ]

    result = _build_scene_captions(scenes, transcript)
    assert len(result) == 3
    assert result[0][1] is False  # scene 0 has speech
    assert result[1][1] is True   # scene 1 no speech → fake
    assert result[2][1] is False  # scene 2 has speech


# ── F1 back-compat: old metadata_export consumers must not break ──


def test_match_summary_keys_compatible_with_old_metadata_export(tmp_path, monkeypatch):
    """F1 back-compat: match_summary preserves the 4 legacy fields
    (total/embedding/heuristic/captions_fake) so existing consumers
    (metadata_export, L2 checklist jq queries, older scripts) don't break.

    This test was listed in the PR #56 review report as 'excellent' but
    was never actually written — the report praised a non-existent test.
    This regression test closes that gap.
    """
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    ctx.scenes = [
        Scene(index=0, start=0.0, end=4.0),
        Scene(index=1, start=4.0, end=10.0),
    ]
    ctx.timed_segments = [
        TimedSegment(text="alpha alpha", start=0.0, end=2.0),
        TimedSegment(text="beta beta", start=2.5, end=5.0),
    ]

    # sentence_transformers available → embedding path runs
    monkeypatch.setattr(match_module, "probe", lambda name: (True, ""))
    mock_transcript = [
        {"start": 0.0, "end": 3.5, "text": "alpha scene zero"},
        {"start": 4.0, "end": 9.0, "text": "beta scene one"},
    ]
    monkeypatch.setattr(
        match_module, "_transcribe_video_audio", lambda *a, **k: mock_transcript
    )

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            arr = np.zeros((len(texts), 3), dtype=float)
            for i, t in enumerate(texts):
                if "alpha" in t or "scene 0" in t.lower():
                    arr[i] = np.array([1.0, 0.0, 0.0])
                elif "beta" in t or "scene 1" in t.lower():
                    arr[i] = np.array([0.0, 1.0, 0.0])
            return arr

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    summary = ctx.metadata["match_summary"]

    # Legacy fields must all be present (back-compat contract)
    legacy_fields = ["total", "embedding", "heuristic", "captions_fake"]
    for field in legacy_fields:
        assert field in summary, (
            f"F1 back-compat: legacy field '{field}' missing from match_summary — "
            f"existing metadata_export consumers will break"
        )

    # Legacy fields must be consistent with new schema fields
    assert summary["total"] == summary["segments"]
    assert summary["embedding"] == summary["source_counts"]["embedding"]
    assert summary["heuristic"] == summary["source_counts"]["heuristic"]
    assert isinstance(summary["captions_fake"], bool)


def test_match_summary_legacy_fields_when_all_heuristic(tmp_path, monkeypatch):
    """F1 back-compat: legacy fields present even when embedding path doesn't run."""
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    ctx.scenes = [
        Scene(index=0, start=0.0, end=4.0),
        Scene(index=1, start=4.0, end=10.0),
    ]

    # sentence_transformers NOT available → all heuristic
    monkeypatch.setattr(
        match_module, "probe", lambda name: (False, ""),
    )

    match_clips(ctx)
    summary = ctx.metadata["match_summary"]

    # Legacy fields still present
    assert "total" in summary
    assert "embedding" in summary
    assert "heuristic" in summary
    assert "captions_fake" in summary
    # Values consistent
    assert summary["total"] == summary["segments"]
    assert summary["embedding"] == 0
    assert summary["heuristic"] == summary["total"]
    assert summary["heuristic_ratio"] == 1.0


# ── MS-02: fake caption detection regression tests ──


def test_match_captions_fake_when_no_transcript(tmp_path, monkeypatch):
    """MS-02: >70% placeholder labels → match_captions_fake=True, force heuristic.

    When WhisperX is available but returns no transcript (or whisperx
    not available), scene captions are all placeholder labels like
    "scene 0 from 0.0s to 10.0s". Embedding re-rank against these is
    meaningless, so the fix forces heuristic match and sets the flag.
    """
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    # Two scenes so embedding path would normally trigger
    ctx.scenes = [
        Scene(index=0, start=0.0, end=5.0),
        Scene(index=1, start=5.0, end=10.0),
    ]

    # sentence_transformers available, but whisperx NOT available
    # → all captions will be placeholders → fake_ratio = 100% > 70%
    monkeypatch.setattr(
        match_module,
        "probe",
        lambda name: (True, "") if name == "sentence_transformers" else (False, ""),
    )

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            # Would be called if embedding path ran, but MS-02 should
            # short-circuit before reaching here
            return np.zeros((len(texts), 2), dtype=float)

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)

    assert ctx.status.match == "success"
    # MS-02: fake caption flag set
    assert ctx.metadata.get("match_captions_fake") is True
    # All clips should be heuristic (embedding path short-circuited)
    assert all(m.source == "heuristic" for m in ctx.matched_clips)
    # match_summary should reflect this
    summary = ctx.metadata.get("match_summary", {})
    assert summary.get("captions_fake") is True
    assert summary.get("heuristic_ratio") == 1.0


def test_match_captions_real_when_transcript_available(tmp_path, monkeypatch):
    """MS-02: real transcript → match_captions_fake=False, embedding path runs."""
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    ctx.scenes = [
        Scene(index=0, start=0.0, end=4.0),
        Scene(index=1, start=4.0, end=10.0),
    ]
    ctx.timed_segments = [
        TimedSegment(text="alpha alpha alpha", start=0.0, end=2.0),
        TimedSegment(text="beta beta beta", start=2.5, end=5.0),
    ]

    # Both available
    monkeypatch.setattr(
        match_module, "probe", lambda name: (True, ""),
    )

    # Real transcript covering both scenes
    mock_transcript = [
        {"start": 0.0, "end": 3.5, "text": "alpha scene zero"},
        {"start": 4.0, "end": 9.0, "text": "beta scene one"},
    ]
    monkeypatch.setattr(
        match_module, "_transcribe_video_audio", lambda *a, **k: mock_transcript
    )

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            arr = np.zeros((len(texts), 2), dtype=float)
            for i, t in enumerate(texts):
                if "alpha" in t:
                    arr[i] = np.array([1.0, 0.0])
                elif "beta" in t:
                    arr[i] = np.array([0.0, 1.0])
                elif "scene 0" in t:
                    arr[i] = np.array([1.0, 0.0])
                elif "scene 1" in t:
                    arr[i] = np.array([0.0, 1.0])
            return arr

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)

    assert ctx.status.match == "success"
    # MS-02: real captions → not fake
    assert ctx.metadata.get("match_captions_fake") is False
    # Embedding path should have run
    summary = ctx.metadata.get("match_summary", {})
    assert summary.get("captions_fake") is False
    assert summary.get("embedding", 0) > 0  # at least one embedding match


# ── tiny-scene drop + merge defaults ───────────────────────


def test_match_drops_tiny_scenes(tmp_path):
    """Scenes shorter than match_drop_scene_min_duration are dropped.

    Merge is disabled (scene_merge_min_duration=0) so the tiny scene is
    not absorbed first, isolating the drop behaviour. After dropping the
    0.2s scene, the remaining scene is re-indexed from 0; no matched clip
    should use source content from the dropped scene's time range.
    """
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
        timed_segments=[
            TimedSegment(text="A", start=0.0, end=2.0),
            TimedSegment(text="B", start=2.5, end=5.0),
        ],
        scenes=[
            Scene(index=0, start=0.0, end=0.2),   # tiny (< 0.4s)
            Scene(index=1, start=0.2, end=8.0),   # long
        ],
    )
    ctx.status.scene = "success"
    ctx.metadata["scene_merge_min_duration"] = 0.0  # disable merge to isolate drop
    (tmp_path / "video.mp4").write_bytes(b"00")
    match_clips(ctx)
    assert ctx.status.match == "success"
    # No clip should use source content from the dropped scene (0.0-0.2).
    assert all(m.src_start >= 0.2 for m in ctx.matched_clips)


def test_match_tiny_scene_filter_keeps_all_if_all_tiny(tmp_path):
    """If dropping would remove every scene, keep them all (last-resort)."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
        timed_segments=[TimedSegment(text="A", start=0.0, end=1.0)],
        scenes=[Scene(index=0, start=0.0, end=0.1)],  # only scene, tiny
    )
    ctx.status.scene = "success"
    ctx.metadata["scene_merge_min_duration"] = 0.0
    (tmp_path / "video.mp4").write_bytes(b"00")
    match_clips(ctx)
    # Should still produce a match (didn't drop the only scene).
    assert ctx.status.match == "success"
    assert len(ctx.matched_clips) >= 1


def test_match_default_merge_merges_short_scenes(tmp_path):
    """Default scene_merge_min_duration=2.0 merges scenes shorter than 2s."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
        timed_segments=[TimedSegment(text="A", start=0.0, end=5.0)],
        scenes=[
            Scene(index=0, start=0.0, end=1.0),   # short (< 2.0 default)
            Scene(index=1, start=1.0, end=8.0),   # long
        ],
    )
    ctx.status.scene = "success"
    (tmp_path / "video.mp4").write_bytes(b"00")
    match_clips(ctx)
    assert ctx.status.match == "success"


# ── score < min_score → heuristic fallback ──────────────────


def test_match_clips_low_score_falls_back_to_heuristic(tmp_path, monkeypatch):
    """Embedding score below min_score should fall back to heuristic, not drop."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
        timed_segments=[
            TimedSegment(text="alpha alpha", start=0.0, end=2.0),
            TimedSegment(text="beta beta", start=2.5, end=5.0),
        ],
        scenes=[
            Scene(index=0, start=0.0, end=5.0),
            Scene(index=1, start=5.0, end=10.0),
        ],
    )
    ctx.status.scene = "success"
    ctx.metadata["match_min_score"] = 0.99  # very high threshold
    (tmp_path / "video.mp4").write_bytes(b"00")

    monkeypatch.setattr(match_module, "probe", lambda name: (True, ""))
    monkeypatch.setattr(match_module, "_transcribe_video_audio", lambda *a, **kw: None)

    call_count = [0]

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            # Scene labels: call 1 → dims 0,1
            # Narration: call 2 → dims 2,3
            # Fully orthogonal → cosine sim = 0.0 < 0.99
            call_count[0] += 1
            n = len(texts)
            base = (call_count[0] - 1) * n  # offset by call
            dim = base + n
            arr = np.zeros((n, dim), dtype=float)
            for i in range(n):
                arr[i, base + i] = 1.0
            return arr

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    assert ctx.status.match == "success"
    # All clips should be present (none dropped)
    assert len(ctx.matched_clips) == 2
    # Low-score clips should fall back to heuristic, not embedding
    sources = [m.source for m in ctx.matched_clips]
    assert all(s == "heuristic" for s in sources), f"Expected all heuristic, got {sources}"


# ── Cache read/write/corrupt ────────────────────────────────


def test_match_clips_cache_write_and_hit(tmp_path, monkeypatch):
    """WhisperX transcript is cached on first call."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
        timed_segments=[
            TimedSegment(text="A", start=0.0, end=2.0),
            TimedSegment(text="B", start=2.5, end=5.0),
        ],
        scenes=[
            Scene(index=0, start=0.0, end=5.0),
            Scene(index=1, start=5.0, end=10.0),
        ],
    )
    ctx.status.scene = "success"
    (tmp_path / "video.mp4").write_bytes(b"00")

    call_count = [0]

    def fake_transcribe(video_path, output_dir, **kw):
        call_count[0] += 1
        # Simulate cache write (real function does this)
        from movie_narrator.pipeline.match import _cache_key
        cache_name = _cache_key(video_path, kw.get("model_name", "medium"), kw.get("language", "zh"))
        (output_dir / cache_name).write_text('[]', encoding="utf-8")
        return [{"start": 0.0, "end": 5.0, "text": "hello"}]

    monkeypatch.setattr(match_module, "_transcribe_video_audio", fake_transcribe)
    monkeypatch.setattr(match_module, "probe", lambda name: (True, ""))

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            return np.ones((len(texts), 2), dtype=float)

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    assert call_count[0] == 1
    # Verify cache file was written by the mock
    cache_files = list(tmp_path.glob("transcript_*.json"))
    assert len(cache_files) == 1


def test_match_clips_cache_corrupt_recovers(tmp_path, monkeypatch):
    """Corrupt cache file → re-transcribe, no crash."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
        timed_segments=[
            TimedSegment(text="A", start=0.0, end=2.0),
            TimedSegment(text="B", start=2.5, end=5.0),
        ],
        scenes=[
            Scene(index=0, start=0.0, end=5.0),
            Scene(index=1, start=5.0, end=10.0),
        ],
    )
    ctx.status.scene = "success"
    (tmp_path / "video.mp4").write_bytes(b"00")

    # Write corrupt cache file
    from movie_narrator.pipeline.match import _cache_key
    cache_name = _cache_key(str(tmp_path / "video.mp4"), "medium", "zh")
    (tmp_path / cache_name).write_text("NOT VALID JSON {{{", encoding="utf-8")

    call_count = [0]

    def fake_transcribe(video_path, output_dir, **kw):
        call_count[0] += 1
        return [{"start": 0.0, "end": 5.0, "text": "recovered"}]

    monkeypatch.setattr(match_module, "_transcribe_video_audio", fake_transcribe)
    monkeypatch.setattr(match_module, "probe", lambda name: (True, ""))

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            return np.ones((len(texts), 2), dtype=float)

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    # Should have re-transcribed due to corrupt cache
    assert call_count[0] == 1
    assert ctx.status.match == "success"


def test_match_clips_cache_key_includes_model_and_language(tmp_path, monkeypatch):
    """Different model/language → different cache file."""
    from movie_narrator.pipeline.match import _cache_key

    # _cache_key reads the file to hash it, so we need a real file
    video_path = tmp_path / "video.mp4"
    video_path.write_bytes(b"00")

    key1 = _cache_key(str(video_path), "small", "zh")
    key2 = _cache_key(str(video_path), "medium", "zh")
    key3 = _cache_key(str(video_path), "medium", "en")

    assert key1 != key2  # different model
    assert key2 != key3  # different language
    assert key1 != key3  # both different


def test_match_clips_whisperx_scene_captions(tmp_path, monkeypatch):
    """WhisperX transcript replaces fake labels → better embedding match."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
        timed_segments=[
            TimedSegment(text="赛车在赛道上飞驰", start=0.0, end=2.0),
            TimedSegment(text="主角在雨中哭泣", start=2.5, end=5.0),
        ],
        scenes=[
            Scene(index=0, start=0.0, end=5.0),
            Scene(index=1, start=5.0, end=10.0),
        ],
    )
    ctx.status.scene = "success"
    (tmp_path / "video.mp4").write_bytes(b"00")

    # Both sentence_transformers and whisperx available
    monkeypatch.setattr(
        match_module,
        "probe",
        lambda name: (True, ""),
    )

    # Fake WhisperX transcript: scene 0 has racing dialogue, scene 1 has crying
    fake_transcript = [
        {"start": 0.5, "end": 3.0, "text": "快看那辆赛车冲过了终点线"},
        {"start": 6.0, "end": 9.0, "text": "她在雨中伤心地哭了起来"},
    ]
    monkeypatch.setattr(
        match_module,
        "_transcribe_video_audio",
        lambda *a, **kw: fake_transcript,
    )

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            arr = np.zeros((len(texts), 2), dtype=float)
            for i, t in enumerate(texts):
                if "赛车" in t or "飞驰" in t:
                    arr[i] = np.array([1.0, 0.0])
                elif "哭" in t or "雨" in t:
                    arr[i] = np.array([0.0, 1.0])
                else:
                    arr[i] = np.array([0.5, 0.5])
            return arr

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    assert ctx.status.match == "success"
    assert len(ctx.matched_clips) == 2
    racing_match = next(m for m in ctx.matched_clips if "赛车" in m.text)
    crying_match = next(m for m in ctx.matched_clips if "哭泣" in m.text)
    # Racing narration should match scene 0 (racing dialogue)
    assert racing_match.scene_index == 0
    # Crying narration should match scene 1 (crying dialogue)
    assert crying_match.scene_index == 1
    assert all(m.source == "embedding" for m in ctx.matched_clips)


def test_match_clips_whisperx_cache_hit(tmp_path, monkeypatch):
    """WhisperX transcript is cached and reused on second call."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
        timed_segments=[
            TimedSegment(text="A", start=0.0, end=2.0),
            TimedSegment(text="B", start=2.5, end=5.0),
        ],
        scenes=[
            Scene(index=0, start=0.0, end=5.0),
            Scene(index=1, start=5.0, end=10.0),
        ],
    )
    ctx.status.scene = "success"
    (tmp_path / "video.mp4").write_bytes(b"00")

    call_count = [0]

    def fake_transcribe(*a, **kw):
        call_count[0] += 1
        return [{"start": 0.0, "end": 5.0, "text": "hello"}]

    monkeypatch.setattr(match_module, "_transcribe_video_audio", fake_transcribe)
    monkeypatch.setattr(
        match_module,
        "probe",
        lambda name: (True, ""),
    )

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            return np.ones((len(texts), 2), dtype=float)

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    assert call_count[0] == 1


def test_match_clips_whisperx_failure_falls_back(tmp_path, monkeypatch):
    """WhisperX transcription fails → fall back to fake labels, no crash."""
    ctx = Context(
        movie_name="m",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "video.mp4"),
        timed_segments=[
            TimedSegment(text="A", start=0.0, end=2.0),
            TimedSegment(text="B", start=2.5, end=5.0),
        ],
        scenes=[
            Scene(index=0, start=0.0, end=5.0),
            Scene(index=1, start=5.0, end=10.0),
        ],
    )
    ctx.status.scene = "success"
    (tmp_path / "video.mp4").write_bytes(b"00")

    monkeypatch.setattr(
        match_module,
        "probe",
        lambda name: (True, ""),
    )
    monkeypatch.setattr(
        match_module,
        "_transcribe_video_audio",
        lambda *a, **kw: None,  # WhisperX fails, returns None
    )

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            return np.random.rand(len(texts), 2).astype(float)

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    assert ctx.status.match == "success"
    assert len(ctx.matched_clips) == 2


def test_match_clips_embedding_reranks_when_available(tmp_path, monkeypatch):
    """sentence_transformers available → re-rank candidates by cosine.

    MS-02 fix: embedding path now requires real transcript (not fake
    placeholder labels). This test provides a mock transcript so the
    embedding path is exercised.
    """
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    # Two scenes so the embedding path can actually pick one over the other.
    ctx.scenes = [
        Scene(index=0, start=0.0, end=4.0),
        Scene(index=1, start=4.0, end=10.0),
    ]
    ctx.timed_segments = [
        TimedSegment(text="alpha alpha alpha", start=0.0, end=2.0),
        TimedSegment(text="beta beta beta", start=2.5, end=5.0),
    ]

    monkeypatch.setattr(
        match_module,
        "probe",
        lambda name: (True, ""),  # both sentence_transformers and whisperx
    )

    # MS-02: provide mock transcript so captions are real (not placeholders)
    mock_transcript = [
        {"start": 0.0, "end": 3.5, "text": "alpha scene zero"},
        {"start": 4.0, "end": 9.0, "text": "beta scene one"},
    ]
    monkeypatch.setattr(
        match_module, "_transcribe_video_audio", lambda *a, **k: mock_transcript
    )

    class FakeST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            # Each scene label includes its index; each narration piece includes
            # its semantic side — extract that to drive the embedding.
            arr = np.zeros((len(texts), 2), dtype=float)
            for i, t in enumerate(texts):
                if "alpha" in t:
                    arr[i] = np.array([1.0, 0.0])
                elif "beta" in t:
                    arr[i] = np.array([0.0, 1.0])
                else:
                    # Scene labels: "scene N from Xs to Ys" — map even N → alpha, odd N → beta
                    if "scene 0" in t:
                        arr[i] = np.array([1.0, 0.0])
                    elif "scene 1" in t:
                        arr[i] = np.array([0.0, 1.0])
            return arr

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = FakeST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    assert ctx.status.match == "success"
    assert len(ctx.matched_clips) == 2
    alpha_match = next(m for m in ctx.matched_clips if "alpha" in m.text)
    beta_match = next(m for m in ctx.matched_clips if "beta" in m.text)
    # alpha should resolve to scene 0, beta to scene 1
    assert alpha_match.scene_index == 0
    assert beta_match.scene_index == 1
    assert all(m.source == "embedding" for m in ctx.matched_clips)
    # Cosine between one-hots is exactly 1.0
    for m in ctx.matched_clips:
        assert math.isclose(m.score, 1.0, abs_tol=1e-6)


def test_match_clips_embedding_failure_falls_back_to_heuristic(tmp_path, monkeypatch):
    """sentence_transformers probe OK, but encode() raises → fall back to heuristic."""
    ctx = _make_ctx(tmp_path)
    (tmp_path / "video.mp4").write_bytes(b"00")
    monkeypatch.setattr(
        match_module,
        "probe",
        lambda name: (True, "") if name == "sentence_transformers" else (False, ""),
    )

    class BrokenST:
        def __init__(self, *a, **kw):
            pass

        def encode(self, texts):
            raise RuntimeError("boom")

    fake_mod = ModuleType("sentence_transformers")
    fake_mod.SentenceTransformer = BrokenST
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_mod)

    match_clips(ctx)
    assert ctx.status.match == "success"
    assert all(m.source == "heuristic" for m in ctx.matched_clips)
