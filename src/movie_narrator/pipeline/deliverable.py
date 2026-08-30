# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Versioned deliverable manifest (v1.3.0).

Writes ``deliverable_manifest.json`` into the pipeline output directory:
a machine-readable, checksummed inventory of what a run *delivered*.

Distinct from the other JSON outputs:

- ``metadata.json`` (utils/metadata_export) — a snapshot of ``ctx.metadata``
  for diagnostics;
- ``execution_manifest.json`` (pipeline/runner) — an execution *audit*
  (per-step timing, attempts, providers);
- ``deliverable_manifest.json`` (this module) — a *deliverables* index:
  which artifacts exist, their sizes and SHA-256 checksums, so consumers
  (web UI, archives, CI) can verify completeness without hashing the
  whole directory themselves.

Artifact kinds:

- **Core** kinds (``video``, ``audio``, ``subtitle``) always appear —
  even when the artifact is missing, with ``present=false`` and the
  conventional declared path, so consumers can rely on the shape.
- **Optional** kinds (``script``, ``clip``, ``metadata``,
  ``execution_manifest``) only appear when present.

All paths are relative to the output directory. Writes are atomic
(temp file + :func:`os.replace`).
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import __version__
from ..models import Context

#: Current manifest schema version (bump on breaking shape changes).
MANIFEST_SCHEMA_VERSION: int = 1

#: Output filename, written into ``ctx.output_dir``.
MANIFEST_FILENAME: str = "deliverable_manifest.json"

_CHUNK_SIZE = 65536

#: Conventional declared paths used for core entries when the artifact
#: is missing (``present=false``), so the manifest shape stays stable.
_CONVENTIONAL_VIDEO = "final.mp4"
_CONVENTIONAL_AUDIO = "narration.mp3"
_CONVENTIONAL_SUBTITLE = "subtitle.srt"


@dataclass
class ManifestEntry:
    """One artifact in the deliverable manifest."""

    kind: str
    path: str  # relative to the output directory
    bytes: int
    sha256: str
    present: bool


@dataclass
class DeliverableManifest:
    """Versioned, checksummed inventory of a run's deliverables."""

    schema_version: int = MANIFEST_SCHEMA_VERSION
    package_version: str = ""
    contract_version: str = ""
    generated_at: str = ""
    movie: str = ""
    generation_mode: str = ""
    artifacts: List[ManifestEntry] = field(default_factory=list)
    qa: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable dict of the manifest."""
        data = asdict(self)
        data["artifacts"] = [asdict(a) for a in self.artifacts]
        return data


def _contract_version_string() -> str:
    """Return the dotted contract version, or "" when unavailable."""
    try:
        from ..contract import CONTRACT_VERSION

        return ".".join(str(v) for v in CONTRACT_VERSION)
    except Exception:  # noqa: BLE001 — contract is optional metadata
        return ""


def _sha256_of(path: Path) -> str:
    """Stream a file through SHA-256 and return the hex digest."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_entry(ctx: Context, kind: str, raw_path: Optional[str], declared: str) -> ManifestEntry:
    """Build one manifest entry from an absolute (or missing) path.

    ``declared`` is the conventional output-dir-relative path used for
    the ``path`` field when *raw_path* is unset, and the file's relative
    name otherwise.
    """
    if raw_path:
        p = Path(raw_path)
        try:
            # POSIX separators keep manifests portable across OSes.
            rel = p.relative_to(Path(ctx.output_dir)).as_posix()
        except ValueError:
            # Artifact living outside the output dir — still index it,
            # but keep the absolute path so the entry stays resolvable.
            rel = p.as_posix()
        if p.is_file():
            return ManifestEntry(
                kind=kind, path=rel, bytes=p.stat().st_size, sha256=_sha256_of(p), present=True
            )
    return ManifestEntry(kind=kind, path=declared, bytes=0, sha256="", present=False)


def _collect_qa(ctx: Context) -> Dict[str, Any]:
    """Collect the QA blocks recorded by the pipeline (best-effort)."""
    qa: Dict[str, Any] = {}
    for key in ("qa_report", "video_qa", "qa_gate"):
        if ctx.metadata.get(key) is not None:
            qa[key] = ctx.metadata.get(key)
    return qa


def _collect_artifacts(ctx: Context) -> List[ManifestEntry]:
    """Collect manifest entries for every known artifact kind."""
    entries: List[ManifestEntry] = []

    # ── Core kinds: always present in the manifest ──
    # video (final.mp4 / preview.mp4)
    entries.append(_make_entry(ctx, "video", ctx.video_path, _CONVENTIONAL_VIDEO))
    # audio (bgm-mixed final audio first, raw narration second)
    audio_path = ctx.final_audio_path or ctx.audio_path
    entries.append(_make_entry(ctx, "audio", audio_path, _CONVENTIONAL_AUDIO))
    # subtitles (original + translated/bilingual variants)
    if ctx.subtitle_paths is not None:
        sp = ctx.subtitle_paths
        entries.append(_make_entry(ctx, "subtitle", sp.original, _CONVENTIONAL_SUBTITLE))
        if sp.translated:
            entries.append(_make_entry(ctx, "subtitle", sp.translated, _CONVENTIONAL_SUBTITLE))
        if sp.bilingual:
            entries.append(_make_entry(ctx, "subtitle", sp.bilingual, _CONVENTIONAL_SUBTITLE))
    else:
        entries.append(
            _make_entry(ctx, "subtitle", None, _CONVENTIONAL_SUBTITLE)
        )

    # ── Optional kinds: only when present ──
    if ctx.script_md_path and Path(ctx.script_md_path).is_file():
        entries.append(_make_entry(ctx, "script", ctx.script_md_path, "script.md"))

    clips_dir = Path(ctx.clips_dir) if ctx.clips_dir else None
    if clips_dir and clips_dir.is_dir():
        for clip in sorted(clips_dir.glob("*.mp4")):
            entries.append(
                ManifestEntry(
                    kind="clip",
                    path=clip.relative_to(Path(ctx.output_dir)).as_posix(),
                    bytes=clip.stat().st_size,
                    sha256=_sha256_of(clip),
                    present=True,
                )
            )

    metadata_json = Path(ctx.output_dir) / "metadata.json"
    if metadata_json.is_file():
        entries.append(
            ManifestEntry(
                kind="metadata",
                path="metadata.json",
                bytes=metadata_json.stat().st_size,
                sha256=_sha256_of(metadata_json),
                present=True,
            )
        )

    execution_json = Path(ctx.output_dir) / "execution_manifest.json"
    if execution_json.is_file():
        entries.append(
            ManifestEntry(
                kind="execution_manifest",
                path="execution_manifest.json",
                bytes=execution_json.stat().st_size,
                sha256=_sha256_of(execution_json),
                present=True,
            )
        )

    return entries


def build_deliverable_manifest(ctx: Context) -> DeliverableManifest:
    """Build the manifest object for *ctx* without writing it."""
    generation_mode = "preview" if ctx.metadata.get("render_preview_mode") else "full"
    return DeliverableManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        package_version=__version__,
        contract_version=_contract_version_string(),
        generated_at=datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z"),
        movie=ctx.movie_name,
        generation_mode=generation_mode,
        artifacts=_collect_artifacts(ctx),
        qa=_collect_qa(ctx),
    )


def write_deliverable_manifest(ctx: Context) -> Path:
    """Write ``deliverable_manifest.json`` atomically and return its path.

    Collects the run's artifacts (final/preview video, narration audio,
    SRT variants, script.md, clips/*.mp4, metadata.json,
    execution_manifest.json), checksums them (streamed SHA-256), and
    writes the manifest into ``ctx.output_dir`` via temp file +
    :func:`os.replace`.

    Core artifact kinds (video/audio/subtitle) always appear — missing
    ones carry ``present=false``. Best-effort from the caller's
    perspective: the runner treats manifest failures as non-fatal.

    Args:
        ctx: Pipeline context (after the pipeline completed).

    Returns:
        The path of the written manifest.
    """
    manifest = build_deliverable_manifest(ctx)
    out_dir = Path(ctx.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    final_path = out_dir / MANIFEST_FILENAME
    tmp_path = out_dir / (MANIFEST_FILENAME + ".tmp")
    tmp_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp_path, final_path)  # atomic on the same filesystem
    return final_path
