# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Optional dependency probing."""

from importlib import import_module
import logging
from typing import Tuple

logger = logging.getLogger(__name__)

_HINTS = {
    "scenedetect": 'pip install "movie-narrator[media]"',
    "whisperx": 'pip install "movie-narrator[ml]"',
    "faster_whisper": 'pip install "movie-narrator[ml]"',
    "sentence_transformers": 'pip install "movie-narrator[ml]"',
}


def probe(name: str) -> Tuple[bool, str]:
    """
    Returns:
        (available, install_hint).
    """
    module_names = {
        "scenedetect": "scenedetect",
        "whisperx": "whisperx",
        "faster_whisper": "faster_whisper",
        "sentence_transformers": "sentence_transformers",
    }
    mod = module_names.get(name, name)
    try:
        import_module(mod)
        return True, ""
    except Exception:  # noqa: BLE001
        logger.debug("Optional dependency '%s' not available", name, exc_info=True)
        return False, _HINTS.get(name, f"install dependency for {name}")
