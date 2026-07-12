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


def test_match_clips_embedding_reranks_when_available(tmp_path, monkeypatch):
    """sentence_transformers available → re-rank candidates by cosine."""
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
        lambda name: (True, "") if name == "sentence_transformers" else (False, ""),
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
