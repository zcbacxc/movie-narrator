from unittest.mock import MagicMock, patch

from movie_narrator.models import Context, Scene, TimedSegment
from movie_narrator.pipeline.align import align_audio
from movie_narrator.pipeline.bgm import mix_bgm
from movie_narrator.pipeline.export_clips import export_clips
from movie_narrator.pipeline.match import match_clips
from movie_narrator.pipeline.research import research_plot
from movie_narrator.pipeline.scenes import detect_scenes


def test_research_disabled_by_workflow_steps(tmp_path):
    ctx = Context(movie_name="X", output_dir=str(tmp_path))
    ctx.metadata["research_enabled"] = True
    ctx.metadata["workflow_steps"] = {"research": False}
    with patch("movie_narrator.pipeline.research.get_llm_client") as gl:
        research_plot(ctx)
    gl.assert_not_called()
    assert ctx.status.research == "disabled"
    assert not (tmp_path / "research.json").exists()


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


def test_align_disabled_by_workflow_steps(tmp_path):
    ctx = Context(
        movie_name="X",
        output_dir=str(tmp_path),
        audio_path=str(tmp_path / "a.mp3"),
    )
    ctx.metadata["workflow_steps"] = {"align": False}
    with patch("movie_narrator.pipeline.align.probe") as probe:
        align_audio(ctx)
    probe.assert_not_called()
    assert ctx.status.align == "disabled"


def test_scene_disabled_by_workflow_steps(tmp_path):
    ctx = Context(
        movie_name="X",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "v.mp4"),
    )
    ctx.metadata["workflow_steps"] = {"scene": False}
    with patch("movie_narrator.pipeline.scenes.probe") as probe:
        detect_scenes(ctx)
    probe.assert_not_called()
    assert ctx.status.scene == "disabled"


def test_match_disabled_by_workflow_steps(tmp_path):
    ctx = Context(
        movie_name="X",
        output_dir=str(tmp_path),
        source_video_path=str(tmp_path / "v.mp4"),
        scenes=[Scene(index=0, start=0.0, end=1.0)],
        timed_segments=[TimedSegment(text="A", start=0.0, end=1.0)],
    )
    ctx.status.scene = "success"
    ctx.metadata["workflow_steps"] = {"match": False}
    match_clips(ctx)
    assert ctx.status.match == "disabled"
    assert ctx.matched_clips == []


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

    def fake_settings():
        s = MagicMock()
        s.match_min_score = 0.01
        return s

    monkeypatch.setattr(match_mod, "get_settings", fake_settings)
    monkeypatch.setattr(match_mod, "probe", lambda name: (False, "no"))
    match_clips(ctx)
    assert ctx.status.match == "success"
    assert ctx.metadata["match_min_score"] == 0.99


def test_bgm_disabled_by_workflow_steps(tmp_path):
    audio = tmp_path / "n.mp3"
    audio.write_bytes(b"00")
    ctx = Context(
        movie_name="X",
        output_dir=str(tmp_path),
        audio_path=str(audio),
    )
    ctx.metadata["bgm_request"] = "explicit"
    ctx.assets.bgm = str(tmp_path / "b.mp3")
    ctx.metadata["workflow_steps"] = {"bgm": False}
    mix_bgm(ctx)
    assert ctx.status.bgm == "disabled"
    assert ctx.final_audio_path == str(audio)


def test_export_disabled_by_workflow_steps(tmp_path):
    ctx = Context(movie_name="X", output_dir=str(tmp_path))
    ctx.metadata["export_clips"] = True
    ctx.metadata["workflow_steps"] = {"export": False}
    with patch("movie_narrator.pipeline.export_clips.probe") as probe:
        export_clips(ctx)
    probe.assert_not_called()
    assert ctx.status.export == "disabled"
