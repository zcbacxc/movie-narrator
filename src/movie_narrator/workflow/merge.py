from typing import Any, Dict, Optional

from ..config import Settings
from .schema import JobConfig, ResolvedJob

_STYLE_DEFAULT = "热血搞笑"
_DURATION_DEFAULT = 60
_FORMAT_DEFAULT = "16:9"


def merge_job(
    cli: Dict[str, Any],
    job: Optional[JobConfig],
    settings: Settings,
) -> ResolvedJob:
    has_job = job is not None

    def yaml_get(name: str):
        if job is None:
            return None
        return getattr(job, name)

    def pick_optional(cli_val, yaml_val, default=None):
        if cli_val is not None and cli_val != "":
            return cli_val
        if yaml_val is not None and yaml_val != "":
            return yaml_val
        return default

    def pick_bool_true_explicit(cli_val: bool, yaml_val: Optional[bool]) -> bool:
        if cli_val is True:
            return True
        if yaml_val is not None:
            return bool(yaml_val)
        return False

    def pick_defaulted(cli_val, yaml_val, typer_default):
        if has_job and yaml_val is not None and cli_val == typer_default:
            return yaml_val
        return cli_val if cli_val is not None else (
            yaml_val if yaml_val is not None else typer_default
        )

    movie = pick_optional(cli.get("movie"), yaml_get("movie"), "")

    style = pick_defaulted(cli.get("style"), yaml_get("style"), _STYLE_DEFAULT)
    duration = pick_defaulted(cli.get("duration"), yaml_get("duration"), _DURATION_DEFAULT)
    fmt = pick_defaulted(cli.get("format"), yaml_get("format"), _FORMAT_DEFAULT)

    voice = pick_optional(cli.get("voice"), yaml_get("voice"), None)
    video = pick_optional(cli.get("video"), yaml_get("video"), None)
    library_dir = pick_optional(cli.get("library_dir"), yaml_get("library_dir"), None)
    bgm = pick_optional(cli.get("bgm"), yaml_get("bgm"), None)

    keep_cache = pick_bool_true_explicit(bool(cli.get("keep_cache")), yaml_get("keep_cache"))
    no_bgm = pick_bool_true_explicit(bool(cli.get("no_bgm")), yaml_get("no_bgm"))
    no_clips = pick_bool_true_explicit(bool(cli.get("no_clips")), yaml_get("no_clips"))
    strict = pick_bool_true_explicit(bool(cli.get("strict")), yaml_get("strict"))

    research = cli.get("research")
    workflow_steps: Dict[str, bool] = {}
    if job is not None and job.steps is not None:
        for key in ("research", "align", "scene", "match", "bgm", "export"):
            val = getattr(job.steps, key)
            if val is not None:
                workflow_steps[key] = bool(val)
        if research is None and "research" in workflow_steps:
            research = workflow_steps["research"]

    params: Dict[str, Any] = {}
    if job is not None and job.params is not None:
        for key in ("scene_threshold", "scene_frame_skip", "match_min_score", "research_provider"):
            val = getattr(job.params, key)
            if val is not None:
                params[key] = val

    config_path = cli.get("config_path")
    if config_path is not None:
        config_path = str(config_path)

    return ResolvedJob(
        movie=movie if movie is not None else "",
        style=style,
        duration=int(duration),
        voice=voice,
        format=fmt,
        keep_cache=keep_cache,
        video=video,
        library_dir=library_dir,
        bgm=bgm,
        no_bgm=no_bgm,
        no_clips=no_clips,
        strict=strict,
        research=research,
        workflow_steps=workflow_steps,
        params=params,
        config_path=config_path,
    )
