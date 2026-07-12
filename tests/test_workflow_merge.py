from movie_narrator.config import Settings
from movie_narrator.workflow.merge import merge_job
from movie_narrator.workflow.schema import JobConfig, JobParams, JobSteps


def _cli(**overrides):
    base = {
        "movie": None,
        "style": "热血搞笑",
        "duration": 60,
        "voice": None,
        "format": "16:9",
        "keep_cache": False,
        "video": None,
        "library_dir": None,
        "research": None,
        "bgm": None,
        "no_bgm": False,
        "no_clips": False,
        "strict": False,
        "config_path": None,
    }
    base.update(overrides)
    return base


def test_cli_overrides_yaml_movie():
    job = JobConfig(movie="FromYAML", style="YAMLStyle")
    settings = Settings()
    r = merge_job(_cli(movie="FromCLI", config_path="job.yaml"), job, settings)
    assert r.movie == "FromCLI"


def test_yaml_used_when_cli_movie_none():
    job = JobConfig(movie="FromYAML")
    r = merge_job(_cli(config_path="job.yaml"), job, settings=Settings())
    assert r.movie == "FromYAML"


def test_yaml_style_when_cli_still_default_with_config():
    job = JobConfig(movie="M", style="YAMLStyle")
    r = merge_job(_cli(style="热血搞笑", config_path="job.yaml"), job, Settings())
    assert r.style == "YAMLStyle"


def test_cli_style_when_differs_from_default():
    job = JobConfig(movie="M", style="YAMLStyle")
    r = merge_job(_cli(style="严肃", config_path="job.yaml"), job, Settings())
    assert r.style == "严肃"


def test_duration_yaml_when_cli_default_with_config():
    job = JobConfig(movie="M", duration=90)
    r = merge_job(_cli(duration=60, config_path="job.yaml"), job, Settings())
    assert r.duration == 90


def test_duration_cli_when_not_default():
    job = JobConfig(movie="M", duration=90)
    r = merge_job(_cli(duration=45, config_path="job.yaml"), job, Settings())
    assert r.duration == 45


def test_format_yaml_when_cli_default_with_config():
    job = JobConfig(movie="M", format="9:16")
    r = merge_job(_cli(format="16:9", config_path="job.yaml"), job, Settings())
    assert r.format == "9:16"


def test_bool_sentinel_keep_cache_false_does_not_override_yaml_true():
    job = JobConfig(movie="M", keep_cache=True)
    r = merge_job(_cli(keep_cache=False, config_path="job.yaml"), job, Settings())
    assert r.keep_cache is True


def test_bool_sentinel_cli_true_wins():
    job = JobConfig(movie="M", keep_cache=False, no_clips=False)
    r = merge_job(
        _cli(keep_cache=True, no_clips=True, config_path="job.yaml"), job,
        Settings(),
    )
    assert r.keep_cache is True
    assert r.no_clips is True


def test_research_cli_none_uses_yaml_steps():
    job = JobConfig(movie="M", steps=JobSteps(research=True))
    r = merge_job(_cli(research=None, config_path="job.yaml"), job, Settings())
    assert r.research is True
    assert r.workflow_steps.get("research") is True


def test_research_cli_false_overrides_yaml():
    job = JobConfig(movie="M", steps=JobSteps(research=True))
    r = merge_job(_cli(research=False, config_path="job.yaml"), job, Settings())
    assert r.research is False


def test_workflow_steps_only_explicit_yaml_keys():
    job = JobConfig(movie="M", steps=JobSteps(align=False, export=True))
    r = merge_job(_cli(config_path="job.yaml"), job, Settings())
    assert r.workflow_steps == {"align": False, "export": True}
    assert "scene" not in r.workflow_steps


def test_params_only_non_none():
    job = JobConfig(
        movie="M",
        params=JobParams(scene_threshold=30.0, research_provider="llm"),
    )
    r = merge_job(_cli(config_path="job.yaml"), job, Settings())
    assert r.params == {"scene_threshold": 30.0, "research_provider": "llm"}
    assert "match_min_score" not in r.params


def test_no_job_uses_cli_and_settings():
    settings = Settings()
    r = merge_job(_cli(movie="OnlyCLI"), None, settings)
    assert r.movie == "OnlyCLI"
    assert r.style == "热血搞笑"
    assert r.duration == 60
    assert r.workflow_steps == {}
    assert r.params == {}
    assert r.config_path is None


def test_export_no_clips_cli_true_forces_off_flag():
    job = JobConfig(movie="M", steps=JobSteps(export=True), no_clips=False)
    r = merge_job(_cli(no_clips=True, config_path="job.yaml"), job, Settings())
    assert r.no_clips is True


def test_yaml_no_bgm_when_cli_false():
    job = JobConfig(movie="M", no_bgm=True)
    r = merge_job(_cli(no_bgm=False, config_path="job.yaml"), job, Settings())
    assert r.no_bgm is True
