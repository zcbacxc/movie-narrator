# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Optional dependency probing."""

from enum import Enum
from importlib import import_module
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# Maps a logical probe key to the root module that must be importable.
_MODULE_NAMES = {
    "scenedetect": "scenedetect",
    "whisperx": "whisperx",
    "faster_whisper": "faster_whisper",
    "funasr": "funasr",
    "sentence_transformers": "sentence_transformers",
}

_HINTS = {
    "scenedetect": 'pip install "movie-narrator[media]"',
    "whisperx": 'pip install "movie-narrator[ml]"',
    "faster_whisper": 'pip install "movie-narrator[ml]"',
    "funasr": 'pip install "movie-narrator[ml]"',
    "sentence_transformers": 'pip install "movie-narrator[ml]"',
}


class DepStatus(str, Enum):
    """Availability of an optional dependency.

    Values are stable strings so they can be surfaced in reports / serialized.
    """

    OK = "ok"
    NOT_INSTALLED = "not_installed"
    MISSING_DEPS = "missing_deps"


def probe_status(name: str) -> Tuple[DepStatus, str]:
    """Classify an optional dependency's availability.

    Distinguishes three states:

    - ``OK`` — the module imports cleanly; the hint is empty.
    - ``NOT_INSTALLED`` — the module itself is absent.
    - ``MISSING_DEPS`` — the module is installed but a transitive dependency
      failed to import (e.g. ``torchaudio`` for ``whisperx``).

    Returns:
        (status, hint) where hint is empty on success and otherwise a precise
        remediation message.
    """
    mod = _MODULE_NAMES.get(name, name)
    install_line = _HINTS.get(name, f"install dependency for {name}")
    try:
        import_module(mod)
        return DepStatus.OK, ""
    except ModuleNotFoundError as exc:
        missing = getattr(exc, "name", None)
        if missing and missing != mod:
            return DepStatus.MISSING_DEPS, (
                f"'{mod}' is installed but a dependency is missing: "
                f"'{missing}'. Run: pip install {missing}"
            )
        return DepStatus.NOT_INSTALLED, (
            f"'{mod}' is not installed. Run: {install_line}"
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("probe '%s' raised %s", name, type(exc).__name__, exc_info=True)
        return DepStatus.MISSING_DEPS, (
            f"'{mod}' import raised {type(exc).__name__}: {exc}. "
            f"Run: {install_line}"
        )


def probe(name: str) -> Tuple[bool, str]:
    """Return whether an optional dependency is usable.

    This is the lightweight wrapper used by pipeline steps; it collapses the
    three-state :func:`probe_status` into a boolean plus a remediation hint.

    Returns:
        (available, hint) — hint is empty when available.
    """
    status, hint = probe_status(name)
    return status is DepStatus.OK, hint
