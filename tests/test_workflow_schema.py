import pytest
from pydantic import ValidationError

from movie_narrator.workflow.schema import JobConfig, JobParams, JobSteps, ResolvedJob


def test_job_config_default_values():
    cfg = JobConfig()
    assert cfg.movie is None
    assert cfg.steps is None
    assert cfg.params is None


def test_job_config_accepts_full_whitelist():
    cfg = JobConfig(
        movie="飞驰人生",
        style="热血搞笑",
        duration=60,
        voice="zh-CN-YunxiNeural",
        format="16:9",
        keep_cache=False,
        video="./a.mp4",
        library_dir="D:/movies",
        bgm="./b.mp3",
        no_bgm=False,
        no_clips=False,
        strict=False,
        steps=JobSteps(research=True, align=False),
        params=JobParams(scene_threshold=27.0, match_min_score=0.25, research_provider="llm"),
    )
    assert cfg.movie == "飞驰人生"
    assert cfg.steps.align is False
    assert cfg.params.scene_threshold == 27.0


def test_job_config_rejects_unknown_top_level_key():
    with pytest.raises(ValidationError):
        JobConfig.model_validate({"movie": "X", "unknown_key": 1})


def test_job_steps_rejects_hard_step_key():
    with pytest.raises(ValidationError):
        JobSteps.model_validate({"render": False})


def test_job_params_rejects_unknown_key():
    with pytest.raises(ValidationError):
        JobParams.model_validate({"unknown_param": 10})


def test_job_config_duration_must_be_positive():
    with pytest.raises(ValidationError):
        JobConfig(duration=0)
    with pytest.raises(ValidationError):
        JobConfig(duration=-5)


def test_job_config_format_whitelist():
    JobConfig(format="16:9")
    JobConfig(format="9:16")
    with pytest.raises(ValidationError):
        JobConfig(format="4:3")


def test_resolved_job_shape():
    r = ResolvedJob(
        movie="M",
        style="S",
        duration=30,
        voice=None,
        format="16:9",
        keep_cache=False,
        video=None,
        library_dir=None,
        bgm=None,
        no_bgm=False,
        no_clips=False,
        strict=False,
        research=None,
        workflow_steps={"align": False},
        params={"scene_threshold": 30.0},
        config_path="/tmp/job.yaml",
    )
    assert r.workflow_steps == {"align": False}
    assert r.params["scene_threshold"] == 30.0
