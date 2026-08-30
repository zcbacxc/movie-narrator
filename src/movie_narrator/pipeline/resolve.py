# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Video resolution step — locate source video in library."""

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, Optional, cast

from ..models import Context
from ..utils.media_cache import MediaCacheError, fetch_into_cache

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
    raw = cast(Dict[str, Any], ctx.metadata).get("reference_media")
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

    v1.5.2: an entry with a ``url`` is fetched through the media cache
    (:func:`movie_narrator.utils.media_cache.fetch_into_cache` — https
    only, content-hash dedupe, TTL + size caps) and the cached local path
    flows through the exact same validation and downstream usage. The
    item's ``note`` doubles as the license note and is REQUIRED (compliance:
    remote media must carry an auditable source attribution). Fetch
    failures (offline, bad URL, size cap) are hard input errors, consistent
    with the local-path failure mode.

    On success the validated entries (absolute path, kind, usage, note —
    plus additive ``source_url`` / ``cache_sha256`` keys for URL items)
    replace ``ctx.metadata["reference_media"]`` so downstream steps (the
    script prompt hint block) consume normalized dicts. When no reference
    media is configured the context is left untouched.
    """
    entries = _reference_media_entries(ctx)
    if not entries:
        return
    validated: list[dict] = []
    for i, entry in enumerate(entries):
        kind = str(entry.get("kind") or "video")
        usage = str(entry.get("usage") or "style")
        note = str(entry.get("note") or "")
        url = str(entry.get("url") or "").strip()
        provenance: dict = {}
        if url:
            # v1.5.2: remote item — license note is mandatory, then fetch
            # (or reuse) through the content-addressed media cache.
            if not note.strip():
                raise ValueError(
                    f"reference_media[{i}] has a 'url' ({url!r}) but no license "
                    "note — set 'note' (source attribution / license) to fetch "
                    "remote reference media"
                )
            try:
                cached = fetch_into_cache(url, license_note=note, kind=kind)
            except MediaCacheError as exc:
                raise ValueError(f"reference_media[{i}] url fetch failed: {exc}") from exc
            path = str(cached)
            # The blob filename IS its sha256 (content addressing).
            provenance = {"source_url": url, "cache_sha256": cached.stem}
        else:
            path = str(entry.get("path") or "")
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
                "usage": usage,
                "note": note,
                **provenance,
            }
        )
    cast(Dict[str, Any], ctx.metadata)["reference_media"] = validated


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
