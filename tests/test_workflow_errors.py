from movie_narrator.workflow.errors import JobConfigError
from movie_narrator.pipeline.errors import PipelineStrictError


def test_job_config_error_is_exception_not_strict():
    err = JobConfigError("bad yaml")
    assert isinstance(err, Exception)
    assert not isinstance(err, PipelineStrictError)
    assert str(err) == "bad yaml"
