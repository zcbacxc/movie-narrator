import re
import unicodedata
from pathlib import Path
from typing import Optional

from ..models import Context, StepResult

_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}


def normalize_title(name: str) -> str:
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[\(\[（].*?[\)\]）]", " ", name)
    name = re.sub(r"\s+", " ", name).strip().lower()
    return name


def find_in_library(movie_name: str, library_dir: str) -> Optional[str]:
    root = Path(library_dir)
    if not root.is_dir():
        return None
    target = normalize_title(movie_name)
    best: tuple[float, Path] | None = None
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _VIDEO_EXTS:
            continue
        stem_n = normalize_title(path.stem)
        if target == stem_n:
            score = 100.0
        elif target in stem_n or stem_n in target:
            score = 50.0 + min(len(target), len(stem_n))
        else:
            continue
        if best is None or score > best[0]:
            best = (score, path)
    if best is None:
        return None
    return str(best[1].resolve())


def resolve_video(ctx: Context) -> Context:
    video_arg = ctx.metadata.get("video_arg")
    if video_arg:
        p = Path(video_arg)
        if not p.is_file():
            raise FileNotFoundError(f"video not found: {video_arg}")
        ctx.source_video_path = str(p.resolve())
        return ctx
    elif ctx.library_dir:
        # No --video flag; try fuzzy match in library
        hit = find_in_library(ctx.movie_name, ctx.library_dir)
        if hit:
            if ctx.services:
                ctx.services.console.debug(f"library match: {hit}")
            ctx.source_video_path = hit
            return ctx
    # No video source found — pipeline continues without footage
    ctx.source_video_path = None
    return ctx
