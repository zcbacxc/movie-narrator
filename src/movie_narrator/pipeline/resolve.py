# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Video resolution step — locate source video in library."""

import re
import unicodedata
from pathlib import Path
from typing import Optional

from ..models import Context

_VIDEO_EXTS = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v"}

# v1.3.2: allowed image extensions for reference media (kind="image").
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def normalize_title(name: str) -> str:
    """Normalize a movie title for matching."""
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[\(\[（].*?[\)\]）]", " ", name)
    name = re.sub(r"\s+", " ", name).strip().lower()
    return name


def find_in_library(movie_name: str, library_dir: str) -> Optional[str]:
    """Find a video file in the library directory."""
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


# ── Reference media validation (v1.3.2) ───────────────────


def _reference_media_entries(ctx: Context) -> list[dict]:
    """Normalize ``ctx.metadata["reference_media"]`` to a list of plain dicts.

    Entries may be pydantic ``ReferenceMediaItem`` models (fresh job),
    plain dicts (resumed from ``pipeline_state.json``), or absent. Returns
    an empty list when reference media is not configured.
    """
    raw = ctx.metadata.get("reference_media")
    if not raw:
        return []
    entries: list[dict] = []
    for item in raw:
        if isinstance(item, dict):
            entries.append(dict(item))
        elif hasattr(item, "model_dump"):
            entries.append(dict(item.model_dump()))
        else:
            entries.append(dict(item))
    return entries


def _validate_reference_media(ctx: Context) -> None:
    """Validate user-provided reference media (hard input check).

    For each entry: the file must exist and its extension must match the
    declared ``kind`` (video extensions: ``_VIDEO_EXTS``; image
    extensions: png/jpg/jpeg/webp). Any violation is a hard input error —
    the pipeline cannot honor a style reference it cannot read, so failing
    fast beats silently ignoring the user's configuration.

    On success the validated entries (absolute path, kind, usage, note)
    replace ``ctx.metadata["reference_media"]`` so downstream steps (the
    script prompt hint block) consume normalized dicts. When no reference
    media is configured the context is left untouched.
    """
    entries = _reference_media_entries(ctx)
    if not entries:
        return
    validated: list[dict] = []
    for i, entry in enumerate(entries):
        path = str(entry.get("path") or "")
        kind = str(entry.get("kind") or "video")
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(f"reference_media[{i}] not found: {path}")
        ext = p.suffix.lower()
        allowed = _VIDEO_EXTS if kind == "video" else _IMAGE_EXTS
        if ext not in allowed:
            raise ValueError(
                f"reference_media[{i}] kind={kind!r} but {path!r} has extension "
                f"{ext!r} (allowed for {kind}: {', '.join(sorted(allowed))})"
            )
        validated.append(
            {
                "path": str(p.resolve()),
                "kind": kind,
                "usage": str(entry.get("usage") or "style"),
                "note": str(entry.get("note") or ""),
            }
        )
    ctx.metadata["reference_media"] = validated


def resolve_video(ctx: Context) -> Context:
    """Resolve the source video path from the library.

    Args:
        ctx: Pipeline execution context.

    Returns:
        Updated pipeline context with resolved video path.
    """
    # v1.3.2: hard input validation for user-provided reference media.
    # Runs first so a bad reference fails before any video lookup; a
    # no-op (no metadata write) when reference media is absent.
    _validate_reference_media(ctx)
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
