# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Community preset sharing — install, inspect, and load YAML presets (v1.5.1).

Community presets are **data files, not code** (ADR-018). A preset is a
YAML document with two parts:

1. a ``preset:`` metadata block (``name`` required; ``description``,
   ``author``, ``license``, ``min_engine`` optional), and
2. optional job-params keys (the same top-level keys a ``job.yaml``
   accepts), validated against the shared :data:`_ALLOWED_TOP`
   whitelist and the :class:`~movie_narrator.workflow.schema.JobConfig`
   schema imported from ``workflow/load.py``.

No code is ever executed: installing, listing, or applying a community
preset only ever parses and validates YAML data. The installed file is
stored under ``~/.movie-narrator/presets/<name>.yaml`` together with a
``registry.json`` index (name, source, sha256, installed_at, metadata).

Resolution rule (documented in ``docs/TUTORIAL.md``): **built-in
presets win** — :func:`~movie_narrator.presets.get_preset` falls back to
an installed community preset only when no built-in preset matches the
requested name.

Typical usage::

    from movie_narrator.presets.community import (
        install_preset, list_installed, uninstall_preset,
    )

    installed = install_preset("https://example.com/presets/slow-burn.yaml")
    install_preset("~/my-preset.yaml")
    for item in list_installed():
        print(item.name, item.source)
    uninstall_preset("slow-burn")
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
import yaml

from ..workflow.load import _ALLOWED_TOP as _JOB_TOP_KEYS
from ..workflow.load import _format_validation_error
from ..workflow.schema import JobConfig

# ── Constants ───────────────────────────────────────────────

#: User-level storage directory for installed community presets.
#: Same convention as the prompt cache (``utils/prompt_cache.py``) and
#: the GPU capability cache: everything lives under ``~/.movie-narrator``.
_PRESETS_DIR = Path.home() / ".movie-narrator" / "presets"

#: Registry index file, co-located with the installed preset files.
REGISTRY_FILENAME = "registry.json"

#: Maximum size of a preset document, from any source. Presets are
#: small style bundles; anything larger is rejected before parsing.
MAX_PRESET_BYTES = 256 * 1024

#: Network timeout for ``https://`` installs (seconds).
INSTALL_TIMEOUT_SECONDS = 15.0

#: Metadata keys allowed inside the top-level ``preset:`` block.
_PRESET_META_KEYS = frozenset({"name", "description", "author", "license", "min_engine"})

#: Conservative preset-name pattern — the name doubles as the on-disk
#: filename, so only filesystem-safe characters are accepted.
_NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: Strict ``X.Y.Z`` prefix pattern for the optional ``min_engine`` field.
_ENGINE_VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)")

_LOCK = threading.Lock()


def _default_presets_dir() -> Path:
    """Return the user-level community preset directory (overridable in tests)."""
    return _PRESETS_DIR


class CommunityPresetError(Exception):
    """Raised when a community preset document or install source is invalid."""


@dataclass(frozen=True)
class InstalledPreset:
    """One installed community preset as recorded in the registry.

    Attributes:
        name: Registry key — always the YAML document's ``preset.name``,
            never the source filename (the two may differ).
        source: Where the preset was installed from (URL or local path).
        sha256: Hex digest of the installed file's raw bytes.
        installed_at: UTC ISO-8601 timestamp of the install.
        description / author / license / min_engine: Metadata from the
            ``preset:`` block.
        filename: File name of the installed YAML inside the presets dir.
    """

    name: str
    source: str
    sha256: str
    installed_at: str
    description: str = ""
    author: str = ""
    license: str = ""
    min_engine: str = ""
    filename: str = ""


@dataclass(frozen=True)
class _ValidatedDocument:
    """Internal result of validating one community preset document."""

    meta_name: str
    description: str
    author: str
    license: str
    min_engine: str
    job: Dict[str, Any]


# ── Validation ──────────────────────────────────────────────


def _parse_min_engine(raw: str) -> Tuple[int, int, int]:
    """Parse a ``min_engine`` value; raise :class:`CommunityPresetError` if malformed."""
    match = _ENGINE_VERSION_PATTERN.match(raw.strip())
    if match is None:
        raise CommunityPresetError(f"invalid min_engine {raw!r} — expected 'X.Y.Z'")
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def _check_min_engine(raw: str) -> None:
    """Reject presets that require an engine newer than the running one."""
    from .. import __version__

    current_match = _ENGINE_VERSION_PATTERN.match(__version__)
    required = _parse_min_engine(raw)
    if current_match is None:
        # Undecorable running version — accept the preset rather than
        # block installs on a non-semver __version__.
        return
    current = (int(current_match.group(1)), int(current_match.group(2)), int(current_match.group(3)))
    if required > current:
        raise CommunityPresetError(
            f"preset requires engine >={raw!r} but running engine is {__version__!r}"
        )


def validate_preset_document(data: Any) -> _ValidatedDocument:
    """Validate a parsed community preset document (data-only, no code).

    Checks, in order:

    1. the document is a mapping;
    2. the ``preset:`` block exists, is a mapping, carries a non-empty
       filesystem-safe ``name``, and has no unknown metadata keys;
    3. every remaining top-level key is in the shared job-config
       whitelist (``workflow/load.py`` ``_ALLOWED_TOP``);
    4. the job part validates against :class:`JobConfig` — the same
       schema ``mn create --config`` enforces, including the
       ``params``/``steps`` sub-whitelists (``extra="forbid"``).

    Returns:
        The validated metadata + job part.

    Raises:
        :class:`CommunityPresetError` on any violation.
    """
    if not isinstance(data, dict):
        raise CommunityPresetError("community preset document must be a mapping")

    preset_block = data.get("preset")
    if not isinstance(preset_block, dict):
        raise CommunityPresetError("missing required 'preset' block (preset.name is required)")

    unknown_meta = [k for k in preset_block if k not in _PRESET_META_KEYS]
    if unknown_meta:
        raise CommunityPresetError(
            f"unknown key: 'preset.{unknown_meta[0]}' "
            f"(allowed: {', '.join(sorted(_PRESET_META_KEYS))})"
        )

    name = preset_block.get("name")
    if not isinstance(name, str) or not name.strip():
        raise CommunityPresetError("preset.name is required and must be a non-empty string")
    if _NAME_PATTERN.match(name.strip()) is None:
        raise CommunityPresetError(
            f"invalid preset.name {name!r} — use 1-64 chars: letters, digits, '.', '_', '-'"
        )
    name = name.strip()

    def _meta_str(key: str) -> str:
        val = preset_block.get(key)
        if val is None:
            return ""
        if not isinstance(val, str):
            raise CommunityPresetError(f"preset.{key} must be a string")
        return val

    min_engine = _meta_str("min_engine")
    if min_engine:
        _check_min_engine(min_engine)

    job: Dict[str, Any] = {k: v for k, v in data.items() if k != "preset"}
    unknown_top = [k for k in job if k not in _JOB_TOP_KEYS]
    if unknown_top:
        raise CommunityPresetError(
            f"unknown key: '{unknown_top[0]}' (allowed: {', '.join(_JOB_TOP_KEYS)})"
        )
    try:
        JobConfig.model_validate(job)
    except Exception as e:  # noqa: BLE001 — surfaced as a single validation error
        # ValidationError from pydantic (or JobConfigError-shaped text via
        # the shared formatter in workflow/load.py).
        try:
            from pydantic import ValidationError

            if isinstance(e, ValidationError):
                raise CommunityPresetError(_format_validation_error(e)) from e
        except ImportError:  # pragma: no cover — pydantic is a hard dependency
            pass
        raise CommunityPresetError(str(e)) from e

    return _ValidatedDocument(
        meta_name=name,
        description=_meta_str("description"),
        author=_meta_str("author"),
        license=_meta_str("license"),
        min_engine=min_engine,
        job=job,
    )


# ── Registry / storage helpers ──────────────────────────────


def _registry_path(presets_dir: Path) -> Path:
    return presets_dir / REGISTRY_FILENAME


def _read_registry(presets_dir: Path) -> Dict[str, Dict[str, Any]]:
    """Load ``registry.json``; a missing file means an empty registry."""
    path = _registry_path(presets_dir)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise CommunityPresetError(f"community preset registry is unreadable: {path} ({e})") from e
    presets = data.get("presets") if isinstance(data, dict) else None
    if not isinstance(presets, dict):
        raise CommunityPresetError(f"community preset registry is corrupt: {path}")
    return presets


def _write_registry(presets_dir: Path, presets: Dict[str, Dict[str, Any]]) -> None:
    """Atomically persist ``registry.json`` (temp file + ``os.replace``)."""
    presets_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=str(presets_dir), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"presets": presets}, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, _registry_path(presets_dir))
    except BaseException:
        with suppress(OSError):
            os.unlink(tmp_path)
        raise


def _entry_to_installed(name: str, entry: Dict[str, Any]) -> InstalledPreset:
    meta = entry.get("metadata") or {}
    if not isinstance(meta, dict):
        meta = {}
    return InstalledPreset(
        name=name,
        source=str(entry.get("source", "")),
        sha256=str(entry.get("sha256", "")),
        installed_at=str(entry.get("installed_at", "")),
        description=str(meta.get("description", "")),
        author=str(meta.get("author", "")),
        license=str(meta.get("license", "")),
        min_engine=str(meta.get("min_engine", "")),
        filename=str(meta.get("filename", "")),
    )


# ── Source readers ──────────────────────────────────────────


def _fetch_https(source: str, transport: Optional[httpx.BaseTransport]) -> bytes:
    """Download a preset over ``https://`` with timeout + size cap."""
    if source.startswith("http://"):
        raise CommunityPresetError(
            "insecure http:// is not supported for preset installs — use https://"
        )
    if not source.startswith("https://"):
        raise CommunityPresetError(f"unsupported preset source: {source!r}")
    try:
        with httpx.Client(
            timeout=INSTALL_TIMEOUT_SECONDS,
            follow_redirects=True,
            transport=transport,
        ) as client:
            with client.stream("GET", source) as resp:
                resp.raise_for_status()
                declared = resp.headers.get("Content-Length")
                if declared is not None and int(declared) > MAX_PRESET_BYTES:
                    raise CommunityPresetError(
                        f"preset exceeds the {MAX_PRESET_BYTES // 1024} KiB size cap"
                    )
                chunks: List[bytes] = []
                received = 0
                for chunk in resp.iter_bytes():
                    received += len(chunk)
                    if received > MAX_PRESET_BYTES:
                        raise CommunityPresetError(
                            f"preset exceeds the {MAX_PRESET_BYTES // 1024} KiB size cap"
                        )
                    chunks.append(chunk)
        return b"".join(chunks)
    except httpx.HTTPError as e:
        raise CommunityPresetError(f"failed to download preset from {source}: {e}") from e


def _read_local(source: str) -> bytes:
    """Read a preset from a local file path with the size cap applied."""
    path = Path(source).expanduser()
    if not path.is_file():
        raise CommunityPresetError(f"preset file not found: {path}")
    size = path.stat().st_size
    if size > MAX_PRESET_BYTES:
        raise CommunityPresetError(
            f"preset exceeds the {MAX_PRESET_BYTES // 1024} KiB size cap"
        )
    try:
        return path.read_bytes()
    except OSError as e:
        raise CommunityPresetError(f"failed to read preset file {path}: {e}") from e


# ── Public API ──────────────────────────────────────────────


def install_preset(
    source: str,
    *,
    transport: Optional[httpx.BaseTransport] = None,
    presets_dir: Optional[Union[str, Path]] = None,
) -> InstalledPreset:
    """Install a community preset from an ``https://`` URL or a local file path.

    The YAML document is validated (data-only, no code execution), its
    ``preset.name`` becomes the registry key, and the file is stored
    atomically as ``<name>.yaml`` inside the presets directory.  A
    sha256 of the raw bytes is recorded; reinstalling the same or a
    different source under the same name atomically overwrites the
    previous file and registry entry.

    Args:
        source: ``https://`` URL (``http://`` is rejected) or a local
            filesystem path.
        transport: Optional ``httpx.BaseTransport`` override — tests
            inject ``httpx.MockTransport`` (no network in unit tests).
        presets_dir: Optional storage directory override (tests).

    Returns:
        The :class:`InstalledPreset` that was recorded.

    Raises:
        :class:`CommunityPresetError` on download, size-cap, or
            validation failures.
    """
    dir_path = Path(presets_dir) if presets_dir is not None else _default_presets_dir()
    scheme_match = re.match(r"^([a-zA-Z][a-zA-Z0-9+.-]*)://", source)
    if scheme_match is not None and scheme_match.group(1).lower() != "https":
        if scheme_match.group(1).lower() == "http":
            raise CommunityPresetError(
                "insecure http:// is not supported for preset installs — use https://"
            )
        raise CommunityPresetError(f"unsupported preset source: {source!r}")
    if source.startswith("https://"):
        raw = _fetch_https(source, transport)
    else:
        raw = _read_local(source)

    try:
        document = yaml.safe_load(raw.decode("utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError) as e:
        raise CommunityPresetError(f"invalid YAML in preset source: {e}") from e

    validated = validate_preset_document(document)
    digest = hashlib.sha256(raw).hexdigest()
    filename = f"{validated.meta_name}.yaml"
    installed_at = datetime.now(timezone.utc).isoformat()

    with _LOCK:
        dir_path.mkdir(parents=True, exist_ok=True)
        # Atomic file write: a crash mid-install can never leave a
        # half-written preset file behind.
        fd, tmp_path = tempfile.mkstemp(dir=str(dir_path), suffix=".yaml.tmp")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(raw)
            os.replace(tmp_path, dir_path / filename)
        except BaseException:
            with suppress(OSError):
                os.unlink(tmp_path)
            raise

        registry = _read_registry(dir_path)
        registry[validated.meta_name] = {
            "source": source,
            "sha256": digest,
            "installed_at": installed_at,
            "metadata": {
                "description": validated.description,
                "author": validated.author,
                "license": validated.license,
                "min_engine": validated.min_engine,
                "filename": filename,
            },
        }
        _write_registry(dir_path, registry)

    return InstalledPreset(
        name=validated.meta_name,
        source=source,
        sha256=digest,
        installed_at=installed_at,
        description=validated.description,
        author=validated.author,
        license=validated.license,
        min_engine=validated.min_engine,
        filename=filename,
    )


def list_installed(presets_dir: Optional[Union[str, Path]] = None) -> List[InstalledPreset]:
    """List installed community presets, sorted by name.

    Returns:
        Zero or more :class:`InstalledPreset` records (empty when none
        are installed).
    """
    dir_path = Path(presets_dir) if presets_dir is not None else _default_presets_dir()
    with _LOCK:
        registry = _read_registry(dir_path)
    return [_entry_to_installed(name, entry) for name, entry in sorted(registry.items())]


def uninstall_preset(name: str, presets_dir: Optional[Union[str, Path]] = None) -> None:
    """Remove an installed community preset (file + registry entry).

    Raises:
        KeyError: When *name* is not an installed community preset.
    """
    dir_path = Path(presets_dir) if presets_dir is not None else _default_presets_dir()
    with _LOCK:
        registry = _read_registry(dir_path)
        entry = registry.pop(name, None)
        if entry is None:
            installed = ", ".join(sorted(registry)) or "none"
            raise KeyError(f"Unknown community preset '{name}'. Installed: {installed}")
        meta = entry.get("metadata") or {}
        filename = str(meta.get("filename", "")) if isinstance(meta, dict) else ""
        if filename:
            (dir_path / filename).unlink(missing_ok=True)
        _write_registry(dir_path, registry)


def load_community_preset(
    name: str, presets_dir: Optional[Union[str, Path]] = None
) -> Dict[str, Any]:
    """Load and re-validate an installed community preset document.

    The file is re-validated on every load so a hand-edited install
    cannot smuggle in keys that the original install rejected.

    Returns:
        The validated YAML document (including its ``preset:`` block).

    Raises:
        KeyError: When *name* is not an installed community preset.
        :class:`CommunityPresetError`: When the installed file is
            missing or no longer valid.
    """
    dir_path = Path(presets_dir) if presets_dir is not None else _default_presets_dir()
    with _LOCK:
        registry = _read_registry(dir_path)
        entry = registry.get(name)
        if entry is None:
            installed = ", ".join(sorted(registry)) or "none"
            raise KeyError(f"Unknown community preset '{name}'. Installed: {installed}")
        meta = entry.get("metadata") or {}
        filename = str(meta.get("filename", "")) if isinstance(meta, dict) else ""
    path = dir_path / (filename or f"{name}.yaml")
    if not path.is_file():
        raise CommunityPresetError(f"installed preset file is missing: {path}")
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, UnicodeDecodeError, OSError) as e:
        raise CommunityPresetError(f"installed preset {name!r} is unreadable: {e}") from e
    validate_preset_document(document)
    return document
