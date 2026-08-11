# SPDX-FileCopyrightText: 2026 zcbacxc
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared ffmpeg binary resolution for rendering and QA utilities.

Extracted from ``deliverable_qa._ffmpeg_bin`` so that every module that
shells out to ffmpeg (rendering, clip export, frame sampling, volume
probing, scene filtering, vision frame extraction, etc.) resolves the
binary with the same policy.

Policy (full-build first, then system, then bare name):
    1. ``MN_FFMPEG_BIN`` — explicit override, gives external users a way to
       force their own ffmpeg/ffprobe build regardless of anything else.
    2. imageio-ffmpeg bundled binary — a full-featured build that always
       ships the encoders/filters this project needs (``aac``, ``libx264``,
       ``image2``, ``sidechaincompress``, ...) and is immune to PATH
       shadowing by a crippled system build (e.g. FFmpeg compiled with
       ``--disable-everything``).
    3. A system ``ffmpeg`` on ``PATH``.
    4. The bare ``"ffmpeg"`` name (letting subprocess raise if none exists).
"""

from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger(__name__)

#: Environment variable that lets external users force a specific ffmpeg.
_FFMPEG_OVERRIDE_ENV = "MN_FFMPEG_BIN"


def _bundled_ffmpeg() -> str | None:
    """Return the imageio-ffmpeg bundled binary, or ``None`` if unavailable."""
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        logger.debug("imageio-ffmpeg bundled binary lookup failed", exc_info=True)
        return None


def ffmpeg_bin() -> str:
    """Return a usable ffmpeg binary path.

    Prefers the imageio-ffmpeg bundled build (a full-featured build immune to
    crippled system ffmpeg shadowing), falls back to a system ``ffmpeg`` on
    PATH, and finally returns ``"ffmpeg"`` as a last resort (subprocess will
    raise if it is not actually installed). An explicit ``MN_FFMPEG_BIN``
    override takes precedence over all of the above.
    """
    override = os.environ.get(_FFMPEG_OVERRIDE_ENV)
    if override and os.path.isfile(override):
        return override

    bundled = _bundled_ffmpeg()
    if bundled:
        return bundled

    sys_ffmpeg = shutil.which("ffmpeg")
    if sys_ffmpeg:
        return sys_ffmpeg

    return "ffmpeg"  # last resort — let subprocess raise
