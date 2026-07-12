from pathlib import Path

import pytest

from movie_narrator.workflow.errors import JobConfigError
from movie_narrator.workflow.load import load_job_config


def test_load_minimal_movie_only(tmp_path):
    p = tmp_path / "job.yaml"
    p.write_text("movie: 飞驰人生\n", encoding="utf-8")
    cfg = load_job_config(p)
    assert cfg.movie == "飞驰人生"
    assert cfg.version == 1


def test_load_full_document_and_relative_paths(tmp_path):
    films = tmp_path / "films"
    films.mkdir()
    (films / "a.mp4").write_bytes(b"00")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "bgm.mp3").write_bytes(b"00")

    p = tmp_path / "job.yaml"
    p.write_text(
        "\n".join(
            [
                "version: 1",
                "movie: 飞驰人生",
                "style: 热血搞笑",
                "duration: 60",
                "voice: zh-CN-YunxiNeural",
                'format: "16:9"',
                "keep_cache: false",
                "video: ./films/a.mp4",
                "library_dir: ./films",
                "bgm: ./assets/bgm.mp3",
                "no_bgm: false",
                "no_clips: false",
                "strict: false",
                "steps:",
                " research: true",
                " align: false",
                " scene: true",
                " match: true",
                " bgm: true",
                " export: false",
                "params:",
                " scene_threshold: 27.0",
                " match_min_score: 0.25",
                " research_provider: llm",
                "",
            ]
        ),
        encoding="utf-8",
    )
    cfg = load_job_config(p)
    assert cfg.movie == "飞驰人生"
    assert cfg.steps.align is False
    assert cfg.params.scene_threshold == 27.0
    assert Path(cfg.video).is_absolute()
    assert Path(cfg.video).name == "a.mp4"
    assert Path(cfg.bgm).is_absolute()
    assert Path(cfg.library_dir).is_absolute()
    assert Path(cfg.video).parent == films.resolve()


def test_load_missing_file(tmp_path):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(JobConfigError) as exc:
        load_job_config(missing)
    assert "config not found:" in str(exc.value)


def test_load_corrupt_yaml(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("movie: [unclosed\n", encoding="utf-8")
    with pytest.raises(JobConfigError) as exc:
        load_job_config(p)
    assert "invalid YAML:" in str(exc.value)


def test_load_root_not_mapping(tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(JobConfigError) as exc:
        load_job_config(p)
    assert "job config must be a mapping" in str(exc.value)


def test_load_unknown_top_level_key(tmp_path):
    p = tmp_path / "job.yaml"
    p.write_text("movie: X\nfoo: 1\n", encoding="utf-8")
    with pytest.raises(JobConfigError) as exc:
        load_job_config(p)
    msg = str(exc.value)
    assert "unknown key" in msg.lower() or "extra" in msg.lower() or "foo" in msg


def test_load_unsupported_version(tmp_path):
    p = tmp_path / "job.yaml"
    p.write_text("version: 2\nmovie: X\n", encoding="utf-8")
    with pytest.raises(JobConfigError) as exc:
        load_job_config(p)
    assert "unsupported config version: 2 (supported: 1)" in str(exc.value)


def test_load_bad_duration_type(tmp_path):
    p = tmp_path / "job.yaml"
    p.write_text("movie: X\nduration: sixty\n", encoding="utf-8")
    with pytest.raises(JobConfigError):
        load_job_config(p)


def test_load_bad_format(tmp_path):
    p = tmp_path / "job.yaml"
    p.write_text('movie: X\nformat: "4:3"\n', encoding="utf-8")
    with pytest.raises(JobConfigError):
        load_job_config(p)


def test_load_does_not_require_video_file_to_exist(tmp_path):
    p = tmp_path / "job.yaml"
    p.write_text("movie: X\nvideo: ./missing.mp4\n", encoding="utf-8")
    cfg = load_job_config(p)
    assert Path(cfg.video).is_absolute()
    assert not Path(cfg.video).is_file()
