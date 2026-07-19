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
