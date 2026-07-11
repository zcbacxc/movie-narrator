from movie_narrator.pipeline.errors import PipelineStrictError


def test_pipeline_strict_error_payload():
    err = PipelineStrictError(step="mix_bgm", status={"bgm": "failed"})
    assert err.step == "mix_bgm"
    assert err.status["bgm"] == "failed"
    assert isinstance(err, RuntimeError)


def test_pipeline_strict_error_default_message():
    err = PipelineStrictError(step="align_audio", status={})
    assert "align_audio" in str(err)
